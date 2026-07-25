"""Focused tests for Chime email authenticity / anti-spoofing validation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.payment_email import ProcessingStatus
from app.services.chime_email_auth import (
    domain_matches,
    validate_chime_email_authenticity,
)
from app.services.email_extractor import extract_email
from app.services import payment_email_parser


GENUINE_GMAIL_AUTH = (
    "mx.google.com; "
    "dkim=pass header.i=@account.chime.com header.s=s1 header.b=abc; "
    "spf=pass (google.com: domain of bounce@em.account.chime.com designates 1.2.3.4 as permitted sender) "
    "smtp.mailfrom=bounce@em.account.chime.com; "
    "dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=account.chime.com"
)


def genuine_headers(**overrides) -> dict:
    headers = {
        "From": "Chime <alerts@account.chime.com>",
        "Reply-To": "noreply@chime.com",
        "Return-Path": "<bounce@em.account.chime.com>",
        "Message-ID": "<abc123@account.chime.com>",
        "Subject": "Emily S. just sent you money",
        "Delivered-To": "user@gmail.com",
        "Authentication-Results": [GENUINE_GMAIL_AUTH],
    }
    headers.update(overrides)
    return headers


def test_domain_matches_rejects_suffix_attack() -> None:
    assert domain_matches("account.chime.com", "account.chime.com")
    assert domain_matches("em.account.chime.com", "chime.com")
    assert not domain_matches("account.chime.com.attacker.example", "account.chime.com")
    assert not domain_matches("notchime.com", "chime.com")


def test_genuine_direct_chime_email_accepted() -> None:
    result = validate_chime_email_authenticity(genuine_headers())
    assert result.accepted is True
    assert result.reason == "ok"
    assert result.normalized_from == "alerts@account.chime.com"
    assert result.authenticated_dkim_domain == "account.chime.com"
    assert result.authenticated_spf_domain == "em.account.chime.com"
    assert result.dmarc_result == "pass"
    assert result.forwarded_detected is False


def test_spoofed_from_dkim_attacker_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=pass header.d=attacker.example header.s=s1; "
        "spf=pass smtp.mailfrom=bounce@em.account.chime.com; "
        "dmarc=pass header.from=account.chime.com"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Authentication-Results": [auth]})
    )
    assert result.accepted is False
    assert result.reason == "dkim_domain_mismatch"
    assert result.authenticated_dkim_domain == "attacker.example"


def test_spf_failure_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=pass header.d=account.chime.com; "
        "spf=fail smtp.mailfrom=bounce@em.account.chime.com; "
        "dmarc=pass header.from=account.chime.com"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Authentication-Results": [auth]})
    )
    assert result.accepted is False
    assert result.reason == "spf_failed"


def test_dkim_failure_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=fail header.d=account.chime.com; "
        "spf=pass smtp.mailfrom=bounce@em.account.chime.com; "
        "dmarc=pass header.from=account.chime.com"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Authentication-Results": [auth]})
    )
    assert result.accepted is False
    assert result.reason == "dkim_failed"


def test_dmarc_failure_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=pass header.d=account.chime.com; "
        "spf=pass smtp.mailfrom=bounce@em.account.chime.com; "
        "dmarc=fail header.from=account.chime.com"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Authentication-Results": [auth]})
    )
    assert result.accepted is False
    assert result.reason == "dmarc_failed"
    assert result.dmarc_result == "fail"


def test_missing_authentication_results_rejected() -> None:
    headers = genuine_headers()
    headers.pop("Authentication-Results")
    result = validate_chime_email_authenticity(headers)
    assert result.accepted is False
    assert result.reason == "authentication_headers_missing"


def test_forwarded_subject_rejected() -> None:
    result = validate_chime_email_authenticity(
        genuine_headers(Subject="Fwd: Emily S. just sent you money"),
        subject="Fwd: Emily S. just sent you money",
    )
    assert result.accepted is False
    assert result.reason == "forwarded_message"
    assert result.forwarded_detected is True


def test_resent_headers_rejected() -> None:
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Resent-From": "friend@example.com", "Resent-Date": "Sat, 25 Jul 2026 01:00:00 +0000"})
    )
    assert result.accepted is False
    assert result.reason == "forwarded_message"


def test_fake_sender_authentication_results_not_trusted() -> None:
    """Attacker-supplied Authentication-Results must not authorize the message."""
    headers = genuine_headers(
        **{
            "Authentication-Results": [
                # Attacker block first (untrusted authserv).
                "attacker.example; dkim=pass header.d=account.chime.com; "
                "spf=pass smtp.mailfrom=bounce@em.account.chime.com; "
                "dmarc=pass header.from=account.chime.com",
                # Gmail reports the real outcome: DKIM from attacker domain.
                "mx.google.com; dkim=pass header.d=attacker.example; "
                "spf=fail smtp.mailfrom=evil@attacker.example; "
                "dmarc=fail header.from=account.chime.com",
            ]
        }
    )
    result = validate_chime_email_authenticity(headers)
    assert result.accepted is False
    # Trust Gmail's block only — attacker.example DKIM domain is rejected.
    assert result.reason in {"dkim_domain_mismatch", "spf_failed", "dmarc_failed"}


def test_fake_auth_results_only_from_attacker_rejected() -> None:
    headers = genuine_headers(
        **{
            "Authentication-Results": [
                "evil.example; dkim=pass header.d=account.chime.com; "
                "spf=pass smtp.mailfrom=bounce@em.account.chime.com; "
                "dmarc=pass header.from=account.chime.com",
            ]
        }
    )
    result = validate_chime_email_authenticity(headers)
    assert result.accepted is False
    assert result.reason == "authentication_headers_missing"


def test_multiple_normal_received_headers_still_accepted() -> None:
    # Received headers are not stored as a forwarding signal; multiple hops are normal.
    result = validate_chime_email_authenticity(
        genuine_headers(),
        subject="Emily S. just sent you money",
        raw_text="You received $50.00 from Emily S.",
    )
    assert result.accepted is True


def test_malicious_similar_domain_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=pass header.d=account.chime.com.attacker.example; "
        "spf=pass smtp.mailfrom=bounce@account.chime.com.attacker.example; "
        "dmarc=pass header.from=account.chime.com.attacker.example"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(
            From="Chime <alerts@account.chime.com.attacker.example>",
            **{"Authentication-Results": [auth]},
        )
    )
    assert result.accepted is False
    assert result.reason in {"sender_not_allowed", "dkim_domain_mismatch", "spf_domain_mismatch", "dmarc_failed"}


def test_extractor_preserves_authentication_results_order() -> None:
    raw = (
        b"Authentication-Results: mx.google.com; dkim=pass header.d=account.chime.com; "
        b"spf=pass smtp.mailfrom=bounce@em.account.chime.com; dmarc=pass header.from=account.chime.com\r\n"
        b"Authentication-Results: attacker.example; dkim=pass header.d=account.chime.com\r\n"
        b"From: Chime <alerts@account.chime.com>\r\n"
        b"Reply-To: noreply@chime.com\r\n"
        b"Return-Path: <bounce@em.account.chime.com>\r\n"
        b"Subject: Emily S. just sent you money\r\n"
        b"Message-ID: <msg@account.chime.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"You received $50.00 from Emily S.\r\n"
    )
    extracted = extract_email(raw)
    auth = extracted.raw_headers_json["Authentication-Results"]
    assert isinstance(auth, list)
    assert auth[0].startswith("mx.google.com")
    assert "attacker.example" in auth[1]
    result = validate_chime_email_authenticity(
        extracted.raw_headers_json,
        subject=extracted.subject,
        raw_text=extracted.raw_text,
        sender_address=extracted.sender_address,
    )
    assert result.accepted is True


def test_auth_rejected_email_remains_visible_without_transaction_or_telegram(monkeypatch) -> None:
    create_calls: list[object] = []
    telegram_calls: list[object] = []

    async def fake_create(session, email, result):
        create_calls.append(result)
        return None

    async def fake_telegram(transaction_id: int):
        telegram_calls.append(transaction_id)

    class FakeSession:
        async def commit(self) -> None:
            return None

    email = SimpleNamespace(
        id=42,
        payment_account=SimpleNamespace(
            friendly_name="Chime",
            provider=SimpleNamespace(parser_key="chime", name="Chime"),
        ),
        subject="Emily S. just sent you money",
        raw_text="You received $50.00 from Emily S.",
        raw_html="",
        raw_headers_json={
            "From": "Chime <alerts@account.chime.com>",
            "Subject": "Emily S. just sent you money",
            # Missing Authentication-Results → fail closed.
        },
        received_at=datetime(2026, 7, 25, 1, 0, tzinfo=UTC),
        sender_address="Chime <alerts@account.chime.com>",
        gmail_message_id="<spoof@example.com>",
        payment_account_id=1,
        processing_status=ProcessingStatus.CAPTURED,
        processing_error=None,
        parsed_at=None,
        parser_key=None,
        parser_version=None,
        parsed_payload_json=None,
    )

    monkeypatch.setattr(payment_email_parser, "create_transaction_from_parser_result", fake_create)
    monkeypatch.setattr(payment_email_parser, "send_transaction_notification", fake_telegram)

    result = asyncio.run(payment_email_parser.parse_payment_email(FakeSession(), email))

    assert result.is_payment is False
    assert email.processing_status == ProcessingStatus.IGNORED
    assert email.processing_error == "email_auth_rejected:authentication_headers_missing"
    assert create_calls == []
    assert telegram_calls == []
    # Captured email record remains (same object, still addressable).
    assert email.id == 42


def test_genuine_email_parsed_twice_is_idempotent(monkeypatch) -> None:
    transactions_created = {"n": 0}
    telegram_calls: list[int] = []
    existing = {"tx": None}

    class FakeTx:
        def __init__(self, tx_id: int) -> None:
            self.id = tx_id

    async def fake_create(session, email, result):
        if not result.is_payment:
            return None
        if existing["tx"] is not None:
            return existing["tx"]
        transactions_created["n"] += 1
        existing["tx"] = FakeTx(7)
        return existing["tx"]

    async def fake_telegram(transaction_id: int):
        # Mirror real telegram layer: already-sent notifications are skipped.
        if transaction_id in telegram_calls:
            return
        telegram_calls.append(transaction_id)

    class FakeSession:
        async def commit(self) -> None:
            return None

    headers = genuine_headers()
    email = SimpleNamespace(
        id=99,
        payment_account=SimpleNamespace(
            friendly_name="Chime",
            provider=SimpleNamespace(parser_key="chime", name="Chime"),
        ),
        subject="Emily S. just sent you money",
        raw_text="You received $50.00 from Emily S.",
        raw_html="",
        raw_headers_json=headers,
        received_at=datetime(2026, 7, 25, 1, 0, tzinfo=UTC),
        sender_address="Chime <alerts@account.chime.com>",
        gmail_message_id="<genuine@account.chime.com>",
        payment_account_id=1,
        processing_status=ProcessingStatus.CAPTURED,
        processing_error=None,
        parsed_at=None,
        parser_key=None,
        parser_version=None,
        parsed_payload_json=None,
    )

    monkeypatch.setattr(payment_email_parser, "create_transaction_from_parser_result", fake_create)
    monkeypatch.setattr(payment_email_parser, "send_transaction_notification", fake_telegram)

    first = asyncio.run(payment_email_parser.parse_payment_email(FakeSession(), email))
    second = asyncio.run(payment_email_parser.parse_payment_email(FakeSession(), email))

    assert first.is_payment is True
    assert second.is_payment is True
    assert transactions_created["n"] == 1
    assert telegram_calls == [7]
    assert email.processing_status == ProcessingStatus.PARSED


def test_softfail_spf_rejected() -> None:
    auth = (
        "mx.google.com; "
        "dkim=pass header.d=account.chime.com; "
        "spf=softfail smtp.mailfrom=bounce@em.account.chime.com; "
        "dmarc=pass header.from=account.chime.com"
    )
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Authentication-Results": [auth]})
    )
    assert result.accepted is False
    assert result.reason == "spf_failed"


def test_return_path_mismatch_rejected() -> None:
    result = validate_chime_email_authenticity(
        genuine_headers(**{"Return-Path": "<evil@attacker.example>"})
    )
    assert result.accepted is False
    assert result.reason == "return_path_mismatch"
