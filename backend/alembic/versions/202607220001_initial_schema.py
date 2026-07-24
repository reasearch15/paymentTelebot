"""initial schema

Revision ID: 202607220001
Revises:
Create Date: 2026-07-22 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("encrypted_value", sa.String(length=2048), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_settings")),
        sa.UniqueConstraint("key", name=op.f("uq_app_settings_key")),
    )
    op.create_index(op.f("ix_app_settings_key"), "app_settings", ["key"], unique=False)

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("parser_key", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("parser_key", name=op.f("uq_providers_parser_key")),
    )
    op.create_index(op.f("ix_providers_parser_key"), "providers", ["parser_key"], unique=False)

    op.create_table(
        "payment_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("friendly_name", sa.String(length=120), nullable=False),
        sa.Column("receiver_tag", sa.String(length=120), nullable=False),
        sa.Column("gmail_address", sa.String(length=255), nullable=False),
        sa.Column("encrypted_app_password", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("listener_status", sa.String(length=40), server_default="idle", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_email_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("receiver_tag LIKE '$%'", name=op.f("ck_payment_accounts_receiver_tag_starts_with_dollar")),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], name=op.f("fk_payment_accounts_provider_id_providers"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_accounts")),
    )
    op.create_index(op.f("ix_payment_accounts_provider_id"), "payment_accounts", ["provider_id"], unique=False)
    op.create_index(op.f("ix_payment_accounts_receiver_tag"), "payment_accounts", ["receiver_tag"], unique=False)

    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_account_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_before_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_cents", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["payment_account_id"], ["payment_accounts.id"], name=op.f("fk_settlements_payment_account_id_payment_accounts"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlements")),
    )
    op.create_index(op.f("ix_settlements_payment_account_id"), "settlements", ["payment_account_id"], unique=False)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_account_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.Enum("IN", "OUT", name="transaction_direction"), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("sender_payment_tag", sa.String(length=120), nullable=True),
        sa.Column("receiver_tag", sa.String(length=120), nullable=False),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_status", sa.String(length=40), server_default="pending", nullable=False),
        sa.Column("raw_subject", sa.String(length=500), nullable=True),
        sa.Column("raw_payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sender_payment_tag IS NULL OR sender_payment_tag LIKE '$%'", name=op.f("ck_transactions_sender_payment_tag_null_or_starts_with_dollar")),
        sa.ForeignKeyConstraint(["payment_account_id"], ["payment_accounts.id"], name=op.f("fk_transactions_payment_account_id_payment_accounts"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
        sa.UniqueConstraint("gmail_message_id", name=op.f("uq_transactions_gmail_message_id")),
    )
    op.create_index(op.f("ix_transactions_gmail_message_id"), "transactions", ["gmail_message_id"], unique=False)
    op.create_index(op.f("ix_transactions_payment_account_id"), "transactions", ["payment_account_id"], unique=False)
    op.create_index(op.f("ix_transactions_received_at"), "transactions", ["received_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_received_at"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_payment_account_id"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_gmail_message_id"), table_name="transactions")
    op.drop_table("transactions")
    op.drop_index(op.f("ix_settlements_payment_account_id"), table_name="settlements")
    op.drop_table("settlements")
    op.drop_index(op.f("ix_payment_accounts_receiver_tag"), table_name="payment_accounts")
    op.drop_index(op.f("ix_payment_accounts_provider_id"), table_name="payment_accounts")
    op.drop_table("payment_accounts")
    op.drop_index(op.f("ix_providers_parser_key"), table_name="providers")
    op.drop_table("providers")
    op.drop_index(op.f("ix_app_settings_key"), table_name="app_settings")
    op.drop_table("app_settings")
    op.execute("DROP TYPE IF EXISTS transaction_direction")
