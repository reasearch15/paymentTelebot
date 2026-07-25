from app.services import gmail_imap
from app.services.gmail_imap import GmailImapClient, IdleEvent


class FakeSocket:
    def __init__(self) -> None:
        self.timeout = 20
        self._peer = ("imap.gmail.com", 993)

    def settimeout(self, value):
        self.timeout = value

    def getpeername(self):
        return self._peer

    def pending(self):
        return 0

    def fileno(self):
        return 1


class FakeImap:
    closed = False
    logged_out = False
    search_queries = []
    sent = []
    lines = []
    tagnum = 0

    def __init__(self, host: str, port: int, timeout: int) -> None:
        assert host == "imap.gmail.com"
        assert port == 993
        assert timeout == 20
        self._sock = FakeSocket()
        self.tagpre = b"TAG"
        self.tagnum = 0

    def login(self, gmail_address: str, app_password: str):
        assert gmail_address == "user@gmail.com"
        assert app_password == "app-password"
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = True):
        assert mailbox == "INBOX"
        assert readonly is True
        return "OK", [b"selected"]

    def uid(self, command: str, *args):
        if command == "search":
            FakeImap.search_queries.append(args[1])
            return "OK", [b"43 44 45"]
        if command == "fetch":
            uid = args[0]
            return "OK", [(b"RFC822", b"Subject: Test " + uid + b"\r\n\r\nBody")]
        raise AssertionError(command)

    def _new_tag(self):
        self.tagnum += 1
        return self.tagpre + str(self.tagnum).encode("ascii")

    def send(self, data: bytes):
        FakeImap.sent.append(data)

    def _get_line(self):
        if not FakeImap.lines:
            raise OSError("no more lines")
        return FakeImap.lines.pop(0)

    def socket(self):
        return self._sock

    def close(self):
        FakeImap.closed = True

    def logout(self):
        FakeImap.logged_out = True
        return "OK", [b"bye"]


def test_fetches_oldest_uid_batch_and_logs_out(monkeypatch) -> None:
    FakeImap.closed = False
    FakeImap.logged_out = False
    FakeImap.search_queries = []
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)

    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        messages = client.fetch_new_messages(42, 1)

    assert len(messages) == 1
    assert messages[0].gmail_uid == 43
    assert b"Subject: Test 43" in messages[0].raw_message
    assert FakeImap.search_queries == ["UID 43:*"]
    assert FakeImap.closed is True
    assert FakeImap.logged_out is True


def test_first_poll_starts_at_uid_one(monkeypatch) -> None:
    FakeImap.closed = False
    FakeImap.logged_out = False
    FakeImap.search_queries = []
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)

    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        messages = client.fetch_new_messages(0, 1)

    assert len(messages) == 1
    assert FakeImap.search_queries == ["UID 1:*"]


def test_search_uids_since_filters_checkpoint(monkeypatch) -> None:
    FakeImap.search_queries = []
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)

    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        uids = client.search_uids_since(42)

    assert uids == [43, 44, 45]
    assert FakeImap.search_queries == ["UID 43:*"]


def test_idle_start_wait_done(monkeypatch) -> None:
    FakeImap.sent = []
    FakeImap.lines = [b"+ idling", b"* 10 EXISTS", b"TAG1 OK IDLE terminated"]
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)

    select_calls = {"n": 0}

    def fake_select(readers, writers, errs, timeout=None):
        select_calls["n"] += 1
        # First wait: readable. Subsequent drain/health selects: not readable.
        if select_calls["n"] == 1:
            return ([readers[0]], [], [])
        return ([], [], [])

    monkeypatch.setattr(gmail_imap.select, "select", fake_select)

    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        client.select_mailbox("INBOX")
        client.idle_start("INBOX")
        assert client.is_idling is True
        assert client.idle_health_ok() is True
        event = client.idle_wait(1)
        assert isinstance(event, IdleEvent)
        assert event.lines == (b"* 10 EXISTS",)
        client.idle_done()
        assert client.is_idling is False

    assert FakeImap.sent[0].startswith(b"TAG1 IDLE")
    assert b"DONE\r\n" in FakeImap.sent


def test_idle_health_ok_false_when_not_idling(monkeypatch) -> None:
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)
    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        assert client.idle_health_ok() is False
