from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.player_settlement import PlayerSettlement, PlayerSettlementDirection
from app.schemas.player_settlement import (
    PlayerLedgerDetailResponse,
    PlayerLedgerListResponse,
    PlayerLedgerRow,
    PlayerLedgerTransaction,
    PlayerSenderListResponse,
    PlayerSettlementCreate,
    PlayerSettlementDirectionValue,
    PlayerSettlementListResponse,
    PlayerSettlementResponse,
)
from app.services.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    apply_newest_first_keyset,
    clamp_page_size,
    require_time_id_cursor,
    slice_page_with_cursor,
)
from app.services.player_settlement import (
    create_player_settlement,
    get_player_ledger_detail,
    list_distinct_sender_names,
    list_player_ledger_rows,
    normalize_sender_identity,
)

router = APIRouter(tags=["player-ledger"], dependencies=[Depends(require_admin)])


def serialize_player_settlement(settlement: PlayerSettlement) -> PlayerSettlementResponse:
    return PlayerSettlementResponse(
        id=settlement.id,
        sender_name=settlement.sender_name,
        direction=PlayerSettlementDirectionValue(settlement.direction.value),
        amount_cents=settlement.amount_cents,
        payment_account_id=settlement.payment_account_id,
        account_name=settlement.payment_account.friendly_name,
        reference=settlement.reference,
        note=settlement.note,
        settled_at=settlement.settled_at,
        created_by_user_id=settlement.created_by_user_id,
        created_at=settlement.created_at,
    )


def serialize_player_ledger_row(row: dict) -> PlayerLedgerRow:
    return PlayerLedgerRow(**row)


@router.get("/player-ledger", response_model=PlayerLedgerListResponse)
async def player_ledger_list(
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PlayerLedgerListResponse:
    rows = await list_player_ledger_rows(db, search=search)
    return PlayerLedgerListResponse(items=[serialize_player_ledger_row(row) for row in rows])


@router.get("/player-ledger/senders", response_model=PlayerSenderListResponse)
async def player_ledger_senders(
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PlayerSenderListResponse:
    return PlayerSenderListResponse(items=await list_distinct_sender_names(db, search=search))


@router.get("/player-ledger/detail", response_model=PlayerLedgerDetailResponse)
async def player_ledger_detail(
    sender_name: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
) -> PlayerLedgerDetailResponse:
    detail = await get_player_ledger_detail(db, sender_name=sender_name)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found.")

    transactions = [
        PlayerLedgerTransaction(
            id=tx.id,
            payment_account_id=tx.payment_account_id,
            account_name=tx.payment_account.friendly_name,
            direction=tx.direction.value,
            amount_cents=tx.amount_cents,
            sender_name=tx.sender_name,
            provider_reference=tx.provider_reference,
            received_at=tx.received_at,
            telegram_status=tx.telegram_status,
        )
        for tx in detail["transactions"]
    ]
    settlements = [serialize_player_settlement(item) for item in detail["settlements"]]
    return PlayerLedgerDetailResponse(
        summary=serialize_player_ledger_row(detail["summary"]),
        transactions=transactions,
        settlements=settlements,
    )


@router.get("/player-settlements", response_model=PlayerSettlementListResponse)
async def player_settlements_list(
    sender_name: str | None = Query(default=None),
    direction: PlayerSettlementDirectionValue | None = Query(default=None),
    settled_from: datetime | None = Query(default=None),
    settled_to: datetime | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PlayerSettlementListResponse:
    page_size = clamp_page_size(limit)
    cursor_key = require_time_id_cursor(cursor)

    direction_enum = PlayerSettlementDirection(direction.value) if direction else None
    # Use filtered query with keyset pagination.
    query = (
        select(PlayerSettlement)
        .options(selectinload(PlayerSettlement.payment_account))
        .order_by(PlayerSettlement.settled_at.desc(), PlayerSettlement.id.desc())
    )
    if sender_name:
        identity = normalize_sender_identity(sender_name)
        if identity:
            from sqlalchemy import func, or_

            sender_key = func.regexp_replace(func.btrim(PlayerSettlement.sender_name), r"\s+", " ", "g")
            query = query.where(
                or_(
                    sender_key == identity,
                    PlayerSettlement.sender_name.ilike(f"%{identity}%"),
                )
            )
    if direction_enum is not None:
        query = query.where(PlayerSettlement.direction == direction_enum)
    if settled_from is not None:
        query = query.where(PlayerSettlement.settled_at >= settled_from)
    if settled_to is not None:
        query = query.where(PlayerSettlement.settled_at <= settled_to)

    query = apply_newest_first_keyset(
        query,
        time_column=PlayerSettlement.settled_at,
        id_column=PlayerSettlement.id,
        cursor=cursor_key,
    )
    query = query.limit(page_size + 1)
    fetched = list((await db.execute(query)).scalars().all())
    page_rows, next_cursor, has_more = slice_page_with_cursor(
        fetched,
        limit=page_size,
        cursor_from_row=lambda row: (row.settled_at, row.id),
    )
    return PlayerSettlementListResponse(
        items=[serialize_player_settlement(row) for row in page_rows],
        limit=page_size,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/player-settlements",
    response_model=PlayerSettlementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_player_settlement_endpoint(
    payload: PlayerSettlementCreate,
    admin_email: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PlayerSettlementResponse:
    try:
        async with db.begin():
            settlement = await create_player_settlement(
                db,
                sender_name=payload.sender_name,
                direction=PlayerSettlementDirection(payload.direction.value),
                amount_cents=payload.amount_cents,
                payment_account_id=payload.payment_account_id,
                reference=payload.reference,
                note=payload.note,
                settled_at=payload.settled_at,
                created_by_user_id=admin_email,
            )
            settlement_id = settlement.id
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    loaded = await db.scalar(
        select(PlayerSettlement)
        .options(selectinload(PlayerSettlement.payment_account))
        .where(PlayerSettlement.id == settlement_id)
    )
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Player settlement was not saved.",
        )
    return serialize_player_settlement(loaded)
