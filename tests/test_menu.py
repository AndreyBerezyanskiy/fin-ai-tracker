from datetime import date
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
from domain.category_templates import CategoryTemplate
from domain.enums import PlannedExpenseStatus, TransactionType
from infrastructure.database import Category, Household


def test_main_menu_contains_primary_sections() -> None:
    labels = [button.text for row in main_menu_keyboard().inline_keyboard for button in row]

    assert labels == [
        "🧾 Останні операції",
        "📊 Статистика",
        "📌 Заплановані витрати",
        "🗂 Без категорії",
        "🏷 Категорії",
        "⚙️ Налаштування",
        "↩️ Скасувати останню",
    ]


@pytest.mark.asyncio
async def test_planned_expenses_are_available_from_main_menu() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сім’я",
        currency="EUR",
    )
    plan = SimpleNamespace(
        id=42,
        status=PlannedExpenseStatus.SCHEDULED,
        amount=Decimal("11.00"),
        currency="EUR",
        description="Мобільний зв’язок",
        due_date=date(2026, 10, 23),
        recurrence=None,
        category=SimpleNamespace(name="Мобільний зв’язок та інтернет"),
    )
    planned_service = SimpleNamespace(list_active=AsyncMock(return_value=(plan,)))

    await menu_callback(
        callback,
        MenuCallback(section="planned"),
        household,
        SimpleNamespace(id=2),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(clear=AsyncMock()),
        planned_expense_service=planned_service,
    )

    planned_service.list_active.assert_awaited_once_with(1)
    text = callback.message.edit_text.await_args.args[0]
    assert "Заплановані витрати" in text
    assert "Мобільний зв’язок — €11.00" in text
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "✅ Сплачено #42" in labels
    assert "📅 Перенести" in labels
    assert "✏️ Змінити" in labels
    assert "🗑 Видалити" in labels
    assert "← До меню" in labels


@pytest.mark.asyncio
async def test_empty_planned_expenses_menu_has_back_button() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await menu_callback(
        callback,
        MenuCallback(section="planned"),
        Household(id=1, telegram_chat_id=-100123, name="Сім’я", currency="EUR"),
        SimpleNamespace(id=2),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(clear=AsyncMock()),
        planned_expense_service=SimpleNamespace(list_active=AsyncMock(return_value=())),
    )

    assert "Запланованих витрат немає" in callback.message.edit_text.await_args.args[0]
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].text == "← Назад"


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
async def test_empty_category_list_offers_creation_and_templates() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    service = SimpleNamespace(list=AsyncMock(return_value=()))

    await category_menu_callback(
        callback,
        CategoryMenuCallback(action="list", value="expense"),
        Household(id=1, telegram_chat_id=-100123, name="Сім’я"),
        service,
        SimpleNamespace(),
    )

    text = callback.message.edit_text.await_args.args[0]
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "ще немає" in text
    assert "➕ Додати категорію" in labels
    assert "📚 Додати із шаблонів" in labels


@pytest.mark.asyncio
async def test_category_template_can_be_added_from_menu() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я")
    template = CategoryTemplate("groceries", "Продукти", TransactionType.EXPENSE)
    category = Category(
        id=20,
        household_id=1,
        code="groceries",
        name="Продукти",
        type=TransactionType.EXPENSE,
        is_active=True,
    )
    service = SimpleNamespace(
        list_templates=AsyncMock(return_value=(template,)),
        add_template=AsyncMock(return_value=category),
    )

    await category_menu_callback(
        callback,
        CategoryMenuCallback(action="templates", value="expense"),
        household,
        service,
        SimpleNamespace(),
    )
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert "➕ Продукти" in [button.text for row in keyboard.inline_keyboard for button in row]

    await category_menu_callback(
        callback,
        CategoryMenuCallback(action="add_template", value="groceries"),
        household,
        service,
        SimpleNamespace(),
    )
    service.add_template.assert_awaited_once_with(household_id=1, code="groceries")
    assert "Категорію додано" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_used_category_is_not_deleted_from_menu() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    category = Category(
        id=20,
        household_id=1,
        code="groceries",
        name="Продукти",
        type=TransactionType.EXPENSE,
        is_active=True,
    )
    service = SimpleNamespace(
        list=AsyncMock(return_value=(category,)),
        delete_unused=AsyncMock(return_value=False),
    )

    await category_menu_callback(
        callback,
        CategoryMenuCallback(action="delete", value="groceries"),
        Household(id=1, telegram_chat_id=-100123, name="Сім’я"),
        service,
        SimpleNamespace(),
    )

    service.delete_unused.assert_awaited_once_with(household_id=1, code="groceries")
    assert "не видалено" in callback.message.edit_text.await_args.args[0]
    assert "приховати" in callback.message.edit_text.await_args.args[0]


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
