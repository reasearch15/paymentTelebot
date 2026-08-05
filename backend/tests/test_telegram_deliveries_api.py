"""Phase 5 Telegram delivery ops center API and retry tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import encryption
from app.db.base import Base
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_delivery_attempt import TelegramDeliveryAttempt
from app.models.telegram_integration import TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.services import telegram as telegram_service
from app.services.telegram import (
    is_delivery_eligible_for_manual_retry,
    retry_telegram_delivery,
)
from app.services.telegram_delivery_ops import (
    DeliveryListFilters,
    deliveries_to_csv_rows,
    list_telegram_deliveries,
    load_integration_delivery_stats,
)


@pytest.fixture()
async def async_session(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(encryption.settings, "app_encryption_key", key)
    if hasattr(encryption.get_fernet, "cache_clear"):
        encryption.get_fernet.cache_clear()

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


async def _seed(session: AsyncSession):
    provider = Provider(name="Chime", parser_key="chime", enabled=True)
    session.add(provider)
    await session.flush()
    account = PaymentAccount(
        provider_id=provider.id,
        friendly_name="Larry Gmail",
        gmail_address="larry@example.com",
        encrypted_app_password=encryption.encrypt_secret("app-pass"),
        enabled=True,
    )
    session.add(account)
    await session.flush()
    integration = TelegramIntegration(
        name="Main Payments",
        bot_token_encrypted=encryption.encrypt_secret("123456789:ABCDEFGHijklmnop"),
        group_id="-100111",
        bot_username="MainBot",
        enabled=True,
    )
    session.add(integration)
    await session.flush()
    session.add(
        PaymentAccountTelegramRoute(
            payment_account_id=account.id,
            telegram_integration_id=integration.id,
        )
    )
    txn = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=1000,
        sender_name="Melanie R.",
        gmail_message_id="gmail-abc",
        received_at=datetime.now(UTC),
        telegram_status="failed",
    )
    session.add(txn)
    await session.flush()
    delivery = TelegramDelivery(
        transaction_id=txn.id,
        telegram_integration_id=integration.id,
        status="failed",
        attempt_count=1,
        last_attempt_at=datetime.now(UTC),
        last_error="403 Bot removed from group",
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    await session.refresh(txn)
    await session.refresh(integration)
    await session.refresh(account)
    return provider, account, integration, txn, delivery


@pytest.mark.asyncio
async def test_list_and_filter_deliveries(async_session: AsyncSession) -> None:
    _provider, account, integration, txn, delivery = await _seed(async_session)

    rows, total = await list_telegram_deliveries(
        async_session,
        DeliveryListFilters(status="failed", payment_account_id=account.id),
        page=1,
        page_size=20,
    )
    assert total == 1
    assert rows[0].id == delivery.id
    assert rows[0].transaction.sender_name == "Melanie R."

    rows, total = await list_telegram_deliveries(
        async_session,
        DeliveryListFilters(search=str(txn.id)),
        page=1,
        page_size=20,
    )
    assert total == 1

    rows, total = await list_telegram_deliveries(
        async_session,
        DeliveryListFilters(integration_id=integration.id, sender="Melanie"),
        page=1,
        page_size=20,
    )
    assert total == 1

    rows, total = await list_telegram_deliveries(
        async_session,
        DeliveryListFilters(status="sent"),
        page=1,
        page_size=20,
    )
    assert total == 0


@pytest.mark.asyncio
async def test_pagination(async_session: AsyncSession) -> None:
    _provider, account, integration, _txn, _delivery = await _seed(async_session)
    for index in range(3):
        txn = Transaction(
            payment_account_id=account.id,
            direction=Direction.IN,
            amount_cents=500 + index,
            sender_name=f"Sender {index}",
            gmail_message_id=f"gmail-{index}",
            received_at=datetime.now(UTC),
            telegram_status="sent",
        )
        async_session.add(txn)
        await async_session.flush()
        async_session.add(
            TelegramDelivery(
                transaction_id=txn.id,
                telegram_integration_id=integration.id,
                status="sent",
                attempt_count=1,
                sent_at=datetime.now(UTC),
                telegram_message_id=str(100 + index),
            )
        )
    await async_session.commit()

    page1, total = await list_telegram_deliveries(async_session, DeliveryListFilters(), page=1, page_size=2)
    page2, _ = await list_telegram_deliveries(async_session, DeliveryListFilters(), page=2, page_size=2)
    assert total == 4
    assert len(page1) == 2
    assert len(page2) == 2
    assert {row.id for row in page1}.isdisjoint({row.id for row in page2})


@pytest.mark.asyncio
async def test_cannot_retry_sent_or_pending(async_session: AsyncSession) -> None:
    _provider, account, integration, _txn, _failed = await _seed(async_session)
    sent_txn = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=2500,
        sender_name="Maria M.",
        gmail_message_id="gmail-sent",
        received_at=datetime.now(UTC),
        telegram_status="sent",
    )
    async_session.add(sent_txn)
    await async_session.flush()
    sent_delivery = TelegramDelivery(
        transaction_id=sent_txn.id,
        telegram_integration_id=integration.id,
        status="sent",
        attempt_count=1,
        sent_at=datetime.now(UTC),
        telegram_message_id="99",
    )
    pending_txn = Transaction(
        payment_account_id=account.id,
        direction=Direction.IN,
        amount_cents=300,
        sender_name="Pending P.",
        gmail_message_id="gmail-pending",
        received_at=datetime.now(UTC),
        telegram_status="pending",
    )
    async_session.add(pending_txn)
    await async_session.flush()
    pending_delivery = TelegramDelivery(
        transaction_id=pending_txn.id,
        telegram_integration_id=integration.id,
        status="pending",
        attempt_count=0,
    )
    async_session.add_all([sent_delivery, pending_delivery])
    await async_session.commit()
    await async_session.refresh(sent_delivery)
    await async_session.refresh(pending_delivery)

    assert is_delivery_eligible_for_manual_retry(sent_delivery) is False
    assert is_delivery_eligible_for_manual_retry(pending_delivery) is False

    sent_result = await retry_telegram_delivery(sent_delivery.id)
    pending_result = await retry_telegram_delivery(pending_delivery.id)
    assert sent_result["reason"] == "already_sent"
    assert pending_result["reason"] == "pending_owned"


@pytest.mark.asyncio
async def test_retry_failed_updates_status_and_history(async_session: AsyncSession, monkeypatch) -> None:
    _provider, _account, _integration, txn, delivery = await _seed(async_session)

    async def fake_send(bot_token: str, group_id: str, text: str):
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    result = await retry_telegram_delivery(delivery.id)
    assert result["ok"] is True
    assert result["status"] == "sent"
    assert result["telegram_message_id"] == "4242"

    await async_session.refresh(delivery)
    assert delivery.status == "sent"
    assert delivery.attempt_count == 2
    assert delivery.telegram_message_id == "4242"
    assert delivery.last_error is None
    assert delivery.sent_at is not None

    attempts = list(
        (
            await async_session.scalars(
                select(TelegramDeliveryAttempt)
                .where(TelegramDeliveryAttempt.telegram_delivery_id == delivery.id)
                .order_by(TelegramDeliveryAttempt.attempt_number)
            )
        ).all()
    )
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 2
    assert attempts[0].status == "sent"
    assert attempts[0].telegram_message_id == "4242"

    await async_session.refresh(txn)
    assert txn.telegram_status == "sent"


@pytest.mark.asyncio
async def test_retry_preserves_prior_failure_history(async_session: AsyncSession, monkeypatch) -> None:
    _provider, _account, _integration, _txn, delivery = await _seed(async_session)
    async_session.add(
        TelegramDeliveryAttempt(
            telegram_delivery_id=delivery.id,
            attempt_number=1,
            status="failed",
            error_message="Timeout",
            attempted_at=datetime.now(UTC) - timedelta(minutes=5),
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await async_session.commit()

    async def fake_send(bot_token: str, group_id: str, text: str):
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    result = await retry_telegram_delivery(delivery.id)
    assert result["ok"] is False
    assert result["status"] == "failed"

    attempts = list(
        (
            await async_session.scalars(
                select(TelegramDeliveryAttempt)
                .where(TelegramDeliveryAttempt.telegram_delivery_id == delivery.id)
                .order_by(TelegramDeliveryAttempt.attempt_number)
            )
        ).all()
    )
    assert len(attempts) == 2
    assert attempts[0].status == "failed"
    assert attempts[0].error_message == "Timeout"
    assert attempts[1].status == "failed"
    assert "429" in (attempts[1].error_message or "")


@pytest.mark.asyncio
async def test_csv_export_rows_sanitize_errors(async_session: AsyncSession) -> None:
    _provider, _account, _integration, _txn, delivery = await _seed(async_session)
    rows, _ = await list_telegram_deliveries(async_session, DeliveryListFilters(), page=1, page_size=10)
    csv_rows = deliveries_to_csv_rows(rows)
    assert csv_rows[0]["integration_name"] == "Main Payments"
    assert "403" in csv_rows[0]["last_error"]
    assert "bot_token" not in csv_rows[0]


@pytest.mark.asyncio
async def test_integration_delivery_stats(async_session: AsyncSession) -> None:
    _provider, _account, integration, _txn, _delivery = await _seed(async_session)
    stats = await load_integration_delivery_stats(async_session, [integration.id])
    bucket = stats[integration.id]
    assert bucket["failed_today"] >= 1 or bucket["messages_today"] >= 1
    assert bucket["pending"] == 0
