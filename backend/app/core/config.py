from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://payment_ledger:payment_ledger_dev@localhost:5432/payment_ledger"
    secret_key: str = "development-only-change-me"
    app_encryption_key: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    session_cookie_name: str = "payment_ledger_session"
    session_max_age_seconds: int = 60 * 60 * 24
    backend_cors_origins: list[str] = ["http://localhost:3000"]
    gmail_poll_interval_seconds: int = 15
    gmail_connection_timeout_seconds: int = 20
    gmail_read_batch_size: int = 50
    gmail_uid_lookback_count: int = 25

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
