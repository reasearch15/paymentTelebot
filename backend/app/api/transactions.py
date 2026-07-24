from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.payment_account import PaymentAccount
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionSummary

router = APIRouter(prefix="/transactions", tags=["transactions"], dependencies=[Depends(require_admin)])


def serialize_transaction(transaction: Transaction) -> TransactionSummary:
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
    )


@router.get("", response_model=list[TransactionSummary])
async def list_transactions(
    payment_account_id: int | None = None,
    provider_id: int | None = None,
    direction: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[TransactionSummary]:
    query = (
        select(Transaction)
        .join(Transaction.payment_account)
        .join(PaymentAccount.provider)
        .options(selectinload(Transaction.payment_account).selectinload(PaymentAccount.provider))
        .order_by(Transaction.received_at.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if payment_account_id is not None:
        query = query.where(Transaction.payment_account_id == payment_account_id)
    if provider_id is not None:
        query = query.where(PaymentAccount.provider_id == provider_id)
    if direction is not None:
        query = query.where(Transaction.direction == direction)

    result = await db.execute(query)
    return [serialize_transaction(row) for row in result.scalars().all()]
