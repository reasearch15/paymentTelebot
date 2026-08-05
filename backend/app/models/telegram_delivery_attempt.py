from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TelegramDeliveryAttempt(Base):
    """Immutable per-attempt history for a telegram_deliveries row.

    Each claim/send cycle appends a row. The parent delivery row still holds current status.
    """

    __tablename__ = "telegram_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "telegram_delivery_id",
            "attempt_number",
            name="uq_telegram_delivery_attempts_delivery_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_delivery_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_deliveries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    delivery: Mapped["TelegramDelivery"] = relationship(back_populates="attempts")
