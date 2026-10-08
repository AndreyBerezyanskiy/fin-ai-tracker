from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.menu import (
    add_category_name,
    category_menu_callback,
    menu_callback,
    menu_command,
    settings_menu_callback,
)
from bot.keyboards import (
    CategoryMenuCallback,
    MenuCallback,
    SettingsMenuCallback,
    main_menu_keyboard,
)
from domain.enums import TransactionType
from infrastructure.database import Category, Household


def test_main_menu_contains_primary_sections() -> None:
    labels = [button.text for row in main_menu_keyboard().inline_keyboard for button in row]

    assert labels == [
        "🧾 Останні операції",
        "📊 Статистика",
        "🏷 Категорії",
        "⚙️ Налаштування",
        "↩️ Скасувати останню",
    ]


@pytest.mark.asyncio
async def test_menu_command_clears_state_and_opens_menu() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())

    await menu_command(message, state)

    state.clear.assert_awaited_once()
    assert message.answer.await_args.args[0] == "Головне меню"
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard


@pytest.mark.asyncio
async def test_stats_menu_callback_edits_current_message() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сім’я",
        currency="EUR",
        timezone="Europe/Paris",
    )
    category_service = SimpleNamespace(
        statistics=AsyncMock(
            return_value=(
                SimpleNamespace(
                    name="Продукти",
                    type=TransactionType.EXPENSE,
                    amount=Decimal("45.00"),
                ),
            )
        )
    )

    await menu_callback(
        callback,
        MenuCallback(section="stats_month"),
        household,
        SimpleNamespace(id=2),
        SimpleNamespace(),
        category_service,
        SimpleNamespace(clear=AsyncMock()),
    )

    text = callback.message.edit_text.await_args.args[0]
    assert "Статистика за" in text
    assert "Продукти: €45.00" in text
    category_service.statistics.assert_awaited_once()


@pytest.mark.asyncio
async def test_category_add_flow_uses_fsm() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я")
    state = SimpleNamespace(
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value={"transaction_type": "expense"}),
        clear=AsyncMock(),
    )
    service = SimpleNamespace(
        add=AsyncMock(
            return_value=Category(
                id=20,
                household_id=1,
                code="custom_12345678",
                name="Тварини",
                type=TransactionType.EXPENSE,
                is_active=True,
            )
        )
    )

    await category_menu_callback(
        callback,
        CategoryMenuCallback(action="add", value="expense"),
        household,
        service,
        state,
    )

    state.set_state.assert_awaited_once()
    state.update_data.assert_awaited_once_with(transaction_type="expense")
    assert "Введіть назву" in callback.message.edit_text.await_args.args[0]

    message = SimpleNamespace(text="Тварини", answer=AsyncMock())
    await add_category_name(message, household, service, state)

    service.add.assert_awaited_once_with(
        household_id=1,
        transaction_type=TransactionType.EXPENSE,
        name="Тварини",
    )
    state.clear.assert_awaited_once()
    assert "Категорію створено" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_settings_callback_toggles_reports() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сім’я",
        currency="EUR",
        timezone="Europe/Paris",
        reports_enabled=True,
    )
    updated = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сім’я",
        currency="EUR",
        timezone="Europe/Paris",
        reports_enabled=False,
    )
    service = SimpleNamespace(toggle_reports=AsyncMock(return_value=updated))

    await settings_menu_callback(
        callback,
        SettingsMenuCallback(action="reports", value="toggle"),
        household,
        service,
    )

    service.toggle_reports.assert_awaited_once_with(1)
    assert "Автоматичні звіти: вимкнені" in callback.message.edit_text.await_args.args[0]
