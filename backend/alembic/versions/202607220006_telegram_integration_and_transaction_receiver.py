"""add telegram integration settings and nullable transaction receiver tags

Revision ID: 202607220006
Revises: 202607220005
Create Date: 2026-07-22 00:06:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220006"
down_revision: str | None = "202607220005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_integrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bot_token_encrypted", sa.String(length=2048), nullable=True),
        sa.Column("group_id", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_integrations")),
    )
    op.alter_column(
        "transactions",
        "receiver_tag",
        existing_type=sa.String(length=120),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "receiver_tag",
        existing_type=sa.String(length=120),
        nullable=False,
    )
    op.drop_table("telegram_integrations")
