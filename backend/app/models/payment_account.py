from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class PaymentAccount(TimestampMixin, Base):
    __tablename__ = "payment_accounts"
    __table_args__ = (
        CheckConstraint("receiver_tag LIKE '$%'", name="receiver_tag_starts_with_dollar"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True)
    friendly_name: Mapped[str] = mapped_column(String(120), nullable=False)
    receiver_tag: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    gmail_address: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    encrypted_app_password: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    listener_status: Mapped[str] = mapped_column(String(40), nullable=False, default="idle", server_default="idle")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    provider: Mapped["Provider"] = relationship(back_populates="payment_accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="payment_account", passive_deletes=True)
    settlements: Mapped[list["Settlement"]] = relationship(back_populates="payment_account", passive_deletes=True)
    payment_emails: Mapped[list["PaymentEmail"]] = relationship(back_populates="payment_account", passive_deletes=True)
