from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher

import main
from config import Settings


def test_dispatcher_can_be_created_without_handlers() -> None:
    dispatcher = main.create_dispatcher()

    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.resolve_used_update_types() == []


@pytest.mark.asyncio
async def test_bot_startup_reaches_long_polling_without_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        openai_api_key="test-openai-key",
        database_url="postgresql://user:password@localhost:5432/finance",
        allowed_chat_ids_raw="-1001234567890",
        app_env="test",
    )
    dispatcher = main.create_dispatcher()
    start_polling = AsyncMock()
    monkeypatch.setattr(dispatcher, "start_polling", start_polling)
    monkeypatch.setattr(main, "create_dispatcher", lambda: dispatcher)

    await main.start_bot(settings)

    start_polling.assert_awaited_once()
