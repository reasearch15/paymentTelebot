import asyncio

import pytest
from fastapi import HTTPException

from app.api.payment_accounts import ensure_receiver_tag_is_unique
from app.schemas.payment_account import PaymentAccountCreate, PaymentAccountUpdate


class FakeScalarResult:
    def __init__(self, value: int | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> int | None:
        return self.value


class FakeDb:
    def __init__(self, existing_id: int | None = None) -> None:
        self.existing_id = existing_id
        self.execute_calls = 0

    async def execute(self, _query):
        self.execute_calls += 1
        return FakeScalarResult(self.existing_id)


def test_payment_account_create_does_not_require_receiver_tag() -> None:
    payload = PaymentAccountCreate(
        provider_id=1,
        friendly_name="Main Chime",
        gmail_address="USER@GMAIL.COM",
        app_password="app-password",
    )

    assert payload.gmail_address == "user@gmail.com"
    assert payload.receiver_tag is None


def test_payment_account_update_does_not_include_receiver_tag() -> None:
    payload = PaymentAccountUpdate(friendly_name="Renamed")

    assert payload.model_dump(exclude_unset=True) == {"friendly_name": "Renamed"}
    assert payload.receiver_tag is None


def test_payment_account_create_normalizes_blank_receiver_tag_to_none() -> None:
    payload = PaymentAccountCreate(
        provider_id=1,
        friendly_name="Main Chime",
        receiver_tag="   ",
        gmail_address="user@gmail.com",
        app_password="app-password",
    )

    assert payload.receiver_tag is None


def test_payment_account_create_accepts_real_receiver_tag() -> None:
    payload = PaymentAccountCreate(
        provider_id=1,
        friendly_name="Main Chime",
        receiver_tag="  $Example  ",
        gmail_address="user@gmail.com",
        app_password="app-password",
    )

    assert payload.receiver_tag == "$Example"


def test_receiver_tag_duplicate_check_skips_missing_values() -> None:
    db = FakeDb()

    assert asyncio.run(ensure_receiver_tag_is_unique(db, None)) is None
    assert asyncio.run(ensure_receiver_tag_is_unique(db, "   ")) is None
    assert db.execute_calls == 0


def test_receiver_tag_duplicate_check_conflicts_only_for_real_values() -> None:
    db = FakeDb(existing_id=1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ensure_receiver_tag_is_unique(db, "  $Example  "))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "receiver_tag must be unique."
    assert db.execute_calls == 1
