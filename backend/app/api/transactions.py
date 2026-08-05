from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.payment_account import PaymentAccount
from app.models.transaction import Transaction
from app.schemas.settlement import AccountUnsettledBalance
from app.schemas.transaction import LedgerListResponse, LedgerTotals, TransactionSummary
from app.schemas.telegram import TelegramDeliverySummary
from app.services.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    apply_newest_first_keyset,
    clamp_page_size,
    require_time_id_cursor,
    slice_page_with_cursor,
)
from app.services.settlement import compute_unsettled_balance_cents, sum_settled_cents, sum_transaction_amounts
from app.services.telegram import load_delivery_summaries_for_transactions

router = APIRouter(prefix="/transactions", tags=["transactions"], dependencies=[Depends(require_admin)])


def serialize_transaction(
    transaction: Transaction,
    delivery_summary: TelegramDeliverySummary | None = None,
) -> TransactionSummary:
    account = transaction.payment_account
    provider = account.provider
    return TransactionSummary(
        id=transaction.id,
        payment_account_id=transaction.payment_account_id,
        provider_id=account.provider_id,
        provider_name=provider.name,
        friendly_name=account.friendly_name,
        direction=transaction.direction.value,
        amount_cents=transaction.amount_cents,
        sender_name=transaction.sender_name,
        sender_payment_tag=transaction.sender_payment_tag,
        receiver_tag=transaction.receiver_tag or account.receiver_tag,
        provider_reference=transaction.provider_reference,
        gmail_message_id=transaction.gmail_message_id,
        received_at=transaction.received_at,
        telegram_status=transaction.telegram_status,
        telegram_sent_at=transaction.telegram_sent_at,
        created_at=transaction.created_at,
        telegram_delivery_summary=delivery_summary,
    )


def apply_transaction_filters(
    query: Select,
    *,
    payment_account_id: int | None,
    provider_id: int | None,
    direction: str | None,
) -> Select:
    if payment_account_id is not None:
        query = query.where(Transaction.payment_account_id == payment_account_id)
    if provider_id is not None:
        query = query.where(PaymentAccount.provider_id == provider_id)
    if direction is not None:
        query = query.where(Transaction.direction == direction)
    return query


async def compute_ledger_totals(
    db: AsyncSession,
    *,
    payment_account_id: int | None = None,
) -> LedgerTotals:
    incoming, outgoing = await sum_transaction_amounts(db, payment_account_id=payment_account_id)
    settled = await sum_settled_cents(db, payment_account_id=payment_account_id)
    count_query = select(func.count(Transaction.id))
    if payment_account_id is not None:
        count_query = count_query.where(Transaction.payment_account_id == payment_account_id)
    total_transactions = int((await db.execute(count_query)).scalar_one())
    return LedgerTotals(
        total_incoming_cents=incoming,
        total_outgoing_cents=outgoing,
        total_settled_cents=settled,
        unsettled_balance_cents=incoming - outgoing - settled,
        total_transactions=total_transactions,
    )


async def list_account_balances(db: AsyncSession) -> list[AccountUnsettledBalance]:
    result = await db.execute(select(PaymentAccount).order_by(PaymentAccount.friendly_name, PaymentAccount.id))
    balances: list[AccountUnsettledBalance] = []
    for account in result.scalars().all():
        unsettled = await compute_unsettled_balance_cents(db, payment_account_id=account.id)
        balances.append(
            AccountUnsettledBalance(
                payment_account_id=account.id,
                friendly_name=account.friendly_name,
                unsettled_balance_cents=unsettled,
            )
        )
    return balances


@router.get("", response_model=LedgerListResponse)
async def list_transactions(
    payment_account_id: int | None = None,
    provider_id: int | None = None,
    direction: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> LedgerListResponse:
    page_size = clamp_page_size(limit)
    cursor_key = require_time_id_cursor(cursor)

    query = (
        select(Transaction)
        .join(Transaction.payment_account)
        .join(PaymentAccount.provider)
        .options(selectinload(Transaction.payment_account).selectinload(PaymentAccount.provider))
        .order_by(Transaction.received_at.desc(), Transaction.id.desc())
    )
    query = apply_transaction_filters(
        query,
        payment_account_id=payment_account_id,
        provider_id=provider_id,
        direction=direction,
    )
    query = apply_newest_first_keyset(
        query,
        time_column=Transaction.received_at,
        id_column=Transaction.id,
        cursor=cursor_key,
    )
    # Fetch one extra row to detect has_more without a separate COUNT for the page window.
    query = query.limit(page_size + 1)

    result = await db.execute(query)
    fetched = list(result.scalars().all())
    page_rows, next_cursor, has_more = slice_page_with_cursor(
        fetched,
        limit=page_size,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )
    delivery_summaries = await load_delivery_summaries_for_transactions(
        db,
        [row.id for row in page_rows],
    )
    transactions = [
        serialize_transaction(row, delivery_summaries.get(row.id))
        for row in page_rows
    ]

    # Totals always cover the full filtered account history, independent of the page window.
    totals = await compute_ledger_totals(db, payment_account_id=payment_account_id)
    account_balances = await list_account_balances(db)
    return LedgerListResponse(
        transactions=transactions,
        totals=totals,
        account_balances=account_balances,
        limit=page_size,
        next_cursor=next_cursor,
        has_more=has_more,
    )
