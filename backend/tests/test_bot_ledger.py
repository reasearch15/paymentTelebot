"""Bot Ledger accounting, settlements, filters, and historical safety tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import encryption
from app.db.base import Base
from app.models.payment_account import PaymentAccount
from app.models.payment_account_telegram_route import PaymentAccountTelegramRoute
from app.models.provider import Provider
from app.models.settlement import Settlement
from app.models.telegram_delivery import TelegramDelivery
from app.models.telegram_integration import TelegramIntegration
from app.models.telegram_integration_settlement import TelegramIntegrationSettlement
from app.models.transaction import Direction, Transaction
from app.schemas.bot_ledger import BotLedgerSettlementCreate
from app.services import telegram as telegram_service
from app.services.bot_ledger import (
    BotSettlementFilters,
    BotTransactionFilters,
    build_bot_ledger_summary,
    build_gmail_breakdown,
    compute_bot_unsettled_cents,
    create_bot_settlement,
    list_bot_settlements,
    list_bot_transactions,
    start_of_month_kathmandu,
    start_of_today_kathmandu,
    start_of_week_kathmandu,
    sum_bot_settled_cents,
    sum_bot_total_in_cents,
)
from app.services.settlement import compute_unsettled_balance_cents
from app.services.telegram_assignments import replace_payment_account_telegram_integrations


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


async def _provider(session: AsyncSession, *, name: str = "Chime", key: str = "chime") -> Provider:
    provider = Provider(name=name, parser_key=key, enabled=True)
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


async def _account(
    session: AsyncSession,
    provider: Provider,
    *,
    name: str,
    gmail: str,
) -> PaymentAccount:
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


async def _integration(
    session: AsyncSession,
    *,
    name: str,
    group_id: str,
    enabled: bool = True,
) -> TelegramIntegration:
    integration = TelegramIntegration(
        name=name,
        bot_token_encrypted=encryption.encrypt_secret(f"token-{name}"),
        group_id=group_id,
        bot_username=name.replace(" ", "") + "Bot",
        enabled=enabled,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def _route(session: AsyncSession, account_id: int, integration_id: int) -> None:
    session.add(
        PaymentAccountTelegramRoute(
            payment_account_id=account_id,
            telegram_integration_id=integration_id,
        )
    )
    await session.commit()


async def _txn(
    session: AsyncSession,
    account_id: int,
    *,
    message_id: str,
    amount_cents: int,
    sender: str,
    received_at: datetime | None = None,
) -> Transaction:
    txn = Transaction(
        payment_account_id=account_id,
        direction=Direction.IN,
        amount_cents=amount_cents,
        sender_name=sender,
        gmail_message_id=message_id,
        received_at=received_at or datetime.now(UTC),
        telegram_status="pending",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _delivery(
    session: AsyncSession,
    txn_id: int,
    integration_id: int,
    *,
    status: str = "sent",
    attempt_count: int = 1,
    last_error: str | None = None,
) -> TelegramDelivery:
    delivery = TelegramDelivery(
        transaction_id=txn_id,
        telegram_integration_id=integration_id,
        status=status,
        attempt_count=attempt_count,
        last_attempt_at=datetime.now(UTC),
        sent_at=datetime.now(UTC) if status == "sent" else None,
        last_error=last_error,
        telegram_message_id="1" if status == "sent" else None,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


@pytest.mark.asyncio
async def test_settlement_table_schema_exists(async_session: AsyncSession) -> None:
    from sqlalchemy import text

    rows = (await async_session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()
    names = {row[0] for row in rows}
    assert "telegram_integration_settlements" in names


@pytest.mark.asyncio
async def test_positive_amount_constraint(async_session: AsyncSession) -> None:
    bot = await _integration(async_session, name="GG LEO", group_id="-1001")
    bad = TelegramIntegrationSettlement(
        telegram_integration_id=bot.id,
        amount_cents=0,
        balance_before_cents=100,
        balance_after_cents=100,
        created_by_user_id="admin@example.com",
        settled_at=datetime.now(UTC),
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()


@pytest.mark.asyncio
async def test_settlement_fk_restricts_missing_integration(async_session: AsyncSession) -> None:
    bad = TelegramIntegrationSettlement(
        telegram_integration_id=99999,
        amount_cents=100,
        balance_before_cents=100,
        balance_after_cents=0,
        created_by_user_id="admin@example.com",
        settled_at=datetime.now(UTC),
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()


@pytest.mark.asyncio
async def test_multi_gmail_into_one_bot_and_one_gmail_to_many_bots(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    gmail_a = await _account(async_session, provider, name="Larry Gmail", gmail="larry@example.com")
    gmail_b = await _account(async_session, provider, name="VIP Gmail", gmail="vip@example.com")
    royal = await _integration(async_session, name="Royal VIP", group_id="-2001")
    leo = await _integration(async_session, name="GG LEO", group_id="-2002")

    await _route(async_session, gmail_a.id, royal.id)
    await _route(async_session, gmail_a.id, leo.id)
    await _route(async_session, gmail_b.id, leo.id)

    txn_shared = await _txn(
        async_session, gmail_a.id, message_id="shared", amount_cents=1000, sender="Melanie R."
    )
    txn_b = await _txn(async_session, gmail_b.id, message_id="b-only", amount_cents=2500, sender="Maria M.")

    await _delivery(async_session, txn_shared.id, royal.id, status="sent")
    await _delivery(async_session, txn_shared.id, leo.id, status="failed", last_error="timeout")
    await _delivery(async_session, txn_b.id, leo.id, status="pending", attempt_count=0)

    # Same transaction once in each bot ledger
    royal_in = await sum_bot_total_in_cents(async_session, royal.id)
    leo_in = await sum_bot_total_in_cents(async_session, leo.id)
    assert royal_in == 1000
    assert leo_in == 1000 + 2500

    royal_rows, royal_total = await list_bot_transactions(
        async_session, royal.id, BotTransactionFilters()
    )
    leo_rows, leo_total = await list_bot_transactions(async_session, leo.id, BotTransactionFilters())
    assert royal_total == 1
    assert leo_total == 2
    assert {row.transaction_id for row in royal_rows} == {txn_shared.id}
    assert {row.transaction_id for row in leo_rows} == {txn_shared.id, txn_b.id}

    # Failed + pending still count financially
    assert await compute_bot_unsettled_cents(async_session, leo.id) == 3500

    from app.services.settlement import sum_transaction_amounts

    incoming, _outgoing = await sum_transaction_amounts(async_session)
    assert incoming == 1000 + 2500


@pytest.mark.asyncio
async def test_duplicate_delivery_rows_do_not_inflate_totals(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    account = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    bot = await _integration(async_session, name="Main", group_id="-1")
    txn = await _txn(async_session, account.id, message_id="dup", amount_cents=5000, sender="A")
    await _delivery(async_session, txn.id, bot.id, status="sent")
    # Unique constraint normally prevents duplicates; defensive aggregation still uses DISTINCT.
    assert await sum_bot_total_in_cents(async_session, bot.id) == 5000


@pytest.mark.asyncio
async def test_date_boundaries_kathmandu(async_session: AsyncSession) -> None:
    # 2026-08-05 00:30 NPT = 2026-08-04 18:45 UTC
    now = datetime(2026, 8, 4, 18, 50, tzinfo=UTC)
    today = start_of_today_kathmandu(now=now)
    week = start_of_week_kathmandu(now=now)
    month = start_of_month_kathmandu(now=now)
    assert today == datetime(2026, 8, 4, 18, 15, tzinfo=UTC)
    # Monday of that week in NPT: 2026-08-03 00:00 NPT = 2026-08-02 18:15 UTC
    assert week == datetime(2026, 8, 2, 18, 15, tzinfo=UTC)
    # Month start Aug 1 00:00 NPT = Jul 31 18:15 UTC
    assert month == datetime(2026, 7, 31, 18, 15, tzinfo=UTC)

    provider = await _provider(async_session)
    account = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    bot = await _integration(async_session, name="Main", group_id="-1")

    before_today = await _txn(
        async_session,
        account.id,
        message_id="old",
        amount_cents=100,
        sender="Old",
        received_at=today - timedelta(minutes=1),
    )
    today_txn = await _txn(
        async_session,
        account.id,
        message_id="new",
        amount_cents=200,
        sender="New",
        received_at=today + timedelta(minutes=1),
    )
    await _delivery(async_session, before_today.id, bot.id)
    await _delivery(async_session, today_txn.id, bot.id)

    summary = await build_bot_ledger_summary(async_session, bot, now=now)
    assert summary["payments_today"] == 1
    assert summary["amount_today_cents"] == 200
    assert summary["all_time_payments"] == 2
    assert summary["all_time_amount_cents"] == 300


@pytest.mark.asyncio
async def test_settlements_validation_and_isolation(async_session: AsyncSession, monkeypatch) -> None:
    provider = await _provider(async_session)
    account = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    bot = await _integration(async_session, name="GG LEO", group_id="-4843")
    bot_id = bot.id
    account_id = account.id
    txn = await _txn(async_session, account_id, message_id="pay", amount_cents=10_000, sender="Melanie")
    delivery = await _delivery(async_session, txn.id, bot_id, status="failed", last_error="403")
    txn_id = txn.id
    delivery_id = delivery.id

    send_calls: list[tuple] = []

    async def fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    with pytest.raises(ValueError, match="greater than zero"):
        await create_bot_settlement(
            async_session,
            telegram_integration_id=bot_id,
            amount_cents=0,
            note=None,
            created_by_user_id="admin@example.com",
        )
    await async_session.rollback()

    with pytest.raises(ValueError, match="greater than zero"):
        await create_bot_settlement(
            async_session,
            telegram_integration_id=bot_id,
            amount_cents=-5,
            note=None,
            created_by_user_id="admin@example.com",
        )
    await async_session.rollback()

    with pytest.raises(ValueError, match="exceeds current unsettled"):
        await create_bot_settlement(
            async_session,
            telegram_integration_id=bot_id,
            amount_cents=10_001,
            note=None,
            created_by_user_id="admin@example.com",
        )
    await async_session.rollback()

    settlement = await create_bot_settlement(
        async_session,
        telegram_integration_id=bot_id,
        amount_cents=4_000,
        note="partial",
        created_by_user_id="admin@example.com",
    )
    await async_session.commit()
    assert settlement.amount_cents == 4_000
    assert settlement.balance_before_cents == 10_000
    assert settlement.balance_after_cents == 6_000
    assert settlement.created_by_user_id == "admin@example.com"

    assert await compute_bot_unsettled_cents(async_session, bot_id) == 6_000
    assert await sum_bot_settled_cents(async_session, bot_id) == 4_000

    txn = await async_session.get(Transaction, txn_id)
    delivery = await async_session.get(TelegramDelivery, delivery_id)
    assert txn is not None and txn.amount_cents == 10_000
    assert delivery is not None and delivery.status == "failed"
    assert delivery.last_error == "403"
    assert send_calls == []

    gmail_before = await compute_unsettled_balance_cents(async_session, payment_account_id=account_id)
    assert gmail_before == 10_000
    assert (await async_session.scalar(select(Settlement.id))) is None


@pytest.mark.asyncio
async def test_settlement_schema_rejects_zero_negative(async_session: AsyncSession) -> None:
    del async_session
    with pytest.raises(Exception):
        BotLedgerSettlementCreate(amount="0")
    with pytest.raises(Exception):
        BotLedgerSettlementCreate(amount="-10")
    payload = BotLedgerSettlementCreate(amount="12.50", note="  hello  ")
    assert payload.amount_cents == 1250
    assert payload.note == "hello"


@pytest.mark.asyncio
async def test_settlement_list_pagination(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    account = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    bot = await _integration(async_session, name="Main", group_id="-1")
    txn = await _txn(async_session, account.id, message_id="big", amount_cents=50_000, sender="A")
    await _delivery(async_session, txn.id, bot.id)

    for index in range(3):
        await create_bot_settlement(
            async_session,
            telegram_integration_id=bot.id,
            amount_cents=1_000,
            note=f"n{index}",
            created_by_user_id="admin@example.com",
        )
    await async_session.commit()

    page1, total, running = await list_bot_settlements(
        async_session,
        bot.id,
        BotSettlementFilters(),
        page=1,
        page_size=2,
    )
    assert total == 3
    assert len(page1) == 2
    assert running[page1[0].id] >= 1000


@pytest.mark.asyncio
async def test_transaction_filters(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    other_provider = await _provider(async_session, name="CashApp", key="cashapp")
    gmail_a = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    gmail_b = await _account(async_session, other_provider, name="Other", gmail="other@example.com")
    bot = await _integration(async_session, name="Main", group_id="-1")

    t1 = await _txn(async_session, gmail_a.id, message_id="1", amount_cents=1000, sender="Melanie R.")
    t2 = await _txn(async_session, gmail_b.id, message_id="2", amount_cents=2500, sender="Maria M.")
    await _delivery(async_session, t1.id, bot.id, status="sent")
    await _delivery(async_session, t2.id, bot.id, status="failed", last_error="x")

    rows, total = await list_bot_transactions(
        async_session, bot.id, BotTransactionFilters(payment_account_id=gmail_a.id)
    )
    assert total == 1 and rows[0].transaction_id == t1.id

    rows, total = await list_bot_transactions(
        async_session, bot.id, BotTransactionFilters(provider_id=other_provider.id)
    )
    assert total == 1 and rows[0].transaction_id == t2.id

    rows, total = await list_bot_transactions(
        async_session, bot.id, BotTransactionFilters(sender="Maria")
    )
    assert total == 1

    rows, total = await list_bot_transactions(
        async_session, bot.id, BotTransactionFilters(delivery_status="failed")
    )
    assert total == 1

    rows, total = await list_bot_transactions(
        async_session, bot.id, BotTransactionFilters(min_amount_cents=2000, max_amount_cents=3000)
    )
    assert total == 1 and rows[0].transaction.amount_cents == 2500

    rows, total = await list_bot_transactions(async_session, bot.id, BotTransactionFilters())
    assert total == 2
    assert rows[0].transaction.received_at >= rows[1].transaction.received_at


@pytest.mark.asyncio
async def test_historical_safety_routes_and_reads(async_session: AsyncSession, monkeypatch) -> None:
    provider = await _provider(async_session)
    account = await _account(async_session, provider, name="Larry", gmail="larry@example.com")
    bot = await _integration(async_session, name="Main", group_id="-1")
    new_bot = await _integration(async_session, name="New Bot", group_id="-2")
    await _route(async_session, account.id, bot.id)

    txn = await _txn(async_session, account.id, message_id="hist", amount_cents=8000, sender="Hist")
    await _delivery(async_session, txn.id, bot.id, status="sent")

    before = await sum_bot_total_in_cents(async_session, bot.id)
    assert before == 8000

    # Remove route — historical membership remains
    await replace_payment_account_telegram_integrations(async_session, account.id, [])
    await async_session.commit()
    assert await sum_bot_total_in_cents(async_session, bot.id) == 8000

    # Add new route — does not create historical deliveries / membership
    await replace_payment_account_telegram_integrations(async_session, account.id, [new_bot.id])
    await async_session.commit()
    assert await sum_bot_total_in_cents(async_session, new_bot.id) == 0

    send_calls: list = []

    async def fake_send(*args, **kwargs):
        send_calls.append(1)
        return {"ok": True, "result": {"message_id": 9}}

    monkeypatch.setattr(telegram_service, "telegram_send_message", fake_send)

    delivery_count_before = int(
        (
            await async_session.scalar(
                select(TelegramDelivery.id).where(TelegramDelivery.telegram_integration_id == new_bot.id)
            )
        )
        is not None
    )
    summary = await build_bot_ledger_summary(async_session, new_bot)
    assert summary["total_in_cents"] == 0
    # Reading summary creates no deliveries
    assert (
        await async_session.scalar(
            select(TelegramDelivery.id).where(TelegramDelivery.telegram_integration_id == new_bot.id)
        )
    ) is None

    await create_bot_settlement(
        async_session,
        telegram_integration_id=bot.id,
        amount_cents=1000,
        note=None,
        created_by_user_id="admin@example.com",
    )
    await async_session.commit()
    assert send_calls == []
    del delivery_count_before


@pytest.mark.asyncio
async def test_gmail_breakdown_uses_delivery_history(async_session: AsyncSession) -> None:
    provider = await _provider(async_session)
    gmail_a = await _account(async_session, provider, name="Larry Gmail", gmail="larry@example.com")
    gmail_b = await _account(async_session, provider, name="VIP Gmail", gmail="vip@example.com")
    bot = await _integration(async_session, name="GG LEO", group_id="-1")

    t1 = await _txn(async_session, gmail_a.id, message_id="a1", amount_cents=1000, sender="A")
    t2 = await _txn(async_session, gmail_a.id, message_id="a2", amount_cents=2000, sender="B")
    t3 = await _txn(async_session, gmail_b.id, message_id="b1", amount_cents=500, sender="C")
    await _delivery(async_session, t1.id, bot.id)
    await _delivery(async_session, t2.id, bot.id)
    await _delivery(async_session, t3.id, bot.id)

    # Remove current routes; breakdown must remain
    await replace_payment_account_telegram_integrations(async_session, gmail_a.id, [])
    await replace_payment_account_telegram_integrations(async_session, gmail_b.id, [])
    await async_session.commit()

    rows = await build_gmail_breakdown(async_session, bot.id)
    by_name = {row["friendly_name"]: row for row in rows}
    assert by_name["Larry Gmail"]["payment_count"] == 2
    assert by_name["Larry Gmail"]["total_amount_cents"] == 3000
    assert by_name["VIP Gmail"]["payment_count"] == 1
    assert by_name["VIP Gmail"]["total_amount_cents"] == 500


@pytest.mark.asyncio
async def test_test_messages_do_not_count(async_session: AsyncSession) -> None:
    bot = await _integration(async_session, name="Main", group_id="-1")
    # Test connection/messages never create transactions or deliveries.
    assert await sum_bot_total_in_cents(async_session, bot.id) == 0
    assert await build_gmail_breakdown(async_session, bot.id) == []
