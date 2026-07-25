"""Cursor pagination helpers for newest-first ledger and settlement lists."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, or_

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 200

T = TypeVar("T")


def clamp_page_size(limit: int | None, *, default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE) -> int:
    """Cap client page size to a safe maximum. Oversized values are clamped, not rejected."""
    if limit is None:
        return default
    if limit < 1:
        return default
    return min(limit, maximum)


def encode_time_id_cursor(moment: datetime, record_id: int) -> str:
    payload = {"t": moment.isoformat(), "id": record_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_time_id_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        moment = datetime.fromisoformat(str(payload["t"]))
        record_id = int(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid pagination cursor.") from exc
    if record_id < 1:
        raise ValueError("Invalid pagination cursor.")
    return moment, record_id


def require_time_id_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if cursor is None or cursor.strip() == "":
        return None
    try:
        return decode_time_id_cursor(cursor.strip())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def apply_newest_first_keyset(
    query: Select,
    *,
    time_column,
    id_column,
    cursor: tuple[datetime, int] | None,
) -> Select:
    """
    Keyset filter for ORDER BY time DESC, id DESC.

    Returns rows strictly older than the cursor (or same timestamp with smaller id).
    """
    if cursor is None:
        return query
    cursor_time, cursor_id = cursor
    return query.where(
        or_(
            time_column < cursor_time,
            and_(time_column == cursor_time, id_column < cursor_id),
        )
    )


def slice_page_with_cursor(
    rows: list[T],
    *,
    limit: int,
    cursor_from_row,
) -> tuple[list[T], str | None, bool]:
    """
    Given limit+1 fetched rows, return the page, next_cursor, and has_more.

    cursor_from_row(row) -> (datetime, id)
    """
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor: str | None = None
    if has_more and page:
        moment, record_id = cursor_from_row(page[-1])
        next_cursor = encode_time_id_cursor(moment, record_id)
    return page, next_cursor, has_more
