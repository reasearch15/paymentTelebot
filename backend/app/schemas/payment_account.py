from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.telegram import PaymentAccountDeliveryStats, PaymentAccountTelegramIntegrationSummary


class PaymentAccountCreate(BaseModel):
    provider_id: int
    friendly_name: str = Field(min_length=1, max_length=120)
    receiver_tag: str | None = Field(default=None, max_length=120)
    gmail_address: EmailStr
    app_password: str = Field(min_length=1, max_length=255)
    telegram_integration_ids: list[int] = Field(default_factory=list)

    @field_validator("friendly_name", "app_password")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field is required.")
        return stripped

    @field_validator("receiver_tag")
    @classmethod
    def normalize_receiver_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.startswith("$"):
            raise ValueError("receiver_tag must start with $.")
        return stripped

    @field_validator("gmail_address")
    @classmethod
    def normalize_gmail_address(cls, value: str) -> str:
        return value.lower()


class PaymentAccountUpdate(BaseModel):
    provider_id: int | None = None
    friendly_name: str | None = Field(default=None, min_length=1, max_length=120)
    receiver_tag: str | None = Field(default=None, max_length=120)
    gmail_address: EmailStr | None = None
    app_password: str | None = Field(default=None, min_length=1, max_length=255)
    telegram_integration_ids: list[int] | None = None

    @field_validator("friendly_name", "app_password")
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped

    @field_validator("receiver_tag")
    @classmethod
    def normalize_optional_receiver_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.startswith("$"):
            raise ValueError("receiver_tag must start with $.")
        return stripped

    @field_validator("gmail_address")
    @classmethod
    def normalize_optional_gmail_address(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class PaymentAccountResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    friendly_name: str
    receiver_tag: str | None
    gmail_address: str
    enabled: bool
    listener_status: str
    last_checked_at: datetime | None
    last_email_at: datetime | None
    last_captured_email_at: datetime | None = None
    has_app_password: bool
    created_at: datetime
    updated_at: datetime
    telegram_integrations: list[PaymentAccountTelegramIntegrationSummary] = Field(default_factory=list)
    telegram_integration_count: int = 0
    telegram_integration_ids: list[int] = Field(default_factory=list)
    delivery_stats: PaymentAccountDeliveryStats | None = None

    model_config = ConfigDict(from_attributes=True)


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    checked_at: datetime
