from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.settlement import Settlement
from app.schemas.settlement import SettlementCreate, SettlementListResponse, SettlementResponse
from app.services.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    apply_newest_first_keyset,
    clamp_page_size,
    require_time_id_cursor,
    slice_page_with_cursor,
)
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


@router.get("", response_model=SettlementListResponse)
async def list_settlements(
    payment_account_id: int | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> SettlementListResponse:
    page_size = clamp_page_size(limit)
    cursor_key = require_time_id_cursor(cursor)

    query = (
        select(Settlement)
        .options(selectinload(Settlement.payment_account))
        .order_by(Settlement.settled_at.desc(), Settlement.id.desc())
    )
    if payment_account_id is not None:
        query = query.where(Settlement.payment_account_id == payment_account_id)
    query = apply_newest_first_keyset(
        query,
        time_column=Settlement.settled_at,
        id_column=Settlement.id,
        cursor=cursor_key,
    )
    query = query.limit(page_size + 1)

    result = await db.execute(query)
    fetched = list(result.scalars().all())
    page_rows, next_cursor, has_more = slice_page_with_cursor(
        fetched,
        limit=page_size,
        cursor_from_row=lambda row: (row.settled_at, row.id),
    )
    return SettlementListResponse(
        items=[serialize_settlement(row) for row in page_rows],
        limit=page_size,
        next_cursor=next_cursor,
        has_more=has_more,
    )


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
