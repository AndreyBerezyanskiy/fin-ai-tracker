from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from application.services import (
    CategoryService,
    ExpenseChartService,
    ExportPeriod,
    ExportTooLargeError,
    FinanceExportService,
    HouseholdSettingsService,
    PlannedExpenseService,
    TransactionService,
)
from bot.handlers.commands import (
    _currency_prefix,
    _format_transaction_line,
    _planned_expenses_text,
    _stats_period,
    _today,
    _uncategorized_text,
)
from bot.keyboards import (
    CategoryMenuCallback,
    ExpenseChartCallback,
    ExportCallback,
    MenuCallback,
    SettingsMenuCallback,
    back_to_main_keyboard,
    cancel_category_edit_keyboard,
    category_detail_keyboard,
    category_list_keyboard,
    category_templates_keyboard,
    category_types_keyboard,
    confirm_category_delete_keyboard,
    currency_keyboard,
    export_confirmation_keyboard,
    export_menu_keyboard,
    export_months_keyboard,
    export_years_keyboard,
    main_menu_keyboard,
    month_selection_keyboard,
    planned_expenses_menu_keyboard,
    settings_keyboard,
    statistics_result_keyboard,
    stats_menu_keyboard,
    timezone_keyboard,
    uncategorized_transactions_keyboard,
)
from domain.enums import TransactionType
from infrastructure.database import Category, Household, Member


class CategoryEditStates(StatesGroup):
    add_name = State()
    rename_name = State()


async def menu_command(
    message: Message, state: FSMContext, is_access_admin: bool = False
) -> None:
    await state.clear()
    await message.answer(
        "Головне меню", reply_markup=main_menu_keyboard(is_access_admin=is_access_admin)
    )


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
    planned_expense_service: PlannedExpenseService | None = None,
    is_access_admin: bool = False,
) -> None:
    section = callback_data.section
    if section == "home":
        await state.clear()
        await _edit(
            callback,
            "Головне меню",
            reply_markup=main_menu_keyboard(is_access_admin=is_access_admin),
        )
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
            reply_markup=(
                statistics_result_keyboard(
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
                if any(item.type is TransactionType.EXPENSE for item in statistics)
                else stats_menu_keyboard()
            ),
        )
        return
    if section == "categories":
        await state.clear()
        await _edit(callback, "Оберіть тип категорій", reply_markup=category_types_keyboard())
        return
    if section == "planned":
        if planned_expense_service is None:
            await callback.answer("Сервіс запланованих витрат недоступний.", show_alert=True)
            return
        plans = await planned_expense_service.list_active(household.id)
        if not plans:
            await _edit(
                callback,
                "📌 Запланованих витрат немає.\n\n"
                "Напишіть, наприклад: «Заплануй страховку 100 євро на 28 жовтня».",
                reply_markup=back_to_main_keyboard(),
            )
            return
        await _edit(
            callback,
            _planned_expenses_text(plans, household.currency),
            reply_markup=planned_expenses_menu_keyboard(plans),
        )
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
    if section == "uncategorized":
        transactions = await transaction_service.list_uncategorized(household_id=household.id)
        await _edit(
            callback,
            _uncategorized_text(transactions),
            reply_markup=(
                uncategorized_transactions_keyboard(transactions)
                if transactions
                else back_to_main_keyboard()
            ),
        )
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
            reply_markup=settings_keyboard(
                reports_enabled=household.reports_enabled, currency=household.currency
            ),
        )
        return
    await callback.answer("Невідомий розділ.", show_alert=True)


async def expense_chart_callback(
    callback: CallbackQuery,
    callback_data: ExpenseChartCallback,
    household: Household,
    expense_chart_service: ExpenseChartService,
) -> None:
    try:
        start_date = date.fromisoformat(callback_data.start_date)
        end_date = date.fromisoformat(callback_data.end_date)
    except ValueError:
        await callback.answer("Некоректний період статистики.", show_alert=True)
        return
    if end_date <= start_date or end_date - start_date > timedelta(days=3660):
        await callback.answer("Некоректний період статистики.", show_alert=True)
        return

    result = await expense_chart_service.create(
        household_id=household.id,
        start_date=start_date,
        end_date=end_date,
        currency=household.currency,
    )
    if result is None:
        await callback.answer("За цей період немає витрат.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    await callback.message.answer_photo(
        BufferedInputFile(
            result.image,
            filename=f"expenses-{start_date.isoformat()}-{end_date.isoformat()}.png",
        ),
        caption=(
            f"📊 Витрати • {result.data.period_label}\n"
            f"Усього: {_currency_prefix(household.currency)}{result.data.total:.2f}"
        ),
    )
    await callback.answer()


