import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from urllib import parse, request
from urllib.error import HTTPError, URLError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.models.payment_account import PaymentAccount
from app.models.telegram_integration import TelegramIntegration
from app.models.transaction import Direction, Transaction

logger = logging.getLogger(__name__)

CONNECTED_MESSAGE = "Payment Ledger Telegram integration is connected."
KATHMANDU = timezone(timedelta(hours=5, minutes=45), name="Asia/Kathmandu")
# Longer than the Telegram HTTP timeout (15s) so in-flight claims stay exclusive,
# but short enough that crash/restart leftovers become retryable.
TELEGRAM_SENDING_STALE_AFTER = timedelta(seconds=60)


@dataclass(frozen=True)
class TelegramApiResult:
    success: bool
    message: str


def mask_bot_token(token: str | None) -> str | None:
    if not token:
        return None
    stripped = token.strip()
    if len(stripped) <= 8:
        return "*" * len(stripped)
    return f"{stripped[:4]}...{stripped[-4:]}"


def sanitize_telegram_error(error: BaseException | str, token: str | None = None) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    if token:
        message = message.replace(token, "[redacted]")
    return (message or "Telegram request failed.")[:1000]


def normalize_group_id(group_id: str | None) -> str | None:
    normalized = group_id.strip() if group_id else None
    return normalized or None


async def get_or_create_telegram_integration(session: AsyncSession) -> TelegramIntegration:
    integration = await session.scalar(select(TelegramIntegration).order_by(TelegramIntegration.id))
    if integration is None:
        integration = TelegramIntegration(enabled=False)
        session.add(integration)
        await session.flush()
    return integration


