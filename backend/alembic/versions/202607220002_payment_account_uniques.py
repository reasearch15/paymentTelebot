"""add payment account unique constraints

Revision ID: 202607220002
Revises: 202607220001
Create Date: 2026-07-22 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202607220002"
down_revision: str | None = "202607220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_payment_accounts_gmail_address"), "payment_accounts", ["gmail_address"])
    op.create_unique_constraint(op.f("uq_payment_accounts_receiver_tag"), "payment_accounts", ["receiver_tag"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_payment_accounts_receiver_tag"), "payment_accounts", type_="unique")
    op.drop_constraint(op.f("uq_payment_accounts_gmail_address"), "payment_accounts", type_="unique")
