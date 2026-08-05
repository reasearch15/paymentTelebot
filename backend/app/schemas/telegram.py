from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TelegramSettingsResponse(BaseModel):
    bot_token_masked: str | None
    group_id: str | None
    enabled: bool
    connected: bool
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TelegramSettingsUpdate(BaseModel):
    bot_token: str | None = Field(default=None, max_length=2048)
    group_id: str = Field(min_length=1, max_length=120)
    enabled: bool = False

    @field_validator("bot_token")
    @classmethod
    def normalize_bot_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("group_id")
    @classmethod
    def normalize_group_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("group_id is required.")
        if not stripped.lstrip("-").isdigit():
            raise ValueError("group_id must be a numeric Telegram group ID.")
        return stripped


class TelegramActionResponse(BaseModel):
    success: bool
    message: str
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    bot_username: str | None = None


class TelegramIntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    bot_token: str = Field(min_length=1, max_length=2048)
    group_id: str = Field(min_length=1, max_length=120)
    enabled: bool = True

    @field_validator("name", "bot_token")
    @classmethod
    def strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field is required.")
        return stripped

    @field_validator("group_id")
    @classmethod
    def normalize_group_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("group_id is required.")
        if not stripped.lstrip("-").isdigit():
            raise ValueError("group_id must be a numeric Telegram group ID.")
        return stripped


class TelegramIntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    bot_token: str | None = Field(default=None, max_length=2048)
    group_id: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be blank.")
        return stripped

    @field_validator("bot_token")
    @classmethod
    def normalize_optional_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("group_id")
    @classmethod
    def normalize_optional_group_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("group_id cannot be blank.")
        if not stripped.lstrip("-").isdigit():
            raise ValueError("group_id must be a numeric Telegram group ID.")
        return stripped


class TelegramIntegrationRead(BaseModel):
    id: int
    name: str
    bot_token_masked: str | None
    has_bot_token: bool
    group_id: str | None
    bot_username: str | None
    enabled: bool
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    assigned_payment_account_count: int = 0
    is_legacy_default: bool = False

    model_config = ConfigDict(from_attributes=True)


class TelegramIntegrationListItem(TelegramIntegrationRead):
    pass


class PaymentAccountAssignmentItem(BaseModel):
    id: int
    friendly_name: str
    gmail_address: str
    provider_name: str
    enabled: bool


class TelegramIntegrationAssignmentUpdate(BaseModel):
    payment_account_ids: list[int] = Field(default_factory=list)


class PaymentAccountTelegramAssignmentUpdate(BaseModel):
    telegram_integration_ids: list[int] = Field(default_factory=list)


class TelegramIntegrationAssignmentRead(BaseModel):
    telegram_integration_id: int
    payment_accounts: list[PaymentAccountAssignmentItem]


class PaymentAccountTelegramIntegrationsRead(BaseModel):
    payment_account_id: int
    telegram_integrations: list[TelegramIntegrationRead]


class PaymentAccountTelegramIntegrationSummary(BaseModel):
    id: int
    name: str
    enabled: bool
    bot_username: str | None = None
    group_id: str | None = None


class TelegramDeliverySummary(BaseModel):
    total: int = 0
    sent: int = 0
    failed: int = 0
    pending: int = 0
    sending: int = 0
