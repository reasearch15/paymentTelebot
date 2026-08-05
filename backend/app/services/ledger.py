from datetime import UTC, datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment_email import PaymentEmail
from app.models.transaction import Direction, Transaction
from app.parsers.base import ParserResult

logger = logging.getLogger(__name__)


def stable_email_identifier(email: PaymentEmail) -> str:
    if email.gmail_message_id:
        return email.gmail_message_id[:255]
    return f"account:{email.payment_account_id}:mailbox:{email.mailbox}:uid:{email.gmail_uid}"[:255]


async def create_transaction_from_parser_result(
    session: AsyncSession,
    email: PaymentEmail,
    result: ParserResult,
) -> tuple[Transaction | None, bool]:
    if not result.is_payment or result.direction is None or result.amount_cents is None:
        return None, False

    gmail_message_id = stable_email_identifier(email)
    existing = await session.scalar(select(Transaction).where(Transaction.gmail_message_id == gmail_message_id))
    if existing is not None:
        logger.info("Ledger transaction duplicate skipped for message %s", gmail_message_id)
        return existing, False

    received_at = result.payment_timestamp or email.received_at or datetime.now(UTC)
    transaction = Transaction(
        payment_account_id=email.payment_account_id,
        direction=Direction(result.direction),
        amount_cents=result.amount_cents,
        sender_name=result.sender_name,
        sender_payment_tag=result.sender_payment_tag,
        receiver_tag=result.receiver_tag,
        provider_reference=result.provider_reference,
        gmail_message_id=gmail_message_id,
        received_at=received_at,
        telegram_status="pending" if result.classification == "incoming_payment" else "not_applicable",
        raw_subject=email.subject,
        raw_payload_json=result.to_json(),
    )
    session.add(transaction)
    await session.flush()
    logger.info(
        "Ledger transaction created for message %s direction=%s amount_cents=%s",
        gmail_message_id,
        transaction.direction.value,
        transaction.amount_cents,
    )
    return transaction, True