def _export_period(value: str) -> ExportPeriod:
    start_month, separator, end_month = value.partition("_")
    if not separator:
        raise ValueError("invalid export period")
    return ExportPeriod.months(start_month, end_month)


async def export_callback(
    callback: CallbackQuery,
    callback_data: ExportCallback,
    household: Household,
    finance_export_service: FinanceExportService,
) -> None:
    action = callback_data.action
    today = _today(household)
    if action == "confirm":
        value = callback_data.value
        full_current_year = f"{today.year:04d}-01_{today.year:04d}-12"
        if value == full_current_year:
            value = f"{today.year:04d}-01_{today.year:04d}-{today.month:02d}"
        period = _export_period(value)
        await _edit(
            callback,
            f"Період звіту: {period.label}",
            reply_markup=export_confirmation_keyboard(value),
        )
        return

    if action == "menu":
        await _edit(
            callback,
            "📥 Excel-звіт\n\nОберіть місяць, рік або проміжок цілих місяців.",
            reply_markup=export_menu_keyboard(today),
        )
        return

    if action in {"month_years", "years", "range_years"}:
        years = await finance_export_service.available_years(
            household_id=household.id, current_year=today.year
        )
        next_action = {
            "month_years": "select_month_year",
            "years": "confirm_year",
            "range_years": "range_start_year",
        }[action]
        title = {
            "month_years": "Оберіть рік",
            "years": "Оберіть рік звіту",
            "range_years": "Оберіть рік початку",
        }[action]
        await _edit(
            callback,
            title,
            reply_markup=export_years_keyboard(years, action=next_action),
        )
        return

    if action in {"select_month_year", "range_start_year"}:
        year = int(callback_data.value)
        maximum_month = today.month if year == today.year else 12
        await _edit(
            callback,
            "Оберіть місяць" if action == "select_month_year" else "Оберіть місяць початку",
            reply_markup=export_months_keyboard(
                year,
                action="confirm_month" if action == "select_month_year" else "range_start",
                maximum_month=maximum_month,
            ),
        )
        return

    if action == "confirm_month":
        value = f"{callback_data.value}_{callback_data.value}"
        period = _export_period(value)
        await _edit(
            callback,
            f"Період звіту: {period.label}",
            reply_markup=export_confirmation_keyboard(value),
        )
        return

    if action == "confirm_year":
        year = int(callback_data.value)
        end_month = today.month if year == today.year else 12
        value = f"{year:04d}-01_{year:04d}-{end_month:02d}"
        period = _export_period(value)
        await _edit(
            callback,
            f"Період звіту: {period.label}",
            reply_markup=export_confirmation_keyboard(value),
        )
        return

    if action == "range_start":
        start_month = callback_data.value
        start_year = int(start_month[:4])
        years = await finance_export_service.available_years(
            household_id=household.id, current_year=today.year
        )
        await _edit(
            callback,
            "Оберіть рік завершення",
            reply_markup=export_years_keyboard(
                (year for year in years if year >= start_year),
                action="range_end_year",
                prefix=start_month,
            ),
        )
        return

    if action == "range_end_year":
        start_month, year_text = callback_data.value.split("_", maxsplit=1)
        year = int(year_text)
        start_year = int(start_month[:4])
        minimum_month = int(start_month[5:]) if year == start_year else 1
        maximum_month = today.month if year == today.year else 12
        await _edit(
            callback,
            "Оберіть місяць завершення",
            reply_markup=export_months_keyboard(
                year,
                action="range_end",
                prefix=start_month,
                minimum_month=minimum_month,
                maximum_month=maximum_month,
            ),
        )
        return

    if action == "range_end":
        period = _export_period(callback_data.value)
        await _edit(
            callback,
            f"Період звіту: {period.label}",
            reply_markup=export_confirmation_keyboard(callback_data.value),
        )
        return

    if action == "generate":
        try:
            period = _export_period(callback_data.value)
            result = await finance_export_service.create(
                household_id=household.id,
                household_name=household.name,
                currency=household.currency,
                period=period,
            )
        except (ValueError, ExportTooLargeError) as error:
            if isinstance(error, ExportTooLargeError):
                text = "За цей період забагато операцій. Оберіть коротший проміжок."
            else:
                text = "Некоректний період звіту."
            await callback.answer(text, show_alert=True)
            return
        if result is None:
            await callback.answer("За цей період немає даних для звіту.", show_alert=True)
            return
        if callback.message is None:
            await callback.answer()
            return
        await callback.message.answer_document(
            BufferedInputFile(result.content, filename=result.filename),
            caption=f"📥 Excel-звіт • {result.report.period.label}",
        )
        await callback.answer()
        return

    await callback.answer("Невідома дія.", show_alert=True)


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
            (
                title
                if categories
                else f"{title}\n\nУ цьому чаті ще немає таких категорій. "
                "Створіть власну або виберіть потрібну із шаблонів."
            ),
            reply_markup=category_list_keyboard(
                categories, transaction_type=transaction_type.value
            ),
        )
        return
    if action == "templates":
        transaction_type = TransactionType(value)
        templates = await category_service.list_templates(
            household_id=household.id, transaction_type=transaction_type
        )
        title = (
            "Шаблони категорій витрат"
            if transaction_type is TransactionType.EXPENSE
            else "Шаблони категорій доходів"
        )
        await _edit(
            callback,
            title if templates else f"{title}\n\nУсі доступні шаблони вже додано.",
            reply_markup=category_templates_keyboard(
                templates, transaction_type=transaction_type.value
            ),
        )
        return
    if action == "add_template":
        category = await category_service.add_template(household_id=household.id, code=value)
        if category is None:
            await callback.answer("Шаблон недоступний або вже доданий.", show_alert=True)
            return
        await _edit(
            callback,
            f"Категорію додано: {category.name}",
            reply_markup=category_detail_keyboard(category),
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
            f"{category.name}\nСтатус: {status}",
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
    if action == "delete_prompt":
        category = await _effective_category(category_service, household.id, value)
        if category is None:
            await callback.answer("Категорію не знайдено.", show_alert=True)
            return
        await _edit(
            callback,
            f"Видалити категорію «{category.name}»?\n\n"
            "Якщо вона вже використовується, бот збереже її та запропонує приховати.",
            reply_markup=confirm_category_delete_keyboard(category),
        )
        return
    if action == "delete":
        category = await _effective_category(category_service, household.id, value)
        if category is None:
            await callback.answer("Категорію не знайдено.", show_alert=True)
            return
        result = await category_service.delete_unused(household_id=household.id, code=value)
        if result is False:
            await _edit(
                callback,
                f"Категорія «{category.name}» уже використовується, тому її не видалено. "
                "Її можна приховати — історія та статистика залишаться коректними.",
                reply_markup=category_detail_keyboard(category),
            )
            return
        if result is None:
            await callback.answer("Категорію не знайдено.", show_alert=True)
            return
        categories = tuple(
            item for item in await category_service.list(household.id) if item.type is category.type
        )
        await _edit(
            callback,
            f"Категорію «{category.name}» видалено.",
            reply_markup=category_list_keyboard(categories, transaction_type=category.type.value),
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
            reply_markup=settings_keyboard(
                reports_enabled=household.reports_enabled, currency=household.currency
            ),
        )
        return
    if callback_data.action == "reports":
        updated = await household_settings_service.toggle_reports(household.id)
        await _edit(
            callback,
            _settings_text(updated),
            reply_markup=settings_keyboard(
                reports_enabled=updated.reports_enabled, currency=updated.currency
            ),
        )
        return
    if callback_data.action == "currency" and callback_data.value == "menu":
        await _edit(
            callback,
            "Оберіть основну валюту.\n\n"
            "Вона застосовуватиметься до нових операцій і звітів. "
            "Старі операції не конвертуються і не ввійдуть у звіти нової валюти.",
            reply_markup=currency_keyboard(current_currency=household.currency),
        )
        return
    if callback_data.action == "currency":
        try:
            updated = await household_settings_service.set_currency(
                household_id=household.id, currency=callback_data.value
            )
        except ValueError:
            await callback.answer("Непідтримувана валюта.", show_alert=True)
            return
        await _edit(
            callback,
            f"Основну валюту змінено на {updated.currency}.\n\n{_settings_text(updated)}",
            reply_markup=settings_keyboard(
                reports_enabled=updated.reports_enabled, currency=updated.currency
            ),
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
            reply_markup=settings_keyboard(
                reports_enabled=updated.reports_enabled, currency=updated.currency
            ),
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
    router.callback_query.register(expense_chart_callback, ExpenseChartCallback.filter())
    router.callback_query.register(export_callback, ExportCallback.filter())
    router.callback_query.register(category_menu_callback, CategoryMenuCallback.filter())
    router.callback_query.register(settings_menu_callback, SettingsMenuCallback.filter())
    return router


__all__ = [
    "CategoryEditStates",
    "create_menu_router",
    "expense_chart_callback",
    "export_callback",
]
