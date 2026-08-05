"""Legacy Telegram helper/unit tests and message formatting.

Fan-out delivery behavior is covered in test_multi_telegram_delivery.py.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.api.telegram import serialize_settings
from app.models.payment_account import PaymentAccount
from app.models.provider import Provider
from app.models.telegram_integration import TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.parsers.base import ParserResult
from app.schemas.telegram import TelegramSettingsUpdate
from app.services.ledger import create_transaction_from_parser_result
from app.services.telegram import (
    TELEGRAM_SENDING_STALE_AFTER,
    format_kathmandu_timestamp,
    format_transaction_message,
    is_stale_sending_claim,
    notify_transaction_in_session,
    should_send_transaction_notification,
)


def parser_result(classification: str = "incoming_payment", direction: str = "IN") -> ParserResult:
    return ParserResult(
        classification=classification,
        is_payment=True,
        direction=direction,
        amount_cents=5000,
        sender_name="Emily S.",
        sender_payment_tag=None,
        receiver_tag=None,
        payment_timestamp=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        provider_reference=None,
        confidence=0.9,
        missing_fields=[],
        parser_key="chime",
        parser_version="0.2.0",
    )


class FakeLedgerSession:
    def __init__(self) -> None:
        self.existing = None
        self.added = []
        self.flushes = 0

    async def scalar(self, _query):
        return self.existing

    def add(self, item) -> None:
        self.added.append(item)
        self.existing = item

    async def flush(self) -> None:
        self.flushes += 1


def fake_email(message_id: str = "<message@example.com>"):
    return SimpleNamespace(
        payment_account_id=1,
        gmail_message_id=message_id,
        gmail_uid=42,
        mailbox="INBOX",
        received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        subject="Emily S. just sent you money 💸",
    )


def test_duplicate_email_creates_one_transaction() -> None:
    session = FakeLedgerSession()
    email = fake_email()
    first, first_created = asyncio.run(create_transaction_from_parser_result(session, email, parser_result()))
    second, second_created = asyncio.run(create_transaction_from_parser_result(session, email, parser_result()))

    assert first is second
    assert first_created is True
    assert second_created is False
    assert len(session.added) == 1
    assert session.flushes == 1


def build_transaction(
    direction: Direction = Direction.IN,
    status: str = "pending",
    *,
    telegram_attempted_at: datetime | None = None,
) -> Transaction:
    provider = Provider(id=1, name="Chime", parser_key="chime", enabled=True)
    account = PaymentAccount(
        id=1,
        provider_id=1,
        friendly_name="Larry",
        gmail_address="larry@example.com",
        encrypted_app_password="encrypted",
    )
    account.provider = provider
    transaction = Transaction(
        id=1,
        payment_account_id=1,
        direction=direction,
        amount_cents=5000,
        sender_name="Emily S.",
        gmail_message_id="<message@example.com>",
        received_at=datetime(2026, 7, 24, 11, 23, tzinfo=UTC),
        telegram_status=status,
        telegram_attempted_at=telegram_attempted_at,
    )
    transaction.payment_account = account
    return transaction


def test_telegram_eligibility_incoming_only(monkeypatch) -> None:
    sent_messages = []

    async def fake_send(_token: str, _group_id: str, text: str):
        sent_messages.append(text)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr("app.services.telegram.decrypt_secret", lambda _value: "token")
    monkeypatch.setattr("app.services.telegram.telegram_send_message", fake_send)
    integration = TelegramIntegration(bot_token_encrypted="encrypted", group_id="-100123", enabled=True)

    incoming = build_transaction(Direction.IN)
    outgoing = build_transaction(Direction.OUT)
    asyncio.run(notify_transaction_in_session(incoming, integration))
    asyncio.run(notify_transaction_in_session(outgoing, integration))

    assert incoming.telegram_status == "sent"
    assert incoming.telegram_sent_at is not None
    assert outgoing.telegram_status == "pending"
    assert len(sent_messages) == 1
    assert sent_messages[0] == (
        "🟢 New Chime Payment\n\n"
        "💵 Amount Received: $50.00\n"
        "👤 Payment Name: Emily S.\n"
        "🕒 Received At: 24 Jul 2026, 5:08 PM"
    )


def test_telegram_failure_keeps_payment_and_redacts_token(monkeypatch) -> None:
    async def failing_send(token: str, _group_id: str, _text: str):
        raise RuntimeError(f"bad token {token}")

    monkeypatch.setattr("app.services.telegram.decrypt_secret", lambda _value: "secret-token")
    monkeypatch.setattr("app.services.telegram.telegram_send_message", failing_send)
    integration = TelegramIntegration(bot_token_encrypted="encrypted", group_id="-100123", enabled=True)
    transaction = build_transaction(Direction.IN)

    asyncio.run(notify_transaction_in_session(transaction, integration))

    assert transaction.telegram_status == "failed"
    assert transaction.telegram_last_error is not None
    assert integration.last_error is not None
    assert "secret-token" not in integration.last_error


def test_duplicate_telegram_notification_is_not_resent(monkeypatch) -> None:
    async def fake_send(_token: str, _group_id: str, _text: str):
        raise AssertionError("sent notification twice")

    monkeypatch.setattr("app.services.telegram.decrypt_secret", lambda _value: "token")
    monkeypatch.setattr("app.services.telegram.telegram_send_message", fake_send)
    integration = TelegramIntegration(bot_token_encrypted="encrypted", group_id="-100123", enabled=True)
    transaction = build_transaction(Direction.IN, status="sent")

    asyncio.run(notify_transaction_in_session(transaction, integration))

    assert transaction.telegram_status == "sent"


def test_legacy_transaction_eligibility_no_auto_retry_failed() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    recent = now - timedelta(seconds=5)
    stale = now - TELEGRAM_SENDING_STALE_AFTER - timedelta(seconds=1)

    assert should_send_transaction_notification(build_transaction(Direction.IN, status="pending"), now=now)
    assert not should_send_transaction_notification(build_transaction(Direction.IN, status="failed"), now=now)
    assert not should_send_transaction_notification(build_transaction(Direction.IN, status="sent"), now=now)
    assert not should_send_transaction_notification(build_transaction(Direction.IN, status="not_applicable"), now=now)
    assert not should_send_transaction_notification(
        build_transaction(Direction.IN, status="sending", telegram_attempted_at=recent),
        now=now,
    )
    assert should_send_transaction_notification(
        build_transaction(Direction.IN, status="sending", telegram_attempted_at=stale),
        now=now,
    )
    assert is_stale_sending_claim(
        build_transaction(Direction.IN, status="sending", telegram_attempted_at=None),
        now=now,
    )


def test_telegram_settings_mask_token_and_accept_negative_group(monkeypatch) -> None:
    monkeypatch.setattr("app.api.telegram.decrypt_secret", lambda _value: "123456789:abcdef")
    payload = TelegramSettingsUpdate(bot_token=" token ", group_id=" -100123 ", enabled=True)
    response = serialize_settings(
        TelegramIntegration(bot_token_encrypted="encrypted", group_id=payload.group_id, enabled=payload.enabled)
    )

    assert payload.group_id == "-100123"
    assert response.bot_token_masked == "1234...cdef"


def test_telegram_message_omits_payment_tag() -> None:
    transaction = build_transaction(Direction.IN)

    assert "Payment Tag" not in format_transaction_message(transaction)


def test_kathmandu_timestamp_formatting() -> None:
    assert format_kathmandu_timestamp(datetime(2026, 7, 24, 11, 23, tzinfo=UTC)) == "24 Jul 2026, 5:08 PM"
