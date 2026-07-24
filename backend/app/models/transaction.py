import enum
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, JSON, String, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Direction(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "sender_payment_tag IS NULL OR sender_payment_tag LIKE '$%'",
            name="sender_payment_tag_null_or_starts_with_dollar",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_account_id: Mapped[int] = mapped_column(
        ForeignKey("payment_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="transaction_direction"), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_payment_tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    receiver_tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    telegram_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", server_default="pending")
    telegram_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    payment_account: Mapped["PaymentAccount"] = relationship(back_populates="transactions")


@event.listens_for(Transaction, "before_delete")
def prevent_transaction_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Transactions are append-only and must not be deleted.")
