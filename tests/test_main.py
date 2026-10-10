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
    monkeypatch.setattr(main, "create_engine", lambda _url, **_kwargs: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(main, "verify_database_connection", AsyncMock())

    await main.start_bot(settings)

    start_polling.assert_awaited_once()
    main.verify_database_connection.assert_awaited_once_with(engine)
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_is_checked_before_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        openai_api_key="test-openai-key",
        database_url="postgresql://user:password@localhost:5432/finance",
        allowed_chat_ids_raw="-1001234567890",
        app_env="test",
    )
    engine = SimpleNamespace(dispose=AsyncMock())
    database_check = AsyncMock(side_effect=ConnectionError("database unavailable"))
    session_factory = AsyncMock()
    monkeypatch.setattr(main, "create_engine", lambda _url, **_kwargs: engine)
    monkeypatch.setattr(main, "verify_database_connection", database_check)
    monkeypatch.setattr(main, "create_session_factory", session_factory)

    with pytest.raises(ConnectionError, match="database unavailable"):
        await main.start_bot(settings)

    database_check.assert_awaited_once_with(engine)
    session_factory.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_commands_are_registered() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())

    await main.set_bot_commands(bot)

    commands = bot.set_my_commands.await_args.args[0]
    assert commands[0].command == "menu"
    assert {item.command for item in commands} >= {
        "today",
        "stats",
        "categories",
        "undo",
        "advice",
    }
