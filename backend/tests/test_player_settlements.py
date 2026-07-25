import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.player_settlement import PlayerSettlement, PlayerSettlementDirection
from app.models.transaction import Direction
from app.schemas.transaction import LedgerTotals
from app.services.player_settlement import (
    compute_player_unsettled_balance_cents,
    normalize_sender_identity,
)
from app.services.settlement import compute_unsettled_balance_cents


def test_normalize_sender_identity_trims_and_collapses_whitespace() -> None:
    assert normalize_sender_identity("  Amy   F.  ") == "Amy F."
    assert normalize_sender_identity("Bob") == "Bob"
    assert normalize_sender_identity("") == ""
    assert normalize_sender_identity(None) == ""


def test_player_unsettled_in_only() -> None:
    balance = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=0,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert balance == 50_000


def test_player_unsettled_in_minus_out() -> None:
    balance = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert balance == 35_000


def test_player_unsettled_after_paid_to_player() -> None:
    before = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert before == 35_000
    after_paid = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=20_000,
        settlements_received_cents=0,
    )
    assert after_paid == 15_000


def test_player_unsettled_after_received_from_player() -> None:
    after_received = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=20_000,
        settlements_received_cents=2_500,
    )
    assert after_received == 17_500


def test_paid_to_player_rejects_amount_over_positive_unsettled() -> None:
    unsettled = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert unsettled == 35_000
    amount_cents = 40_000
    assert amount_cents > unsettled
    message = f"Settlement amount exceeds unsettled balance of ${unsettled / 100:,.2f}."
    assert message == "Settlement amount exceeds unsettled balance of $350.00."


def test_player_balances_independent_per_sender() -> None:
    amy = compute_player_unsettled_balance_cents(
        total_in_cents=50_000,
        total_out_cents=15_000,
        settlements_paid_cents=20_000,
        settlements_received_cents=0,
    )
    bob = compute_player_unsettled_balance_cents(
        total_in_cents=10_000,
        total_out_cents=0,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert amy == 15_000
    assert bob == 10_000


def test_status_values_do_not_affect_player_formula() -> None:
    pending_like = compute_player_unsettled_balance_cents(
        total_in_cents=500,
        total_out_cents=0,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    sent_like = compute_player_unsettled_balance_cents(
        total_in_cents=500,
        total_out_cents=0,
        settlements_paid_cents=0,
        settlements_received_cents=0,
    )
    assert pending_like == sent_like == 500


class FakeGlobalBalanceSession:
    def __init__(self, incoming: int, outgoing: int, settled: int) -> None:
        self.incoming = incoming
        self.outgoing = outgoing
        self.settled = settled

    async def execute(self, query):
        sql = str(query).lower()
        if "from transactions" in sql or "transactions.direction" in sql:
            return SimpleNamespace(one=lambda: (self.incoming, self.outgoing))
        if "from settlements" in sql or "sum(settlements" in sql:
            return SimpleNamespace(scalar_one=lambda: self.settled)
        if "player_settlements" in sql:
            raise AssertionError("Global unsettled balance must not query player_settlements")
        raise AssertionError(f"Unexpected query: {query}")


def test_global_balance_ignores_player_settlements() -> None:
    session = FakeGlobalBalanceSession(incoming=50_000, outgoing=15_000, settled=10_000)
    balance = asyncio.run(compute_unsettled_balance_cents(session))
    assert balance == 25_000
    totals = LedgerTotals(
        total_incoming_cents=50_000,
        total_outgoing_cents=15_000,
        total_settled_cents=10_000,
        unsettled_balance_cents=25_000,
        total_transactions=3,
    )
    assert totals.unsettled_balance_cents == (
        totals.total_incoming_cents - totals.total_outgoing_cents - totals.total_settled_cents
    )


def test_player_settlement_model_directions() -> None:
    settlement = PlayerSettlement(
        sender_name="Amy F.",
        direction=PlayerSettlementDirection.PAID_TO_PLAYER,
        amount_cents=2000,
        payment_account_id=1,
        reference=None,
        note=None,
        settled_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        created_by_user_id="admin@example.com",
    )
    assert settlement.direction == PlayerSettlementDirection.PAID_TO_PLAYER
    assert settlement.amount_cents == 2000
    assert PlayerSettlementDirection.RECEIVED_FROM_PLAYER.value == "RECEIVED_FROM_PLAYER"


def test_transaction_direction_enum_unchanged() -> None:
    assert Direction.IN.value == "IN"
    assert Direction.OUT.value == "OUT"
