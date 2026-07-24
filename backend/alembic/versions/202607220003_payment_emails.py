"""add payment emails

Revision ID: 202607220003
Revises: 202607220002
Create Date: 2026-07-22 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220003"
down_revision: str | None = "202607220002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_account_id", sa.Integer(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=500), nullable=True),
        sa.Column("gmail_uid", sa.Integer(), nullable=False),
        sa.Column("mailbox", sa.String(length=120), server_default="INBOX", nullable=False),
        sa.Column("sender_address", sa.String(length=500), nullable=True),
        sa.Column("subject", sa.String(length=1000), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("raw_headers_json", sa.JSON(), nullable=True),
        sa.Column(
            "processing_status",
            sa.Enum("captured", "ignored", "pending_parse", "parsed", "failed", name="payment_email_processing_status"),
            server_default="captured",
            nullable=False,
        ),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["payment_account_id"], ["payment_accounts.id"], name=op.f("fk_payment_emails_payment_account_id_payment_accounts"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_emails")),
        sa.UniqueConstraint("gmail_message_id", name="uq_payment_emails_gmail_message_id"),
        sa.UniqueConstraint("payment_account_id", "mailbox", "gmail_uid", name="uq_payment_emails_account_mailbox_uid"),
    )
    op.create_index(op.f("ix_payment_emails_payment_account_id"), "payment_emails", ["payment_account_id"], unique=False)
    op.create_index(op.f("ix_payment_emails_processing_status"), "payment_emails", ["processing_status"], unique=False)
    op.create_index(op.f("ix_payment_emails_received_at"), "payment_emails", ["received_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_emails_received_at"), table_name="payment_emails")
    op.drop_index(op.f("ix_payment_emails_processing_status"), table_name="payment_emails")
    op.drop_index(op.f("ix_payment_emails_payment_account_id"), table_name="payment_emails")
    op.drop_table("payment_emails")
    op.execute("DROP TYPE IF EXISTS payment_email_processing_status")
