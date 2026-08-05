from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DEFAULT_TELEGRAM_INTEGRATION_NAME = "Default Telegram Integration"


class TelegramIntegration(Base):
    __tablename__ = "telegram_integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default=DEFAULT_TELEGRAM_INTEGRATION_NAME,
        server_default=DEFAULT_TELEGRAM_INTEGRATION_NAME,
    )
    bot_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bot_token_encrypted: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account_routes: Mapped[list["PaymentAccountTelegramRoute"]] = relationship(
        back_populates="telegram_integration",
        passive_deletes=True,
    )
    deliveries: Mapped[list["TelegramDelivery"]] = relationship(
        back_populates="telegram_integration",
        passive_deletes=True,
    )
