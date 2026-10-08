from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher

import main
from config import Settings


def test_dispatcher_registers_message_handlers() -> None:
    dispatcher = main.create_dispatcher()

    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.resolve_used_update_types() == ["callback_query", "message"]


@pytest.mark.asyncio
async def test_bot_startup_reaches_long_polling(
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
    monkeypatch.setattr(main, "create_dispatcher", lambda **_kwargs: dispatcher)
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(main, "create_engine", lambda _url: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda _engine: object())

    await main.start_bot(settings)

    start_polling.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_commands_are_registered() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())

    await main.set_bot_commands(bot)

    commands = bot.set_my_commands.await_args.args[0]
    assert commands[0].command == "menu"
    assert {item.command for item in commands} >= {"today", "stats", "categories", "undo"}
