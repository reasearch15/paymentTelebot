"""Bot Ledger API — per-Telegram-integration financial accounting.

Does not modify fan-out, Gmail parsing, global ledger totals, or Gmail/player settlements.
Never sends Telegram messages. Never creates telegram_deliveries.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.session import get_db
from app.schemas.bot_ledger import (
    BotLedgerDeliveryCounts,
    BotLedgerGmailBreakdownItem,
    BotLedgerIntegrationItem,
    BotLedgerIntegrationSummary,
    BotLedgerSettlementCreate,
    BotLedgerSettlementItem,
    BotLedgerSettlementListResponse,
    BotLedgerSummary,
    BotLedgerTransactionItem,
    BotLedgerTransactionListResponse,
)
from app.services.bot_ledger import (
    BotSettlementFilters,
    BotTransactionFilters,
    build_bot_ledger_summary,
    build_gmail_breakdown,
    create_bot_settlement,
    get_bot_integration_or_none,
    list_bot_ledger_integrations,
    list_bot_settlements,
    list_bot_transactions,
    resolve_preset_range,
    sum_bot_settled_cents,
)
from app.services.telegram import sanitize_telegram_error
from app.services.telegram_delivery_ops import parse_amount_to_cents

router = APIRouter(
    prefix="/bot-ledger",
    tags=["bot-ledger"],
    dependencies=[Depends(require_admin)],
)


async def _require_integration(db: AsyncSession, telegram_integration_id: int):
    integration = await get_bot_integration_or_none(db, telegram_integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram integration not found.")
    return integration


@router.get("/integrations", response_model=list[BotLedgerIntegrationItem])
async def list_integrations(db: AsyncSession = Depends(get_db)) -> list[BotLedgerIntegrationItem]:
    integrations = await list_bot_ledger_integrations(db)
    return [
        BotLedgerIntegrationItem(
            id=item.id,
            name=item.name,
            bot_username=item.bot_username,
            group_id=item.group_id,
            enabled=item.enabled,
        )
        for item in integrations
    ]


@router.get("/{telegram_integration_id}/summary", response_model=BotLedgerSummary)
async def get_summary(
    telegram_integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> BotLedgerSummary:
    integration = await _require_integration(db, telegram_integration_id)
    data = await build_bot_ledger_summary(db, integration)
    return BotLedgerSummary(
        telegram_integration=BotLedgerIntegrationSummary(**data["telegram_integration"]),
        current_unsettled_cents=data["current_unsettled_cents"],
        total_in_cents=data["total_in_cents"],
        total_settled_cents=data["total_settled_cents"],
        payments_today=data["payments_today"],
        amount_today_cents=data["amount_today_cents"],
        payments_week=data["payments_week"],
        amount_week_cents=data["amount_week_cents"],
        payments_month=data["payments_month"],
        amount_month_cents=data["amount_month_cents"],
        all_time_payments=data["all_time_payments"],
        all_time_amount_cents=data["all_time_amount_cents"],
        assigned_gmail_accounts=data["assigned_gmail_accounts"],
        delivery_counts=BotLedgerDeliveryCounts(**data["delivery_counts"]),
        last_payment_at=data["last_payment_at"],
        last_settlement_at=data["last_settlement_at"],
    )


@router.get("/{telegram_integration_id}/gmail-breakdown", response_model=list[BotLedgerGmailBreakdownItem])
async def get_gmail_breakdown(
    telegram_integration_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[BotLedgerGmailBreakdownItem]:
    await _require_integration(db, telegram_integration_id)
    rows = await build_gmail_breakdown(db, telegram_integration_id)
    return [BotLedgerGmailBreakdownItem(**row) for row in rows]


@router.get("/{telegram_integration_id}/transactions", response_model=BotLedgerTransactionListResponse)
async def get_transactions(
    telegram_integration_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    preset: str | None = Query(default="all"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    payment_account_id: int | None = Query(default=None),
    provider_id: int | None = Query(default=None),
    sender: str | None = Query(default=None),
    delivery_status: str | None = Query(default=None),
    min_amount: str | None = Query(default=None),
    max_amount: str | None = Query(default=None),
    min_amount_cents: int | None = Query(default=None),
    max_amount_cents: int | None = Query(default=None),
    sort: str | None = Query(default="newest"),
    db: AsyncSession = Depends(get_db),
) -> BotLedgerTransactionListResponse:
    del sort
    await _require_integration(db, telegram_integration_id)

    if delivery_status and delivery_status not in {"pending", "sending", "sent", "failed"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid delivery_status.")

    try:
        min_cents = min_amount_cents if min_amount_cents is not None else parse_amount_to_cents(min_amount)
        max_cents = max_amount_cents if max_amount_cents is not None else parse_amount_to_cents(max_amount)
        resolve_preset_range(preset, date_from=date_from, date_to=date_to)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    filters = BotTransactionFilters(
        preset=preset,
        date_from=date_from,
        date_to=date_to,
        payment_account_id=payment_account_id,
        provider_id=provider_id,
        sender=sender,
        delivery_status=delivery_status,
        min_amount_cents=min_cents,
        max_amount_cents=max_cents,
    )
    rows, total = await list_bot_transactions(
        db,
        telegram_integration_id,
        filters,
        page=page,
        page_size=page_size,
    )
    items = []
    for delivery in rows:
        txn = delivery.transaction
        account = txn.payment_account
        provider = account.provider
        items.append(
            BotLedgerTransactionItem(
                transaction_id=txn.id,
                received_at=txn.received_at,
                sender_name=txn.sender_name,
                payment_account_id=account.id,
                payment_account_name=account.friendly_name,
                payment_gmail=account.gmail_address,
                provider_id=provider.id,
                provider_name=provider.name,
                amount_cents=txn.amount_cents,
                delivery_status=delivery.status,
                attempt_count=int(delivery.attempt_count or 0),
                telegram_message_id=delivery.telegram_message_id,
                delivery_id=delivery.id,
                last_error=sanitize_telegram_error(delivery.last_error) if delivery.last_error else None,
            )
        )
    return BotLedgerTransactionListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{telegram_integration_id}/settlements", response_model=BotLedgerSettlementListResponse)
async def get_settlements(
    telegram_integration_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    created_by: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> BotLedgerSettlementListResponse:
    await _require_integration(db, telegram_integration_id)
    rows, total, running_by_id = await list_bot_settlements(
        db,
        telegram_integration_id,
        BotSettlementFilters(date_from=date_from, date_to=date_to, created_by=created_by),
        page=page,
        page_size=page_size,
    )
    return BotLedgerSettlementListResponse(
        items=[
            BotLedgerSettlementItem(
                id=row.id,
                telegram_integration_id=row.telegram_integration_id,
                amount_cents=row.amount_cents,
                balance_before_cents=row.balance_before_cents,
                balance_after_cents=row.balance_after_cents,
                note=row.note,
                created_by_user_id=row.created_by_user_id,
                settled_at=row.settled_at,
                created_at=row.created_at,
                running_settled_total_cents=running_by_id.get(row.id, 0),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{telegram_integration_id}/settlements",
    response_model=BotLedgerSettlementItem,
    status_code=status.HTTP_201_CREATED,
)
async def post_settlement(
    telegram_integration_id: int,
    payload: BotLedgerSettlementCreate,
    admin_email: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BotLedgerSettlementItem:
    await _require_integration(db, telegram_integration_id)

    try:
        amount_cents = payload.amount_cents
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        async with db.begin():
            settlement = await create_bot_settlement(
                db,
                telegram_integration_id=telegram_integration_id,
                amount_cents=amount_cents,
                note=payload.note,
                created_by_user_id=admin_email,
            )
            settlement_id = settlement.id
            amount = settlement.amount_cents
            before = settlement.balance_before_cents
            after = settlement.balance_after_cents
            note = settlement.note
            created_by = settlement.created_by_user_id
            settled_at = settlement.settled_at
            created_at = settlement.created_at
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    running = await sum_bot_settled_cents(db, telegram_integration_id)
    return BotLedgerSettlementItem(
        id=settlement_id,
        telegram_integration_id=telegram_integration_id,
        amount_cents=amount,
        balance_before_cents=before,
        balance_after_cents=after,
        note=note,
        created_by_user_id=created_by,
        settled_at=settled_at,
        created_at=created_at,
        running_settled_total_cents=running,
    )
