from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

SAFE_HEADERS = (
    "Message-ID",
    "From",
    "To",
    "Subject",
    "Date",
    "Reply-To",
    "Return-Path",
    "Delivered-To",
)


@dataclass(frozen=True)
class ExtractedEmail:
    gmail_message_id: str | None
    sender_address: str | None
    subject: str | None
    received_at: datetime | None
    raw_text: str | None
    raw_html: str | None
    raw_headers_json: dict[str, str]


def decode_mime_header(value: str | None) -> str | None:
    if value is None:
        return None
    return str(make_header(decode_header(value)))


def decode_part_payload(message: EmailMessage) -> str | None:
    payload = message.get_payload(decode=True)
    if payload is None:
        text_payload = message.get_payload()
        return text_payload if isinstance(text_payload, str) else None

    charset = message.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def append_body(existing: str | None, value: str | None) -> str | None:
    if not value:
        return existing
    return f"{existing}\n\n{value}" if existing else value


def extract_email(raw_message: bytes) -> ExtractedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    raw_text: str | None = None
    raw_html: str | None = None

    if message.is_multipart():
        for part in message.walk():
            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                raw_text = append_body(raw_text, decode_part_payload(part))
            elif content_type == "text/html":
                raw_html = append_body(raw_html, decode_part_payload(part))
    else:
        content_type = message.get_content_type()
        if content_type == "text/html":
            raw_html = decode_part_payload(message)
        else:
            raw_text = decode_part_payload(message)

    received_at: datetime | None = None
    date_header = message.get("Date")
    if date_header:
        try:
            received_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            received_at = None

    headers = {
        header: decoded
        for header in SAFE_HEADERS
        if (decoded := decode_mime_header(message.get(header))) is not None
    }

    return ExtractedEmail(
        gmail_message_id=decode_mime_header(message.get("Message-ID")),
        sender_address=decode_mime_header(message.get("From")),
        subject=decode_mime_header(message.get("Subject")),
        received_at=received_at,
        raw_text=raw_text,
        raw_html=raw_html,
        raw_headers_json=headers,
    )
