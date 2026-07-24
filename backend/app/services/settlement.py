from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment_account import PaymentAccount
from app.models.settlement import Settlement
from app.models.transaction import Direction, Transaction


async def sum_transaction_amounts(
    session: AsyncSession,
    *,
    payment_account_id: int | None = None,
) -> tuple[int, int]:
    query = select(
        func.coalesce(func.sum(case((Transaction.direction == Direction.IN, Transaction.amount_cents), else_=0)), 0),
        func.coalesce(func.sum(case((Transaction.direction == Direction.OUT, Transaction.amount_cents), else_=0)), 0),
    )
    if payment_account_id is not None:
        query = query.where(Transaction.payment_account_id == payment_account_id)
    incoming, outgoing = (await session.execute(query)).one()
    return int(incoming), int(outgoing)


async def sum_settled_cents(
    session: AsyncSession,
    *,
    payment_account_id: int | None = None,
) -> int:
    query = select(func.coalesce(func.sum(Settlement.amount_cents), 0))
    if payment_account_id is not None:
        query = query.where(Settlement.payment_account_id == payment_account_id)
    return int((await session.execute(query)).scalar_one())


async def compute_unsettled_balance_cents(
    session: AsyncSession,
    *,
    payment_account_id: int | None = None,
) -> int:
    incoming, outgoing = await sum_transaction_amounts(session, payment_account_id=payment_account_id)
    settled = await sum_settled_cents(session, payment_account_id=payment_account_id)
    return incoming - outgoing - settled


async def create_settlement(
    session: AsyncSession,
    *,
    payment_account_id: int,
    amount_cents: int,
    note: str | None,
) -> Settlement:
    if amount_cents <= 0:
        raise ValueError("Settlement amount must be greater than zero.")

    account = await session.scalar(
        select(PaymentAccount).where(PaymentAccount.id == payment_account_id).with_for_update()
    )
    if account is None:
        raise LookupError("Payment account not found.")

    balance_before = await compute_unsettled_balance_cents(session, payment_account_id=payment_account_id)
    if amount_cents > balance_before:
        raise ValueError(
            f"Settlement amount exceeds unsettled balance of ${balance_before / 100:,.2f}."
        )

    balance_after = balance_before - amount_cents
    settlement = Settlement(
        payment_account_id=payment_account_id,
        amount_cents=amount_cents,
        balance_before_cents=balance_before,
        balance_after_cents=balance_after,
        note=note,
        settled_at=datetime.now(UTC),
    )
    session.add(settlement)
    await session.flush()
    return settlement
