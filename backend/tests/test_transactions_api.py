import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.transactions import serialize_transaction
from app.models.payment_account import PaymentAccount
from app.models.provider import Provider
from app.models.settlement import Settlement
from app.models.transaction import Direction, Transaction
from app.parsers.base import ParserResult
from app.schemas.settlement import SettlementCreate, parse_dollar_amount_to_cents
from app.schemas.transaction import LedgerTotals
from app.services.ledger import create_transaction_from_parser_result
from app.services.settlement import create_settlement


def test_serialize_transaction_includes_ledger_fields() -> None:
    provider = Provider(id=1, name="Chime", parser_key="chime", enabled=True)
    account = PaymentAccount(
        id=5,
        provider_id=1,
        friendly_name="Larry",
        receiver_tag="$larry",
        gmail_address="larry@example.com",
        encrypted_app_password="encrypted",
    )
    account.provider = provider
    transaction = Transaction(
        id=4,
        payment_account_id=5,
        direction=Direction.IN,
        amount_cents=1000,
        sender_name="Amy F.",
        receiver_tag=None,
        provider_reference=None,
        gmail_message_id="<msg@example.com>",
        received_at=datetime(2026, 7, 24, 16, 8, 27, tzinfo=UTC),
        telegram_status="sent",
        telegram_sent_at=datetime(2026, 7, 24, 16, 8, 34, tzinfo=UTC),
        created_at=datetime(2026, 7, 24, 16, 8, 33, tzinfo=UTC),
    )
    transaction.payment_account = account

    summary = serialize_transaction(transaction)

    assert summary.id == 4
    assert summary.direction == "IN"
    assert summary.amount_cents == 1000
    assert summary.sender_name == "Amy F."
    assert summary.friendly_name == "Larry"
    assert summary.provider_name == "Chime"
    assert summary.receiver_tag == "$larry"
    assert summary.telegram_status == "sent"


def test_ledger_totals_unsettled_balance_math() -> None:
    totals = LedgerTotals(
        total_incoming_cents=2000,
        total_outgoing_cents=0,
        total_settled_cents=1500,
        unsettled_balance_cents=500,
        total_transactions=2,
    )
    assert totals.unsettled_balance_cents == (
        totals.total_incoming_cents - totals.total_outgoing_cents - totals.total_settled_cents
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15", 1500),
        ("15.00", 1500),
        ("$15.00", 1500),
        ("1,234.56", 123456),
        ("0.01", 1),
    ],
)
def test_parse_dollar_amount_to_cents(raw: str, expected: int) -> None:
    assert parse_dollar_amount_to_cents(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "0.00", "-5", "-0.01"])
def test_parse_dollar_amount_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_dollar_amount_to_cents(raw)


def test_settlement_create_schema_rejects_empty_amount() -> None:
    with pytest.raises(ValidationError):
        SettlementCreate(payment_account_id=1, amount="   ")


class FakeLedgerSession:
    def __init__(self) -> None:
        self.by_message_id: dict[str, object] = {}
        self.added = []
        self.flushes = 0

    async def scalar(self, query):
        for value in getattr(query, "_where_criteria", ()):
            right = getattr(value, "right", None)
            message_id = getattr(right, "value", None)
            if isinstance(message_id, str):
                return self.by_message_id.get(message_id)
        return None

    def add(self, item) -> None:
        self.added.append(item)
        self.by_message_id[item.gmail_message_id] = item

    async def flush(self) -> None:
        self.flushes += 1


def _payment_result(message_suffix: str = "a") -> tuple[SimpleNamespace, ParserResult]:
    email = SimpleNamespace(
        payment_account_id=1,
        gmail_message_id=f"<amy-{message_suffix}@example.com>",
        gmail_uid=10,
        mailbox="INBOX",
        received_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        subject="Amy F. just sent you money",
    )
    result = ParserResult(
        classification="incoming_payment",
        is_payment=True,
        direction="IN",
        amount_cents=500,
        sender_name="Amy F.",
        sender_payment_tag=None,
        receiver_tag=None,
        payment_timestamp=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        provider_reference=None,
        confidence=0.9,
        missing_fields=[],
        parser_key="chime",
        parser_version="0.2.0",
    )
    return email, result


def test_same_sender_amount_keeps_separate_transactions() -> None:
    session = FakeLedgerSession()
    first_email, first_result = _payment_result("one")
    second_email, second_result = _payment_result("two")

    first, _ = asyncio.run(create_transaction_from_parser_result(session, first_email, first_result))
    second, _ = asyncio.run(create_transaction_from_parser_result(session, second_email, second_result))

    assert first is not second
    assert len(session.added) == 2
    assert first.amount_cents == second.amount_cents == 500
    assert first.sender_name == second.sender_name == "Amy F."
    assert first.gmail_message_id != second.gmail_message_id


def test_duplicate_gmail_message_creates_one_transaction() -> None:
    session = FakeLedgerSession()
    email, result = _payment_result("dup")

    first, first_created = asyncio.run(create_transaction_from_parser_result(session, email, result))
    second, second_created = asyncio.run(create_transaction_from_parser_result(session, email, result))

    assert first is second
    assert first_created is True
    assert second_created is False
    assert len(session.added) == 1
    assert session.flushes == 1


class FakeSettlementSession:
    def __init__(self, account: PaymentAccount, incoming_cents: int) -> None:
        self.account = account
        self.incoming_cents = incoming_cents
        self.settlements: list[Settlement] = []
        self.added: list[Settlement] = []

    async def scalar(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is PaymentAccount:
            return self.account
        return None

    async def execute(self, query):
        sql = str(query).lower()
        if "from transactions" in sql or "transactions.direction" in sql:
            return SimpleNamespace(one=lambda: (self.incoming_cents, 0))
        if "from settlements" in sql or "sum(settlements" in sql:
            settled = sum(item.amount_cents for item in self.settlements)
            return SimpleNamespace(scalar_one=lambda: settled)
        raise AssertionError(f"Unexpected query: {query}")

    def add(self, item) -> None:
        self.added.append(item)
        self.settlements.append(item)

    async def flush(self) -> None:
        if self.added and getattr(self.added[-1], "id", None) is None:
            self.added[-1].id = len(self.settlements)


def test_create_settlement_partial_and_rejects_over_balance() -> None:
    account = PaymentAccount(
        id=1,
        provider_id=1,
        friendly_name="Larry",
        gmail_address="larry@example.com",
        encrypted_app_password="encrypted",
    )
    session = FakeSettlementSession(account, incoming_cents=2000)

    first = asyncio.run(create_settlement(session, payment_account_id=1, amount_cents=1500, note="partial"))
    assert first.amount_cents == 1500
    assert first.balance_before_cents == 2000
    assert first.balance_after_cents == 500

    with pytest.raises(ValueError, match="exceeds unsettled balance"):
        asyncio.run(create_settlement(session, payment_account_id=1, amount_cents=2100, note=None))

    second = asyncio.run(create_settlement(session, payment_account_id=1, amount_cents=500, note="remainder"))
    assert second.balance_before_cents == 500
    assert second.balance_after_cents == 0
    assert len(session.settlements) == 2
