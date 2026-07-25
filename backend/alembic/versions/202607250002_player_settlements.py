"""add player_settlements table

Revision ID: 202607250002
Revises: 202607250001
Create Date: 2026-07-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607250002"
down_revision: str | None = "202607250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sender_name", sa.String(length=255), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("PAID_TO_PLAYER", "RECEIVED_FROM_PLAYER", name="player_settlement_direction"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_account_id",
            sa.Integer(),
            sa.ForeignKey("payment_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_player_settlements_sender_name", "player_settlements", ["sender_name"])
    op.create_index("ix_player_settlements_payment_account_id", "player_settlements", ["payment_account_id"])
    op.create_index(
        "ix_player_settlements_settled_at_id",
        "player_settlements",
        ["settled_at", "id"],
    )
    op.create_index(
        "ix_player_settlements_direction",
        "player_settlements",
        ["direction"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_settlements_direction", table_name="player_settlements")
    op.drop_index("ix_player_settlements_settled_at_id", table_name="player_settlements")
    op.drop_index("ix_player_settlements_payment_account_id", table_name="player_settlements")
    op.drop_index("ix_player_settlements_sender_name", table_name="player_settlements")
    op.drop_table("player_settlements")
    op.execute("DROP TYPE IF EXISTS player_settlement_direction")
