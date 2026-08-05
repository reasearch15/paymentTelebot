"""Data helpers for Phase 2 legacy Telegram multi-integration schema migration.

Used by Alembic and unit tests. Does not call the Telegram API and does not create
delivery rows or change transaction telegram_status values.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from app.models.telegram_integration import DEFAULT_TELEGRAM_INTEGRATION_NAME


def integration_display_name(*, row_index: int, total_count: int) -> str:
    """Deterministic display name for an existing telegram_integrations row.

    row_index is 1-based position when ordered by id ascending.
    """
    if total_count == 1:
        return DEFAULT_TELEGRAM_INTEGRATION_NAME
    return f"Telegram Integration {row_index}"


def backfill_telegram_integration_names(connection: Connection) -> None:
    """Set name on every telegram_integrations row. Leaves bot_username null."""
    rows = connection.execute(text("SELECT id FROM telegram_integrations ORDER BY id ASC")).fetchall()
    total = len(rows)
    for index, (integration_id,) in enumerate(rows, start=1):
        connection.execute(
            text("UPDATE telegram_integrations SET name = :name WHERE id = :id"),
            {"name": integration_display_name(row_index=index, total_count=total), "id": integration_id},
        )


def select_default_telegram_integration_id(connection: Connection) -> int | None:
    """Lowest-id telegram_integrations row, or None when the table is empty.

    Does not insert a placeholder integration: get_or_create_telegram_integration still
    creates an empty disabled row on first settings API access, matching prior behavior.
    """
    row = connection.execute(text("SELECT id FROM telegram_integrations ORDER BY id ASC LIMIT 1")).fetchone()
    return int(row[0]) if row is not None else None


def assign_payment_accounts_to_default_telegram_integration(connection: Connection) -> int:
    """Insert one route per payment account to the default (lowest-id) integration.

    Conflict-safe / idempotent: duplicate (account, integration) pairs are skipped.
    Returns the number of routes inserted on this invocation.
    """
    default_id = select_default_telegram_integration_id(connection)
    if default_id is None:
        return 0

    result = connection.execute(
        text(
            """
            INSERT INTO payment_account_telegram_routes (
                payment_account_id,
                telegram_integration_id
            )
            SELECT pa.id, :integration_id
            FROM payment_accounts AS pa
            WHERE NOT EXISTS (
                SELECT 1
                FROM payment_account_telegram_routes AS route
                WHERE route.payment_account_id = pa.id
                  AND route.telegram_integration_id = :integration_id
            )
            """
        ),
        {"integration_id": default_id},
    )
    return int(result.rowcount or 0)
