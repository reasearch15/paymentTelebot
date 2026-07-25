from datetime import UTC, datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.payment_account import PaymentAccount
from app.models.player_settlement import PlayerSettlement, PlayerSettlementDirection
from app.models.transaction import Direction, Transaction
from app.parsers.extraction import normalize_whitespace


def normalize_sender_identity(value: str | None) -> str:
    """Exact ledger sender identity: trim/collapse whitespace only (no fuzzy merge)."""
    return normalize_whitespace(value)


def compute_player_unsettled_balance_cents(
    *,
    total_in_cents: int,
    total_out_cents: int,
    settlements_paid_cents: int,
    settlements_received_cents: int,
) -> int:
    """Player unsettled = IN − OUT − paid_to_player + received_from_player.

    Direction of transactions only; telegram/status never participates.
    """
    return (
        int(total_in_cents)
        - int(total_out_cents)
        - int(settlements_paid_cents)
        + int(settlements_received_cents)
    )


async def list_distinct_sender_names(
    session: AsyncSession,
    *,
    search: str | None = None,
) -> list[str]:
    """Return exact normalized sender names known from transactions or player settlements."""
    search_norm = normalize_sender_identity(search) if search else ""

    tx_query = (
        select(Transaction.sender_name)
        .where(Transaction.sender_name.is_not(None))
        .where(func.btrim(Transaction.sender_name) != "")
        .distinct()
    )
    ps_query = select(PlayerSettlement.sender_name).distinct()
    if search_norm:
        pattern = f"%{search_norm}%"
        tx_query = tx_query.where(Transaction.sender_name.ilike(pattern))
        ps_query = ps_query.where(PlayerSettlement.sender_name.ilike(pattern))

    tx_names = [normalize_sender_identity(row[0]) for row in (await session.execute(tx_query)).all()]
    ps_names = [normalize_sender_identity(row[0]) for row in (await session.execute(ps_query)).all()]
    names = sorted({name for name in [*tx_names, *ps_names] if name})
    return names


async def _transaction_aggregates_by_sender(
    session: AsyncSession,
    *,
    search: str | None = None,
) -> dict[str, dict]:
    # Group by exact normalized identity (trim + collapse whitespace in SQL).
    sender_key = func.regexp_replace(func.btrim(Transaction.sender_name), r"\s+", " ", "g")
    query = (
        select(
            sender_key.label("sender_name"),
            func.coalesce(
                func.sum(case((Transaction.direction == Direction.IN, Transaction.amount_cents), else_=0)),
                0,
            ).label("total_in_cents"),
            func.coalesce(
                func.sum(case((Transaction.direction == Direction.OUT, Transaction.amount_cents), else_=0)),
                0,
            ).label("total_out_cents"),
            func.coalesce(
                func.sum(case((Transaction.direction == Direction.IN, 1), else_=0)),
                0,
            ).label("in_count"),
            func.coalesce(
                func.sum(case((Transaction.direction == Direction.OUT, 1), else_=0)),
                0,
            ).label("out_count"),
            func.min(Transaction.received_at).label("first_transaction_at"),
            func.max(Transaction.received_at).label("latest_transaction_at"),
        )
        .where(Transaction.sender_name.is_not(None))
        .where(func.btrim(Transaction.sender_name) != "")
        .group_by(sender_key)
    )
    search_norm = normalize_sender_identity(search) if search else ""
    if search_norm:
        query = query.where(sender_key.ilike(f"%{search_norm}%"))

    rows = (await session.execute(query)).all()
    result: dict[str, dict] = {}
    for row in rows:
        name = normalize_sender_identity(row.sender_name)
        if not name:
            continue
        result[name] = {
            "sender_name": name,
            "total_in_cents": int(row.total_in_cents),
            "total_out_cents": int(row.total_out_cents),
            "in_count": int(row.in_count),
            "out_count": int(row.out_count),
            "first_transaction_at": row.first_transaction_at,
            "latest_transaction_at": row.latest_transaction_at,
        }
    return result


