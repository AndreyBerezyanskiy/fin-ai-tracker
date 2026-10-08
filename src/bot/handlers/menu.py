from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from application.services import CategoryService, HouseholdSettingsService, TransactionService
from bot.handlers.commands import _currency_prefix, _format_transaction_line, _stats_period, _today
from bot.keyboards import (
    CategoryMenuCallback,
    MenuCallback,
    SettingsMenuCallback,
    back_to_main_keyboard,
    cancel_category_edit_keyboard,
    category_detail_keyboard,
    category_list_keyboard,
    category_types_keyboard,
    main_menu_keyboard,
    month_selection_keyboard,
    settings_keyboard,
    stats_menu_keyboard,
    timezone_keyboard,
)
from domain.enums import TransactionType
from infrastructure.database import Category, Household, Member


class CategoryEditStates(StatesGroup):
    add_name = State()
    rename_name = State()


async def menu_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Головне меню", reply_markup=main_menu_keyboard())


async def _edit(callback: CallbackQuery, text: str, *, reply_markup: object) -> None:
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer()


def _statistics_text(title: str, statistics: tuple[object, ...], currency: str) -> str:
    if not statistics:
        return f"{title}\nПідтверджених операцій немає."
    lines = [title]
    for transaction_type, section in (
        (TransactionType.EXPENSE, "Витрати"),
        (TransactionType.INCOME, "Доходи"),
    ):
        items = [item for item in statistics if item.type is transaction_type]  # type: ignore[attr-defined]
        if not items:
            continue
        lines.extend(["", f"{section}:"])
        lines.extend(
            f"• {item.name}: {_currency_prefix(currency)}{item.amount:.2f}"  # type: ignore[attr-defined]
            for item in items
        )
        total = sum((item.amount for item in items), Decimal("0.00"))  # type: ignore[attr-defined]
        lines.append(f"Разом: {_currency_prefix(currency)}{total:.2f}")
    return "\n".join(lines)


def _recent_months(today: date, count: int = 6) -> tuple[str, ...]:
    year, month = today.year, today.month
    result: list[str] = []
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return tuple(result)


def _settings_text(household: Household) -> str:
    reports = "увімкнені" if household.reports_enabled else "вимкнені"
    return (
        "Налаштування групи\n"
        f"Валюта: {household.currency}\n"
        f"Часовий пояс: {household.timezone}\n"
        f"Автоматичні звіти: {reports}"
    )


