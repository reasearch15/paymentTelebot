import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

PARSER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class ProviderBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parser_key: str = Field(min_length=1, max_length=100)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def strip_required_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Provider name is required.")
        return stripped

    @field_validator("parser_key")
    @classmethod
    def validate_parser_key(cls, value: str) -> str:
        stripped = value.strip()
        if not PARSER_KEY_PATTERN.fullmatch(stripped):
            raise ValueError("parser_key must be lowercase snake_case.")
        return stripped


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parser_key: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("Provider name is required.")
        return stripped

    @field_validator("parser_key")
    @classmethod
    def validate_optional_parser_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not PARSER_KEY_PATTERN.fullmatch(stripped):
            raise ValueError("parser_key must be lowercase snake_case.")
        return stripped


class ProviderResponse(BaseModel):
    id: int
    name: str
    parser_key: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
