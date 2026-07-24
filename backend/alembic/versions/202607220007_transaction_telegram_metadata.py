"""add telegram delivery metadata to transactions

Revision ID: 202607220007
Revises: 202607220006
Create Date: 2026-07-22 00:07:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220007"
down_revision: str | None = "202607220006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("telegram_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transactions", sa.Column("telegram_last_error", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "telegram_last_error")
    op.drop_column("transactions", "telegram_sent_at")