def _telegram_post(bot_token: str, method: str, payload: dict[str, str]) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = parse.urlencode(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram API connection failed: {exc.reason}") from exc

    decoded = json.loads(body)
    if not decoded.get("ok"):
        raise RuntimeError(decoded.get("description") or "Telegram API returned ok=false.")
    return decoded


async def telegram_get_me(bot_token: str) -> dict:
    return await asyncio.to_thread(_telegram_post, bot_token, "getMe", {})


async def telegram_send_message(bot_token: str, group_id: str, text: str) -> dict:
    return await asyncio.to_thread(_telegram_post, bot_token, "sendMessage", {"chat_id": group_id, "text": text})


async def telegram_get_chat(bot_token: str, group_id: str) -> dict:
    return await asyncio.to_thread(_telegram_post, bot_token, "getChat", {"chat_id": group_id})


async def test_telegram_integration(session: AsyncSession, send_message: bool = False) -> TelegramApiResult:
    integration = await get_or_create_telegram_integration(session)
    now = datetime.now(UTC)
    integration.last_checked_at = now

    if not integration.bot_token_encrypted or not normalize_group_id(integration.group_id):
        integration.last_error = "Bot token and group ID are required."
        await session.commit()
        return TelegramApiResult(False, integration.last_error)

    bot_token = decrypt_secret(integration.bot_token_encrypted)
    group_id = normalize_group_id(integration.group_id)
    try:
        await telegram_get_me(bot_token)
        if send_message:
            await telegram_send_message(bot_token, group_id or "", CONNECTED_MESSAGE)
        else:
            await telegram_get_chat(bot_token, group_id or "")
        integration.last_success_at = now
        integration.last_error = None
        await session.commit()
        return TelegramApiResult(True, "Telegram integration connected.")
    except Exception as exc:
        integration.last_error = sanitize_telegram_error(exc, bot_token)
        await session.commit()
        return TelegramApiResult(False, integration.last_error)


def format_transaction_message(transaction: Transaction) -> str:
    received = format_kathmandu_timestamp(transaction.received_at)
    return "\n".join(
        [
            "🟢 New Chime Payment",
            "",
            f"💵 Amount Received: ${transaction.amount_cents / 100:,.2f}",
            f"👤 Payment Name: {transaction.sender_name or 'Unknown'}",
            f"🕒 Received At: {received}",
        ]
    )


def format_kathmandu_timestamp(value: datetime) -> str:
    localized = value
    if localized.tzinfo is None:
        localized = localized.replace(tzinfo=UTC)
    localized = localized.astimezone(KATHMANDU)
    formatted = localized.strftime("%d %b %Y, %I:%M %p")
    return formatted.replace(", 0", ", ", 1)


async def send_transaction_notification(transaction_id: int) -> None:
    bot_token: str | None = None
    group_id: str | None = None

    async with AsyncSessionLocal() as session:
        async with session.begin():
            transaction = await session.scalar(
                select(Transaction)
                .options(selectinload(Transaction.payment_account).selectinload(PaymentAccount.provider))
                .where(Transaction.id == transaction_id)
                .with_for_update()
            )
            if transaction is None or not should_send_transaction_notification(transaction):
                return

            integration = await get_or_create_telegram_integration(session)
            if not integration.enabled or not integration.bot_token_encrypted or not normalize_group_id(integration.group_id):
                return

            bot_token = decrypt_secret(integration.bot_token_encrypted)
            group_id = normalize_group_id(integration.group_id)
            transaction.telegram_status = "sending"
            transaction.telegram_attempted_at = datetime.now(UTC)
            transaction.telegram_last_error = None

    if bot_token is None or group_id is None:
        return

    try:
        await telegram_send_message(bot_token, group_id, await format_transaction_message_by_id(transaction_id))
    except Exception as exc:
        await mark_transaction_notification_failed(transaction_id, exc, bot_token)
        return

    await mark_transaction_notification_sent(transaction_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_stale_sending_claim(transaction: Transaction, *, now: datetime | None = None) -> bool:
    """True when a sending claim is old enough to safely retry after crash/restart."""
    if transaction.telegram_status != "sending":
        return False
    attempted_at = transaction.telegram_attempted_at
    if attempted_at is None:
        # Pre-migration / crash without a stamp: treat as recoverable rather than stuck forever.
        return True
    current = now if now is not None else datetime.now(UTC)
    return _as_utc(current) - _as_utc(attempted_at) >= TELEGRAM_SENDING_STALE_AFTER


def should_send_transaction_notification(transaction: Transaction, *, now: datetime | None = None) -> bool:
    if transaction.direction != Direction.IN:
        return False
    if transaction.telegram_status == "sent":
        return False
    if transaction.telegram_status == "sending":
        return is_stale_sending_claim(transaction, now=now)
    return True


async def format_transaction_message_by_id(transaction_id: int) -> str:
    async with AsyncSessionLocal() as session:
        transaction = await session.scalar(
            select(Transaction)
            .options(selectinload(Transaction.payment_account).selectinload(PaymentAccount.provider))
            .where(Transaction.id == transaction_id)
        )
        if transaction is None:
            raise RuntimeError(f"Transaction {transaction_id} no longer exists.")
        return format_transaction_message(transaction)


async def mark_transaction_notification_sent(transaction_id: int) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            transaction = await session.scalar(select(Transaction).where(Transaction.id == transaction_id).with_for_update())
            integration = await get_or_create_telegram_integration(session)
            if transaction is None:
                return
            sent_at = datetime.now(UTC)
            transaction.telegram_status = "sent"
            transaction.telegram_sent_at = sent_at
            transaction.telegram_last_error = None
            integration.last_success_at = sent_at
            integration.last_error = None
            logger.info("Telegram sent for transaction %s", transaction.id)


async def mark_transaction_notification_failed(transaction_id: int, exc: BaseException, bot_token: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            transaction = await session.scalar(select(Transaction).where(Transaction.id == transaction_id).with_for_update())
            integration = await get_or_create_telegram_integration(session)
            if transaction is None:
                return
            safe_error = sanitize_telegram_error(exc, bot_token)
            transaction.telegram_status = "failed"
            transaction.telegram_last_error = safe_error
            integration.last_error = safe_error
            logger.warning("Telegram notification failed for transaction %s: %s", transaction.id, integration.last_error)


async def notify_transaction_in_session(transaction: Transaction, integration: TelegramIntegration) -> None:
    if not should_send_transaction_notification(transaction):
        return
    if not integration.enabled or not integration.bot_token_encrypted or not normalize_group_id(integration.group_id):
        return

    bot_token = decrypt_secret(integration.bot_token_encrypted)
    group_id = normalize_group_id(integration.group_id) or ""
    try:
        await telegram_send_message(bot_token, group_id, format_transaction_message(transaction))
        sent_at = datetime.now(UTC)
        transaction.telegram_status = "sent"
        transaction.telegram_sent_at = sent_at
        transaction.telegram_last_error = None
        integration.last_success_at = sent_at
        integration.last_error = None
        logger.info("Telegram sent for transaction %s", transaction.id)
    except Exception as exc:
        transaction.telegram_status = "failed"
        safe_error = sanitize_telegram_error(exc, bot_token)
        transaction.telegram_last_error = safe_error
        integration.last_error = safe_error
        logger.warning("Telegram notification failed for transaction %s: %s", transaction.id, integration.last_error)


def apply_telegram_settings(
    integration: TelegramIntegration,
    bot_token: str | None,
    group_id: str,
    enabled: bool,
) -> None:
    normalized_group_id = normalize_group_id(group_id)
    if normalized_group_id is None:
        raise ValueError("group_id is required.")
    if bot_token is not None and bot_token.strip():
        integration.bot_token_encrypted = encrypt_secret(bot_token.strip())
    integration.group_id = normalized_group_id
    integration.enabled = enabled
