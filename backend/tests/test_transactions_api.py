from datetime import UTC, datetime
from types import SimpleNamespace

from app.api.transactions import serialize_transaction
from app.models.payment_account import PaymentAccount
from app.models.provider import Provider
from app.models.transaction import Direction, Transaction
from app.parsers.base import ParserResult
from app.schemas.transaction import LedgerTotals
from app.services.ledger import create_transaction_from_parser_result
import asyncio


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


def test_ledger_totals_net_balance_math() -> None:
    totals = LedgerTotals(
        total_incoming_cents=1500,
        total_outgoing_cents=400,
        net_balance_cents=1100,
        total_transactions=3,
    )
    assert totals.net_balance_cents == totals.total_incoming_cents - totals.total_outgoing_cents
    assert totals.total_transactions == 3


class FakeLedgerSession:
    def __init__(self) -> None:
        self.by_message_id: dict[str, object] = {}
        self.added = []
        self.flushes = 0

    async def scalar(self, query):
        # Dedup lookup is always by gmail_message_id equality in create_transaction_from_parser_result.
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

    first = asyncio.run(create_transaction_from_parser_result(session, first_email, first_result))
    second = asyncio.run(create_transaction_from_parser_result(session, second_email, second_result))

    assert first is not second
    assert len(session.added) == 2
    assert first.amount_cents == second.amount_cents == 500
    assert first.sender_name == second.sender_name == "Amy F."
    assert first.gmail_message_id != second.gmail_message_id


def test_duplicate_gmail_message_creates_one_transaction() -> None:
    session = FakeLedgerSession()
    email, result = _payment_result("dup")

    first = asyncio.run(create_transaction_from_parser_result(session, email, result))
    second = asyncio.run(create_transaction_from_parser_result(session, email, result))

    assert first is second
    assert len(session.added) == 1
    assert session.flushes == 1
