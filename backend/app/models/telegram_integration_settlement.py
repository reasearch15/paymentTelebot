from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.telegram_integration import TelegramIntegration


class TelegramIntegrationSettlement(Base):
    """Per-bot cash settlements. Independent from Gmail `settlements` and player settlements.

    Bot Ledger membership comes from telegram_deliveries, not current routes.
    A transaction routed to multiple bots counts once in each Bot Ledger and once globally.
    """

    __tablename__ = "telegram_integration_settlements"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="telegram_integration_settlement_amount_positive"),
        Index(
            "ix_telegram_integration_settlements_settled_at_id",
            "settled_at",
            "id",
        ),
        Index(
            "ix_telegram_integration_settlements_created_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_integration_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_integrations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    telegram_integration: Mapped["TelegramIntegration"] = relationship(
        back_populates="settlements",
    )


@event.listens_for(TelegramIntegrationSettlement, "before_delete")
def prevent_telegram_integration_settlement_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Bot settlements are append-only and must not be deleted.")
