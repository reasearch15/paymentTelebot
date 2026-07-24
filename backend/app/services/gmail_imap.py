import imaplib
import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import settings

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
MAILBOX = "INBOX"
logger = logging.getLogger(__name__)


class GmailConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawEmailFetch:
    gmail_uid: int
    raw_message: bytes


@dataclass(frozen=True)
class ConnectionTestResult:
    success: bool
    message: str
    checked_at: datetime


class GmailImapClient:
    def __init__(self, gmail_address: str, app_password: str, timeout_seconds: int | None = None) -> None:
        self.gmail_address = gmail_address
        self.app_password = app_password
        self.timeout_seconds = timeout_seconds or settings.gmail_connection_timeout_seconds
        self.connection: imaplib.IMAP4_SSL | None = None
        self._previous_timeout: float | None = None

    def __enter__(self) -> "GmailImapClient":
        self._previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.timeout_seconds)
        try:
            self.connection = imaplib.IMAP4_SSL(GMAIL_HOST, GMAIL_PORT, timeout=self.timeout_seconds)
            self._check(self.connection.login(self.gmail_address, self.app_password), "login")
            return self
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
            self.close()
            raise GmailConnectionError(safe_imap_error(exc)) from exc

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        connection = self.connection
        self.connection = None
        try:
            if connection is not None:
                try:
                    connection.close()
                except imaplib.IMAP4.error:
                    pass
                connection.logout()
        finally:
            socket.setdefaulttimeout(self._previous_timeout)

    def select_mailbox(self, mailbox: str = MAILBOX) -> None:
        self._check(self._require_connection().select(mailbox, readonly=True), "select mailbox")

    def fetch_new_messages(self, since_uid: int, batch_size: int, mailbox: str = MAILBOX) -> list[RawEmailFetch]:
        connection = self._require_connection()
        self.select_mailbox(mailbox)
        search_start_uid = max(1, since_uid - settings.gmail_uid_lookback_count + 1)
        search_query = f"UID {search_start_uid}:*"
        logger.info(
            "Gmail poll mailbox=%s checkpoint=%s search=%s",
            safe_log_text(mailbox),
            since_uid,
            search_query,
        )
        typ, data = connection.uid("search", None, search_query)
        self._check((typ, data), "search")
        uid_values = data[0].split() if data and data[0] else []
        if len(uid_values) > batch_size:
            uid_values = uid_values[-batch_size:]
        logger.info("Gmail search returned UIDs=%s", [int(uid) for uid in uid_values])
        fetches: list[RawEmailFetch] = []
        for uid_bytes in uid_values:
            uid = int(uid_bytes)
            logger.debug("Gmail fetching UID=%s", uid)
            typ, fetch_data = connection.uid("fetch", uid_bytes, "(RFC822)")
            self._check((typ, fetch_data), "fetch")
            raw_message = first_message_bytes(fetch_data)
            if raw_message is not None:
                fetches.append(RawEmailFetch(gmail_uid=uid, raw_message=raw_message))
            else:
                logger.info("Gmail UID=%s skipped: no RFC822 bytes returned", uid)
        return fetches

    def _require_connection(self) -> imaplib.IMAP4_SSL:
        if self.connection is None:
            raise GmailConnectionError("Gmail IMAP connection is not open.")
        return self.connection

    def _check(self, result: tuple[str, list[bytes] | list[tuple[bytes, bytes]]], action: str) -> None:
        typ, data = result
        if typ != "OK":
            detail = data[0].decode("utf-8", errors="replace") if data else action
            raise GmailConnectionError(f"Gmail IMAP {action} failed: {detail}")


def first_message_bytes(fetch_data: list[object]) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def safe_imap_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "Gmail IMAP connection timed out."
    message = str(exc).strip()
    if not message:
        return "Gmail IMAP connection failed."
    return message.replace("\r", " ").replace("\n", " ")[:300]


def safe_log_text(value: object, max_length: int = 300) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text.encode("ascii", errors="backslashreplace").decode("ascii")[:max_length]


def test_gmail_connection(gmail_address: str, app_password: str) -> ConnectionTestResult:
    checked_at = datetime.now(UTC)
    try:
        with GmailImapClient(gmail_address, app_password) as client:
            client.select_mailbox(MAILBOX)
    except GmailConnectionError as exc:
        return ConnectionTestResult(success=False, message=str(exc), checked_at=checked_at)
    return ConnectionTestResult(success=True, message="Gmail IMAP connection succeeded.", checked_at=checked_at)
