import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.encryption import decrypt_secret
from app.db.session import AsyncSessionLocal
from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail, ProcessingStatus
from app.models.provider import Provider
from app.services.email_extractor import ExtractedEmail, extract_email
from app.services.gmail_imap import GmailConnectionError, GmailImapClient, MAILBOX, RawEmailFetch, safe_log_text
from app.services.deduplication import is_duplicate_payment_email_error
from app.services.listener_state import get_last_uid, set_last_uid, write_heartbeat
from app.services.payment_email_parser import get_email_for_parsing, parse_payment_email_safely

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountPollTarget:
    id: int
    friendly_name: str
    gmail_address: str
    app_password: str
    last_uid: int


async def load_enabled_accounts() -> list[AccountPollTarget]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PaymentAccount)
            .join(PaymentAccount.provider)
            .options(selectinload(PaymentAccount.provider))
            .where(PaymentAccount.enabled.is_(True), Provider.enabled.is_(True))
            .order_by(PaymentAccount.id)
        )
        accounts = list(result.scalars().all())
        targets: list[AccountPollTarget] = []
        for account in accounts:
            try:
                targets.append(
                    AccountPollTarget(
                        id=account.id,
                        friendly_name=account.friendly_name,
                        gmail_address=account.gmail_address,
                        app_password=decrypt_secret(account.encrypted_app_password),
                        last_uid=await get_last_uid(session, account.id, MAILBOX),
                    )
                )
            except ValueError:
                account.listener_status = "error"
        await session.commit()
        return targets


async def update_account_status(account_id: int, status: str, checked_at: datetime) -> None:
    async with AsyncSessionLocal() as session:
        account = await session.get(PaymentAccount, account_id)
        if account is not None:
            account.listener_status = status
            account.last_checked_at = checked_at
            await session.commit()


async def persist_captured_email(
    account_id: int,
    raw_fetch: RawEmailFetch,
    extracted: ExtractedEmail,
    mailbox: str = MAILBOX,
) -> bool:
    async with AsyncSessionLocal() as session:
        account = await session.get(PaymentAccount, account_id)
        payment_email = PaymentEmail(
            payment_account_id=account_id,
            gmail_message_id=extracted.gmail_message_id,
            gmail_uid=raw_fetch.gmail_uid,
            mailbox=mailbox,
            sender_address=extracted.sender_address,
            subject=extracted.subject,
            received_at=extracted.received_at,
            raw_text=extracted.raw_text,
            raw_html=extracted.raw_html,
            raw_headers_json=extracted.raw_headers_json,
        )
        session.add(payment_email)
        if account is not None:
            account.last_email_at = extracted.received_at or datetime.now(UTC)

        try:
            await set_last_uid(session, account_id, raw_fetch.gmail_uid, mailbox)
            await session.commit()
            logger.info(
                "Captured email inserted account_id=%s uid=%s message_id=%s subject=%s",
                account_id,
                raw_fetch.gmail_uid,
                safe_log_text(extracted.gmail_message_id),
                safe_log_text(extracted.subject),
            )
            await parse_captured_email(payment_email.id)
            logger.info("Parser invoked account_id=%s uid=%s email_id=%s", account_id, raw_fetch.gmail_uid, payment_email.id)
            return True
        except IntegrityError as exc:
            await session.rollback()
            if not is_duplicate_payment_email_error(exc):
                raise
            logger.debug(
                "Captured email duplicate skipped account_id=%s uid=%s message_id=%s subject=%s",
                account_id,
                raw_fetch.gmail_uid,
                safe_log_text(extracted.gmail_message_id),
                safe_log_text(extracted.subject),
            )
            async with AsyncSessionLocal() as checkpoint_session:
                await set_last_uid(checkpoint_session, account_id, raw_fetch.gmail_uid, mailbox)
                await checkpoint_session.commit()
            return False


async def parse_captured_email(email_id: int) -> None:
    async with AsyncSessionLocal() as session:
        email = await get_email_for_parsing(session, email_id)
        if email is not None:
            await parse_payment_email_safely(session, email)


async def mark_email_failure(account_id: int, raw_fetch: RawEmailFetch, error: str, mailbox: str = MAILBOX) -> None:
    async with AsyncSessionLocal() as session:
        failed_email = PaymentEmail(
            payment_account_id=account_id,
            gmail_uid=raw_fetch.gmail_uid,
            mailbox=mailbox,
            processing_status=ProcessingStatus.FAILED,
            processing_error=error[:1000],
        )
        session.add(failed_email)
        try:
            await set_last_uid(session, account_id, raw_fetch.gmail_uid, mailbox)
            await session.commit()
            logger.info(
                "Captured email failure recorded account_id=%s uid=%s reason=%s",
                account_id,
                raw_fetch.gmail_uid,
                safe_log_text(error, 500),
            )
        except IntegrityError:
            await session.rollback()


async def fetch_account_messages(target: AccountPollTarget) -> list[RawEmailFetch]:
    def fetch() -> list[RawEmailFetch]:
        with GmailImapClient(target.gmail_address, target.app_password) as client:
            return client.fetch_new_messages(target.last_uid, settings.gmail_read_batch_size, MAILBOX)

    return await asyncio.to_thread(fetch)


async def poll_account(target: AccountPollTarget) -> None:
    checked_at = datetime.now(UTC)
    logger.info(
        "Polling Gmail account id=%s friendly=%s mailbox=%s checkpoint=%s",
        target.id,
        safe_log_text(target.friendly_name),
        MAILBOX,
        target.last_uid,
    )
    try:
        raw_messages = await fetch_account_messages(target)
    except GmailConnectionError:
        await update_account_status(target.id, "error", checked_at)
        return
    except Exception:
        await update_account_status(target.id, "error", checked_at)
        return

    await update_account_status(target.id, "connected", checked_at)
    logger.info("Gmail account id=%s fetched_count=%s", target.id, len(raw_messages))
    for raw_fetch in raw_messages:
        try:
            extracted = extract_email(raw_fetch.raw_message)
            logger.debug(
                "Fetched email account_id=%s uid=%s message_id=%s sender=%s subject=%s received=%s",
                target.id,
                raw_fetch.gmail_uid,
                safe_log_text(extracted.gmail_message_id),
                safe_log_text(extracted.sender_address),
                safe_log_text(extracted.subject),
                extracted.received_at,
            )
            await persist_captured_email(target.id, raw_fetch, extracted)
        except Exception as exc:  # Keep one malformed email from stopping the account.
            logger.info(
                "Fetched email skipped account_id=%s uid=%s reason=%s",
                target.id,
                raw_fetch.gmail_uid,
                safe_log_text(exc, 500),
            )
            await mark_email_failure(target.id, raw_fetch, str(exc))


async def heartbeat(status: str = "alive") -> None:
    async with AsyncSessionLocal() as session:
        await write_heartbeat(session, status)
        await session.commit()


async def run_listener(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await heartbeat("alive")
        targets = await load_enabled_accounts()
        for target in targets:
            if stop_event.is_set():
                break
            await poll_account(target)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.gmail_poll_interval_seconds)
        except asyncio.TimeoutError:
            continue

    await heartbeat("stopped")


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda _signum, _frame: stop_event.set())


async def main_async() -> None:
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    await run_listener(stop_event)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
