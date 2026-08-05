"""add telegram_integration_settlements for Bot Ledger

Revision ID: 202608050003
Revises: 202608050002
Create Date: 2026-08-05 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608050003"
down_revision: str | None = "202608050002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_integration_settlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_integration_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_before_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_cents", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "amount_cents > 0",
            name="telegram_integration_settlement_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_integration_id"],
            ["telegram_integrations.id"],
            name=op.f("fk_telegram_integration_settlements_telegram_integration_id_telegram_integrations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_integration_settlements")),
    )
    op.create_index(
        op.f("ix_telegram_integration_settlements_telegram_integration_id"),
        "telegram_integration_settlements",
        ["telegram_integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_integration_settlements_settled_at_id",
        "telegram_integration_settlements",
        ["settled_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_integration_settlements_created_at",
        "telegram_integration_settlements",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_integration_settlements_created_at",
        table_name="telegram_integration_settlements",
    )
    op.drop_index(
        "ix_telegram_integration_settlements_settled_at_id",
        table_name="telegram_integration_settlements",
    )
    op.drop_index(
        op.f("ix_telegram_integration_settlements_telegram_integration_id"),
        table_name="telegram_integration_settlements",
    )
    op.drop_table("telegram_integration_settlements")
