from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
