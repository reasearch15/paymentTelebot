import asyncio
import logging
import signal
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic, perf_counter

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.encryption import decrypt_secret
from app.db.session import AsyncSessionLocal
from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail, ProcessingStatus
from app.models.provider import Provider
from app.services.deduplication import is_duplicate_payment_email_error
from app.services.email_extractor import ExtractedEmail, extract_email
from app.services.gmail_imap import GmailConnectionError, GmailImapClient, MAILBOX, RawEmailFetch, safe_log_text
from app.services.listener_state import get_last_uid, set_last_uid, write_heartbeat
from app.services.payment_email_parser import get_email_for_parsing, parse_payment_email_safely

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# One processing lock per (account_id, mailbox) so IDLE / reconnect / safety poll never overlap.
_account_locks: dict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)


@dataclass(frozen=True)
class AccountPollTarget:
    id: int
    friendly_name: str
    gmail_address: str
    app_password: str
    last_uid: int

    @property
    def identity(self) -> tuple[int, str, str]:
        return (self.id, self.gmail_address, self.app_password)


def account_lock(account_id: int, mailbox: str = MAILBOX) -> asyncio.Lock:
    return _account_locks[(account_id, mailbox)]


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


async def load_account_checkpoint(account_id: int, mailbox: str = MAILBOX) -> int:
    async with AsyncSessionLocal() as session:
        return await get_last_uid(session, account_id, mailbox)


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
    persist_started = perf_counter()
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
            persist_ms = int((perf_counter() - persist_started) * 1000)
            logger.info(
                "Captured email inserted account_id=%s uid=%s message_id=%s subject=%s persist_ms=%s",
                account_id,
                raw_fetch.gmail_uid,
                safe_log_text(extracted.gmail_message_id),
                safe_log_text(extracted.subject),
                persist_ms,
            )
            parse_started = perf_counter()
            await parse_captured_email(payment_email.id)
            parse_ms = int((perf_counter() - parse_started) * 1000)
            logger.info(
                "Parser invoked account_id=%s uid=%s email_id=%s parse_ms=%s",
                account_id,
                raw_fetch.gmail_uid,
                payment_email.id,
                parse_ms,
            )
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


def _fetch_with_client(client: GmailImapClient, since_uid: int) -> list[RawEmailFetch]:
    return client.fetch_new_messages(since_uid, settings.gmail_read_batch_size, MAILBOX)


def _fetch_with_new_connection(gmail_address: str, app_password: str, since_uid: int) -> list[RawEmailFetch]:
    with GmailImapClient(gmail_address, app_password) as client:
        return _fetch_with_client(client, since_uid)


async def process_account_emails(
    target: AccountPollTarget,
    *,
    reason: str,
    client: GmailImapClient | None = None,
    detection_started: float | None = None,
) -> int:
    """
    Canonical mailbox processing path used by IDLE events, reconnect catch-up, and safety poll.

    Protected by one lock per (account_id, mailbox). Fetches only UID last_uid+1:*.
    """
    lock = account_lock(target.id, MAILBOX)
    async with lock:
        since_uid = await load_account_checkpoint(target.id, MAILBOX)
        fetch_started = perf_counter()
        try:
            if client is not None:
                raw_messages = await asyncio.to_thread(_fetch_with_client, client, since_uid)
            else:
                raw_messages = await asyncio.to_thread(
                    _fetch_with_new_connection,
                    target.gmail_address,
                    target.app_password,
                    since_uid,
                )
        except GmailConnectionError:
            await update_account_status(target.id, "error", datetime.now(UTC))
            raise

        fetch_ms = int((perf_counter() - fetch_started) * 1000)
        logger.info(
            "catchup_fetch=true reason=%s account_id=%s checkpoint=%s new_uid_count=%s fetch_ms=%s",
            reason,
            target.id,
            since_uid,
            len(raw_messages),
            fetch_ms,
        )
        await update_account_status(target.id, "connected", datetime.now(UTC))

        processed = 0
        for raw_fetch in raw_messages:
            try:
                extracted = extract_email(raw_fetch.raw_message)
                inserted = await persist_captured_email(target.id, raw_fetch, extracted)
                if inserted:
                    processed += 1
                    if detection_started is not None:
                        logger.info(
                            "gmail_detection_ms=%s account_id=%s uid=%s reason=%s",
                            int((perf_counter() - detection_started) * 1000),
                            target.id,
                            raw_fetch.gmail_uid,
                            reason,
                        )
            except Exception as exc:  # Keep one malformed email from stopping later UIDs.
                logger.info(
                    "Fetched email skipped account_id=%s uid=%s reason=%s",
                    target.id,
                    raw_fetch.gmail_uid,
                    safe_log_text(exc, 500),
                )
                await mark_email_failure(target.id, raw_fetch, str(exc))
        return processed


async def fetch_account_messages(target: AccountPollTarget) -> list[RawEmailFetch]:
    return await asyncio.to_thread(
        _fetch_with_new_connection,
        target.gmail_address,
        target.app_password,
        target.last_uid,
    )


async def poll_account(target: AccountPollTarget) -> None:
    """Legacy poll path used when IDLE is disabled."""
    checked_at = datetime.now(UTC)
    poll_started = perf_counter()
    logger.info(
        "Polling Gmail account id=%s friendly=%s mailbox=%s checkpoint=%s",
        target.id,
        safe_log_text(target.friendly_name),
        MAILBOX,
        target.last_uid,
    )
    try:
        await process_account_emails(target, reason="poll")
    except GmailConnectionError:
        await update_account_status(target.id, "error", checked_at)
        return
    except Exception:
        await update_account_status(target.id, "error", checked_at)
        return
    logger.info("Gmail account id=%s poll_elapsed_ms=%s", target.id, int((perf_counter() - poll_started) * 1000))


