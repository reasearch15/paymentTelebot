"""Phase 1/2 multi-telegram schema, legacy assignment, and compatibility tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.telegram import serialize_settings
from app.db.base import Base
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import DEFAULT_TELEGRAM_INTEGRATION_NAME, TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.schemas.telegram import TelegramSettingsUpdate
from app.services.telegram import (
    apply_telegram_settings,
    get_or_create_telegram_integration,
    notify_transaction_in_session,
)
from app.services.telegram_legacy_migration import (
    assign_payment_accounts_to_default_telegram_integration,
    backfill_telegram_integration_names,
    integration_display_name,
    select_default_telegram_integration_id,
)


def _sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _create_schema(engine) -> None:
    Base.metadata.create_all(engine)


@pytest.fixture()
def db_session():
    engine = _sqlite_engine()
    _create_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()
        engine.dispose()


def _provider(session: Session, *, parser_key: str = "chime") -> Provider:
    provider = Provider(name="Chime", parser_key=parser_key, enabled=True)
    session.add(provider)
    session.flush()
    return provider


def _account(
    session: Session,
    provider: Provider,
    *,
    friendly_name: str,
    gmail_address: str,
) -> PaymentAccount:
    account = PaymentAccount(
        provider_id=provider.id,
        friendly_name=friendly_name,
        gmail_address=gmail_address,
        encrypted_app_password="encrypted-password",
        enabled=True,
    )
    session.add(account)
    session.flush()
    return account


def _integration(session: Session, *, name: str = "Bot A", enabled: bool = True) -> TelegramIntegration:
    integration = TelegramIntegration(
        name=name,
        bot_token_encrypted="encrypted-token",
        group_id="-100123",
        enabled=enabled,
    )
    session.add(integration)
    session.flush()
    return integration


def _transaction(session: Session, account: PaymentAccount, *, message_id: str) -> Transaction:
    transaction = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=2500,
        sender_name="Test Sender",
        gmail_message_id=message_id,
        received_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        telegram_status="sent",
        telegram_sent_at=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_integration_display_name_single_and_multiple() -> None:
    assert integration_display_name(row_index=1, total_count=1) == DEFAULT_TELEGRAM_INTEGRATION_NAME
    assert integration_display_name(row_index=1, total_count=2) == "Telegram Integration 1"
    assert integration_display_name(row_index=2, total_count=2) == "Telegram Integration 2"


def test_create_multiple_named_telegram_integrations(db_session: Session) -> None:
    first = _integration(db_session, name="Royal VIP Bot")
    second = _integration(db_session, name="Charlie Bot")
    db_session.commit()

    names = sorted(db_session.scalars(select(TelegramIntegration.name)).all())
    assert names == ["Charlie Bot", "Royal VIP Bot"]
    assert first.bot_username is None
    assert second.bot_username is None


def test_one_integration_linked_to_multiple_accounts(db_session: Session) -> None:
    provider = _provider(db_session)
    integration = _integration(db_session, name="Shared Bot")
    account_a = _account(db_session, provider, friendly_name="A", gmail_address="a@example.com")
    account_b = _account(db_session, provider, friendly_name="B", gmail_address="b@example.com")

    db_session.add_all(
        [
            PaymentAccountTelegramRoute(
                payment_account_id=account_a.id,
                telegram_integration_id=integration.id,
            ),
            PaymentAccountTelegramRoute(
                payment_account_id=account_b.id,
                telegram_integration_id=integration.id,
            ),
        ]
    )
    db_session.commit()

    routes = db_session.scalars(
        select(PaymentAccountTelegramRoute).where(PaymentAccountTelegramRoute.telegram_integration_id == integration.id)
    ).all()
    assert {route.payment_account_id for route in routes} == {account_a.id, account_b.id}


def test_one_account_linked_to_multiple_integrations(db_session: Session) -> None:
    provider = _provider(db_session)
    account = _account(db_session, provider, friendly_name="Shared", gmail_address="shared@example.com")
    bot_one = _integration(db_session, name="Bot One")
    bot_two = _integration(db_session, name="Bot Two")

    db_session.add_all(
        [
            PaymentAccountTelegramRoute(payment_account_id=account.id, telegram_integration_id=bot_one.id),
            PaymentAccountTelegramRoute(payment_account_id=account.id, telegram_integration_id=bot_two.id),
        ]
    )
    db_session.commit()

    routes = db_session.scalars(
        select(PaymentAccountTelegramRoute).where(PaymentAccountTelegramRoute.payment_account_id == account.id)
    ).all()
    assert {route.telegram_integration_id for route in routes} == {bot_one.id, bot_two.id}


def test_duplicate_route_pair_is_rejected(db_session: Session) -> None:
    provider = _provider(db_session)
    account = _account(db_session, provider, friendly_name="Dup", gmail_address="dup@example.com")
    integration = _integration(db_session)
    db_session.add(
        PaymentAccountTelegramRoute(payment_account_id=account.id, telegram_integration_id=integration.id)
    )
    db_session.commit()

    db_session.add(
        PaymentAccountTelegramRoute(payment_account_id=account.id, telegram_integration_id=integration.id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_delivery_pair_is_rejected(db_session: Session) -> None:
    provider = _provider(db_session)
    account = _account(db_session, provider, friendly_name="Txn", gmail_address="txn@example.com")
    integration = _integration(db_session)
    transaction = _transaction(db_session, account, message_id="<msg-1@example.com>")

    db_session.add(
        TelegramDelivery(
            transaction_id=transaction.id,
            telegram_integration_id=integration.id,
            status="sent",
            telegram_message_id="42",
        )
    )
    db_session.commit()

    db_session.add(
        TelegramDelivery(
            transaction_id=transaction.id,
            telegram_integration_id=integration.id,
            status="pending",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_backfill_names_single_and_multiple_rows(db_session: Session) -> None:
    connection = db_session.connection()
    connection.execute(
        text(
            "INSERT INTO telegram_integrations (bot_token_encrypted, group_id, enabled) "
            "VALUES ('enc', '-1001', 0)"
        )
    )
    db_session.commit()
    backfill_telegram_integration_names(db_session.connection())
    db_session.commit()
    assert db_session.scalar(select(TelegramIntegration.name)) == DEFAULT_TELEGRAM_INTEGRATION_NAME

    db_session.execute(text("DELETE FROM telegram_integrations"))
    db_session.commit()
    connection = db_session.connection()
    connection.execute(
        text(
            "INSERT INTO telegram_integrations (bot_token_encrypted, group_id, enabled) "
            "VALUES ('enc-a', '-1001', 0), ('enc-b', '-1002', 0)"
        )
    )
    db_session.commit()
    backfill_telegram_integration_names(db_session.connection())
    db_session.commit()
    names = db_session.execute(text("SELECT name FROM telegram_integrations ORDER BY id")).fetchall()
    assert [row[0] for row in names] == ["Telegram Integration 1", "Telegram Integration 2"]


def test_legacy_assignment_routes_all_accounts_idempotently(db_session: Session) -> None:
    provider = _provider(db_session)
    _account(db_session, provider, friendly_name="One", gmail_address="one@example.com")
    _account(db_session, provider, friendly_name="Two", gmail_address="two@example.com")
    older = TelegramIntegration(bot_token_encrypted="enc-old", group_id="-100", enabled=True)
    newer = TelegramIntegration(bot_token_encrypted="enc-new", group_id="-200", enabled=True)
    db_session.add_all([older, newer])
    db_session.flush()
    assert older.id < newer.id
    db_session.commit()

    connection = db_session.connection()
    backfill_telegram_integration_names(connection)
    first_insert = assign_payment_accounts_to_default_telegram_integration(connection)
    second_insert = assign_payment_accounts_to_default_telegram_integration(connection)
    db_session.commit()

    assert first_insert == 2
    assert second_insert == 0
    assert select_default_telegram_integration_id(db_session.connection()) == older.id

    routes = db_session.scalars(select(PaymentAccountTelegramRoute)).all()
    assert len(routes) == 2
    assert {route.telegram_integration_id for route in routes} == {older.id}


def test_legacy_assignment_with_zero_integrations_and_zero_accounts(db_session: Session) -> None:
    assert select_default_telegram_integration_id(db_session.connection()) is None
    assert assign_payment_accounts_to_default_telegram_integration(db_session.connection()) == 0
    assert db_session.scalar(select(TelegramIntegration.id)) is None
    assert db_session.scalars(select(PaymentAccountTelegramRoute)).all() == []


def test_migration_does_not_create_deliveries_or_change_telegram_status(db_session: Session) -> None:
    provider = _provider(db_session)
    account = _account(db_session, provider, friendly_name="Keep", gmail_address="keep@example.com")
    integration = _integration(db_session, name="Default")
    transaction = _transaction(db_session, account, message_id="<keep@example.com>")
    original_status = transaction.telegram_status
    original_sent_at = transaction.telegram_sent_at
    db_session.commit()

    connection = db_session.connection()
    assign_payment_accounts_to_default_telegram_integration(connection)
    db_session.commit()

    db_session.refresh(transaction)
    assert transaction.telegram_status == original_status
    assert transaction.telegram_sent_at is not None
    assert transaction.telegram_sent_at.replace(tzinfo=UTC) == original_sent_at.replace(tzinfo=UTC)
    assert db_session.scalars(select(TelegramDelivery)).all() == []
    assert db_session.scalar(
        select(PaymentAccountTelegramRoute).where(
            PaymentAccountTelegramRoute.payment_account_id == account.id,
            PaymentAccountTelegramRoute.telegram_integration_id == integration.id,
        )
    ) is not None


def test_get_or_create_still_creates_disabled_default_without_fake_token() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.existing = None
            self.added = []

        async def scalar(self, _query):
            return self.existing

        def add(self, item) -> None:
            self.added.append(item)
            self.existing = item

        async def flush(self) -> None:
            if self.existing is not None and getattr(self.existing, "id", None) is None:
                self.existing.id = 1

    session = FakeSession()
    integration = asyncio.run(get_or_create_telegram_integration(session))  # type: ignore[arg-type]

    assert integration.enabled is False
    assert integration.bot_token_encrypted is None
    assert integration.group_id is None
    assert integration.name == DEFAULT_TELEGRAM_INTEGRATION_NAME


def test_legacy_settings_serialize_and_update_without_name_in_payload(monkeypatch, db_session: Session) -> None:
    integration = TelegramIntegration(
        name=DEFAULT_TELEGRAM_INTEGRATION_NAME,
        bot_token_encrypted="encrypted",
        group_id="-100999",
        enabled=False,
    )
    db_session.add(integration)
    db_session.commit()

    monkeypatch.setattr("app.services.telegram.encrypt_secret", lambda value: f"enc:{value}")
    apply_telegram_settings(integration, "123456789:abcdefTOKEN", "-100999", True)
    assert integration.enabled is True
    assert integration.group_id == "-100999"
    assert integration.bot_token_encrypted == "enc:123456789:abcdefTOKEN"
    assert integration.name == DEFAULT_TELEGRAM_INTEGRATION_NAME

    payload = TelegramSettingsUpdate(bot_token=None, group_id="-100999", enabled=True)
    assert payload.bot_token is None
    assert "name" not in TelegramSettingsUpdate.model_fields


def test_legacy_serialize_settings_masks_token(monkeypatch) -> None:
    monkeypatch.setattr("app.api.telegram.decrypt_secret", lambda _value: "123456789:abcdef")
    response = serialize_settings(
        TelegramIntegration(
            name=DEFAULT_TELEGRAM_INTEGRATION_NAME,
            bot_token_encrypted="encrypted",
            group_id="-100123",
            enabled=True,
        )
    )
    assert response.bot_token_masked == "1234...cdef"
    assert not hasattr(response, "bot_token")
    assert "abcdef" not in (response.bot_token_masked or "")


def test_singleton_send_path_unchanged_does_not_create_delivery_rows(monkeypatch, db_session: Session) -> None:
    provider = _provider(db_session)
    account = _account(db_session, provider, friendly_name="Send", gmail_address="send@example.com")
    account.provider = provider
    integration = _integration(db_session, name=DEFAULT_TELEGRAM_INTEGRATION_NAME)
    transaction = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=5000,
        sender_name="Emily S.",
        gmail_message_id="<send@example.com>",
        received_at=datetime(2026, 7, 24, 11, 23, tzinfo=UTC),
        telegram_status="pending",
    )
    transaction.payment_account = account
    db_session.add(transaction)
    db_session.commit()

    sent_messages: list[str] = []

    async def fake_send(_token: str, _group_id: str, text: str):
        sent_messages.append(text)
        return {"ok": True}

    monkeypatch.setattr("app.services.telegram.decrypt_secret", lambda _value: "token")
    monkeypatch.setattr("app.services.telegram.telegram_send_message", fake_send)

    asyncio.run(notify_transaction_in_session(transaction, integration))

    assert transaction.telegram_status == "sent"
    assert len(sent_messages) == 1
    assert db_session.scalars(select(TelegramDelivery)).all() == []
