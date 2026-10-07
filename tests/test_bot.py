from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Update, User

from application.services import PeriodTotals, TelegramContext
from bot.handlers.commands import help_command, settings_command, today_command
from bot.middlewares import AllowedMessageMiddleware, UpdateLoggingMiddleware
from infrastructure.database import Household, Member


def make_message(
    *,
    chat_id: int = -100123,
    chat_type: str = "supergroup",
    is_bot: bool = False,
    text: str = "/start",
) -> tuple[Message, Update]:
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Сімейні фінанси"),
        from_user=User(id=42, is_bot=is_bot, first_name="Олена", username="olena"),
        text=text,
    )
    return message, Update(update_id=99, message=message)


@pytest.mark.asyncio
async def test_allowed_message_bootstraps_context_and_calls_handler() -> None:
    message, update = make_message()
    household = Household(id=1, telegram_chat_id=message.chat.id, name=message.chat.title)
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    bootstrap = AsyncMock(return_value=TelegramContext(household=household, member=member))
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock(return_value="handled")
    data = {"event_update": update}

    result = await middleware(handler, message, data)

    assert result == "handled"
    assert data["household"] is household
    assert data["member"] is member
    handler.assert_awaited_once()
    bootstrap.assert_awaited_once_with(
        update_id=99,
        chat_id=-100123,
        chat_name="Сімейні фінанси",
        user_id=42,
        display_name="Олена",
        username="olena",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_id", "chat_type", "is_bot"),
    [(-100999, "supergroup", False), (-100123, "supergroup", True), (-100123, "private", False)],
)
async def test_non_allowed_chats_and_bots_are_ignored(
    chat_id: int, chat_type: str, is_bot: bool
) -> None:
    message, update = make_message(chat_id=chat_id, chat_type=chat_type, is_bot=is_bot)
    bootstrap = AsyncMock()
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({-100123}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock()

    result = await middleware(handler, message, {"event_update": update})

    assert result is None
    bootstrap.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_update_is_ignored() -> None:
    message, update = make_message()
    bootstrap = AsyncMock(return_value=None)
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock()

    result = await middleware(handler, message, {"event_update": update})

    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_handler_releases_update_for_retry() -> None:
    message, update = make_message()
    context = TelegramContext(
        household=Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси"),
        member=Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
    )
    bootstrap_service = SimpleNamespace(
        bootstrap=AsyncMock(return_value=context),
        release=AsyncMock(),
    )
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=bootstrap_service,
    )
    handler = AsyncMock(side_effect=RuntimeError("failed"))

    with pytest.raises(RuntimeError, match="failed"):
        await middleware(handler, message, {"event_update": update})

    bootstrap_service.release.assert_awaited_once_with(99)


@pytest.mark.asyncio
async def test_update_logging_excludes_message_text(caplog: pytest.LogCaptureFixture) -> None:
    _message, update = make_message(text="secret payment details")
    middleware = UpdateLoggingMiddleware()

    with caplog.at_level("INFO"):
        await middleware(AsyncMock(), update, {})

    assert "update_id=99" in caplog.text
    assert "secret payment details" not in caplog.text


@pytest.mark.asyncio
async def test_help_and_settings_commands_answer_in_ukrainian() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
        reports_enabled=True,
    )

    await help_command(message)
    await settings_command(message, household)

    assert "/today" in message.answer.await_args_list[0].args[0]
    assert "Europe/Paris" in message.answer.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_today_command_uses_deterministic_report_totals() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    report_service = SimpleNamespace(
        totals=AsyncMock(
            return_value=PeriodTotals(income=Decimal("100.00"), expense=Decimal("25.50"))
        )
    )

    await today_command(message, household, report_service)

    report_service.totals.assert_awaited_once()
    answer = message.answer.await_args.args[0]
    assert "100.00 EUR" in answer
    assert "25.50 EUR" in answer
    assert "74.50 EUR" in answer
