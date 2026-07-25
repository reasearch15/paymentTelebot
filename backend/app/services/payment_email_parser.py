from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail, ProcessingStatus
from app.parsers.base import ParserInput, ParserResult
from app.parsers.extraction import (
    extract_amounts,
    extract_datetime_candidates,
    extract_payment_tags,
    html_to_visible_text,
    nearby_label_value,
    normalize_whitespace,
)
from app.parsers.registry import get_parser
from app.services.chime_email_auth import validate_chime_email_authenticity
from app.services.ledger import create_transaction_from_parser_result
from app.services.telegram import send_transaction_notification

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParserInspection:
    normalized_subject: str
    normalized_plain_text: str
    visible_text_from_html: str
    detected_dollar_prefixed_tags: list[str]
    detected_monetary_amounts: list[int]
    detected_date_time_candidates: list[str]
    detected_sender_name_candidates: list[str]
    gmail_message_id: str | None
    provider_name: str
    parser_key: str
    parser_version: str
    parsed_result: dict | None

    def to_json(self) -> dict:
        return asdict(self)


async def get_email_for_parsing(session: AsyncSession, email_id: int) -> PaymentEmail | None:
    result = await session.execute(
        select(PaymentEmail)
        .options(selectinload(PaymentEmail.payment_account).selectinload(PaymentAccount.provider))
        .where(PaymentEmail.id == email_id)
    )
    return result.scalar_one_or_none()


def build_parser_input(email: PaymentEmail) -> ParserInput:
    return ParserInput(
        subject=email.subject,
        raw_text=email.raw_text,
        html_visible_text=html_to_visible_text(email.raw_html),
        headers=email.raw_headers_json or {},
        received_at=email.received_at,
        sender_address=getattr(email, "sender_address", None),
        gmail_message_id=getattr(email, "gmail_message_id", None),
        payment_account_id=getattr(email, "payment_account_id", None),
        payment_account_friendly_name=getattr(email.payment_account, "friendly_name", None),
        provider=email.payment_account.provider.parser_key,
    )


def parser_result_status(result: ParserResult) -> ProcessingStatus:
    if result.is_payment and not result.missing_fields:
        return ProcessingStatus.PARSED
    if result.is_payment:
        return ProcessingStatus.FAILED
    return ProcessingStatus.IGNORED


def _auth_rejected_parser_result(parser_key: str, parser_version: str, reason: str) -> ParserResult:
    return ParserResult(
        classification="unknown",
        is_payment=False,
        direction=None,
        amount_cents=None,
        sender_name=None,
        sender_payment_tag=None,
        receiver_tag=None,
        payment_timestamp=None,
        provider_reference=None,
        confidence=0.0,
        missing_fields=[],
        parser_key=parser_key,
        parser_version=parser_version,
        debug_evidence={"email_auth_rejected": reason},
    )


async def _mark_chime_auth_rejected(
    session: AsyncSession,
    email: PaymentEmail,
    *,
    parser_key: str,
    parser_version: str,
    reason: str,
) -> ParserResult:
    result = _auth_rejected_parser_result(parser_key, parser_version, reason)
    email.parser_key = parser_key
    email.parser_version = parser_version
    email.parsed_payload_json = result.to_json()
    email.parsed_at = datetime.now(UTC)
    email.processing_status = ProcessingStatus.IGNORED
    email.processing_error = f"email_auth_rejected:{reason}"
    await session.commit()
    logger.info(
        "Skipped chime email %s: authenticity rejected reason=%s",
        email.id,
        reason,
    )
    return result


async def parse_payment_email(session: AsyncSession, email: PaymentEmail) -> ParserResult:
    try:
        parser = get_parser(email.payment_account.provider.parser_key)
        parser_input = build_parser_input(email)

        # Fail-closed Chime authenticity gate before amount/name parsing can create a transaction.
        if parser.parser_key == "chime":
            auth = validate_chime_email_authenticity(
                parser_input.headers,
                subject=parser_input.subject,
                raw_text=parser_input.raw_text,
                html_visible_text=parser_input.html_visible_text,
                sender_address=parser_input.sender_address,
            )
            if not auth.accepted:
                return await _mark_chime_auth_rejected(
                    session,
                    email,
                    parser_key=parser.parser_key,
                    parser_version=parser.parser_version,
                    reason=auth.reason,
                )

        result = parser.parse(parser_input)
        if result.parser_key == "chime":
            logger.info("Chime email detected for account %s", email.payment_account_id)
        logger.info(
            "Parser result key=%s classification=%s amount_cents=%s name=%s",
            result.parser_key,
            result.classification,
            result.amount_cents,
            result.sender_name,
        )
        transaction = await create_transaction_from_parser_result(session, email, result)
        email.parser_key = result.parser_key
        email.parser_version = result.parser_version
        email.parsed_payload_json = result.to_json()
        email.parsed_at = datetime.now(UTC)
        email.processing_status = parser_result_status(result)
        email.processing_error = None
        if email.processing_status == ProcessingStatus.PARSED and transaction is None:
            # Parsed means the payment pipeline produced a durable ledger row.
            email.processing_status = ProcessingStatus.FAILED
            email.processing_error = "Parser completed a payment but no ledger transaction was created."
        elif email.processing_status == ProcessingStatus.FAILED:
            email.processing_error = "Parser result missing required fields: " + ", ".join(result.missing_fields)
        elif email.processing_status == ProcessingStatus.IGNORED:
            logger.info("Skipped %s email %s: %s", result.parser_key, email.id, result.classification)
        await session.commit()
        if transaction is not None and result.classification == "incoming_payment":
            await send_transaction_notification(transaction.id)
        return result
    except Exception as exc:
        email.processing_status = ProcessingStatus.FAILED
        email.processing_error = safe_parser_error(exc)
        email.parsed_at = datetime.now(UTC)
        await session.commit()
        raise


async def parse_payment_email_safely(session: AsyncSession, email: PaymentEmail) -> ParserResult | None:
    try:
        return await parse_payment_email(session, email)
    except Exception:
        return None


def inspect_payment_email(email: PaymentEmail) -> ParserInspection:
    parser = get_parser(email.payment_account.provider.parser_key)
    subject = normalize_whitespace(email.subject)
    plain_text = normalize_whitespace(email.raw_text)
    visible_html = html_to_visible_text(email.raw_html)
    combined = normalize_whitespace(f"{subject} {plain_text} {visible_html}")
    parser_input = build_parser_input(email)
    parsed = parser.parse(parser_input)
    return ParserInspection(
        normalized_subject=subject,
        normalized_plain_text=plain_text,
        visible_text_from_html=visible_html,
        detected_dollar_prefixed_tags=extract_payment_tags(combined),
        detected_monetary_amounts=extract_amounts(combined),
        detected_date_time_candidates=extract_datetime_candidates(combined),
        detected_sender_name_candidates=nearby_label_value(combined, ["Sender", "From", "Paid by"], max_chars=60),
        gmail_message_id=email.gmail_message_id,
        provider_name=email.payment_account.provider.name,
        parser_key=parser.parser_key,
        parser_version=parser.parser_version,
        parsed_result=parsed.to_json(),
    )


def safe_parser_error(exc: BaseException) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (message or exc.__class__.__name__)[:500]
