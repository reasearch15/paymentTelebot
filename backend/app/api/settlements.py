from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.settlement import Settlement
from app.schemas.settlement import SettlementCreate, SettlementResponse
from app.services.settlement import create_settlement

router = APIRouter(prefix="/settlements", tags=["settlements"], dependencies=[Depends(require_admin)])


def serialize_settlement(settlement: Settlement) -> SettlementResponse:
    return SettlementResponse(
        id=settlement.id,
        payment_account_id=settlement.payment_account_id,
        friendly_name=settlement.payment_account.friendly_name,
        amount_cents=settlement.amount_cents,
        balance_before_cents=settlement.balance_before_cents,
        balance_after_cents=settlement.balance_after_cents,
        note=settlement.note,
        status="completed",
        settled_at=settlement.settled_at,
        created_at=settlement.created_at,
    )


@router.get("", response_model=list[SettlementResponse])
async def list_settlements(
    payment_account_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[SettlementResponse]:
    query = (
        select(Settlement)
        .options(selectinload(Settlement.payment_account))
        .order_by(Settlement.settled_at.desc(), Settlement.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if payment_account_id is not None:
        query = query.where(Settlement.payment_account_id == payment_account_id)
    result = await db.execute(query)
    return [serialize_settlement(row) for row in result.scalars().all()]


@router.post("", response_model=SettlementResponse, status_code=status.HTTP_201_CREATED)
async def create_settlement_endpoint(
    payload: SettlementCreate,
    db: AsyncSession = Depends(get_db),
) -> SettlementResponse:
    try:
        async with db.begin():
            settlement = await create_settlement(
                db,
                payment_account_id=payload.payment_account_id,
                amount_cents=payload.amount_cents,
                note=payload.note,
            )
            settlement_id = settlement.id
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    loaded = await db.scalar(
        select(Settlement)
        .options(selectinload(Settlement.payment_account))
        .where(Settlement.id == settlement_id)
    )
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settlement was not saved.")
    return serialize_settlement(loaded)
