import pytest
from pydantic import ValidationError

from config import Settings


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5432/finance")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "-1001234567890, 42")
    monkeypatch.setenv("ADMIN_USER_IDS", "42, 43")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")


def test_settings_are_loaded_and_normalized(settings_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.allowed_chat_ids == frozenset({-1001234567890, 42})
    assert settings.admin_user_ids == frozenset({42, 43})
    assert settings.sqlalchemy_database_url.startswith("postgresql+psycopg://")
    assert settings.openai_model == "gpt-6-luna"
    assert settings.openai_transcription_model == "gpt-transcribe"
    assert settings.voice_input_enabled is False
    assert settings.max_voice_duration_seconds == 60
    assert "test-openai-key" not in repr(settings)


def test_invalid_allowed_chat_id_is_rejected(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "42,not-a-chat")

    with pytest.raises(ValidationError, match="comma-separated integers"):
        Settings(_env_file=None)


def test_empty_allowlist_is_rejected(settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    monkeypatch.setenv("ADMIN_USER_IDS", "")

    with pytest.raises(ValidationError, match="ALLOWED_CHAT_IDS or ADMIN_USER_IDS"):
        Settings(_env_file=None)


def test_admin_id_is_enough_to_bootstrap_access(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    monkeypatch.setenv("ADMIN_USER_IDS", "42")

    settings = Settings(_env_file=None)

    assert settings.allowed_chat_ids == frozenset()
    assert settings.admin_user_ids == frozenset({42})


def test_invalid_admin_user_id_is_rejected(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_USER_IDS", "42,not-a-user")

    with pytest.raises(ValidationError, match="ADMIN_USER_IDS"):
        Settings(_env_file=None)


def test_required_secrets_cannot_be_empty(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_worker_heartbeat_file_must_be_absolute(
    settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", "relative/heartbeat")

    with pytest.raises(ValidationError, match="must be an absolute path"):
        Settings(_env_file=None)
