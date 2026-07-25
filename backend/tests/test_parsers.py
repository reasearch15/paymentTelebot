import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.payment_email import ProcessingStatus
from app.parsers.base import ParserInput
from app.parsers.chime import ChimeParser
from app.parsers.registry import get_parser
from app.services.payment_email_parser import parse_payment_email_safely


def test_missing_sender_tag_does_not_fail_chime_payment() -> None:
    parser = ChimeParser()
    result = parser.parse(
        ParserInput(
            subject="Chime: Derek S. sent you $13.00",
            raw_text="Chime payment received from Derek S.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
            receiver_tag="$Demaul_Goins",
        )
    )

    assert result.is_payment
    assert result.sender_payment_tag is None
    assert "sender_payment_tag" not in result.missing_fields


def test_chime_detects_receiver_tag_from_email_text() -> None:
    parser = ChimeParser()
    result = parser.parse(
        ParserInput(
            subject="Chime: Derek S. sent you $13.00",
            raw_text="Sender: $DerekS Receiver: $Demaul_Goins",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )
    )

    assert result.is_payment
    assert result.sender_payment_tag is None
    assert result.receiver_tag == "$Demaul_Goins"
    assert "receiver_tag" not in result.missing_fields


def test_chime_payment_without_receiver_tag_can_still_parse() -> None:
    parser = ChimeParser()
    result = parser.parse(
        ParserInput(
            subject="Chime: Derek S. sent you $13.00",
            raw_text="Chime payment received from Derek S. Sender: $DerekS",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )
    )

    assert result.is_payment
    assert result.sender_payment_tag is None
    assert result.receiver_tag is None
    assert "receiver_tag" not in result.missing_fields


def test_parser_registry_selection() -> None:
    assert get_parser("chime").parser_key == "chime"
    assert get_parser(" ChImE ").parser_key == "chime"
    assert get_parser("cash_app").parse(
        ParserInput(None, None, None, {}, None, "$Receiver")
    ).debug_evidence["reason"] == "parser not implemented"


def test_chime_incoming_payment_subject_and_body_amount() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="Emily S. just sent you money 💸",
            raw_text="You received $50.00 from Emily S. Memo: thanks.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        )
    )

    assert result.classification == "incoming_payment"
    assert result.direction == "IN"
    assert result.sender_name == "Emily S."
    assert result.sender_payment_tag is None
    assert result.amount_cents == 5000


def test_chime_incoming_without_tag_keeps_tag_null() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="Amanda M. just sent you money 💸",
            raw_text="You received $10.00 from Amanda M.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 11, 23, tzinfo=UTC),
        )
    )

    assert result.classification == "incoming_payment"
    assert result.amount_cents == 1000
    assert result.sender_name == "Amanda M."
    assert result.sender_payment_tag is None
    assert result.missing_fields == []


def test_chime_real_received_wording_does_not_treat_amount_as_tag() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="Amy F. just sent you money 💸",
            raw_text="Larry, you just received $5.00 from Amy F. for 🔥. The funds are in your Chime account.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 11, 49, tzinfo=UTC),
        )
    )

    assert result.classification == "incoming_payment"
    assert result.amount_cents == 500
    assert result.sender_name == "Amy F."
    assert result.sender_payment_tag is None


def test_chime_outgoing_payment_extracts_recipient() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="You sent money. 💸",
            raw_text="You just sent $50.00 instantly to Alejandro P.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        )
    )

    assert result.classification == "outgoing_payment"
    assert result.direction == "OUT"
    assert result.sender_name == "Alejandro P"
    assert result.amount_cents == 5000


def test_chime_payment_request_is_ignored() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="Jennifer M. is requesting $50.00",
            raw_text='Jennifer M. just requested $50.00 for "For Jason".',
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        )
    )

    assert result.classification == "payment_request"
    assert not result.is_payment
    assert result.direction is None


def test_unknown_chime_email_is_ignored_safely() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="Your Chime statement is ready",
            raw_text="Review your monthly statement.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        )
    )

    assert result.classification == "unknown"
    assert not result.is_payment


def test_chime_debit_card_activation_is_unknown() -> None:
    result = ChimeParser().parse(
        ParserInput(
            subject="You’ve activated your Chime Debit card.",
            raw_text="Your card is ready to use.",
            html_visible_text="",
            headers={},
            received_at=datetime(2026, 7, 24, 6, 57, tzinfo=UTC),
        )
    )

    assert result.classification == "unknown"
    assert not result.is_payment


def test_parser_failure_isolation(monkeypatch) -> None:
    class BrokenParser:
        parser_key = "broken"
        parser_version = "0.0.0"

        def parse(self, _parser_input):
            raise RuntimeError("boom with no credentials")

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0

        async def commit(self) -> None:
            self.commits += 1

    fake_email = SimpleNamespace(
        payment_account=SimpleNamespace(
            receiver_tag="$Receiver",
            provider=SimpleNamespace(parser_key="broken"),
        ),
        subject="",
        raw_text="",
        raw_html="",
        raw_headers_json={},
        received_at=None,
        processing_status=None,
        processing_error=None,
        parsed_at=None,
    )
    fake_session = FakeSession()

    monkeypatch.setattr("app.services.payment_email_parser.get_parser", lambda _key: BrokenParser())

    result = asyncio.run(parse_payment_email_safely(fake_session, fake_email))

    assert result is None
    assert fake_email.processing_status == ProcessingStatus.FAILED
    assert "boom" in fake_email.processing_error
    assert fake_session.commits == 1