async def heartbeat(status: str = "alive") -> None:
    async with AsyncSessionLocal() as session:
        await write_heartbeat(session, status)
        await session.commit()


def _open_client(target: AccountPollTarget) -> GmailImapClient:
    client = GmailImapClient(target.gmail_address, target.app_password)
    client.__enter__()
    return client


def _close_client(client: GmailImapClient | None) -> None:
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


async def run_account_idle_session(target: AccountPollTarget, stop_event: asyncio.Event) -> None:
    """
    Hybrid IDLE session for one mailbox/account:

    connect → immediate catch-up → IDLE → health checks → safety poll → reconnect recovery
    """
    backoff = 1
    max_backoff = settings.gmail_reconnect_max_backoff_seconds
    reconnecting = False

    while not stop_event.is_set():
        client: GmailImapClient | None = None
        try:
            client = await asyncio.to_thread(_open_client, target)
            if reconnecting:
                logger.info("idle_reconnected=true account_id=%s mailbox=%s", target.id, MAILBOX)
            else:
                logger.info(
                    "listener_mode=idle idle_connected=true account_id=%s mailbox=%s",
                    target.id,
                    MAILBOX,
                )
            # Startup and every reconnect: one catch-up from last_uid+1 before IDLE.
            await process_account_emails(
                target,
                reason="reconnect_catchup" if reconnecting else "startup_catchup",
                client=client,
            )
            reconnecting = False
            backoff = 1

            await asyncio.to_thread(client.idle_start, MAILBOX)
            idle_started_at = monotonic()
            last_safety_poll_at = monotonic()

            while not stop_event.is_set():
                event = await asyncio.to_thread(client.idle_wait, float(settings.gmail_idle_healthcheck_seconds))

                if event is not None:
                    detection_started = perf_counter()
                    logger.info(
                        "idle_event_received=true account_id=%s lines=%s",
                        target.id,
                        len(event.lines),
                    )
                    await asyncio.to_thread(client.idle_done)
                    await process_account_emails(
                        target,
                        reason="idle_event",
                        client=client,
                        detection_started=detection_started,
                    )
                    await asyncio.to_thread(client.idle_start, MAILBOX)
                    idle_started_at = monotonic()
                    continue

                # Health check only — no mailbox poll while IDLE is healthy.
                if not client.idle_health_ok():
                    logger.info("healthcheck_failed=true account_id=%s", target.id)
                    raise GmailConnectionError("IDLE health check failed.")

                await heartbeat("alive")

                now = monotonic()
                needs_refresh = (now - idle_started_at) >= settings.gmail_idle_refresh_seconds
                needs_safety = (now - last_safety_poll_at) >= settings.gmail_safety_poll_seconds

                if needs_refresh or needs_safety:
                    await asyncio.to_thread(client.idle_done)
                    if needs_safety:
                        logger.info("safety_poll=true account_id=%s", target.id)
                        await process_account_emails(target, reason="safety_poll", client=client)
                        last_safety_poll_at = monotonic()
                    await asyncio.to_thread(client.idle_start, MAILBOX)
                    idle_started_at = monotonic()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reconnecting = True
            logger.info(
                "idle_session_error=true account_id=%s error=%s backoff_seconds=%s",
                target.id,
                safe_log_text(exc, 300),
                backoff,
            )
            await update_account_status(target.id, "error", datetime.now(UTC))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, max_backoff)
                continue
        finally:
            _close_client(client)


async def run_idle_listener(stop_event: asyncio.Event) -> None:
    """Supervise per-account IDLE sessions. Reloads enabled accounts on each health interval."""
    running: dict[int, tuple[AccountPollTarget, asyncio.Task[None]]] = {}
    logger.info(
        "listener_mode=idle healthcheck_seconds=%s refresh_seconds=%s safety_poll_seconds=%s",
        settings.gmail_idle_healthcheck_seconds,
        settings.gmail_idle_refresh_seconds,
        settings.gmail_safety_poll_seconds,
    )

    try:
        while not stop_event.is_set():
            await heartbeat("alive")
            targets = await load_enabled_accounts()
            wanted = {target.id: target for target in targets}

            for account_id, (existing, task) in list(running.items()):
                replacement = wanted.get(account_id)
                should_restart = (
                    account_id not in wanted
                    or task.done()
                    or (replacement is not None and replacement.identity != existing.identity)
                )
                if should_restart:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    running.pop(account_id, None)

            for account_id, target in wanted.items():
                if account_id not in running:
                    task = asyncio.create_task(
                        run_account_idle_session(target, stop_event),
                        name=f"gmail-idle-{account_id}",
                    )
                    running[account_id] = (target, task)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.gmail_idle_healthcheck_seconds)
            except asyncio.TimeoutError:
                continue
    finally:
        for _, task in running.values():
            task.cancel()
        if running:
            await asyncio.gather(*(task for _, task in running.values()), return_exceptions=True)
        await heartbeat("stopped")


async def run_poll_listener(stop_event: asyncio.Event) -> None:
    """Fallback fixed-interval poll loop when GMAIL_IDLE_ENABLED=false."""
    logger.info("listener_mode=poll interval_seconds=%s", settings.gmail_poll_interval_seconds)
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


async def run_listener(stop_event: asyncio.Event) -> None:
    if settings.gmail_idle_enabled:
        await run_idle_listener(stop_event)
    else:
        await run_poll_listener(stop_event)


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
