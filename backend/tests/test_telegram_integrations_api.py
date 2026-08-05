"""Phase 4 Telegram integration CRUD, assignments, and delivery summary tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.telegram_integrations import serialize_integration
from app.core import encryption
from app.db.base import Base
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.schemas.telegram import TelegramIntegrationCreate, TelegramIntegrationUpdate
from app.services import telegram as telegram_service
from app.services.telegram import (
    build_telegram_delivery_summary,
    find_duplicate_telegram_destination,
    mask_bot_token,
)
from app.services.telegram_assignments import (
    replace_integration_payment_accounts,
    replace_payment_account_telegram_integrations,
)


@pytest.fixture()
async def async_session(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(encryption.settings, "app_encryption_key", key)
    encryption.get_fernet.cache_clear() if hasattr(encryption.get_fernet, "cache_clear") else None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    telegram_service.AsyncSessionLocal = SessionLocal

    async with SessionLocal() as session:
        yield session

    await engine.dispose()


async def _provider(session: AsyncSession, key: str = "chime") -> Provider:
    provider = Provider(name="Chime", parser_key=key, enabled=True)
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


async def _account(session: AsyncSession, provider: Provider, *, name: str, gmail: str) -> PaymentAccount:
    account = PaymentAccount(
        provider_id=provider.id,
        friendly_name=name,
        gmail_address=gmail,
        encrypted_app_password=encryption.encrypt_secret("app-pass"),
        enabled=True,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_create_integration_encrypts_and_masks_token(async_session: AsyncSession) -> None:
    payload = TelegramIntegrationCreate(
        name="Main Payments",
        bot_token="123456789:ABCDEFGHijklmnop",
        group_id="-100111",
        enabled=True,
    )
    integration = TelegramIntegration(
        name=payload.name,
        bot_token_encrypted=encryption.encrypt_secret(payload.bot_token),
        group_id=payload.group_id,
        enabled=payload.enabled,
    )
    async_session.add(integration)
    await async_session.commit()
    await async_session.refresh(integration)

    assert integration.bot_token_encrypted != payload.bot_token
    assert "ABCDEF" not in integration.bot_token_encrypted
    serialized = serialize_integration(integration, assigned_count=0, legacy_default_id=integration.id)
    assert serialized.bot_token_masked == mask_bot_token(payload.bot_token)
    assert serialized.has_bot_token is True
    assert "ABCDEF" not in (serialized.bot_token_masked or "")
    assert not hasattr(serialized, "bot_token")


@pytest.mark.asyncio
async def test_blank_token_preserves_existing_and_duplicate_destination(async_session: AsyncSession) -> None:
    token = "111111111:tokenAAAA"
    first = TelegramIntegration(
        name="Bot A",
        bot_token_encrypted=encryption.encrypt_secret(token),
        group_id="-1001",
        enabled=True,
    )
    async_session.add(first)
    await async_session.commit()
    await async_session.refresh(first)
    original = first.bot_token_encrypted

    # blank update conceptually: do not change encrypted token
    update = TelegramIntegrationUpdate(name="Bot A Renamed", bot_token=None, group_id="-1001", enabled=True)
    assert update.bot_token is None
    first.name = update.name
    await async_session.commit()
    await async_session.refresh(first)
    assert first.bot_token_encrypted == original

    duplicate = await find_duplicate_telegram_destination(
        async_session,
        bot_token=token,
        group_id="-1001",
        exclude_integration_id=None,
    )
    assert duplicate is not None
    assert duplicate.id == first.id


@pytest.mark.asyncio
async def test_assignment_many_to_many_and_idempotent_replace(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    a1 = await _account(async_session, provider, name="A", gmail="a@example.com")
    a2 = await _account(async_session, provider, name="B", gmail="b@example.com")
    bot1 = TelegramIntegration(
        name="Bot1",
        bot_token_encrypted=encryption.encrypt_secret("t1"),
        group_id="-2001",
        enabled=True,
    )
    bot2 = TelegramIntegration(
        name="Bot2",
        bot_token_encrypted=encryption.encrypt_secret("t2"),
        group_id="-2002",
        enabled=True,
    )
    async_session.add_all([bot1, bot2])
    await async_session.commit()
    await async_session.refresh(bot1)
    await async_session.refresh(bot2)

    await replace_integration_payment_accounts(async_session, bot1.id, [a1.id, a2.id, a1.id])
    await async_session.commit()
    routes = (await async_session.scalars(select(PaymentAccountTelegramRoute))).all()
    assert len(routes) == 2

    await replace_payment_account_telegram_integrations(async_session, a1.id, [bot1.id, bot2.id, bot2.id])
    await async_session.commit()
    a1_routes = (
        await async_session.scalars(
            select(PaymentAccountTelegramRoute).where(PaymentAccountTelegramRoute.payment_account_id == a1.id)
        )
    ).all()
    assert {route.telegram_integration_id for route in a1_routes} == {bot1.id, bot2.id}

    # Empty assignment allowed
    await replace_payment_account_telegram_integrations(async_session, a1.id, [])
    await async_session.commit()
    assert (
        await async_session.scalars(
            select(PaymentAccountTelegramRoute).where(PaymentAccountTelegramRoute.payment_account_id == a1.id)
        )
    ).all() == []


@pytest.mark.asyncio
async def test_removing_route_keeps_delivery_history(async_session: AsyncSession) -> None:
    provider = await _provider(async_session, key="chime-x")
    account = await _account(async_session, provider, name="Keep", gmail="keep@example.com")
    bot = TelegramIntegration(
        name="Hist",
        bot_token_encrypted=encryption.encrypt_secret("tok"),
        group_id="-3001",
        enabled=True,
    )
    async_session.add(bot)
    await async_session.commit()
    await async_session.refresh(bot)
    await replace_integration_payment_accounts(async_session, bot.id, [account.id])
    await async_session.commit()

    txn = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=100,
        gmail_message_id="<hist@example.com>",
        received_at=datetime.now(UTC),
        telegram_status="sent",
    )
    async_session.add(txn)
    await async_session.flush()
    delivery = TelegramDelivery(
        transaction_id=txn.id,
        telegram_integration_id=bot.id,
        status="sent",
        telegram_message_id="9",
        attempt_count=1,
    )
    async_session.add(delivery)
    await async_session.commit()

    await replace_integration_payment_accounts(async_session, bot.id, [])
    await async_session.commit()
    assert (await async_session.scalars(select(PaymentAccountTelegramRoute))).all() == []
    remaining = (await async_session.scalars(select(TelegramDelivery))).all()
    assert len(remaining) == 1
    assert remaining[0].status == "sent"


@pytest.mark.asyncio
async def test_assignment_does_not_dispatch_or_create_historical_deliveries(async_session: AsyncSession, monkeypatch) -> None:
    provider = await _provider(async_session, key="chime-y")
    account = await _account(async_session, provider, name="Future", gmail="future@example.com")
    bot = TelegramIntegration(
        name="New",
        bot_token_encrypted=encryption.encrypt_secret("tok"),
        group_id="-4001",
        enabled=True,
    )
    async_session.add(bot)
    await async_session.commit()
    await async_session.refresh(bot)

    txn = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=100,
        gmail_message_id="<old@example.com>",
        received_at=datetime.now(UTC),
        telegram_status="sent",
    )
    async_session.add(txn)
    await async_session.commit()

    called = {"n": 0}

    async def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(telegram_service, "dispatch_transaction_notifications", boom)
    monkeypatch.setattr(telegram_service, "telegram_send_message", boom)

    await replace_payment_account_telegram_integrations(async_session, account.id, [bot.id])
    await async_session.commit()
    assert called["n"] == 0
    assert (await async_session.scalars(select(TelegramDelivery))).all() == []


@pytest.mark.asyncio
async def test_delete_rules_routes_and_deliveries(async_session: AsyncSession) -> None:
    provider = await _provider(async_session, key="chime-z")
    account = await _account(async_session, provider, name="Z", gmail="z@example.com")
    unused = TelegramIntegration(
        name="Unused",
        bot_token_encrypted=encryption.encrypt_secret("u"),
        group_id="-5001",
        enabled=True,
    )
    routed = TelegramIntegration(
        name="Routed",
        bot_token_encrypted=encryption.encrypt_secret("r"),
        group_id="-5002",
        enabled=True,
    )
    async_session.add_all([unused, routed])
    await async_session.commit()
    await async_session.refresh(unused)
    await async_session.refresh(routed)
    await replace_integration_payment_accounts(async_session, routed.id, [account.id])
    await async_session.commit()

    route_count = len(
        (
            await async_session.scalars(
                select(PaymentAccountTelegramRoute).where(
                    PaymentAccountTelegramRoute.telegram_integration_id == routed.id
                )
            )
        ).all()
    )
    assert route_count == 1

    # unused can be deleted
    await async_session.delete(unused)
    await async_session.commit()
    assert await async_session.get(TelegramIntegration, unused.id) is None


@pytest.mark.asyncio
async def test_specific_integration_test_stores_username(async_session: AsyncSession, monkeypatch) -> None:
    integration = TelegramIntegration(
        name="Testable",
        bot_token_encrypted=encryption.encrypt_secret("secret-token"),
        group_id="-6001",
        enabled=True,
    )
    async_session.add(integration)
    await async_session.commit()
    await async_session.refresh(integration)

    async def fake_me(_token: str):
        return {"ok": True, "result": {"username": "MainPaymentBot", "id": 1}}

    async def fake_chat(_token: str, _group: str):
        return {"ok": True, "result": {"id": -6001}}

    monkeypatch.setattr(telegram_service, "telegram_get_me", fake_me)
    monkeypatch.setattr(telegram_service, "telegram_get_chat", fake_chat)

    result = await telegram_service.test_specific_telegram_integration(
        async_session,
        integration,
        send_message=False,
    )
    assert result.success is True
    await async_session.refresh(integration)
    assert integration.bot_username == "MainPaymentBot"
    assert integration.last_error is None


@pytest.mark.asyncio
async def test_failed_test_sanitizes_token(async_session: AsyncSession, monkeypatch) -> None:
    integration = TelegramIntegration(
        name="Fail",
        bot_token_encrypted=encryption.encrypt_secret("secret-token"),
        group_id="-7001",
        enabled=True,
    )
    async_session.add(integration)
    await async_session.commit()
    await async_session.refresh(integration)

    async def failing_me(token: str):
        raise RuntimeError(f"unauthorized {token}")

    monkeypatch.setattr(telegram_service, "telegram_get_me", failing_me)
    result = await telegram_service.test_specific_telegram_integration(
        async_session,
        integration,
        send_message=False,
    )
    assert result.success is False
    await async_session.refresh(integration)
    assert "secret-token" not in (integration.last_error or "")


def test_delivery_summary_counts() -> None:
    rows = [
        TelegramDelivery(status="sent"),
        TelegramDelivery(status="sent"),
        TelegramDelivery(status="failed"),
        TelegramDelivery(status="pending"),
        TelegramDelivery(status="sending"),
    ]
    summary = build_telegram_delivery_summary(rows)
    assert summary is not None
    assert summary.total == 5
    assert summary.sent == 2
    assert summary.failed == 1
    assert summary.pending == 1
    assert summary.sending == 1
    assert build_telegram_delivery_summary([]) is None
