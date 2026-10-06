import pytest
from pydantic import ValidationError

from config import Settings


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5432/finance")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "-1001234567890, 42")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")


def test_settings_are_loaded_and_normalized(settings_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.allowed_chat_ids == frozenset({-1001234567890, 42})
    assert settings.sqlalchemy_database_url.startswith("postgresql+psycopg://")
    assert "test-openai-key" not in repr(settings)


def test_invalid_allowed_chat_id_is_rejected(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "42,not-a-chat")

    with pytest.raises(ValidationError, match="comma-separated integers"):
        Settings(_env_file=None)


def test_required_secrets_cannot_be_empty(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
