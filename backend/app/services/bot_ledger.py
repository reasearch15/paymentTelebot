"""Bot Ledger accounting service.

Membership rule:
  A transaction belongs to a Bot Ledger iff a telegram_deliveries row exists for
  (transaction_id, telegram_integration_id). Current route assignments are ignored
  for historical totals. Reading Bot Ledger never creates deliveries or sends Telegram.

Accounting (integer cents only):
  Total In       = SUM(amount_cents) over unique IN transactions with a delivery
  Total Settled  = SUM(telegram_integration_settlements.amount_cents)
  Current Unsettled = Total In - Total Settled

Over-settlement is rejected (same rule as Gmail settlements). Bot balance cannot go negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import TelegramIntegration
from app.models.telegram_integration_settlement import TelegramIntegrationSettlement
from app.models.transaction import Direction, Transaction
from app.services.telegram import KATHMANDU


@dataclass(frozen=True)
class BotTransactionFilters:
    preset: str | None = None  # today | week | month | all | custom
    date_from: datetime | None = None
    date_to: datetime | None = None
    payment_account_id: int | None = None
    provider_id: int | None = None
    sender: str | None = None
    delivery_status: str | None = None
    min_amount_cents: int | None = None
    max_amount_cents: int | None = None


@dataclass(frozen=True)
class BotSettlementFilters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    created_by: str | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def start_of_today_kathmandu(*, now: datetime | None = None) -> datetime:
    current = _as_utc(now) or datetime.now(UTC)
    local = current.astimezone(KATHMANDU)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def start_of_week_kathmandu(*, now: datetime | None = None) -> datetime:
    """Monday 00:00 Asia/Kathmandu through current time."""
    current = _as_utc(now) or datetime.now(UTC)
    local = current.astimezone(KATHMANDU)
    monday = local - timedelta(days=local.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def start_of_month_kathmandu(*, now: datetime | None = None) -> datetime:
    current = _as_utc(now) or datetime.now(UTC)
    local = current.astimezone(KATHMANDU)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def resolve_preset_range(
    preset: str | None,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    normalized = (preset or "all").strip().lower()
    if normalized in {"", "all", "all_time", "all-time"}:
        return None, None
    if normalized == "today":
        return start_of_today_kathmandu(now=now), None
    if normalized == "week":
        return start_of_week_kathmandu(now=now), None
    if normalized == "month":
        return start_of_month_kathmandu(now=now), None
    if normalized == "custom":
        return _as_utc(date_from), _as_utc(date_to)
    raise ValueError("Invalid preset. Use today, week, month, all, or custom.")


async def get_bot_integration_or_none(
    session: AsyncSession,
    telegram_integration_id: int,
) -> TelegramIntegration | None:
    return await session.get(TelegramIntegration, telegram_integration_id)


async def list_bot_ledger_integrations(session: AsyncSession) -> list[TelegramIntegration]:
    """Return all integrations (enabled and disabled) for historical accounting."""
    return list(
        (
            await session.scalars(
                select(TelegramIntegration).order_by(TelegramIntegration.id.asc())
            )
        ).all()
    )


def _unique_in_transaction_ids(telegram_integration_id: int) -> Select:
    """Defensive DISTINCT so duplicate delivery rows cannot inflate financial totals."""
    return (
        select(TelegramDelivery.transaction_id)
        .where(TelegramDelivery.telegram_integration_id == telegram_integration_id)
        .distinct()
    )


def _unique_in_transactions_subquery(telegram_integration_id: int):
    unique_ids = _unique_in_transaction_ids(telegram_integration_id).subquery()
    return (
        select(
            Transaction.id.label("transaction_id"),
            Transaction.amount_cents.label("amount_cents"),
            Transaction.received_at.label("received_at"),
            Transaction.payment_account_id.label("payment_account_id"),
        )
        .join(unique_ids, unique_ids.c.transaction_id == Transaction.id)
        .where(Transaction.direction == Direction.IN)
    ).subquery()


async def sum_bot_total_in_cents(session: AsyncSession, telegram_integration_id: int) -> int:
    unique_txns = _unique_in_transactions_subquery(telegram_integration_id)
    total = await session.scalar(select(func.coalesce(func.sum(unique_txns.c.amount_cents), 0)))
    return int(total or 0)


async def sum_bot_settled_cents(session: AsyncSession, telegram_integration_id: int) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(TelegramIntegrationSettlement.amount_cents), 0)).where(
            TelegramIntegrationSettlement.telegram_integration_id == telegram_integration_id
        )
    )
    return int(total or 0)


async def compute_bot_unsettled_cents(session: AsyncSession, telegram_integration_id: int) -> int:
    return await sum_bot_total_in_cents(session, telegram_integration_id) - await sum_bot_settled_cents(
        session, telegram_integration_id
    )


async def _count_and_sum_unique_in_since(
    session: AsyncSession,
    telegram_integration_id: int,
    *,
    since: datetime | None,
) -> tuple[int, int]:
    unique_txns = _unique_in_transactions_subquery(telegram_integration_id)
    query = select(
        func.count(unique_txns.c.transaction_id),
        func.coalesce(func.sum(unique_txns.c.amount_cents), 0),
    )
    if since is not None:
        query = query.where(unique_txns.c.received_at >= since)
    count, amount = (await session.execute(query)).one()
    return int(count or 0), int(amount or 0)


async def _delivery_status_counts(
    session: AsyncSession,
    telegram_integration_id: int,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(TelegramDelivery.status, func.count(TelegramDelivery.id))
            .where(TelegramDelivery.telegram_integration_id == telegram_integration_id)
            .group_by(TelegramDelivery.status)
        )
    ).all()
    counts = {"sent": 0, "failed": 0, "pending": 0, "sending": 0}
    for status_value, count in rows:
        if status_value in counts:
            counts[status_value] = int(count)
    return counts


async def _last_payment_at(session: AsyncSession, telegram_integration_id: int) -> datetime | None:
    unique_txns = _unique_in_transactions_subquery(telegram_integration_id)
    return await session.scalar(select(func.max(unique_txns.c.received_at)))


async def _last_settlement_at(session: AsyncSession, telegram_integration_id: int) -> datetime | None:
    return await session.scalar(
        select(func.max(TelegramIntegrationSettlement.settled_at)).where(
            TelegramIntegrationSettlement.telegram_integration_id == telegram_integration_id
        )
    )


async def count_assigned_gmail_accounts(session: AsyncSession, telegram_integration_id: int) -> int:
    total = await session.scalar(
        select(func.count(PaymentAccountTelegramRoute.id)).where(
            PaymentAccountTelegramRoute.telegram_integration_id == telegram_integration_id
        )
    )
    return int(total or 0)


async def build_bot_ledger_summary(
    session: AsyncSession,
    integration: TelegramIntegration,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    today_start = start_of_today_kathmandu(now=now)
    week_start = start_of_week_kathmandu(now=now)
    month_start = start_of_month_kathmandu(now=now)

    total_in = await sum_bot_total_in_cents(session, integration.id)
    total_settled = await sum_bot_settled_cents(session, integration.id)
    payments_today, amount_today = await _count_and_sum_unique_in_since(
        session, integration.id, since=today_start
    )
    payments_week, amount_week = await _count_and_sum_unique_in_since(
        session, integration.id, since=week_start
    )
    payments_month, amount_month = await _count_and_sum_unique_in_since(
        session, integration.id, since=month_start
    )
    all_time_payments, all_time_amount = await _count_and_sum_unique_in_since(
        session, integration.id, since=None
    )

    return {
        "telegram_integration": {
            "id": integration.id,
            "name": integration.name,
            "bot_username": integration.bot_username,
            "group_id": integration.group_id,
            "enabled": integration.enabled,
        },
        "current_unsettled_cents": total_in - total_settled,
        "total_in_cents": total_in,
        "total_settled_cents": total_settled,
        "payments_today": payments_today,
        "amount_today_cents": amount_today,
        "payments_week": payments_week,
        "amount_week_cents": amount_week,
        "payments_month": payments_month,
        "amount_month_cents": amount_month,
        "all_time_payments": all_time_payments,
        "all_time_amount_cents": all_time_amount,
        "assigned_gmail_accounts": await count_assigned_gmail_accounts(session, integration.id),
        "delivery_counts": await _delivery_status_counts(session, integration.id),
        "last_payment_at": await _last_payment_at(session, integration.id),
        "last_settlement_at": await _last_settlement_at(session, integration.id),
    }


async def build_gmail_breakdown(
    session: AsyncSession,
    telegram_integration_id: int,
) -> list[dict[str, Any]]:
    """Historical Gmail source breakdown from deliveries, not current routes."""
    unique_txns = _unique_in_transactions_subquery(telegram_integration_id)

    rows = (
        await session.execute(
            select(
                PaymentAccount.id,
                PaymentAccount.friendly_name,
                PaymentAccount.gmail_address,
                Provider.name,
                func.count(unique_txns.c.transaction_id),
                func.coalesce(func.sum(unique_txns.c.amount_cents), 0),
                func.max(unique_txns.c.received_at),
            )
            .select_from(unique_txns)
            .join(PaymentAccount, PaymentAccount.id == unique_txns.c.payment_account_id)
            .join(Provider, Provider.id == PaymentAccount.provider_id)
            .group_by(
                PaymentAccount.id,
                PaymentAccount.friendly_name,
                PaymentAccount.gmail_address,
                Provider.name,
            )
            .order_by(func.coalesce(func.sum(unique_txns.c.amount_cents), 0).desc(), PaymentAccount.id)
        )
    ).all()

    return [
        {
            "payment_account_id": account_id,
            "gmail_account": gmail_address,
            "friendly_name": friendly_name,
            "provider_name": provider_name,
            "payment_count": int(payment_count or 0),
            "total_amount_cents": int(total_amount or 0),
            "last_payment_at": last_payment_at,
        }
        for account_id, friendly_name, gmail_address, provider_name, payment_count, total_amount, last_payment_at in rows
    ]


def apply_bot_transaction_filters(
    query: Select,
    telegram_integration_id: int,
    filters: BotTransactionFilters,
    *,
    now: datetime | None = None,
) -> Select:
    query = (
        query.join(Transaction, Transaction.id == TelegramDelivery.transaction_id)
        .join(PaymentAccount, PaymentAccount.id == Transaction.payment_account_id)
        .join(Provider, Provider.id == PaymentAccount.provider_id)
        .where(
            TelegramDelivery.telegram_integration_id == telegram_integration_id,
            Transaction.direction == Direction.IN,
        )
    )

    date_from, date_to = resolve_preset_range(
        filters.preset,
        date_from=filters.date_from,
        date_to=filters.date_to,
        now=now,
    )
    if date_from is not None:
        query = query.where(Transaction.received_at >= date_from)
    if date_to is not None:
        query = query.where(Transaction.received_at <= date_to)
    if filters.payment_account_id is not None:
        query = query.where(Transaction.payment_account_id == filters.payment_account_id)
    if filters.provider_id is not None:
        query = query.where(PaymentAccount.provider_id == filters.provider_id)
    if filters.sender:
        query = query.where(Transaction.sender_name.ilike(f"%{filters.sender.strip()}%"))
    if filters.delivery_status:
        query = query.where(TelegramDelivery.status == filters.delivery_status)
    if filters.min_amount_cents is not None:
        query = query.where(Transaction.amount_cents >= filters.min_amount_cents)
    if filters.max_amount_cents is not None:
        query = query.where(Transaction.amount_cents <= filters.max_amount_cents)
    return query


async def list_bot_transactions(
    session: AsyncSession,
    telegram_integration_id: int,
    filters: BotTransactionFilters,
    *,
    page: int = 1,
    page_size: int = 50,
    now: datetime | None = None,
) -> tuple[list[TelegramDelivery], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    filtered = apply_bot_transaction_filters(
        select(TelegramDelivery).options(
            selectinload(TelegramDelivery.transaction)
            .selectinload(Transaction.payment_account)
            .selectinload(PaymentAccount.provider),
            selectinload(TelegramDelivery.telegram_integration),
        ),
        telegram_integration_id,
        filters,
        now=now,
    ).order_by(Transaction.received_at.desc(), Transaction.id.desc(), TelegramDelivery.id.desc())

    count_query = apply_bot_transaction_filters(
        select(func.count(func.distinct(TelegramDelivery.transaction_id))),
        telegram_integration_id,
        filters,
        now=now,
    )
    total = int((await session.execute(count_query)).scalar_one())
    rows = list((await session.scalars(filtered.offset((page - 1) * page_size).limit(page_size))).all())
    return rows, total


async def create_bot_settlement(
    session: AsyncSession,
    *,
    telegram_integration_id: int,
    amount_cents: int,
    note: str | None,
    created_by_user_id: str,
) -> TelegramIntegrationSettlement:
    """Record a bot settlement atomically. Never sends Telegram or mutates deliveries/transactions."""
    if amount_cents <= 0:
        raise ValueError("Settlement amount must be greater than zero.")

    created_by = (created_by_user_id or "").strip()
    if not created_by:
        raise ValueError("created_by_user_id is required.")

    integration = await session.scalar(
        select(TelegramIntegration)
        .where(TelegramIntegration.id == telegram_integration_id)
        .with_for_update()
    )
    if integration is None:
        raise LookupError("Telegram integration not found.")

    balance_before = await compute_bot_unsettled_cents(session, telegram_integration_id)
    if amount_cents > balance_before:
        raise ValueError(
            f"Settlement amount exceeds current unsettled balance of ${balance_before / 100:,.2f}."
        )

    balance_after = balance_before - amount_cents
    settlement = TelegramIntegrationSettlement(
        telegram_integration_id=telegram_integration_id,
        amount_cents=amount_cents,
        balance_before_cents=balance_before,
        balance_after_cents=balance_after,
        note=note,
        created_by_user_id=created_by,
        settled_at=datetime.now(UTC),
    )
    session.add(settlement)
    await session.flush()
    return settlement


def apply_bot_settlement_filters(
    query: Select,
    telegram_integration_id: int,
    filters: BotSettlementFilters,
) -> Select:
    query = query.where(TelegramIntegrationSettlement.telegram_integration_id == telegram_integration_id)
    if filters.date_from is not None:
        query = query.where(TelegramIntegrationSettlement.settled_at >= _as_utc(filters.date_from))
    if filters.date_to is not None:
        query = query.where(TelegramIntegrationSettlement.settled_at <= _as_utc(filters.date_to))
    if filters.created_by:
        query = query.where(
            TelegramIntegrationSettlement.created_by_user_id.ilike(f"%{filters.created_by.strip()}%")
        )
    return query


async def list_bot_settlements(
    session: AsyncSession,
    telegram_integration_id: int,
    filters: BotSettlementFilters,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TelegramIntegrationSettlement], int, dict[int, int]]:
    """Return settlements newest-first with running settled totals (all-time ascending sum)."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    count_query = apply_bot_settlement_filters(
        select(func.count(TelegramIntegrationSettlement.id)),
        telegram_integration_id,
        filters,
    )
    total = int((await session.execute(count_query)).scalar_one())

    query = apply_bot_settlement_filters(
        select(TelegramIntegrationSettlement),
        telegram_integration_id,
        filters,
    ).order_by(
        TelegramIntegrationSettlement.settled_at.desc(),
        TelegramIntegrationSettlement.id.desc(),
    )
    rows = list((await session.scalars(query.offset((page - 1) * page_size).limit(page_size))).all())

    all_settlements = list(
        (
            await session.scalars(
                select(TelegramIntegrationSettlement)
                .where(TelegramIntegrationSettlement.telegram_integration_id == telegram_integration_id)
                .order_by(
                    TelegramIntegrationSettlement.settled_at.asc(),
                    TelegramIntegrationSettlement.id.asc(),
                )
            )
        ).all()
    )
    running = 0
    running_by_id: dict[int, int] = {}
    for settlement in all_settlements:
        running += int(settlement.amount_cents)
        running_by_id[settlement.id] = running

    return rows, total, running_by_id
