"""List, filter, stats, and bulk retry helpers for telegram_deliveries (Delivery Ops Center)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Select, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.types import String

from app.models.payment_account import PaymentAccount
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import TelegramIntegration
from app.models.transaction import Transaction
from app.services.telegram import (
    KATHMANDU,
    is_delivery_eligible_for_manual_retry,
    retry_telegram_delivery,
    sanitize_telegram_error,
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class DeliveryListFilters:
    status: str | None = None
    integration_id: int | None = None
    payment_account_id: int | None = None
    provider_id: int | None = None
    sender: str | None = None
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    search: str | None = None
    transaction_id: int | None = None
    retryable_only: bool = False


def start_of_today_kathmandu(*, now: datetime | None = None) -> datetime:
    current = now if now is not None else datetime.now(UTC)
    local = current.astimezone(KATHMANDU)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC)


def parse_amount_to_cents(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        amount = Decimal(stripped.replace("$", "").replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Invalid amount.") from exc
    return int((amount * 100).quantize(Decimal("1")))


def apply_delivery_filters(query: Select, filters: DeliveryListFilters) -> Select:
    query = query.join(TelegramDelivery.transaction).join(Transaction.payment_account).join(PaymentAccount.provider)
    query = query.join(TelegramDelivery.telegram_integration)

    if filters.status:
        query = query.where(TelegramDelivery.status == filters.status)
    if filters.integration_id is not None:
        query = query.where(TelegramDelivery.telegram_integration_id == filters.integration_id)
    if filters.payment_account_id is not None:
        query = query.where(Transaction.payment_account_id == filters.payment_account_id)
    if filters.provider_id is not None:
        query = query.where(PaymentAccount.provider_id == filters.provider_id)
    if filters.sender:
        query = query.where(Transaction.sender_name.ilike(f"%{filters.sender.strip()}%"))
    if filters.amount_min_cents is not None:
        query = query.where(Transaction.amount_cents >= filters.amount_min_cents)
    if filters.amount_max_cents is not None:
        query = query.where(Transaction.amount_cents <= filters.amount_max_cents)
    if filters.date_from is not None:
        query = query.where(TelegramDelivery.created_at >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(TelegramDelivery.created_at <= filters.date_to)
    if filters.transaction_id is not None:
        query = query.where(TelegramDelivery.transaction_id == filters.transaction_id)
    if filters.retryable_only:
        # Failed always; stale sending handled in Python after fetch for SQLite/Postgres parity.
        query = query.where(TelegramDelivery.status.in_(("failed", "sending")))

    search = (filters.search or "").strip()
    if search:
        clauses = [
            Transaction.sender_name.ilike(f"%{search}%"),
            Transaction.gmail_message_id.ilike(f"%{search}%"),
            TelegramDelivery.telegram_message_id.ilike(f"%{search}%"),
        ]
        if search.isdigit():
            clauses.append(TelegramDelivery.transaction_id == int(search))
            clauses.append(TelegramDelivery.id == int(search))
        else:
            clauses.append(cast(TelegramDelivery.transaction_id, String).ilike(f"%{search}%"))
        query = query.where(or_(*clauses))

    return query


def delivery_list_base_query() -> Select:
    return (
        select(TelegramDelivery)
        .options(
            selectinload(TelegramDelivery.telegram_integration),
            selectinload(TelegramDelivery.transaction)
            .selectinload(Transaction.payment_account)
            .selectinload(PaymentAccount.provider),
            selectinload(TelegramDelivery.attempts),
        )
        .order_by(TelegramDelivery.created_at.desc(), TelegramDelivery.id.desc())
    )


async def list_telegram_deliveries(
    session: AsyncSession,
    filters: DeliveryListFilters,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TelegramDelivery], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    base = apply_delivery_filters(delivery_list_base_query(), filters)
    count_query = apply_delivery_filters(select(func.count(TelegramDelivery.id)), filters)
    total = int((await session.execute(count_query)).scalar_one())
    rows = list(
        (
            await session.scalars(
                base.offset((page - 1) * page_size).limit(page_size)
            )
        ).all()
    )
    if filters.retryable_only:
        rows = [row for row in rows if is_delivery_eligible_for_manual_retry(row)]
    return rows, total


async def list_all_filtered_delivery_ids(
    session: AsyncSession,
    filters: DeliveryListFilters,
    *,
    limit: int = 500,
) -> list[int]:
    deliveries = list(
        (
            await session.scalars(
                apply_delivery_filters(delivery_list_base_query(), filters).limit(limit)
            )
        ).all()
    )
    return [d.id for d in deliveries if is_delivery_eligible_for_manual_retry(d)]


async def get_telegram_delivery_detail(
    session: AsyncSession,
    delivery_id: int,
) -> TelegramDelivery | None:
    return await session.scalar(
        select(TelegramDelivery)
        .where(TelegramDelivery.id == delivery_id)
        .options(
            selectinload(TelegramDelivery.telegram_integration),
            selectinload(TelegramDelivery.transaction)
            .selectinload(Transaction.payment_account)
            .selectinload(PaymentAccount.provider),
            selectinload(TelegramDelivery.attempts),
        )
    )


async def retry_deliveries_by_ids(delivery_ids: list[int]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    skipped = 0
    for delivery_id in delivery_ids:
        result = await retry_telegram_delivery(delivery_id)
        results.append(result)
        if result.get("ok"):
            succeeded += 1
        elif result.get("reason") in {"already_sent", "pending_owned", "sending_in_progress", "ineligible", "not_found", "not_claimed"}:
            skipped += 1
        else:
            failed += 1
    return {
        "attempted": len(delivery_ids),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


async def load_integration_delivery_stats(
    session: AsyncSession,
    integration_ids: list[int],
    *,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    if not integration_ids:
        return {}
    today_start = start_of_today_kathmandu(now=now)

    rows = list(
        (
            await session.execute(
                select(
                    TelegramDelivery.telegram_integration_id,
                    TelegramDelivery.status,
                    TelegramDelivery.attempt_count,
                    TelegramDelivery.last_attempt_at,
                    TelegramDelivery.sent_at,
                    TelegramDelivery.created_at,
                    TelegramDelivery.last_error,
                ).where(TelegramDelivery.telegram_integration_id.in_(integration_ids))
            )
        ).all()
    )

    stats: dict[int, dict[str, Any]] = {
        integration_id: {
            "messages_today": 0,
            "sent_today": 0,
            "failed_today": 0,
            "pending": 0,
            "last_delivery_at": None,
            "last_failure_at": None,
            "last_failure_error": None,
            "success_rate": None,
            "average_attempts": None,
            "_sent_total": 0,
            "_failed_total": 0,
            "_attempt_sum": 0,
            "_attempt_n": 0,
        }
        for integration_id in integration_ids
    }

    for integration_id, status, attempt_count, last_attempt_at, sent_at, created_at, last_error in rows:
        bucket = stats[integration_id]
        activity_at = _as_utc(last_attempt_at) or _as_utc(created_at)
        if activity_at is not None and activity_at >= today_start:
            bucket["messages_today"] += 1
            if status == "sent":
                bucket["sent_today"] += 1
            elif status == "failed":
                bucket["failed_today"] += 1
        if status in {"pending", "sending"}:
            bucket["pending"] += 1
        if activity_at is not None and (
            bucket["last_delivery_at"] is None or activity_at > bucket["last_delivery_at"]
        ):
            bucket["last_delivery_at"] = activity_at
        failure_at = _as_utc(last_attempt_at)
        if status == "failed" and failure_at is not None and (
            bucket["last_failure_at"] is None or failure_at > bucket["last_failure_at"]
        ):
            bucket["last_failure_at"] = failure_at
            bucket["last_failure_error"] = last_error
        if status == "sent":
            bucket["_sent_total"] += 1
        elif status == "failed":
            bucket["_failed_total"] += 1
        if int(attempt_count or 0) > 0:
            bucket["_attempt_sum"] += int(attempt_count)
            bucket["_attempt_n"] += 1

    for bucket in stats.values():
        completed = bucket["_sent_total"] + bucket["_failed_total"]
        bucket["success_rate"] = round((bucket["_sent_total"] / completed) * 100, 1) if completed else None
        bucket["average_attempts"] = (
            round(bucket["_attempt_sum"] / bucket["_attempt_n"], 2) if bucket["_attempt_n"] else None
        )
        for key in ("_sent_total", "_failed_total", "_attempt_sum", "_attempt_n"):
            del bucket[key]
    return stats


async def load_payment_account_delivery_stats(
    session: AsyncSession,
    account_ids: list[int],
    *,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    if not account_ids:
        return {}
    today_start = start_of_today_kathmandu(now=now)

    delivery_rows = list(
        (
            await session.execute(
                select(
                    Transaction.payment_account_id,
                    TelegramDelivery.status,
                    TelegramDelivery.last_attempt_at,
                    TelegramDelivery.sent_at,
                    TelegramDelivery.created_at,
                )
                .join(TelegramDelivery.transaction)
                .where(Transaction.payment_account_id.in_(account_ids))
            )
        ).all()
    )

    payment_rows = list(
        (
            await session.execute(
                select(
                    Transaction.payment_account_id,
                    func.max(Transaction.received_at),
                )
                .where(Transaction.payment_account_id.in_(account_ids))
                .group_by(Transaction.payment_account_id)
            )
        ).all()
    )
    last_payment_by_account = {account_id: received_at for account_id, received_at in payment_rows}

    stats: dict[int, dict[str, Any]] = {
        account_id: {
            "messages_today": 0,
            "telegram_destination_count": 0,
            "last_payment_at": last_payment_by_account.get(account_id),
            "last_telegram_delivery_at": None,
        }
        for account_id in account_ids
    }

    for account_id, status, last_attempt_at, sent_at, created_at in delivery_rows:
        bucket = stats[account_id]
        activity_at = _as_utc(last_attempt_at) or _as_utc(sent_at) or _as_utc(created_at)
        if activity_at is not None and activity_at >= today_start:
            bucket["messages_today"] += 1
        if activity_at is not None and (
            bucket["last_telegram_delivery_at"] is None or activity_at > bucket["last_telegram_delivery_at"]
        ):
            bucket["last_telegram_delivery_at"] = activity_at

    return stats


async def load_delivery_briefs_for_transactions(
    session: AsyncSession,
    transaction_ids: list[int],
) -> dict[int, list[TelegramDelivery]]:
    if not transaction_ids:
        return {}
    deliveries = list(
        (
            await session.scalars(
                select(TelegramDelivery)
                .where(TelegramDelivery.transaction_id.in_(transaction_ids))
                .options(selectinload(TelegramDelivery.telegram_integration))
                .order_by(TelegramDelivery.id)
            )
        ).all()
    )
    grouped: dict[int, list[TelegramDelivery]] = {txn_id: [] for txn_id in transaction_ids}
    for delivery in deliveries:
        grouped.setdefault(delivery.transaction_id, []).append(delivery)
    return grouped


def build_delivery_timeline(delivery: TelegramDelivery) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"event": "created", "at": delivery.created_at, "detail": None},
    ]
    for attempt in sorted(delivery.attempts, key=lambda item: item.attempt_number):
        events.append(
            {
                "event": "claimed",
                "at": attempt.attempted_at,
                "detail": f"Attempt {attempt.attempt_number}",
            }
        )
        events.append(
            {
                "event": "sending",
                "at": attempt.attempted_at,
                "detail": f"Attempt {attempt.attempt_number}",
            }
        )
        if attempt.status == "sent":
            events.append(
                {
                    "event": "sent",
                    "at": attempt.completed_at or attempt.attempted_at,
                    "detail": attempt.telegram_message_id,
                }
            )
        elif attempt.status == "failed":
            events.append(
                {
                    "event": "failed",
                    "at": attempt.completed_at or attempt.attempted_at,
                    "detail": attempt.error_message,
                }
            )
    if not delivery.attempts:
        if delivery.last_attempt_at:
            events.append({"event": "claimed", "at": delivery.last_attempt_at, "detail": None})
            events.append({"event": "sending", "at": delivery.last_attempt_at, "detail": None})
        if delivery.status == "sent" and delivery.sent_at:
            events.append(
                {
                    "event": "sent",
                    "at": delivery.sent_at,
                    "detail": delivery.telegram_message_id,
                }
            )
        elif delivery.status == "failed":
            events.append(
                {
                    "event": "failed",
                    "at": delivery.last_attempt_at or delivery.updated_at,
                    "detail": delivery.last_error,
                }
            )
    return events


def deliveries_to_csv_rows(deliveries: list[TelegramDelivery]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for delivery in deliveries:
        txn = delivery.transaction
        account = txn.payment_account
        integration = delivery.telegram_integration
        rows.append(
            {
                "id": str(delivery.id),
                "status": delivery.status,
                "integration_name": integration.name if integration else "",
                "bot_username": integration.bot_username or "" if integration else "",
                "group_id": integration.group_id or "" if integration else "",
                "transaction_id": str(delivery.transaction_id),
                "sender_name": txn.sender_name or "",
                "amount_cents": str(txn.amount_cents),
                "provider": account.provider.name if account and account.provider else "",
                "payment_gmail": account.gmail_address if account else "",
                "payment_account": account.friendly_name if account else "",
                "attempt_count": str(delivery.attempt_count),
                "telegram_message_id": delivery.telegram_message_id or "",
                "gmail_message_id": txn.gmail_message_id or "",
                "created_at": delivery.created_at.isoformat() if delivery.created_at else "",
                "last_attempt_at": delivery.last_attempt_at.isoformat() if delivery.last_attempt_at else "",
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else "",
                "last_error": sanitize_telegram_error(delivery.last_error or ""),
            }
        )
    return rows