async def menu_callback(
    callback: CallbackQuery,
    callback_data: MenuCallback,
    household: Household,
    member: Member,
    transaction_service: TransactionService,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    section = callback_data.section
    if section == "home":
        await state.clear()
        await _edit(callback, "Головне меню", reply_markup=main_menu_keyboard())
        return
    if section == "stats":
        await _edit(callback, "Оберіть період статистики", reply_markup=stats_menu_keyboard())
        return
    if section == "stats_choose":
        await _edit(
            callback,
            "Оберіть місяць",
            reply_markup=month_selection_keyboard(_recent_months(_today(household))),
        )
        return
    if section in {"stats_today", "stats_month"} or section.startswith("stats_20"):
        today = _today(household)
        argument = (
            "today"
            if section == "stats_today"
            else section.removeprefix("stats_")
            if section.startswith("stats_20")
            else ""
        )
        start_date, end_date, title = _stats_period(argument, today) or (
            today,
            today + timedelta(days=1),
            "Статистика",
        )
        statistics = await category_service.statistics(
            household_id=household.id, start_date=start_date, end_date=end_date
        )
        await _edit(
            callback,
            _statistics_text(title, statistics, household.currency),
            reply_markup=stats_menu_keyboard(),
        )
        return
    if section == "categories":
        await state.clear()
        await _edit(callback, "Оберіть тип категорій", reply_markup=category_types_keyboard())
        return
    if section == "last":
        transactions = await transaction_service.list_recent(
            household_id=household.id, currency=household.currency
        )
        lines = ["Останні підтверджені операції:"]
        lines.extend(
            f"{_format_transaction_line(item)} · {item.transaction_date:%d.%m.%Y}"
            for item in transactions
        )
        if not transactions:
            lines.append("Операцій ще немає.")
        await _edit(callback, "\n".join(lines), reply_markup=back_to_main_keyboard())
        return
    if section == "undo":
        transaction = await transaction_service.undo_last(
            household_id=household.id, member_id=member.id
        )
        text = (
            f"Скасовано: {_format_transaction_line(transaction)}"
            if transaction is not None
            else "Немає підтвердженої операції для скасування."
        )
        await _edit(callback, text, reply_markup=back_to_main_keyboard())
        return
    if section == "settings":
        await _edit(
            callback,
            _settings_text(household),
            reply_markup=settings_keyboard(reports_enabled=household.reports_enabled),
        )
        return
    await callback.answer("Невідомий розділ.", show_alert=True)


async def _effective_category(
    category_service: CategoryService, household_id: int, code: str
) -> Category | None:
    return next(
        (item for item in await category_service.list(household_id) if item.code == code), None
    )


async def category_menu_callback(
    callback: CallbackQuery,
    callback_data: CategoryMenuCallback,
    household: Household,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    action, value = callback_data.action, callback_data.value
    if action == "cancel":
        await state.clear()
        await _edit(callback, "Головне меню", reply_markup=main_menu_keyboard())
        return
    if action == "list":
        transaction_type = TransactionType(value)
        categories = tuple(
            item
            for item in await category_service.list(household.id)
            if item.type is transaction_type
        )
        title = (
            "Категорії витрат"
            if transaction_type is TransactionType.EXPENSE
            else "Категорії доходів"
        )
        await _edit(
            callback,
            title,
            reply_markup=category_list_keyboard(
                categories, transaction_type=transaction_type.value
            ),
        )
        return
    if action == "view":
        category = await _effective_category(category_service, household.id, value)
        if category is None:
            await callback.answer("Категорію не знайдено.", show_alert=True)
            return
        status = "активна" if category.is_active else "прихована"
        await _edit(
            callback,
            f"{category.name}\nКод: {category.code}\nСтатус: {status}",
            reply_markup=category_detail_keyboard(category),
        )
        return
    if action in {"add", "rename"}:
        target_state = (
            CategoryEditStates.add_name if action == "add" else CategoryEditStates.rename_name
        )
        await state.set_state(target_state)
        await state.update_data(
            **({"transaction_type": value} if action == "add" else {"category_code": value})
        )
        await _edit(
            callback,
            "Введіть назву нової категорії."
            if action == "add"
            else "Введіть нову назву категорії.",
            reply_markup=cancel_category_edit_keyboard(),
        )
        return
    if action in {"hide", "show"}:
        category = await category_service.set_active(
            household_id=household.id, code=value, is_active=action == "show"
        )
        if category is None:
            await callback.answer("Категорію не знайдено.", show_alert=True)
            return
        await _edit(
            callback,
            f"{category.name}\nСтатус: {'активна' if category.is_active else 'прихована'}",
            reply_markup=category_detail_keyboard(category),
        )
        return
    await callback.answer("Невідома дія.", show_alert=True)


async def settings_menu_callback(
    callback: CallbackQuery,
    callback_data: SettingsMenuCallback,
    household: Household,
    household_settings_service: HouseholdSettingsService,
) -> None:
    if callback_data.action == "back":
        await _edit(
            callback,
            _settings_text(household),
            reply_markup=settings_keyboard(reports_enabled=household.reports_enabled),
        )
        return
    if callback_data.action == "reports":
        updated = await household_settings_service.toggle_reports(household.id)
        await _edit(
            callback,
            _settings_text(updated),
            reply_markup=settings_keyboard(reports_enabled=updated.reports_enabled),
        )
        return
    if callback_data.action == "timezone" and callback_data.value == "menu":
        await _edit(callback, "Оберіть часовий пояс", reply_markup=timezone_keyboard())
        return
    if callback_data.action == "timezone":
        updated = await household_settings_service.set_timezone(
            household_id=household.id, timezone=callback_data.value
        )
        await _edit(
            callback,
            _settings_text(updated),
            reply_markup=settings_keyboard(reports_enabled=updated.reports_enabled),
        )
        return
    await callback.answer("Невідома дія.", show_alert=True)


def _category_name(message: Message) -> str | None:
    name = (message.text or "").strip()
    return name[:255] if name and not name.startswith("/") else None


async def add_category_name(
    message: Message,
    household: Household,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    name = _category_name(message)
    if name is None:
        await message.answer("Введіть назву категорії або натисніть «Скасувати».")
        return
    data = await state.get_data()
    category = await category_service.add(
        household_id=household.id,
        transaction_type=TransactionType(data["transaction_type"]),
        name=name,
    )
    await state.clear()
    await message.answer(
        f"Категорію створено: {category.name}", reply_markup=category_detail_keyboard(category)
    )


async def rename_category_name(
    message: Message,
    household: Household,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    name = _category_name(message)
    if name is None:
        await message.answer("Введіть нову назву або натисніть «Скасувати».")
        return
    data = await state.get_data()
    category = await category_service.rename(
        household_id=household.id, code=data["category_code"], name=name
    )
    await state.clear()
    if category is None:
        await message.answer("Категорію не знайдено.", reply_markup=main_menu_keyboard())
        return
    await message.answer(
        f"Категорію перейменовано: {category.name}",
        reply_markup=category_detail_keyboard(category),
    )


def create_menu_router() -> Router:
    router = Router(name="menu")
    router.message.register(menu_command, Command("menu"))
    router.message.register(add_category_name, StateFilter(CategoryEditStates.add_name))
    router.message.register(rename_category_name, StateFilter(CategoryEditStates.rename_name))
    router.callback_query.register(menu_callback, MenuCallback.filter())
    router.callback_query.register(category_menu_callback, CategoryMenuCallback.filter())
    router.callback_query.register(settings_menu_callback, SettingsMenuCallback.filter())
    return router


__all__ = ["CategoryEditStates", "create_menu_router"]
