from datetime import UTC, datetime

from app.api.transactions import serialize_transaction
from app.models.payment_account import PaymentAccount
from app.models.provider import Provider
from app.models.transaction import Direction, Transaction


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
