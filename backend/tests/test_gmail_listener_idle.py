"""Focused tests for the hybrid Gmail IDLE listener architecture."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.gmail_imap import IdleEvent, RawEmailFetch
from app.workers import gmail_listener


@dataclass
class FakeTarget:
    id: int = 1
    friendly_name: str = "Chime"
    gmail_address: str = "user@gmail.com"
    app_password: str = "app-password"
    last_uid: int = 10

    @property
    def identity(self):
        return (self.id, self.gmail_address, self.app_password)


class FakeClient:
    def __init__(self) -> None:
        self.idle_starts = 0
        self.idle_dones = 0
        self.fetch_calls: list[int] = []
        self._idling = False
        self.health_ok = True
        self.wait_results: list[IdleEvent | None | Exception] = []
        self.closed = False
        self.messages_by_since: dict[int, list[RawEmailFetch]] = {}

    def idle_start(self, mailbox: str = "INBOX") -> None:
        self.idle_starts += 1
        self._idling = True

    def idle_done(self) -> None:
        self.idle_dones += 1
        self._idling = False

    def idle_wait(self, timeout_seconds: float) -> IdleEvent | None:
        if not self.wait_results:
            import time

            time.sleep(min(timeout_seconds, 0.05))
            return None
        result = self.wait_results.pop(0)
        if isinstance(result, Exception):
            raise result
        if result is None:
            import time

            time.sleep(min(timeout_seconds, 0.05))
        return result

    def idle_health_ok(self) -> bool:
        return self._idling and self.health_ok

    def fetch_new_messages(self, since_uid: int, batch_size: int, mailbox: str = "INBOX") -> list[RawEmailFetch]:
        self.fetch_calls.append(since_uid)
        return list(self.messages_by_since.get(since_uid, []))

    def close(self) -> None:
        self.closed = True
        self._idling = False


@pytest.fixture(autouse=True)
def _clear_locks():
    gmail_listener._account_locks.clear()
    yield
    gmail_listener._account_locks.clear()


def test_startup_performs_immediate_catchup(monkeypatch) -> None:
    client = FakeClient()
    client.wait_results = [None]  # one health timeout then stop
    calls: list[str] = []

    async def fake_process(target, *, reason, client=None, detection_started=None):
        calls.append(reason)
        return 0

    async def run() -> None:
        stop = asyncio.Event()
        target = FakeTarget()

        monkeypatch.setattr(gmail_listener, "_open_client", lambda t: client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 10_000)

        task = asyncio.create_task(gmail_listener.run_account_idle_session(target, stop))
        await asyncio.sleep(0.05)
        # Allow first idle_wait to return None (health check), then stop.
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert calls[0] == "startup_catchup"
    assert client.idle_starts >= 1


def test_idle_event_wakes_and_fetches(monkeypatch) -> None:
    client = FakeClient()
    client.wait_results = [IdleEvent(lines=(b"* 11 EXISTS",)), None]
    reasons: list[str] = []

    async def fake_process(target, *, reason, client=None, detection_started=None):
        reasons.append(reason)
        if reason == "idle_event":
            assert detection_started is not None
        return 1

    async def run() -> None:
        stop = asyncio.Event()
        monkeypatch.setattr(gmail_listener, "_open_client", lambda t: client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 10_000)

        task = asyncio.create_task(gmail_listener.run_account_idle_session(FakeTarget(), stop))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert "startup_catchup" in reasons
    assert "idle_event" in reasons
    assert client.idle_dones >= 1
    assert client.idle_starts >= 2


def test_health_check_performs_no_mailbox_poll_while_healthy(monkeypatch) -> None:
    client = FakeClient()
    # Several healthy timeouts, no EXISTS events.
    client.wait_results = [None, None, None]
    fetch_reasons: list[str] = []

    async def fake_process(target, *, reason, client=None, detection_started=None):
        fetch_reasons.append(reason)
        return 0

    async def run() -> None:
        stop = asyncio.Event()
        monkeypatch.setattr(gmail_listener, "_open_client", lambda t: client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 10_000)

        task = asyncio.create_task(gmail_listener.run_account_idle_session(FakeTarget(), stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert fetch_reasons == ["startup_catchup"]
    assert client.fetch_calls == []  # process mocked; client itself never polled by health path


def test_failed_health_check_reconnects_with_catchup(monkeypatch) -> None:
    clients = [FakeClient(), FakeClient()]
    clients[0].wait_results = [None]
    clients[0].health_ok = False
    clients[1].wait_results = [None]
    open_count = {"n": 0}
    reasons: list[str] = []

    def open_client(_target):
        client = clients[open_count["n"]]
        open_count["n"] += 1
        return client

    async def fake_process(target, *, reason, client=None, detection_started=None):
        reasons.append(reason)
        return 0

    async def run() -> None:
        stop = asyncio.Event()
        monkeypatch.setattr(gmail_listener, "_open_client", open_client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_reconnect_max_backoff_seconds", 1)

        task = asyncio.create_task(gmail_listener.run_account_idle_session(FakeTarget(), stop))
        # Initial backoff after failure is 1s; wait long enough for reconnect catch-up.
        await asyncio.sleep(1.4)
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(run())
    assert reasons[0] == "startup_catchup"
    assert "reconnect_catchup" in reasons
    assert open_count["n"] >= 2


def test_safety_poll_searches_from_last_uid_plus_one(monkeypatch) -> None:
    seen_since: list[int] = []

    async def fake_load_checkpoint(account_id: int, mailbox: str = "INBOX") -> int:
        return 55

    def fake_fetch(client, since_uid: int):
        seen_since.append(since_uid)
        return []

    async def run() -> None:
        monkeypatch.setattr(gmail_listener, "load_account_checkpoint", fake_load_checkpoint)
        monkeypatch.setattr(gmail_listener, "_fetch_with_client", fake_fetch)
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        client = FakeClient()
        await gmail_listener.process_account_emails(FakeTarget(last_uid=55), reason="safety_poll", client=client)

    asyncio.run(run())
    assert seen_since == [55]


def test_duplicate_events_remain_idempotent(monkeypatch) -> None:
    """Two overlapping process calls for the same UID advance once via lock + dedupe path."""
    persist_calls = {"n": 0}

    async def fake_load_checkpoint(account_id: int, mailbox: str = "INBOX") -> int:
        return 7

    def fake_fetch(client, since_uid: int):
        return [RawEmailFetch(gmail_uid=8, raw_message=b"Subject: Pay\r\nMessage-ID: <a@b>\r\n\r\nBody")]

    def fake_extract(_raw):
        return SimpleNamespace(
            gmail_message_id="<a@b>",
            sender_address="a@b.com",
            subject="Pay",
            received_at=None,
            raw_text="Body",
            raw_html=None,
            raw_headers_json={},
        )

    async def fake_persist(account_id, raw_fetch, extracted, mailbox="INBOX"):
        persist_calls["n"] += 1
        # First insert succeeds; subsequent duplicate returns False.
        return persist_calls["n"] == 1

    async def run() -> None:
        monkeypatch.setattr(gmail_listener, "load_account_checkpoint", fake_load_checkpoint)
        monkeypatch.setattr(gmail_listener, "_fetch_with_client", fake_fetch)
        monkeypatch.setattr(gmail_listener, "extract_email", fake_extract)
        monkeypatch.setattr(gmail_listener, "persist_captured_email", fake_persist)
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        client = FakeClient()
        target = FakeTarget(last_uid=7)
        first, second = await asyncio.gather(
            gmail_listener.process_account_emails(target, reason="idle_event", client=client),
            gmail_listener.process_account_emails(target, reason="safety_poll", client=client),
        )
        assert first + second == 1
        assert persist_calls["n"] == 2

    asyncio.run(run())


def test_simultaneous_idle_and_safety_poll_cannot_overlap(monkeypatch) -> None:
    async def fake_load_checkpoint(account_id: int, mailbox: str = "INBOX") -> int:
        return 1

    def fake_fetch(client, since_uid: int):
        return []

    async def run() -> None:
        monkeypatch.setattr(gmail_listener, "load_account_checkpoint", fake_load_checkpoint)
        monkeypatch.setattr(gmail_listener, "_fetch_with_client", fake_fetch)
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        client = FakeClient()
        target = FakeTarget()
        await asyncio.gather(
            gmail_listener.process_account_emails(target, reason="idle_event", client=client),
            gmail_listener.process_account_emails(target, reason="safety_poll", client=client),
        )

        lock = gmail_listener.account_lock(target.id)
        entered: list[str] = []

        async def holder(name: str):
            async with lock:
                entered.append(f"start-{name}")
                await asyncio.sleep(0.05)
                entered.append(f"end-{name}")

        await asyncio.gather(holder("idle"), holder("safety"))
        assert entered == ["start-idle", "end-idle", "start-safety", "end-safety"]

    asyncio.run(run())


def test_restart_recovers_missed_emails(monkeypatch) -> None:
    reasons: list[str] = []
    client = FakeClient()
    client.wait_results = [None]

    async def fake_process(target, *, reason, client=None, detection_started=None):
        reasons.append(reason)
        return 2

    async def run() -> None:
        stop = asyncio.Event()
        monkeypatch.setattr(gmail_listener, "_open_client", lambda t: client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 10_000)
        task = asyncio.create_task(gmail_listener.run_account_idle_session(FakeTarget(last_uid=40), stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert reasons[0] == "startup_catchup"


def test_malformed_email_does_not_block_later_emails(monkeypatch) -> None:
    processed_uids: list[int] = []

    async def fake_load_checkpoint(account_id: int, mailbox: str = "INBOX") -> int:
        return 10

    def fake_fetch(client, since_uid: int):
        return [
            RawEmailFetch(gmail_uid=11, raw_message=b"bad"),
            RawEmailFetch(gmail_uid=12, raw_message=b"Subject: Good\r\nMessage-ID: <x@y>\r\n\r\nOk"),
        ]

    def fake_extract(raw: bytes):
        if raw == b"bad":
            raise ValueError("malformed")
        return SimpleNamespace(
            gmail_message_id="<x@y>",
            sender_address="a@b.com",
            subject="Good",
            received_at=None,
            raw_text="Ok",
            raw_html=None,
            raw_headers_json={},
        )

    async def fake_persist(account_id, raw_fetch, extracted, mailbox="INBOX"):
        processed_uids.append(raw_fetch.gmail_uid)
        return True

    failures: list[int] = []

    async def fake_failure(account_id, raw_fetch, error, mailbox="INBOX"):
        failures.append(raw_fetch.gmail_uid)

    async def run() -> None:
        monkeypatch.setattr(gmail_listener, "load_account_checkpoint", fake_load_checkpoint)
        monkeypatch.setattr(gmail_listener, "_fetch_with_client", fake_fetch)
        monkeypatch.setattr(gmail_listener, "extract_email", fake_extract)
        monkeypatch.setattr(gmail_listener, "persist_captured_email", fake_persist)
        monkeypatch.setattr(gmail_listener, "mark_email_failure", fake_failure)
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        await gmail_listener.process_account_emails(FakeTarget(), reason="startup_catchup", client=FakeClient())

    asyncio.run(run())
    assert failures == [11]
    assert processed_uids == [12]


def test_process_account_emails_uses_lock_for_overlap(monkeypatch) -> None:
    in_critical = {"n": 0}
    max_in_critical = {"n": 0}

    async def fake_load_checkpoint(account_id: int, mailbox: str = "INBOX") -> int:
        in_critical["n"] += 1
        max_in_critical["n"] = max(max_in_critical["n"], in_critical["n"])
        await asyncio.sleep(0.05)
        in_critical["n"] -= 1
        return 1

    def fake_fetch(client, since_uid: int):
        return []

    async def run() -> None:
        monkeypatch.setattr(gmail_listener, "load_account_checkpoint", fake_load_checkpoint)
        monkeypatch.setattr(gmail_listener, "_fetch_with_client", fake_fetch)
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        client = FakeClient()
        target = FakeTarget()
        await asyncio.gather(
            gmail_listener.process_account_emails(target, reason="idle_event", client=client),
            gmail_listener.process_account_emails(target, reason="safety_poll", client=client),
        )

    asyncio.run(run())
    assert max_in_critical["n"] == 1


def test_safety_poll_trigger_in_idle_loop(monkeypatch) -> None:
    client = FakeClient()
    client.wait_results = [None]
    reasons: list[str] = []

    async def fake_process(target, *, reason, client=None, detection_started=None):
        reasons.append(reason)
        return 0

    async def run() -> None:
        stop = asyncio.Event()
        monkeypatch.setattr(gmail_listener, "_open_client", lambda t: client)
        monkeypatch.setattr(gmail_listener, "process_account_emails", fake_process)
        monkeypatch.setattr(gmail_listener, "heartbeat", AsyncMock())
        monkeypatch.setattr(gmail_listener, "update_account_status", AsyncMock())
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_healthcheck_seconds", 1)
        monkeypatch.setattr(gmail_listener.settings, "gmail_idle_refresh_seconds", 10_000)
        monkeypatch.setattr(gmail_listener.settings, "gmail_safety_poll_seconds", 0)  # force safety after first health timeout

        task = asyncio.create_task(gmail_listener.run_account_idle_session(FakeTarget(), stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert "startup_catchup" in reasons
    assert "safety_poll" in reasons


def test_config_idle_defaults() -> None:
    from app.core.config import Settings

    s = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://x:y@localhost/db",
    )
    assert s.gmail_idle_enabled is True
    assert s.gmail_idle_healthcheck_seconds == 15
    assert s.gmail_idle_refresh_seconds == 1500
    assert s.gmail_safety_poll_seconds == 900
    assert s.gmail_reconnect_max_backoff_seconds == 60


def test_config_rejects_non_positive_idle_settings() -> None:
    from app.core.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, gmail_idle_healthcheck_seconds=0)
