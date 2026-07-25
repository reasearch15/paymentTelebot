import imaplib
import logging
import select
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

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


@dataclass(frozen=True)
class IdleEvent:
    """Server push observed while waiting in IMAP IDLE."""

    lines: tuple[bytes, ...]


class GmailImapClient:
    def __init__(self, gmail_address: str, app_password: str, timeout_seconds: int | None = None) -> None:
        self.gmail_address = gmail_address
        self.app_password = app_password
        self.timeout_seconds = timeout_seconds or settings.gmail_connection_timeout_seconds
        self.connection: imaplib.IMAP4_SSL | None = None
        self._previous_timeout: float | None = None
        self._idling = False
        self._idle_tag: bytes | None = None
        self._mailbox_selected: str | None = None

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
        was_idling = self._idling
        self.connection = None
        self._idling = False
        self._idle_tag = None
        self._mailbox_selected = None
        try:
            if connection is not None:
                if was_idling:
                    try:
                        connection.send(b"DONE\r\n")
                    except OSError:
                        pass
                try:
                    connection.close()
                except imaplib.IMAP4.error:
                    pass
                try:
                    connection.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
        finally:
            socket.setdefaulttimeout(self._previous_timeout)

    def select_mailbox(self, mailbox: str = MAILBOX) -> None:
        self._ensure_not_idling()
        self._check(self._require_connection().select(mailbox, readonly=True), "select mailbox")
        self._mailbox_selected = mailbox

    def search_uids_since(self, since_uid: int, mailbox: str = MAILBOX) -> list[int]:
        """Search for UIDs strictly after the checkpoint. Does not fetch bodies."""
        self._ensure_not_idling()
        connection = self._require_connection()
        if self._mailbox_selected != mailbox:
            self.select_mailbox(mailbox)
        search_start_uid = since_uid + 1 if since_uid > 0 else 1
        search_query = f"UID {search_start_uid}:*"
        typ, data = connection.uid("search", None, search_query)
        self._check((typ, data), "search")
        uid_values = data[0].split() if data and data[0] else []
        uids = [int(uid) for uid in uid_values]
        # IMAP UID n:* can resurface the highest existing UID when n is past the max.
        return [uid for uid in uids if uid > since_uid]

    def fetch_uids(self, uids: list[int], batch_size: int | None = None) -> list[RawEmailFetch]:
        """Fetch RFC822 bodies for the given UIDs in ascending order (oldest first)."""
        self._ensure_not_idling()
        connection = self._require_connection()
        limit = batch_size if batch_size is not None else settings.gmail_read_batch_size
        ordered = sorted(uid for uid in uids if uid > 0)
        if len(ordered) > limit:
            ordered = ordered[:limit]
        fetches: list[RawEmailFetch] = []
        for uid in ordered:
            typ, fetch_data = connection.uid("fetch", str(uid).encode("ascii"), "(RFC822)")
            self._check((typ, fetch_data), "fetch")
            raw_message = first_message_bytes(fetch_data)
            if raw_message is not None:
                fetches.append(RawEmailFetch(gmail_uid=uid, raw_message=raw_message))
            else:
                logger.info("Gmail UID=%s skipped: no RFC822 bytes returned", uid)
        return fetches

    def fetch_new_messages(self, since_uid: int, batch_size: int, mailbox: str = MAILBOX) -> list[RawEmailFetch]:
        search_start_uid = since_uid + 1 if since_uid > 0 else 1
        search_query = f"UID {search_start_uid}:*"
        logger.info(
            "Gmail poll mailbox=%s checkpoint=%s search=%s",
            safe_log_text(mailbox),
            since_uid,
            search_query,
        )
        uids = self.search_uids_since(since_uid, mailbox)
        if len(uids) > batch_size:
            uids = uids[:batch_size]
        logger.info("Gmail search returned UIDs=%s", uids)
        return self.fetch_uids(uids, batch_size=batch_size)

    def idle_start(self, mailbox: str = MAILBOX) -> None:
        """Enter IMAP IDLE. Caller must later call idle_done() or close()."""
        self._ensure_not_idling()
        connection = self._require_connection()
        if self._mailbox_selected != mailbox:
            self.select_mailbox(mailbox)
        # IDLE waits can exceed the short login socket timeout.
        sock = connection.socket()
        sock.settimeout(None)
        tag = connection._new_tag()
        self._idle_tag = tag if isinstance(tag, bytes) else str(tag).encode("ascii")
        try:
            connection.send(self._idle_tag + b" IDLE\r\n")
            while True:
                line = connection._get_line()
                if line.startswith(b"+"):
                    self._idling = True
                    return
                # Drain any untagged lines before the continuation.
                if line.startswith(self._idle_tag + b" "):
                    raise GmailConnectionError(f"Gmail IMAP IDLE rejected: {safe_log_text(line)}")
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
            self._idling = False
            self._idle_tag = None
            raise GmailConnectionError(safe_imap_error(exc)) from exc

    def idle_wait(self, timeout_seconds: float) -> IdleEvent | None:
        """
        Wait up to timeout_seconds for server push while IDLE is active.

        Returns IdleEvent when the server sent data, or None on timeout.
        Does not exit IDLE.
        """
        if not self._idling:
            raise GmailConnectionError("Gmail IMAP IDLE is not active.")
        connection = self._require_connection()
        sock = connection.socket()
        deadline = monotonic() + max(0.0, timeout_seconds)
        lines: list[bytes] = []

        def _socket_pending() -> bool:
            pending = getattr(sock, "pending", None)
            try:
                return bool(pending()) if callable(pending) else False
            except OSError:
                return False

        try:
            while True:
                remaining = deadline - monotonic()
                if lines:
                    # Drain any immediately available follow-up lines, then return.
                    if not _socket_pending():
                        ready, _, _ = select.select([sock], [], [], 0)
                        if not ready:
                            break
                    line = connection._get_line()
                    lines.append(line)
                    continue

                if remaining <= 0:
                    return None

                if _socket_pending():
                    line = connection._get_line()
                    lines.append(line)
                    continue

                ready, _, _ = select.select([sock], [], [], remaining)
                if not ready:
                    return None
                line = connection._get_line()
                lines.append(line)
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
            self._idling = False
            raise GmailConnectionError(safe_imap_error(exc)) from exc

        return IdleEvent(lines=tuple(lines)) if lines else None

    def idle_done(self) -> None:
        """Exit IDLE and restore a normal tagged command session."""
        if not self._idling:
            return
        connection = self._require_connection()
        tag = self._idle_tag
        try:
            connection.send(b"DONE\r\n")
            while True:
                line = connection._get_line()
                if tag is not None and line.startswith(tag + b" "):
                    if not line.startswith(tag + b" OK"):
                        raise GmailConnectionError(f"Gmail IMAP IDLE DONE failed: {safe_log_text(line)}")
                    break
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
            self._idling = False
            self._idle_tag = None
            raise GmailConnectionError(safe_imap_error(exc)) from exc
        finally:
            self._idling = False
            self._idle_tag = None
            try:
                connection.socket().settimeout(self.timeout_seconds)
            except OSError:
                pass

    def idle_health_ok(self) -> bool:
        """
        Lightweight IDLE health check: connection present, IDLE active, socket alive.

        Does NOT perform a mailbox poll or send IMAP commands.
        """
        if self.connection is None or not self._idling:
            return False
        try:
            sock = self.connection.socket()
            sock.getpeername()
            return True
        except OSError:
            return False

    @property
    def is_idling(self) -> bool:
        return self._idling

    @property
    def is_connected(self) -> bool:
        if self.connection is None:
            return False
        try:
            self.connection.socket().getpeername()
            return True
        except OSError:
            return False

    def _ensure_not_idling(self) -> None:
        if self._idling:
            raise GmailConnectionError("Gmail IMAP command not allowed while IDLE is active.")

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
