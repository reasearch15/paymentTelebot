"""add telegram_delivery_attempts for retry history

Revision ID: 202608050002
Revises: 202608050001
Create Date: 2026-08-05 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608050002"
down_revision: str | None = "202608050001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_delivery_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_delivery_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_delivery_id"],
            ["telegram_deliveries.id"],
            name=op.f("fk_telegram_delivery_attempts_telegram_delivery_id_telegram_deliveries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_delivery_attempts")),
        sa.UniqueConstraint(
            "telegram_delivery_id",
            "attempt_number",
            name="uq_telegram_delivery_attempts_delivery_attempt",
        ),
    )
    op.create_index(
        op.f("ix_telegram_delivery_attempts_telegram_delivery_id"),
        "telegram_delivery_attempts",
        ["telegram_delivery_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_telegram_delivery_attempts_telegram_delivery_id"),
        table_name="telegram_delivery_attempts",
    )
    op.drop_table("telegram_delivery_attempts")
