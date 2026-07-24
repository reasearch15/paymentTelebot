from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_dollar_amount_to_cents(value: str | int | float | Decimal) -> int:
    """Convert a user-facing dollar amount into integer cents."""
    if isinstance(value, bool):
        raise ValueError("Settlement amount is invalid.")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("Settlement amount must be greater than zero.")
        return value
    try:
        amount = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Settlement amount is invalid.") from exc
    if not amount.is_finite():
        raise ValueError("Settlement amount is invalid.")
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized <= 0:
        raise ValueError("Settlement amount must be greater than zero.")
    cents = int(quantized * 100)
    if cents <= 0:
        raise ValueError("Settlement amount must be greater than zero.")
    return cents


class SettlementCreate(BaseModel):
    payment_account_id: int
    amount: str = Field(min_length=1, max_length=32, description="Dollar amount entered by the user, e.g. 15.00")
    note: str | None = Field(default=None, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        parse_dollar_amount_to_cents(value)
        return value.strip()

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def amount_cents(self) -> int:
        return parse_dollar_amount_to_cents(self.amount)


class SettlementResponse(BaseModel):
    id: int
    payment_account_id: int
    friendly_name: str
    amount_cents: int
    balance_before_cents: int
    balance_after_cents: int
    note: str | None
    status: str
    settled_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountUnsettledBalance(BaseModel):
    payment_account_id: int
    friendly_name: str
    unsettled_balance_cents: int
