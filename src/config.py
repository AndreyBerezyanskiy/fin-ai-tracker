from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PositiveFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type AppEnvironment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Application settings loaded exclusively from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    openai_api_key: SecretStr = Field(min_length=1)
    database_url: SecretStr = Field(min_length=1)
    allowed_chat_ids_raw: str = Field(alias="ALLOWED_CHAT_IDS")
    openai_model: str = "gpt-6-luna"
    app_env: AppEnvironment = "development"
    log_level: str = "INFO"
    telegram_timeout_seconds: PositiveFloat = 20.0
    openai_timeout_seconds: PositiveFloat = 20.0
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    database_connect_timeout_seconds: PositiveInt = 10
    max_message_length: PositiveInt = Field(default=1000, le=4096)
    ai_requests_per_minute: PositiveInt = Field(default=10, le=120)
    store_original_text: bool = True
    worker_heartbeat_file: str = "/tmp/family-finance-worker.heartbeat"
    worker_heartbeat_interval_seconds: PositiveFloat = 10.0

    @field_validator("allowed_chat_ids_raw")
    @classmethod
    def validate_allowed_chat_ids(cls, value: str) -> str:
        entries = [entry.strip() for entry in value.split(",") if entry.strip()]
        if not entries:
            raise ValueError("ALLOWED_CHAT_IDS must contain at least one Telegram chat ID")
        try:
            for entry in entries:
                int(entry)
        except ValueError as error:
            raise ValueError("ALLOWED_CHAT_IDS must contain comma-separated integers") from error
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(valid_levels))}")
        return normalized

    @field_validator("worker_heartbeat_file")
    @classmethod
    def validate_worker_heartbeat_file(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("WORKER_HEARTBEAT_FILE must be an absolute path")
        return value

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        return frozenset(
            int(entry.strip()) for entry in self.allowed_chat_ids_raw.split(",") if entry.strip()
        )

    @property
    def sqlalchemy_database_url(self) -> str:
        url = self.database_url.get_secret_value()
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide immutable-by-convention settings instance."""

    return Settings()
