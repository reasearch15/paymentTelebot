"""allow payment accounts without receiver tags

Revision ID: 202607220005
Revises: 202607220004
Create Date: 2026-07-22 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220005"
down_revision: str | None = "202607220004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE payment_accounts SET receiver_tag = NULL WHERE btrim(receiver_tag) = ''")
    op.alter_column(
        "payment_accounts",
        "receiver_tag",
        existing_type=sa.String(length=120),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "payment_accounts",
        "receiver_tag",
        existing_type=sa.String(length=120),
        nullable=False,
    )
