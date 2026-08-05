from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

TELEGRAM_DELIVERY_STATUSES = ("pending", "sending", "sent", "failed")


class TelegramDelivery(Base):
    """Per-(transaction, telegram_integration) delivery state for multi-destination sends.

    Unique pair prevents duplicate sends to the same integration. FK RESTRICT preserves
    delivery history if an integration is removed.
    """

    __tablename__ = "telegram_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "telegram_integration_id",
            name="uq_telegram_deliveries_transaction_integration",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name="telegram_delivery_status_allowed",
        ),
        Index("ix_telegram_deliveries_status_last_attempt_at", "status", "last_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    telegram_integration_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_integrations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", server_default="pending")
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="telegram_deliveries")
    telegram_integration: Mapped["TelegramIntegration"] = relationship(back_populates="deliveries")
