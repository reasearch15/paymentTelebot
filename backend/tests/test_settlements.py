import pytest

from app.schemas.settlement import SettlementCreate, parse_dollar_amount_to_cents


def test_settlement_create_amount_cents_property() -> None:
    payload = SettlementCreate(payment_account_id=5, amount="15.00", note=" pickup ")
    assert payload.amount_cents == 1500
    assert payload.note == "pickup"


def test_settlement_create_rejects_zero() -> None:
    with pytest.raises(Exception):
        SettlementCreate(payment_account_id=5, amount="0")


def test_parse_accepts_two_decimal_places() -> None:
    assert parse_dollar_amount_to_cents("20.00") == 2000
    assert parse_dollar_amount_to_cents("20.1") == 2010
