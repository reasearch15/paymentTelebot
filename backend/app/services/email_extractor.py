from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

# Single-value headers retained for display / parsing.
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

# Multi-value / security headers. All occurrences are preserved (list when >1).
SECURITY_HEADERS = (
    "Authentication-Results",
    "ARC-Authentication-Results",
    "Received-SPF",
    "DKIM-Signature",
    "Resent-From",
    "Resent-Sender",
    "Resent-To",
    "Resent-Date",
    "Resent-Message-ID",
    "X-Forwarded-For",
    "X-Forwarded-To",
    "X-Forwarded-By",
    "X-Forwarded-Return-Path",
)


@dataclass(frozen=True)
class ExtractedEmail:
    gmail_message_id: str | None
    sender_address: str | None
    subject: str | None
    received_at: datetime | None
    raw_text: str | None
    raw_html: str | None
    raw_headers_json: dict[str, str | list[str]]


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


def _collect_header_values(message: EmailMessage, header_name: str) -> list[str]:
    values: list[str] = []
    for raw in message.get_all(header_name, []) or []:
        decoded = decode_mime_header(raw if isinstance(raw, str) else str(raw))
        if decoded:
            values.append(decoded)
    return values


def extract_headers(message: EmailMessage) -> dict[str, str | list[str]]:
    headers: dict[str, str | list[str]] = {}
    for header in SAFE_HEADERS:
        values = _collect_header_values(message, header)
        if not values:
            continue
        headers[header] = values[0] if len(values) == 1 else values

    for header in SECURITY_HEADERS:
        values = _collect_header_values(message, header)
        if not values:
            continue
        # Always keep Authentication-Results / ARC as lists so ordering is explicit.
        if header in {"Authentication-Results", "ARC-Authentication-Results"} or len(values) > 1:
            headers[header] = values
        else:
            headers[header] = values[0]
    return headers


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

    headers = extract_headers(message)

    return ExtractedEmail(
        gmail_message_id=decode_mime_header(message.get("Message-ID")),
        sender_address=decode_mime_header(message.get("From")),
        subject=decode_mime_header(message.get("Subject")),
        received_at=received_at,
        raw_text=raw_text,
        raw_html=raw_html,
        raw_headers_json=headers,
    )
