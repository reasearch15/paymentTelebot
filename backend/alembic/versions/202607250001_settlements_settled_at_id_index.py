"""add settlements settled_at+id index for cursor pagination

Revision ID: 202607250001
Revises: 202607240001
Create Date: 2026-07-25 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202607250001"
down_revision: str | None = "202607240001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_settlements_settled_at_id",
        "settlements",
        ["settled_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_settlements_settled_at_id", table_name="settlements")
