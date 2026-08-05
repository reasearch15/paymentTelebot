from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PaymentAccountTelegramRoute(Base):
    """Many-to-many assignment of a Gmail payment account to a Telegram integration.

    Presence of a row means the account is assigned. There is no per-route enabled flag in v1.
    Foreign keys use RESTRICT so integrations (and accounts) with routes cannot be casually
    hard-deleted while history or assignments remain.
    """

    __tablename__ = "payment_account_telegram_routes"
    __table_args__ = (
        UniqueConstraint(
            "payment_account_id",
            "telegram_integration_id",
            name="uq_payment_account_telegram_routes_account_integration",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_account_id: Mapped[int] = mapped_column(
        ForeignKey("payment_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    telegram_integration_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_integrations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    payment_account: Mapped["PaymentAccount"] = relationship(back_populates="telegram_routes")
    telegram_integration: Mapped["TelegramIntegration"] = relationship(back_populates="account_routes")
