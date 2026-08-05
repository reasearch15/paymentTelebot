"""Phase 3 multi-destination Telegram fan-out delivery tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import DEFAULT_TELEGRAM_INTEGRATION_NAME, TelegramIntegration
from app.models.transaction import Direction, Transaction
from app.services import telegram as telegram_service
from app.services.telegram import (
    NO_DESTINATIONS_REASON,
    TELEGRAM_SENDING_STALE_AFTER,
    compute_transaction_telegram_rollup,
    dispatch_transaction_notifications,
    extract_telegram_message_id,
    is_delivery_eligible_for_normal_dispatch,
    sanitize_telegram_error,
    should_send_transaction_notification,
)


@pytest.fixture()
async def async_session():
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


async def _seed_account(
    session: AsyncSession,
    *,
    friendly_name: str = "Larry",
    gmail: str = "larry@example.com",
    parser_key: str = "chime",
) -> PaymentAccount:
    provider = Provider(name="Chime", parser_key=parser_key, enabled=True)
    session.add(provider)
    await session.flush()
    account = PaymentAccount(
        provider_id=provider.id,
        friendly_name=friendly_name,
        gmail_address=gmail,
        encrypted_app_password="enc-pass",
        enabled=True,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _seed_integration(
    session: AsyncSession,
    *,
    name: str,
    group_id: str | None = "-1001",
    enabled: bool = True,
    token: str | None = "enc-token",
) -> TelegramIntegration:
    integration = TelegramIntegration(
        name=name,
        bot_token_encrypted=token,
        group_id=group_id,
        enabled=enabled,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def _route(session: AsyncSession, account_id: int, integration_id: int) -> None:
    session.add(
        PaymentAccountTelegramRoute(payment_account_id=account_id, telegram_integration_id=integration_id)
    )
    await session.commit()


async def _transaction(
    session: AsyncSession,
    account_id: int,
    *,
    message_id: str,
    status: str = "pending",
) -> Transaction:
    txn = Transaction(
        payment_account_id=account_id,
        direction=Direction.IN,
        amount_cents=5000,
        sender_name="Emily S.",
        gmail_message_id=message_id,
        received_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        telegram_status=status,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_one_account_two_integrations_sends_to_both(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    bot1 = await _seed_integration(async_session, name="Bot 1", group_id="-1001")
    bot2 = await _seed_integration(async_session, name="Bot 2", group_id="-1002")
    await _route(async_session, account.id, bot1.id)
    await _route(async_session, account.id, bot2.id)
    txn = await _transaction(async_session, account.id, message_id="<a@example.com>")

    sent: list[str] = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sent.append(group_id)
        return {"ok": True, "result": {"message_id": int(group_id.replace("-", ""))}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)

    await async_session.refresh(txn)
    deliveries = (await async_session.scalars(select(TelegramDelivery))).all()
    assert len(deliveries) == 2
    assert {d.status for d in deliveries} == {"sent"}
    assert {d.telegram_message_id for d in deliveries} == {"1001", "1002"}
    assert set(sent) == {"-1001", "-1002"}
    assert txn.telegram_status == "sent"
    assert txn.telegram_last_error is None


@pytest.mark.asyncio
async def test_two_accounts_one_integration(async_session: AsyncSession, monkeypatch) -> None:
    a1 = await _seed_account(async_session, friendly_name="A", gmail="a@example.com", parser_key="chime")
    a2 = await _seed_account(async_session, friendly_name="B", gmail="b@example.com", parser_key="chime2")
    bot = await _seed_integration(async_session, name="Shared")
    await _route(async_session, a1.id, bot.id)
    await _route(async_session, a2.id, bot.id)
    t1 = await _transaction(async_session, a1.id, message_id="<1@example.com>")
    t2 = await _transaction(async_session, a2.id, message_id="<2@example.com>")

    sent = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sent.append(group_id)
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    await dispatch_transaction_notifications(t1.id, create_missing_destinations=True)
    await dispatch_transaction_notifications(t2.id, create_missing_destinations=True)

    assert sent == ["-1001", "-1001"]
    assert len((await async_session.scalars(select(TelegramDelivery))).all()) == 2


@pytest.mark.asyncio
async def test_unassigned_account_sends_nowhere(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    await _seed_integration(async_session, name="Orphan Bot")
    txn = await _transaction(async_session, account.id, message_id="<none@example.com>")

    async def boom(*_a, **_k):
        raise AssertionError("should not send")

    monkeypatch.setattr(telegram_service, "telegram_send_message", boom)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)

    await async_session.refresh(txn)
    assert (await async_session.scalars(select(TelegramDelivery))).all() == []
    assert txn.telegram_status == "not_applicable"
    assert txn.telegram_last_error == NO_DESTINATIONS_REASON


@pytest.mark.asyncio
async def test_disabled_and_incomplete_integrations_ignored(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    good = await _seed_integration(async_session, name="Good", group_id="-1009")
    disabled = await _seed_integration(async_session, name="Disabled", group_id="-1010", enabled=False)
    no_token = await _seed_integration(async_session, name="NoToken", group_id="-1011", token=None)
    no_group = await _seed_integration(async_session, name="NoGroup", group_id=None)

    for integration in (good, disabled, no_token, no_group):
        await _route(async_session, account.id, integration.id)

    txn = await _transaction(async_session, account.id, message_id="<filter@example.com>")
    sent = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sent.append(group_id)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)

    deliveries = (await async_session.scalars(select(TelegramDelivery))).all()
    assert len(deliveries) == 1
    assert deliveries[0].telegram_integration_id == good.id
    assert sent == ["-1009"]


@pytest.mark.asyncio
async def test_duplicate_dispatch_idempotent(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    bot = await _seed_integration(async_session, name="Bot")
    await _route(async_session, account.id, bot.id)
    txn = await _transaction(async_session, account.id, message_id="<dup@example.com>")
    sends = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sends.append(group_id)
        return {"ok": True, "result": {"message_id": 55}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)

    deliveries = (await async_session.scalars(select(TelegramDelivery))).all()
    assert len(deliveries) == 1
    assert deliveries[0].attempt_count == 1
    assert deliveries[0].telegram_message_id == "55"
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_failure_isolation_and_no_auto_retry_on_reparse(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    bot1 = await _seed_integration(async_session, name="Ok1", group_id="-2001")
    bot2 = await _seed_integration(async_session, name="Bad", group_id="-2002")
    bot3 = await _seed_integration(async_session, name="Ok3", group_id="-2003")
    for bot in (bot1, bot2, bot3):
        await _route(async_session, account.id, bot.id)
    txn = await _transaction(async_session, account.id, message_id="<mix@example.com>")

    async def fake_send(_token: str, group_id: str, _text: str):
        if group_id == "-2002":
            raise RuntimeError("bad token secret-token")
        return {"ok": True, "result": {"message_id": 9}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "secret-token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)
    await async_session.refresh(txn)
    by_integration = {
        d.telegram_integration_id: d
        for d in (await async_session.scalars(select(TelegramDelivery))).all()
    }
    assert by_integration[bot1.id].status == "sent"
    assert by_integration[bot2.id].status == "failed"
    assert by_integration[bot3.id].status == "sent"
    assert "secret-token" not in (by_integration[bot2.id].last_error or "")
    assert txn.telegram_status == "failed"
    assert "1 of 3" in (txn.telegram_last_error or "")
    assert txn.telegram_sent_at is not None

    # Normal reparse must not retry failed or resent successes.
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    refreshed = {
        d.telegram_integration_id: d
        for d in (await async_session.scalars(select(TelegramDelivery))).all()
    }
    assert refreshed[bot1.id].attempt_count == 1
    assert refreshed[bot2.id].attempt_count == 1
    assert refreshed[bot3.id].attempt_count == 1
    assert refreshed[bot2.id].status == "failed"


@pytest.mark.asyncio
async def test_reparse_does_not_send_to_newly_assigned_integration(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    bot1 = await _seed_integration(async_session, name="Original", group_id="-3001")
    await _route(async_session, account.id, bot1.id)
    txn = await _transaction(async_session, account.id, message_id="<hist@example.com>", status="sent")

    sends = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sends.append(group_id)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    # Historical txn has no delivery rows; reparse must not create/send.
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    assert sends == []
    assert (await async_session.scalars(select(TelegramDelivery))).all() == []
    await async_session.refresh(txn)
    assert txn.telegram_status == "sent"

    bot2 = await _seed_integration(async_session, name="New Bot", group_id="-3002")
    await _route(async_session, account.id, bot2.id)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    assert sends == []
    assert (await async_session.scalars(select(TelegramDelivery))).all() == []


@pytest.mark.asyncio
async def test_stale_sending_reclaimed_non_stale_not(async_session: AsyncSession, monkeypatch) -> None:
    account = await _seed_account(async_session)
    bot = await _seed_integration(async_session, name="Bot")
    await _route(async_session, account.id, bot.id)
    txn = await _transaction(async_session, account.id, message_id="<stale@example.com>")

    stale = TelegramDelivery(
        transaction_id=txn.id,
        telegram_integration_id=bot.id,
        status="sending",
        attempt_count=1,
        last_attempt_at=datetime.now(UTC) - TELEGRAM_SENDING_STALE_AFTER - timedelta(seconds=5),
    )
    async_session.add(stale)
    await async_session.commit()

    sends = []

    async def fake_send(_token: str, group_id: str, _text: str):
        sends.append("sent")
        return {"ok": True, "result": {"message_id": 3}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    assert sends == ["sent"]
    await async_session.refresh(stale)
    assert stale.status == "sent"
    assert stale.attempt_count == 2

    # Non-stale sending is not reclaimed.
    fresh = TelegramDelivery(
        transaction_id=txn.id,
        telegram_integration_id=(
            await _seed_integration(async_session, name="Other", group_id="-4002")
        ).id,
        status="sending",
        attempt_count=1,
        last_attempt_at=datetime.now(UTC),
    )
    # Need route for the other bot? Not required for eligibility of existing delivery row.
    async_session.add(fresh)
    await async_session.commit()
    sends.clear()
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    assert sends == []
    await async_session.refresh(fresh)
    assert fresh.status == "sending"


@pytest.mark.asyncio
async def test_concurrent_dispatch_does_not_double_send(async_session: AsyncSession, monkeypatch) -> None:
    """Sequential claim simulation: second caller sees non-stale sending and skips."""
    account = await _seed_account(async_session)
    bot = await _seed_integration(async_session, name="Bot")
    await _route(async_session, account.id, bot.id)
    txn = await _transaction(async_session, account.id, message_id="<race@example.com>")

    sends: list[str] = []

    async def recording_send(_token: str, group_id: str, _text: str):
        sends.append(group_id)
        return {"ok": True, "result": {"message_id": 11}}

    monkeypatch.setattr(telegram_service, "decrypt_secret", lambda _v: "token")
    monkeypatch.setattr(telegram_service, "telegram_send_message", recording_send)

    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)

    # Simulate a second caller while status is already sent — no second send.
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=True)

    # Simulate non-stale in-flight claim: second dispatch must not send again.
    delivery = (await async_session.scalars(select(TelegramDelivery))).one()
    delivery.status = "sending"
    delivery.last_attempt_at = datetime.now(UTC)
    delivery.attempt_count = 1
    await async_session.commit()
    sends.clear()
    await dispatch_transaction_notifications(txn.id, create_missing_destinations=False)
    assert sends == []

    deliveries = (await async_session.scalars(select(TelegramDelivery))).all()
    assert len(deliveries) == 1
    assert deliveries[0].status == "sending"


def test_rollup_rules() -> None:
    now = datetime.now(UTC)
    earlier = now - timedelta(minutes=1)
    all_sent = [
        TelegramDelivery(status="sent", sent_at=earlier, last_attempt_at=earlier),
        TelegramDelivery(status="sent", sent_at=now, last_attempt_at=now),
    ]
    rollup = compute_transaction_telegram_rollup(all_sent)
    assert rollup["telegram_status"] == "sent"
    assert rollup["telegram_sent_at"] == now
    assert rollup["telegram_last_error"] is None

    mixed = [
        TelegramDelivery(status="sent", sent_at=now, last_attempt_at=now),
        TelegramDelivery(status="failed", last_attempt_at=now, last_error="x"),
    ]
    rollup = compute_transaction_telegram_rollup(mixed)
    assert rollup["telegram_status"] == "failed"
    assert rollup["telegram_sent_at"] == now
    assert "1 of 2" in str(rollup["telegram_last_error"])

    sending = [TelegramDelivery(status="sending", last_attempt_at=now)]
    assert compute_transaction_telegram_rollup(sending)["telegram_status"] == "sending"

    empty = compute_transaction_telegram_rollup([])
    assert empty["telegram_status"] == "not_applicable"
    assert empty["telegram_last_error"] == NO_DESTINATIONS_REASON


def test_delivery_eligibility_and_message_id_helpers() -> None:
    now = datetime.now(UTC)
    pending = TelegramDelivery(status="pending", attempt_count=0)
    asserted = TelegramDelivery(status="pending", attempt_count=1)
    failed = TelegramDelivery(status="failed", attempt_count=1)
    sent = TelegramDelivery(status="sent", attempt_count=1)
    fresh_sending = TelegramDelivery(status="sending", attempt_count=1, last_attempt_at=now)
    stale_sending = TelegramDelivery(
        status="sending",
        attempt_count=1,
        last_attempt_at=now - TELEGRAM_SENDING_STALE_AFTER - timedelta(seconds=1),
    )
    assert is_delivery_eligible_for_normal_dispatch(pending, now=now)
    assert not is_delivery_eligible_for_normal_dispatch(asserted, now=now)
    assert not is_delivery_eligible_for_normal_dispatch(failed, now=now)
    assert not is_delivery_eligible_for_normal_dispatch(sent, now=now)
    assert not is_delivery_eligible_for_normal_dispatch(fresh_sending, now=now)
    assert is_delivery_eligible_for_normal_dispatch(stale_sending, now=now)

    assert extract_telegram_message_id({"ok": True, "result": {"message_id": 99}}) == "99"
    assert extract_telegram_message_id({"ok": True, "result": {}}) is None

    assert "secret-token" not in sanitize_telegram_error(RuntimeError("bad secret-token"), "secret-token")
    assert not should_send_transaction_notification(
        Transaction(
            payment_account_id=1,
            direction=Direction.IN,
            amount_cents=1,
            gmail_message_id="x",
            received_at=now,
            telegram_status="failed",
        )
    )


@pytest.mark.asyncio
async def test_reading_transaction_does_not_create_deliveries(async_session: AsyncSession) -> None:
    account = await _seed_account(async_session)
    bot = await _seed_integration(async_session, name=DEFAULT_TELEGRAM_INTEGRATION_NAME)
    await _route(async_session, account.id, bot.id)
    txn = await _transaction(async_session, account.id, message_id="<read@example.com>", status="sent")

    loaded = await async_session.get(Transaction, txn.id)
    assert loaded is not None
    assert (await async_session.scalars(select(TelegramDelivery))).all() == []
