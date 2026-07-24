"""add parser fields to payment emails

Revision ID: 202607220004
Revises: 202607220003
Create Date: 2026-07-22 00:04:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220004"
down_revision: str | None = "202607220003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_emails", sa.Column("parser_key", sa.String(length=100), nullable=True))
    op.add_column("payment_emails", sa.Column("parser_version", sa.String(length=40), nullable=True))
    op.add_column("payment_emails", sa.Column("parsed_payload_json", sa.JSON(), nullable=True))
    op.add_column("payment_emails", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_payment_emails_parser_key"), "payment_emails", ["parser_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_emails_parser_key"), table_name="payment_emails")
    op.drop_column("payment_emails", "parsed_at")
    op.drop_column("payment_emails", "parsed_payload_json")
    op.drop_column("payment_emails", "parser_version")
    op.drop_column("payment_emails", "parser_key")
