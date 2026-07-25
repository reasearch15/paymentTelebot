"""Focused tests for cursor-based Ledger and Settlements pagination."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    clamp_page_size,
    decode_time_id_cursor,
    encode_time_id_cursor,
    require_time_id_cursor,
    slice_page_with_cursor,
)


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 25, 12, minute, second, tzinfo=UTC)


def test_default_page_size_is_thirty() -> None:
    assert DEFAULT_PAGE_SIZE == 30
    assert clamp_page_size(None) == 30


def test_page_size_above_maximum_is_capped() -> None:
    assert clamp_page_size(10_000) == MAX_PAGE_SIZE
    assert MAX_PAGE_SIZE == 200


def test_cursor_round_trip() -> None:
    moment = _ts(1, 5)
    cursor = encode_time_id_cursor(moment, 42)
    decoded_moment, decoded_id = decode_time_id_cursor(cursor)
    assert decoded_moment == moment
    assert decoded_id == 42


def test_invalid_cursor_raises_http_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_time_id_cursor("not-a-valid-cursor!!!")
    assert exc_info.value.status_code == 400
    assert "Invalid pagination cursor" in str(exc_info.value.detail)


def test_first_page_returns_at_most_limit_and_has_more() -> None:
    rows = [SimpleNamespace(id=i, received_at=_ts(0) - timedelta(seconds=i)) for i in range(1, 36)]
    # Simulate ORDER BY received_at DESC, id DESC with descending ids for uniqueness.
    ordered = sorted(rows, key=lambda row: (row.received_at, row.id), reverse=True)
    fetched = ordered[: 30 + 1]
    page, next_cursor, has_more = slice_page_with_cursor(
        fetched,
        limit=30,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )
    assert len(page) == 30
    assert has_more is True
    assert next_cursor is not None
    cursor_time, cursor_id = decode_time_id_cursor(next_cursor)
    assert (cursor_time, cursor_id) == (page[-1].received_at, page[-1].id)


def test_next_page_has_no_overlap_with_first_page() -> None:
    rows = [SimpleNamespace(id=i, received_at=_ts(0) - timedelta(seconds=i)) for i in range(1, 61)]
    ordered = sorted(rows, key=lambda row: (row.received_at, row.id), reverse=True)

    first_fetch = ordered[:31]
    first_page, cursor, has_more = slice_page_with_cursor(
        first_fetch,
        limit=30,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )
    assert has_more is True
    assert cursor is not None
    cursor_time, cursor_id = decode_time_id_cursor(cursor)

    # Keyset: strictly older than cursor.
    remaining = [
        row
        for row in ordered
        if row.received_at < cursor_time or (row.received_at == cursor_time and row.id < cursor_id)
    ]
    second_fetch = remaining[:31]
    second_page, _, second_has_more = slice_page_with_cursor(
        second_fetch,
        limit=30,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )

    first_ids = {row.id for row in first_page}
    second_ids = {row.id for row in second_page}
    assert first_ids.isdisjoint(second_ids)
    assert len(first_page) + len(second_page) == 60
    assert second_has_more is False


def test_identical_timestamps_do_not_skip_or_duplicate() -> None:
    same_time = _ts(10)
    # Three rows share the exact same timestamp; id breaks ties newest-first.
    ordered = [
        SimpleNamespace(id=30, received_at=same_time),
        SimpleNamespace(id=20, received_at=same_time),
        SimpleNamespace(id=10, received_at=same_time),
        SimpleNamespace(id=5, received_at=_ts(9)),
    ]

    first_page, cursor, has_more = slice_page_with_cursor(
        ordered[: 2 + 1],
        limit=2,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )
    assert [row.id for row in first_page] == [30, 20]
    assert has_more is True
    cursor_time, cursor_id = decode_time_id_cursor(cursor)

    remaining = [
        row
        for row in ordered
        if row.received_at < cursor_time or (row.received_at == cursor_time and row.id < cursor_id)
    ]
    second_page, _, second_has_more = slice_page_with_cursor(
        remaining[:3],
        limit=2,
        cursor_from_row=lambda row: (row.received_at, row.id),
    )
    assert [row.id for row in second_page] == [10, 5]
    assert {row.id for row in first_page}.isdisjoint({row.id for row in second_page})
    assert second_has_more is False


def test_final_page_has_more_false() -> None:
    rows = [SimpleNamespace(id=i, settled_at=_ts(0) - timedelta(seconds=i)) for i in range(1, 11)]
    ordered = sorted(rows, key=lambda row: (row.settled_at, row.id), reverse=True)
    page, next_cursor, has_more = slice_page_with_cursor(
        ordered[:30],
        limit=30,
        cursor_from_row=lambda row: (row.settled_at, row.id),
    )
    assert len(page) == 10
    assert has_more is False
    assert next_cursor is None


def test_settlement_pagination_no_duplicates_or_skips() -> None:
    rows = [SimpleNamespace(id=i, settled_at=_ts(0) - timedelta(minutes=i)) for i in range(1, 46)]
    ordered = sorted(rows, key=lambda row: (row.settled_at, row.id), reverse=True)

    seen: list[int] = []
    cursor = None
    while True:
        if cursor is None:
            window = ordered
        else:
            cursor_time, cursor_id = decode_time_id_cursor(cursor)
            window = [
                row
                for row in ordered
                if row.settled_at < cursor_time or (row.settled_at == cursor_time and row.id < cursor_id)
            ]
        page, cursor, has_more = slice_page_with_cursor(
            window[:31],
            limit=30,
            cursor_from_row=lambda row: (row.settled_at, row.id),
        )
        page_ids = [row.id for row in page]
        assert len(page_ids) == len(set(page_ids))
        assert set(page_ids).isdisjoint(seen)
        seen.extend(page_ids)
        if not has_more:
            break

    assert seen == [row.id for row in ordered]


def test_ledger_totals_schema_covers_full_history_not_page() -> None:
    """Summary cards use LedgerTotals computed independently of the page window."""
    from app.schemas.transaction import LedgerListResponse, LedgerTotals

    totals = LedgerTotals(
        total_incoming_cents=500_00,
        total_outgoing_cents=100_00,
        total_settled_cents=50_00,
        unsettled_balance_cents=350_00,
        total_transactions=120,
    )
    response = LedgerListResponse(
        transactions=[],
        totals=totals,
        account_balances=[],
        limit=30,
        next_cursor=None,
        has_more=False,
    )
    # Page may be empty/short while totals still reflect complete history.
    assert response.totals.total_transactions == 120
    assert response.totals.unsettled_balance_cents == 350_00
    assert len(response.transactions) == 0


def test_settlement_list_response_shape() -> None:
    from app.schemas.settlement import SettlementListResponse

    payload = SettlementListResponse(items=[], limit=30, next_cursor=None, has_more=False)
    assert payload.limit == 30
    assert payload.has_more is False
    assert payload.next_cursor is None
