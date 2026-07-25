import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PlayerSettlementDirection(str, enum.Enum):
    PAID_TO_PLAYER = "PAID_TO_PLAYER"
    RECEIVED_FROM_PLAYER = "RECEIVED_FROM_PLAYER"


class PlayerSettlement(Base):
    """Sender-level settlements. Independent from account-level `settlements`.

    Global unsettled balance still uses only `settlements` (account cash).
    Player unsettled balance uses only `player_settlements` so the same cash
    is never subtracted twice.
    """

    __tablename__ = "player_settlements"
    __table_args__ = (Index("ix_player_settlements_settled_at_id", "settled_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    direction: Mapped[PlayerSettlementDirection] = mapped_column(
        Enum(PlayerSettlementDirection, name="player_settlement_direction"),
        nullable=False,
        index=True,
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_account_id: Mapped[int] = mapped_column(
        ForeignKey("payment_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    payment_account: Mapped["PaymentAccount"] = relationship()


@event.listens_for(PlayerSettlement, "before_delete")
def prevent_player_settlement_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Player settlements are append-only and must not be deleted.")
