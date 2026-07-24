"""add telegram_attempted_at for stale sending claim recovery

Revision ID: 202607240001
Revises: 202607220007
Create Date: 2026-07-24 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607240001"
down_revision: str | None = "202607220007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("telegram_attempted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "telegram_attempted_at")