async def _settlement_aggregates_by_sender(
    session: AsyncSession,
    *,
    search: str | None = None,
) -> dict[str, dict]:
    sender_key = func.regexp_replace(func.btrim(PlayerSettlement.sender_name), r"\s+", " ", "g")
    query = (
        select(
            sender_key.label("sender_name"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            PlayerSettlement.direction == PlayerSettlementDirection.PAID_TO_PLAYER,
                            PlayerSettlement.amount_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("settlements_paid_cents"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            PlayerSettlement.direction == PlayerSettlementDirection.RECEIVED_FROM_PLAYER,
                            PlayerSettlement.amount_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("settlements_received_cents"),
            func.max(PlayerSettlement.settled_at).label("latest_settlement_at"),
        )
        .group_by(sender_key)
    )
    search_norm = normalize_sender_identity(search) if search else ""
    if search_norm:
        query = query.where(sender_key.ilike(f"%{search_norm}%"))

    rows = (await session.execute(query)).all()
    result: dict[str, dict] = {}
    for row in rows:
        name = normalize_sender_identity(row.sender_name)
        if not name:
            continue
        result[name] = {
            "settlements_paid_cents": int(row.settlements_paid_cents),
            "settlements_received_cents": int(row.settlements_received_cents),
            "latest_settlement_at": row.latest_settlement_at,
        }
    return result


async def list_player_ledger_rows(
    session: AsyncSession,
    *,
    search: str | None = None,
) -> list[dict]:
    tx_by_sender = await _transaction_aggregates_by_sender(session, search=search)
    ps_by_sender = await _settlement_aggregates_by_sender(session, search=search)
    sender_names = sorted(set(tx_by_sender) | set(ps_by_sender))

    rows: list[dict] = []
    for name in sender_names:
        tx = tx_by_sender.get(name, {})
        ps = ps_by_sender.get(name, {})
        total_in = int(tx.get("total_in_cents", 0))
        total_out = int(tx.get("total_out_cents", 0))
        paid = int(ps.get("settlements_paid_cents", 0))
        received = int(ps.get("settlements_received_cents", 0))
        latest_tx = tx.get("latest_transaction_at")
        latest_ps = ps.get("latest_settlement_at")
        latest_candidates = [value for value in (latest_tx, latest_ps) if value is not None]
        latest_at = max(latest_candidates) if latest_candidates else None
        rows.append(
            {
                "sender_name": name,
                "total_in_cents": total_in,
                "total_out_cents": total_out,
                "settlements_paid_cents": paid,
                "settlements_received_cents": received,
                "unsettled_balance_cents": compute_player_unsettled_balance_cents(
                    total_in_cents=total_in,
                    total_out_cents=total_out,
                    settlements_paid_cents=paid,
                    settlements_received_cents=received,
                ),
                "in_count": int(tx.get("in_count", 0)),
                "out_count": int(tx.get("out_count", 0)),
                "first_transaction_at": tx.get("first_transaction_at"),
                "latest_transaction_at": latest_tx,
                "latest_activity_at": latest_at,
            }
        )

    rows.sort(
        key=lambda row: (
            row["latest_activity_at"] is not None,
            row["latest_activity_at"] or datetime.min.replace(tzinfo=UTC),
            row["sender_name"],
        ),
        reverse=True,
    )
    return rows


async def get_player_ledger_detail(session: AsyncSession, *, sender_name: str) -> dict | None:
    identity = normalize_sender_identity(sender_name)
    if not identity:
        return None

    rows = await list_player_ledger_rows(session, search=None)
    summary = next((row for row in rows if row["sender_name"] == identity), None)
    if summary is None:
        # Exact identity miss — do not fuzzy-match.
        return None

    sender_key = func.regexp_replace(func.btrim(Transaction.sender_name), r"\s+", " ", "g")
    tx_result = await session.execute(
        select(Transaction)
        .options(selectinload(Transaction.payment_account))
        .where(sender_key == identity)
        .order_by(Transaction.received_at.desc(), Transaction.id.desc())
    )
    transactions = list(tx_result.scalars().all())

    ps_key = func.regexp_replace(func.btrim(PlayerSettlement.sender_name), r"\s+", " ", "g")
    ps_result = await session.execute(
        select(PlayerSettlement)
        .options(selectinload(PlayerSettlement.payment_account))
        .where(ps_key == identity)
        .order_by(PlayerSettlement.settled_at.desc(), PlayerSettlement.id.desc())
    )
    settlements = list(ps_result.scalars().all())

    return {
        "summary": summary,
        "transactions": transactions,
        "settlements": settlements,
    }


async def get_player_unsettled_balance_cents(session: AsyncSession, *, sender_name: str) -> int:
    identity = normalize_sender_identity(sender_name)
    if not identity:
        return 0
    rows = await list_player_ledger_rows(session, search=None)
    summary = next((row for row in rows if row["sender_name"] == identity), None)
    return int(summary["unsettled_balance_cents"]) if summary else 0


async def create_player_settlement(
    session: AsyncSession,
    *,
    sender_name: str,
    direction: PlayerSettlementDirection,
    amount_cents: int,
    payment_account_id: int,
    reference: str | None,
    note: str | None,
    settled_at: datetime | None,
    created_by_user_id: str,
) -> PlayerSettlement:
    identity = normalize_sender_identity(sender_name)
    if not identity:
        raise ValueError("Player/sender name is required.")
    if amount_cents <= 0:
        raise ValueError("Settlement amount must be greater than zero.")
    created_by = normalize_whitespace(created_by_user_id)
    if not created_by:
        raise ValueError("Created by is required.")

    account = await session.scalar(
        select(PaymentAccount).where(PaymentAccount.id == payment_account_id).with_for_update()
    )
    if account is None:
        raise LookupError("Payment account not found.")

    unsettled = await get_player_unsettled_balance_cents(session, sender_name=identity)
    if direction == PlayerSettlementDirection.PAID_TO_PLAYER:
        if unsettled <= 0:
            raise ValueError("Player has no positive unsettled balance to pay.")
        if amount_cents > unsettled:
            raise ValueError(
                f"Settlement amount exceeds unsettled balance of ${unsettled / 100:,.2f}."
            )

    settlement = PlayerSettlement(
        sender_name=identity,
        direction=direction,
        amount_cents=amount_cents,
        payment_account_id=payment_account_id,
        reference=normalize_whitespace(reference) or None,
        note=normalize_whitespace(note) or None,
        settled_at=settled_at or datetime.now(UTC),
        created_by_user_id=created_by,
    )
    session.add(settlement)
    await session.flush()
    return settlement


async def list_player_settlements(
    session: AsyncSession,
    *,
    sender_name: str | None = None,
    direction: PlayerSettlementDirection | None = None,
    settled_from: datetime | None = None,
    settled_to: datetime | None = None,
):
    query = (
        select(PlayerSettlement)
        .options(selectinload(PlayerSettlement.payment_account))
        .order_by(PlayerSettlement.settled_at.desc(), PlayerSettlement.id.desc())
    )
    if sender_name:
        identity = normalize_sender_identity(sender_name)
        if identity:
            # Exact identity match when a full sender is provided; otherwise substring search.
            sender_key = func.regexp_replace(func.btrim(PlayerSettlement.sender_name), r"\s+", " ", "g")
            query = query.where(
                or_(
                    sender_key == identity,
                    PlayerSettlement.sender_name.ilike(f"%{identity}%"),
                )
            )
    if direction is not None:
        query = query.where(PlayerSettlement.direction == direction)
    if settled_from is not None:
        query = query.where(PlayerSettlement.settled_at >= settled_from)
    if settled_to is not None:
        query = query.where(PlayerSettlement.settled_at <= settled_to)
    result = await session.execute(query)
    return list(result.scalars().all())
