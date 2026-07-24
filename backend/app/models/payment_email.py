import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProcessingStatus(str, enum.Enum):
    CAPTURED = "captured"
    IGNORED = "ignored"
    PENDING_PARSE = "pending_parse"
    PARSED = "parsed"
    FAILED = "failed"


class PaymentEmail(Base):
    __tablename__ = "payment_emails"
    __table_args__ = (
        UniqueConstraint("gmail_message_id", name="uq_payment_emails_gmail_message_id"),
        UniqueConstraint("payment_account_id", "mailbox", "gmail_uid", name="uq_payment_emails_account_mailbox_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_account_id: Mapped[int] = mapped_column(
        ForeignKey("payment_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    gmail_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gmail_uid: Mapped[int] = mapped_column(nullable=False)
    mailbox: Mapped[str] = mapped_column(String(120), nullable=False, default="INBOX", server_default="INBOX")
    sender_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parser_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parsed_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(
            ProcessingStatus,
            name="payment_email_processing_status",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=ProcessingStatus.CAPTURED,
        server_default=ProcessingStatus.CAPTURED.value,
        index=True,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    payment_account: Mapped["PaymentAccount"] = relationship(back_populates="payment_emails")


@event.listens_for(PaymentEmail, "before_delete")
def prevent_payment_email_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Payment emails are append-only and must not be deleted.")
