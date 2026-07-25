from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.parsers.extraction import normalize_whitespace
from app.schemas.settlement import parse_dollar_amount_to_cents


class PlayerSettlementDirectionValue(str, Enum):
    PAID_TO_PLAYER = "PAID_TO_PLAYER"
    RECEIVED_FROM_PLAYER = "RECEIVED_FROM_PLAYER"


class PlayerSettlementCreate(BaseModel):
    sender_name: str = Field(min_length=1, max_length=255)
    direction: PlayerSettlementDirectionValue
    amount: str = Field(min_length=1, max_length=32, description="Dollar amount, e.g. 15.00")
    payment_account_id: int
    reference: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=500)
    settled_at: datetime | None = None

    @field_validator("sender_name")
    @classmethod
    def normalize_sender(cls, value: str) -> str:
        normalized = normalize_whitespace(value)
        if not normalized:
            raise ValueError("Player/sender name is required.")
        return normalized

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        parse_dollar_amount_to_cents(value)
        return value.strip()

    @field_validator("reference", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = normalize_whitespace(value)
        return stripped or None

    @property
    def amount_cents(self) -> int:
        return parse_dollar_amount_to_cents(self.amount)


class PlayerSettlementResponse(BaseModel):
    id: int
    sender_name: str
    direction: PlayerSettlementDirectionValue
    amount_cents: int
    payment_account_id: int
    account_name: str
    reference: str | None
    note: str | None
    settled_at: datetime
    created_by_user_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlayerSettlementListResponse(BaseModel):
    items: list[PlayerSettlementResponse]
    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class PlayerLedgerRow(BaseModel):
    sender_name: str
    total_in_cents: int
    total_out_cents: int
    settlements_paid_cents: int
    settlements_received_cents: int
    unsettled_balance_cents: int
    in_count: int
    out_count: int
    first_transaction_at: datetime | None
    latest_transaction_at: datetime | None
    latest_activity_at: datetime | None = None


class PlayerLedgerListResponse(BaseModel):
    items: list[PlayerLedgerRow]


class PlayerLedgerTransaction(BaseModel):
    id: int
    payment_account_id: int
    account_name: str
    direction: str
    amount_cents: int
    sender_name: str | None
    provider_reference: str | None
    received_at: datetime
    telegram_status: str


class PlayerLedgerDetailResponse(BaseModel):
    summary: PlayerLedgerRow
    transactions: list[PlayerLedgerTransaction]
    settlements: list[PlayerSettlementResponse]


class PlayerSenderListResponse(BaseModel):
    items: list[str]
