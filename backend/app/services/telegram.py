import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_delivery_attempt import TelegramDeliveryAttempt
from app.models.telegram_integration import DEFAULT_TELEGRAM_INTEGRATION_NAME, TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.schemas.telegram import TelegramDeliverySummary

logger = logging.getLogger(__name__)

CONNECTED_MESSAGE = "Payment Ledger Telegram integration is connected."
KATHMANDU = timezone(timedelta(hours=5, minutes=45), name="Asia/Kathmandu")
# Longer than the Telegram HTTP timeout (15s) so in-flight claims stay exclusive,
# but short enough that crash/restart leftovers become retryable.
TELEGRAM_SENDING_STALE_AFTER = timedelta(seconds=60)
NO_DESTINATIONS_REASON = "No Telegram destinations assigned"


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


def bot_token_fingerprint(token: str) -> str:
    """Stable SHA-256 fingerprint for duplicate destination checks. Never log or return this with secrets."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def sanitize_telegram_error(error: BaseException | str, token: str | None = None) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    if token:
        message = message.replace(token, "[redacted]")
    return (message or "Telegram request failed.")[:1000]


def normalize_group_id(group_id: str | None) -> str | None:
    normalized = group_id.strip() if group_id else None
    return normalized or None


def integration_is_usable(integration: TelegramIntegration) -> bool:
    return bool(
        integration.enabled
        and integration.bot_token_encrypted
        and normalize_group_id(integration.group_id)
    )


def extract_telegram_message_id(api_response: dict) -> str | None:
    result = api_response.get("result")
    if isinstance(result, dict) and result.get("message_id") is not None:
        return str(result["message_id"])
    return None


async def get_or_create_telegram_integration(session: AsyncSession) -> TelegramIntegration:
    integration = await session.scalar(select(TelegramIntegration).order_by(TelegramIntegration.id))
    if integration is None:
        # Name is required after Phase 1/2; keep singleton create path disabled and unconfigured.
        integration = TelegramIntegration(enabled=False, name=DEFAULT_TELEGRAM_INTEGRATION_NAME)
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


def extract_bot_username(get_me_response: dict) -> str | None:
    result = get_me_response.get("result")
    if not isinstance(result, dict):
        return None
    username = result.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip().lstrip("@")
    return None


async def test_telegram_integration(session: AsyncSession, send_message: bool = False) -> TelegramApiResult:
    integration = await get_or_create_telegram_integration(session)
    return await test_specific_telegram_integration(session, integration, send_message=send_message)


async def test_specific_telegram_integration(
    session: AsyncSession,
    integration: TelegramIntegration,
    *,
    send_message: bool = False,
) -> TelegramApiResult:
    now = datetime.now(UTC)
    integration.last_checked_at = now

    if not integration.bot_token_encrypted or not normalize_group_id(integration.group_id):
        integration.last_error = "Bot token and group ID are required."
        await session.commit()
        return TelegramApiResult(False, integration.last_error)

    bot_token = decrypt_secret(integration.bot_token_encrypted)
    group_id = normalize_group_id(integration.group_id)
    try:
        me = await telegram_get_me(bot_token)
        username = extract_bot_username(me)
        if username:
            integration.bot_username = username
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


async def find_duplicate_telegram_destination(
    session: AsyncSession,
    *,
    bot_token: str,
    group_id: str,
    exclude_integration_id: int | None = None,
) -> TelegramIntegration | None:
    """Return an existing integration with the same token fingerprint + group_id, if any."""
    normalized_group = normalize_group_id(group_id)
    if normalized_group is None:
        return None
    target = bot_token_fingerprint(bot_token)
    query = select(TelegramIntegration).where(TelegramIntegration.group_id == normalized_group)
    if exclude_integration_id is not None:
        query = query.where(TelegramIntegration.id != exclude_integration_id)
    integrations = list((await session.scalars(query)).all())
    for integration in integrations:
        if not integration.bot_token_encrypted:
            continue
        try:
            existing_token = decrypt_secret(integration.bot_token_encrypted)
        except ValueError:
            continue
        if bot_token_fingerprint(existing_token) == target:
            return integration
    return None


async def count_routes_by_integration(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(
            PaymentAccountTelegramRoute.telegram_integration_id,
            func.count(PaymentAccountTelegramRoute.id),
        ).group_by(PaymentAccountTelegramRoute.telegram_integration_id)
    )
    return {integration_id: int(count) for integration_id, count in result.all()}


async def lowest_telegram_integration_id(session: AsyncSession) -> int | None:
    return await session.scalar(select(TelegramIntegration.id).order_by(TelegramIntegration.id).limit(1))


def build_telegram_delivery_summary(deliveries: list[TelegramDelivery]) -> TelegramDeliverySummary | None:
    if not deliveries:
        return None
    summary = TelegramDeliverySummary(total=len(deliveries))
    for delivery in deliveries:
        if delivery.status == "sent":
            summary.sent += 1
        elif delivery.status == "failed":
            summary.failed += 1
        elif delivery.status == "pending":
            summary.pending += 1
        elif delivery.status == "sending":
            summary.sending += 1
    return summary


async def load_delivery_summaries_for_transactions(
    session: AsyncSession,
    transaction_ids: list[int],
) -> dict[int, TelegramDeliverySummary]:
    if not transaction_ids:
        return {}
    deliveries = list(
        (
            await session.scalars(
                select(TelegramDelivery).where(TelegramDelivery.transaction_id.in_(transaction_ids))
            )
        ).all()
    )
    by_txn: dict[int, list[TelegramDelivery]] = {}
    for delivery in deliveries:
        by_txn.setdefault(delivery.transaction_id, []).append(delivery)
    summaries: dict[int, TelegramDeliverySummary] = {}
    for txn_id, rows in by_txn.items():
        summary = build_telegram_delivery_summary(rows)
        if summary is not None:
            summaries[txn_id] = summary
    return summaries


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_stale_sending_claim_at(attempted_at: datetime | None, *, now: datetime | None = None) -> bool:
    if attempted_at is None:
        return True
    current = now if now is not None else datetime.now(UTC)
    return _as_utc(current) - _as_utc(attempted_at) >= TELEGRAM_SENDING_STALE_AFTER


def is_stale_sending_claim(transaction: Transaction, *, now: datetime | None = None) -> bool:
    """True when a legacy transaction-level sending claim is old enough to retry."""
    if transaction.telegram_status != "sending":
        return False
    return is_stale_sending_claim_at(transaction.telegram_attempted_at, now=now)


def is_stale_delivery_sending(delivery: TelegramDelivery, *, now: datetime | None = None) -> bool:
    if delivery.status != "sending":
        return False
    return is_stale_sending_claim_at(delivery.last_attempt_at, now=now)


def should_send_transaction_notification(transaction: Transaction, *, now: datetime | None = None) -> bool:
    """Legacy pre-check for IN payments. Delivery rows own idempotency in Phase 3.

    Failed is not auto-retried on normal dispatch (explicit retry is a future phase).
    """
    if transaction.direction != Direction.IN:
        return False
    if transaction.telegram_status in {"sent", "not_applicable"}:
        return False
    if transaction.telegram_status == "failed":
        return False
    if transaction.telegram_status == "sending":
        return is_stale_sending_claim(transaction, now=now)
    return True


def is_delivery_eligible_for_normal_dispatch(delivery: TelegramDelivery, *, now: datetime | None = None) -> bool:
    """Normal parse/reparse eligibility. Does not auto-retry failed or resend sent."""
    if delivery.status == "sent":
        return False
    if delivery.status == "failed":
        return False
    if delivery.status == "sending":
        return is_stale_delivery_sending(delivery, now=now)
    if delivery.status == "pending":
        return delivery.attempt_count == 0
    return False


async def list_usable_integrations_for_account(session: AsyncSession, payment_account_id: int) -> list[TelegramIntegration]:
    result = await session.execute(
        select(TelegramIntegration)
        .join(
            PaymentAccountTelegramRoute,
            PaymentAccountTelegramRoute.telegram_integration_id == TelegramIntegration.id,
        )
        .where(PaymentAccountTelegramRoute.payment_account_id == payment_account_id)
        .order_by(TelegramIntegration.id)
    )
    return [integration for integration in result.scalars().all() if integration_is_usable(integration)]


async def ensure_transaction_deliveries(session: AsyncSession, transaction_id: int) -> list[int]:
    """Create pending delivery rows for currently usable routed integrations.

    Conflict-safe: concurrent creators reload the existing row. Does not modify sent/failed rows.
    Only creates deliveries for integrations that are enabled and configured right now.
    """
    transaction = await session.get(Transaction, transaction_id)
    if transaction is None or transaction.direction != Direction.IN:
        return []

    integrations = await list_usable_integrations_for_account(session, transaction.payment_account_id)
    delivery_ids: list[int] = []

    for integration in integrations:
        existing = await session.scalar(
            select(TelegramDelivery).where(
                TelegramDelivery.transaction_id == transaction_id,
                TelegramDelivery.telegram_integration_id == integration.id,
            )
        )
        if existing is not None:
            delivery_ids.append(existing.id)
            continue

        delivery = TelegramDelivery(
            transaction_id=transaction_id,
            telegram_integration_id=integration.id,
            status="pending",
            attempt_count=0,
        )
        try:
            async with session.begin_nested():
                session.add(delivery)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(TelegramDelivery).where(
                    TelegramDelivery.transaction_id == transaction_id,
                    TelegramDelivery.telegram_integration_id == integration.id,
                )
            )
            if existing is None:
                raise
            delivery_ids.append(existing.id)
            continue
        delivery_ids.append(delivery.id)

    return delivery_ids


def is_delivery_eligible_for_manual_retry(
    delivery: TelegramDelivery,
    *,
    now: datetime | None = None,
) -> bool:
    """Manual retry: failed always; sending only when stale. Never sent or pending."""
    if delivery.status == "failed":
        return True
    if delivery.status == "sending":
        return is_stale_delivery_sending(delivery, now=now)
    return False


async def _complete_delivery_attempt(
    session: AsyncSession,
    delivery: TelegramDelivery,
    *,
    status: str,
    telegram_message_id: str | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> None:
    done_at = completed_at or datetime.now(UTC)
    attempt_number = max(int(delivery.attempt_count or 0), 1)
    attempt = await session.scalar(
        select(TelegramDeliveryAttempt)
        .where(
            TelegramDeliveryAttempt.telegram_delivery_id == delivery.id,
            TelegramDeliveryAttempt.attempt_number == attempt_number,
        )
        .with_for_update()
    )
    if attempt is None:
        session.add(
            TelegramDeliveryAttempt(
                telegram_delivery_id=delivery.id,
                attempt_number=attempt_number,
                status=status,
                telegram_message_id=telegram_message_id,
                error_message=error_message,
                attempted_at=delivery.last_attempt_at or done_at,
                completed_at=done_at,
            )
        )
        return
    attempt.status = status
    attempt.telegram_message_id = telegram_message_id
    attempt.error_message = error_message
    attempt.completed_at = done_at


async def _claim_telegram_delivery(
    delivery_id: int,
    *,
    eligibility: Callable[[TelegramDelivery], bool],
) -> tuple[str, str] | None:
    """Claim a delivery for sending. Returns (bot_token, group_id) when claimed, else None."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            delivery = await session.scalar(
                select(TelegramDelivery).where(TelegramDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None or not eligibility(delivery):
                return None

            integration = await session.get(TelegramIntegration, delivery.telegram_integration_id)
            if integration is None or not integration_is_usable(integration):
                return None

            bot_token = decrypt_secret(integration.bot_token_encrypted or "")
            group_id = normalize_group_id(integration.group_id)
            if not group_id:
                return None

            now = datetime.now(UTC)
            delivery.status = "sending"
            delivery.attempt_count = int(delivery.attempt_count or 0) + 1
            delivery.last_attempt_at = now
            delivery.last_error = None
            session.add(
                TelegramDeliveryAttempt(
                    telegram_delivery_id=delivery.id,
                    attempt_number=int(delivery.attempt_count),
                    status="sending",
                    attempted_at=now,
                )
            )
            return bot_token, group_id


async def claim_telegram_delivery(delivery_id: int) -> tuple[str, str] | None:
    return await _claim_telegram_delivery(
        delivery_id,
        eligibility=is_delivery_eligible_for_normal_dispatch,
    )


async def claim_telegram_delivery_for_manual_retry(delivery_id: int) -> tuple[str, str] | None:
    return await _claim_telegram_delivery(
        delivery_id,
        eligibility=is_delivery_eligible_for_manual_retry,
    )


async def mark_telegram_delivery_sent(delivery_id: int, telegram_message_id: str | None) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            delivery = await session.scalar(
                select(TelegramDelivery).where(TelegramDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None:
                return
            integration = await session.get(TelegramIntegration, delivery.telegram_integration_id)
            sent_at = datetime.now(UTC)
            delivery.status = "sent"
            delivery.sent_at = sent_at
            delivery.telegram_message_id = telegram_message_id
            delivery.last_error = None
            await _complete_delivery_attempt(
                session,
                delivery,
                status="sent",
                telegram_message_id=telegram_message_id,
                completed_at=sent_at,
            )
            if integration is not None:
                integration.last_success_at = sent_at
                integration.last_error = None
            logger.info(
                "Telegram delivery sent transaction_id=%s integration_id=%s delivery_id=%s",
                delivery.transaction_id,
                delivery.telegram_integration_id,
                delivery.id,
            )


async def mark_telegram_delivery_failed(delivery_id: int, exc: BaseException, bot_token: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            delivery = await session.scalar(
                select(TelegramDelivery).where(TelegramDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None:
                return
            integration = await session.get(TelegramIntegration, delivery.telegram_integration_id)
            safe_error = sanitize_telegram_error(exc, bot_token)
            delivery.status = "failed"
            delivery.last_error = safe_error
            await _complete_delivery_attempt(
                session,
                delivery,
                status="failed",
                error_message=safe_error,
            )
            if integration is not None:
                integration.last_error = safe_error
            logger.warning(
                "Telegram delivery failed transaction_id=%s integration_id=%s delivery_id=%s error=%s",
                delivery.transaction_id,
                delivery.telegram_integration_id,
                delivery.id,
                safe_error,
            )


async def send_telegram_delivery(
    delivery_id: int,
    *,
    claim_fn: Callable[[int], Awaitable[tuple[str, str] | None]] | None = None,
) -> bool:
    """Send one delivery. Returns True if a claim was obtained (send was attempted)."""
    claim = claim_fn or claim_telegram_delivery
    claimed = await claim(delivery_id)
    if claimed is None:
        return False

    bot_token, group_id = claimed
    delivery_transaction_id: int | None = None
    async with AsyncSessionLocal() as session:
        delivery = await session.get(TelegramDelivery, delivery_id)
        if delivery is None:
            return False
        delivery_transaction_id = delivery.transaction_id

    try:
        api_response = await telegram_send_message(
            bot_token,
            group_id,
            await format_transaction_message_by_id(delivery_transaction_id),
        )
    except Exception as exc:
        await mark_telegram_delivery_failed(delivery_id, exc, bot_token)
        return True

    await mark_telegram_delivery_sent(delivery_id, extract_telegram_message_id(api_response))
    return True


async def retry_telegram_delivery(delivery_id: int) -> dict[str, Any]:
    """Manual retry for a single delivery using the shared claim/send path."""
    async with AsyncSessionLocal() as session:
        delivery = await session.get(TelegramDelivery, delivery_id)
        if delivery is None:
            return {"ok": False, "reason": "not_found", "delivery_id": delivery_id}
        if delivery.status == "sent":
            return {"ok": False, "reason": "already_sent", "delivery_id": delivery_id}
        if delivery.status == "pending":
            return {"ok": False, "reason": "pending_owned", "delivery_id": delivery_id}
        if delivery.status == "sending" and not is_stale_delivery_sending(delivery):
            return {"ok": False, "reason": "sending_in_progress", "delivery_id": delivery_id}
        if not is_delivery_eligible_for_manual_retry(delivery):
            return {"ok": False, "reason": "ineligible", "delivery_id": delivery_id}
        transaction_id = delivery.transaction_id

    attempted = await send_telegram_delivery(
        delivery_id,
        claim_fn=claim_telegram_delivery_for_manual_retry,
    )
    await update_transaction_telegram_rollup(transaction_id)

    async with AsyncSessionLocal() as session:
        delivery = await session.get(TelegramDelivery, delivery_id)
        if not attempted or delivery is None:
            return {"ok": False, "reason": "not_claimed", "delivery_id": delivery_id}
        return {
            "ok": delivery.status == "sent",
            "reason": None if delivery.status == "sent" else "send_failed",
            "delivery_id": delivery_id,
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
            "telegram_message_id": delivery.telegram_message_id,
            "last_error": delivery.last_error,
            "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
        }


def compute_transaction_telegram_rollup(deliveries: list[TelegramDelivery]) -> dict[str, object]:
    """Derive legacy transactions.telegram_* fields from delivery rows.

    telegram_sent_at uses the latest successful delivery sent_at when any exist.
    """
    if not deliveries:
        return {
            "telegram_status": "not_applicable",
            "telegram_sent_at": None,
            "telegram_attempted_at": None,
            "telegram_last_error": NO_DESTINATIONS_REASON,
        }

    statuses = {delivery.status for delivery in deliveries}
    attempted_times = [d.last_attempt_at for d in deliveries if d.last_attempt_at is not None]
    sent_times = [d.sent_at for d in deliveries if d.status == "sent" and d.sent_at is not None]
    latest_attempt = max(attempted_times) if attempted_times else None
    latest_sent = max(sent_times) if sent_times else None

    failed_count = sum(1 for d in deliveries if d.status == "failed")
    total = len(deliveries)

    if failed_count:
        return {
            "telegram_status": "failed",
            "telegram_sent_at": latest_sent,
            "telegram_attempted_at": latest_attempt,
            "telegram_last_error": f"{failed_count} of {total} Telegram deliveries failed",
        }
    if statuses == {"sent"}:
        return {
            "telegram_status": "sent",
            "telegram_sent_at": latest_sent,
            "telegram_attempted_at": latest_attempt,
            "telegram_last_error": None,
        }
    if "sending" in statuses:
        return {
            "telegram_status": "sending",
            "telegram_sent_at": latest_sent,
            "telegram_attempted_at": latest_attempt,
            "telegram_last_error": None,
        }
    return {
        "telegram_status": "pending",
        "telegram_sent_at": latest_sent,
        "telegram_attempted_at": latest_attempt,
        "telegram_last_error": None,
    }


async def update_transaction_telegram_rollup(transaction_id: int) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            transaction = await session.scalar(
                select(Transaction).where(Transaction.id == transaction_id).with_for_update()
            )
            if transaction is None:
                return
            deliveries = list(
                (
                    await session.scalars(
                        select(TelegramDelivery).where(TelegramDelivery.transaction_id == transaction_id)
                    )
                ).all()
            )
            rollup = compute_transaction_telegram_rollup(deliveries)
            transaction.telegram_status = str(rollup["telegram_status"])
            transaction.telegram_sent_at = rollup["telegram_sent_at"]  # type: ignore[assignment]
            transaction.telegram_attempted_at = rollup["telegram_attempted_at"]  # type: ignore[assignment]
            transaction.telegram_last_error = rollup["telegram_last_error"]  # type: ignore[assignment]


async def _list_eligible_delivery_ids(session: AsyncSession, transaction_id: int) -> list[int]:
    deliveries = list(
        (await session.scalars(select(TelegramDelivery).where(TelegramDelivery.transaction_id == transaction_id))).all()
    )
    return [delivery.id for delivery in deliveries if is_delivery_eligible_for_normal_dispatch(delivery)]


async def dispatch_transaction_notifications(
    transaction_id: int,
    *,
    create_missing_destinations: bool = False,
) -> None:
    """Fan-out Telegram notifications for one transaction using account routes.

    create_missing_destinations=True only on first creation of an incoming payment transaction.
    Ordinary reparse uses False so newly assigned bots never receive historical payments.
    """
    delivery_ids: list[int] = []

    async with AsyncSessionLocal() as session:
        async with session.begin():
            transaction = await session.scalar(
                select(Transaction)
                .options(selectinload(Transaction.payment_account))
                .where(Transaction.id == transaction_id)
            )
            if transaction is None or transaction.direction != Direction.IN:
                return

            payment_account_id = transaction.payment_account_id

            if create_missing_destinations:
                await ensure_transaction_deliveries(session, transaction_id)
                deliveries = list(
                    (
                        await session.scalars(
                            select(TelegramDelivery).where(TelegramDelivery.transaction_id == transaction_id)
                        )
                    ).all()
                )
                if not deliveries:
                    logger.warning(
                        "No Telegram destinations assigned transaction_id=%s payment_account_id=%s",
                        transaction_id,
                        payment_account_id,
                    )
                    transaction.telegram_status = "not_applicable"
                    transaction.telegram_sent_at = None
                    transaction.telegram_attempted_at = None
                    transaction.telegram_last_error = NO_DESTINATIONS_REASON
                    return

            delivery_ids = await _list_eligible_delivery_ids(session, transaction_id)

    if not delivery_ids:
        # Reparse / historical with no eligible rows: do not rewrite legacy telegram_* fields.
        return

    for delivery_id in delivery_ids:
        try:
            await send_telegram_delivery(delivery_id)
        except Exception as exc:
            logger.warning(
                "Telegram delivery processing error delivery_id=%s error=%s",
                delivery_id,
                sanitize_telegram_error(exc),
            )

    await update_transaction_telegram_rollup(transaction_id)


async def send_transaction_notification(
    transaction_id: int,
    *,
    create_missing_destinations: bool = False,
) -> None:
    """Compatibility wrapper around route-driven fan-out dispatch."""
    await dispatch_transaction_notifications(
        transaction_id,
        create_missing_destinations=create_missing_destinations,
    )


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


async def notify_transaction_in_session(transaction: Transaction, integration: TelegramIntegration) -> None:
    """Test/helper path for a single in-session send. Production uses dispatch_transaction_notifications."""
    if transaction.direction != Direction.IN:
        return
    if transaction.telegram_status == "sent":
        return
    if not integration_is_usable(integration):
        return

    bot_token = decrypt_secret(integration.bot_token_encrypted or "")
    group_id = normalize_group_id(integration.group_id) or ""
    try:
        api_response = await telegram_send_message(bot_token, group_id, format_transaction_message(transaction))
        sent_at = datetime.now(UTC)
        transaction.telegram_status = "sent"
        transaction.telegram_sent_at = sent_at
        transaction.telegram_last_error = None
        integration.last_success_at = sent_at
        integration.last_error = None
        message_id = extract_telegram_message_id(api_response)
        if message_id:
            logger.info("Telegram sent for transaction %s message_id=%s", transaction.id, message_id)
        else:
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
