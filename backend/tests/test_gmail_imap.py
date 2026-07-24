from app.services import gmail_imap
from app.services.gmail_imap import GmailImapClient


class FakeImap:
    closed = False
    logged_out = False

    def __init__(self, host: str, port: int, timeout: int) -> None:
        assert host == "imap.gmail.com"
        assert port == 993
        assert timeout == 20

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
            assert args == (None, "UID 18:*")
            return "OK", [b"43 44"]
        if command == "fetch":
            uid = args[0]
            return "OK", [(b"RFC822", b"Subject: Test " + uid + b"\r\n\r\nBody")]
        raise AssertionError(command)

    def close(self):
        FakeImap.closed = True

    def logout(self):
        FakeImap.logged_out = True
        return "OK", [b"bye"]


def test_fetches_uid_batch_and_logs_out(monkeypatch) -> None:
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap)

    with GmailImapClient("user@gmail.com", "app-password", timeout_seconds=20) as client:
        messages = client.fetch_new_messages(42, 1)

    assert len(messages) == 1
    assert messages[0].gmail_uid == 44
    assert b"Subject: Test 44" in messages[0].raw_message
    assert FakeImap.closed is True
    assert FakeImap.logged_out is True
