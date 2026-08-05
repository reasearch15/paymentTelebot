"""multi-telegram schema: names, routes, deliveries + legacy assignment

Revision ID: 202608050001
Revises: 202607250002
Create Date: 2026-08-05 00:01:00.000000

Phase 1: additive schema for many-to-many Telegram routing and per-destination deliveries.
Phase 2: name existing integrations and assign every payment account to the default
(lowest-id) integration. Does not create telegram_deliveries rows, does not call Telegram,
and does not change transactions.telegram_* columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.telegram_integration import DEFAULT_TELEGRAM_INTEGRATION_NAME
from app.services.telegram_legacy_migration import (
    assign_payment_accounts_to_default_telegram_integration,
    backfill_telegram_integration_names,
)

revision: str = "202608050001"
down_revision: str | None = "202607250002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_integrations",
        sa.Column("name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "telegram_integrations",
        sa.Column("bot_username", sa.String(length=120), nullable=True),
    )

    connection = op.get_bind()
    backfill_telegram_integration_names(connection)

    op.alter_column(
        "telegram_integrations",
        "name",
        existing_type=sa.String(length=120),
        nullable=False,
        server_default=DEFAULT_TELEGRAM_INTEGRATION_NAME,
    )

    op.create_table(
        "payment_account_telegram_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_account_id", sa.Integer(), nullable=False),
        sa.Column("telegram_integration_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_account_id"],
            ["payment_accounts.id"],
            name=op.f("fk_payment_account_telegram_routes_payment_account_id_payment_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_integration_id"],
            ["telegram_integrations.id"],
            name=op.f("fk_payment_account_telegram_routes_telegram_integration_id_telegram_integrations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_account_telegram_routes")),
        sa.UniqueConstraint(
            "payment_account_id",
            "telegram_integration_id",
            name="uq_payment_account_telegram_routes_account_integration",
        ),
    )
    op.create_index(
        op.f("ix_payment_account_telegram_routes_payment_account_id"),
        "payment_account_telegram_routes",
        ["payment_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_account_telegram_routes_telegram_integration_id"),
        "payment_account_telegram_routes",
        ["telegram_integration_id"],
        unique=False,
    )

    op.create_table(
        "telegram_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("telegram_integration_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
        sa.Column("telegram_message_id", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name=op.f("ck_telegram_deliveries_telegram_delivery_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_integration_id"],
            ["telegram_integrations.id"],
            name=op.f("fk_telegram_deliveries_telegram_integration_id_telegram_integrations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_telegram_deliveries_transaction_id_transactions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_deliveries")),
        sa.UniqueConstraint(
            "transaction_id",
            "telegram_integration_id",
            name="uq_telegram_deliveries_transaction_integration",
        ),
    )
    op.create_index(
        op.f("ix_telegram_deliveries_transaction_id"),
        "telegram_deliveries",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_deliveries_telegram_integration_id"),
        "telegram_deliveries",
        ["telegram_integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_deliveries_status_last_attempt_at",
        "telegram_deliveries",
        ["status", "last_attempt_at"],
        unique=False,
    )

    assign_payment_accounts_to_default_telegram_integration(connection)


def downgrade() -> None:
    op.drop_index("ix_telegram_deliveries_status_last_attempt_at", table_name="telegram_deliveries")
    op.drop_index(op.f("ix_telegram_deliveries_telegram_integration_id"), table_name="telegram_deliveries")
    op.drop_index(op.f("ix_telegram_deliveries_transaction_id"), table_name="telegram_deliveries")
    op.drop_table("telegram_deliveries")

    op.drop_index(
        op.f("ix_payment_account_telegram_routes_telegram_integration_id"),
        table_name="payment_account_telegram_routes",
    )
    op.drop_index(
        op.f("ix_payment_account_telegram_routes_payment_account_id"),
        table_name="payment_account_telegram_routes",
    )
    op.drop_table("payment_account_telegram_routes")

    op.drop_column("telegram_integrations", "bot_username")
    op.drop_column("telegram_integrations", "name")
