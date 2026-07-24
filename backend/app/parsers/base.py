from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

Direction = Literal["IN", "OUT"]
PaymentClassification = Literal["incoming_payment", "outgoing_payment", "payment_request", "unknown"]


@dataclass(frozen=True)
class ParserInput:
    subject: str | None
    raw_text: str | None
    html_visible_text: str | None
    headers: dict
    received_at: datetime | None
    receiver_tag: str | None = None
    sender_address: str | None = None
    gmail_message_id: str | None = None
    payment_account_id: int | None = None
    payment_account_friendly_name: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class ParserResult:
    classification: PaymentClassification
    is_payment: bool
    direction: Direction | None
    amount_cents: int | None
    sender_name: str | None
    sender_payment_tag: str | None
    receiver_tag: str | None
    payment_timestamp: datetime | None
    provider_reference: str | None
    confidence: float
    missing_fields: list[str]
    parser_key: str
    parser_version: str
    debug_evidence: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        payload = asdict(self)
        if self.payment_timestamp is not None:
            payload["payment_timestamp"] = self.payment_timestamp.isoformat()
        return payload


class PaymentParser:
    parser_key = "base"
    parser_version = "0.0.0"

    def parse(self, parser_input: ParserInput) -> ParserResult:
        raise NotImplementedError
