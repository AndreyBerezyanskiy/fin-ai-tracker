from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, Message, ReactionTypeEmoji

from application.rate_limit import AIRateLimiter
from application.services import (
    AdviceService,
    AutomaticReportService,
    BudgetService,
    BudgetStatus,
    CategoryService,
    ClarificationService,
    HouseholdSettingsService,
    MonthReport,
    PeriodTotals,
    PlannedExpenseService,
    ReportService,
    TransactionRecognitionService,
    TransactionService,
)
from application.services.clarifications import MAX_CLARIFICATION_ATTEMPTS
from bot.keyboards import (
    BudgetActionCallback,
    CategoryChoiceCallback,
    ClarificationCallback,
    PlannedExpenseActionCallback,
    TransactionActionCallback,
    UncategorizedCallback,
    budget_status_keyboard,
    category_choice_keyboard,
    clarification_keyboard,
    pending_transactions_keyboard,
    planned_expense_confirmation_keyboard,
    planned_expense_edit_cancel_keyboard,
    planned_expense_edit_keyboard,
    planned_expenses_keyboard,
    settings_keyboard,
    statistics_result_keyboard,
    transaction_category_keyboard,
    uncategorized_transactions_keyboard,
)
from domain.currencies import currency_prefix
from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    ReportType,
    TransactionStatus,
    TransactionType,
)
from domain.models import RecognitionIntent, RecognizedStatisticsQuery
from infrastructure.database import Household, Member, TransactionClarification
from infrastructure.observability import pseudonymize

logger = logging.getLogger(__name__)

AI_UNAVAILABLE_MESSAGE = (
    "Не вдалося обробити повідомлення. Спробуйте ще раз або скористайтеся /help."
)

HELP_TEXT = """Доступні команди:
/start — підключити цю сімейну групу
/menu — відкрити головне меню
/help — показати цю інструкцію
/today — підсумок за сьогодні
/month — підсумок за поточний місяць
/budget — доходи, витрати та ліміти витрат
/advice — короткі спостереження на основі місячного звіту
/report — сформувати поточний звіт
/reports_on — увімкнути автоматичні звіти
/reports_off — вимкнути автоматичні звіти
/set_budget 2000 — встановити ліміт витрат на поточний місяць
/set_category_budget groceries 600 — встановити ліміт категорії
/last — останні підтверджені операції
/uncategorized — операції, яким потрібно призначити категорію
/planned — заплановані витрати й резерви
/undo — скасувати свою останню підтверджену операцію
/cancel — скасувати активне уточнення
/categories — список категорій
/category_add expense Назва — додати категорію
/category_rename code Нова назва — перейменувати категорію
/category_hide code — приховати категорію
/category_show code — повернути категорію
/stats — статистика за категоріями за поточний місяць
/settings — налаштування групи

Надсилайте витрати й доходи звичайним текстом, наприклад: «кава 4,50».
Плани теж можна додавати текстом: «Заплануй страховку 100 євро на 28 жовтня».
Статистику можна запитати так: «Покажи витрати за останні 40 днів».
Щоб бот надалі бачив такі повідомлення, вимкніть Privacy Mode через @BotFather."""


class TransactionCategoryStates(StatesGroup):
    new_name = State()


def _format_totals(title: str, totals: PeriodTotals, currency: str) -> str:
    def amount(value: Decimal) -> str:
        return f"{value:.2f} {currency}"

    lines = [title]
    if totals.income != 0:
        lines.append(f"Доходи: {amount(totals.income)}")
    if totals.expense != 0:
        lines.append(f"Витрати: {amount(totals.expense)}")
    lines.append(f"Баланс: {amount(totals.balance)}")
    return "\n".join(lines)


async def _answer_statistics_query(
    message: Message,
    *,
    household: Household,
    category_service: CategoryService,
    query: RecognizedStatisticsQuery,
) -> None:
    statistics = await category_service.statistics(
        household_id=household.id,
        start_date=query.start_date,
        end_date=query.end_date + timedelta(days=1),
    )
    items = tuple(
        item
        for item in statistics
        if query.transaction_type is None or item.type is query.transaction_type
    )
    subject = {
        TransactionType.INCOME: "доходів",
        TransactionType.EXPENSE: "витрат",
        None: "доходів і витрат",
    }[query.transaction_type]
    lines = [
        f"📊 Статистика {subject}",
        f"📅 {query.start_date:%d.%m.%Y} — {query.end_date:%d.%m.%Y}",
    ]
    if not items:
        lines.extend(["", "Підтверджених операцій за цей період немає."])
        await message.answer("\n".join(lines))
        return
    for transaction_type, section in (
        (TransactionType.EXPENSE, "Витрати"),
        (TransactionType.INCOME, "Доходи"),
    ):
        section_items = tuple(item for item in items if item.type is transaction_type)
        if not section_items:
            continue
        lines.extend(["", f"{section}:"])
        lines.extend(
            f"• {item.name}: {_currency_prefix(household.currency)}{item.amount:.2f}"
            for item in section_items
        )
        total = sum((item.amount for item in section_items), Decimal("0.00"))
        lines.append(f"Разом: {_currency_prefix(household.currency)}{total:.2f}")
    has_expenses = any(item.type is TransactionType.EXPENSE for item in items)
    await message.answer(
        "\n".join(lines),
        reply_markup=(
            statistics_result_keyboard(
                start_date=query.start_date.isoformat(),
                end_date=(query.end_date + timedelta(days=1)).isoformat(),
            )
            if has_expenses
            else None
        ),
    )


def _today(household: Household) -> date:
    return datetime.now(ZoneInfo(household.timezone)).date()


async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT)


async def today_command(
    message: Message,
    household: Household,
    report_service: ReportService,
) -> None:
    today = _today(household)
    totals = await report_service.totals(
        household_id=household.id,
        start_date=today,
        end_date=today + timedelta(days=1),
        currency=household.currency,
    )
    transactions = await report_service.recent_transactions(
        household_id=household.id,
        start_date=today,
        end_date=today + timedelta(days=1),
        currency=household.currency,
    )
    lines = [_format_totals("Сьогодні", totals, household.currency), "", "Останні операції:"]
    lines.extend(_format_transaction_line(item) for item in transactions)
    if not transactions:
        lines.append("Підтверджених операцій сьогодні немає.")
    await message.answer("\n".join(lines))


def _month_report_text(report: MonthReport, currency: str) -> str:
    lines = [_format_totals("Цього місяця", report.totals, currency)]
    if report.expense_categories:
        lines.extend(["", "Витрати за категоріями:"])
        lines.extend(
            f"• {item.name}: {_currency_prefix(currency)}{item.amount:.2f}"
            for item in report.expense_categories
        )
    if report.previous_totals is not None:
        previous = report.previous_totals
        lines.extend(
            [
                "",
                "Порівняно з попереднім місяцем:",
                f"• доходи: {_signed_amount(report.totals.income - previous.income, currency)}",
                f"• витрати: {_signed_amount(report.totals.expense - previous.expense, currency)}",
            ]
        )
    return "\n".join(lines)


def _signed_amount(value: Decimal, currency: str) -> str:
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{_currency_prefix(currency)}{abs(value):.2f}"


async def month_command(
    message: Message,
    household: Household,
    report_service: ReportService,
) -> None:
    today = _today(household)
    start_date = today.replace(day=1)
    end_date = (
        date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    )
    previous_start = (
        date(start_date.year - 1, 12, 1)
        if start_date.month == 1
        else date(start_date.year, start_date.month - 1, 1)
    )
    report = await report_service.month_report(
        household_id=household.id,
        start_date=start_date,
        end_date=end_date,
        previous_start_date=previous_start,
        currency=household.currency,
    )
    await message.answer(_month_report_text(report, household.currency))


def _parse_budget_amount(raw: str) -> Decimal | None:
    try:
        amount = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999999999.99"):
        return None
    return amount.quantize(Decimal("0.01"))


async def set_budget_command(
    message: Message,
    household: Household,
    budget_service: BudgetService,
) -> None:
    amount = _parse_budget_amount(_command_arguments(message))
    if amount is None:
        await message.answer("Формат: /set_budget 2000")
        return
    today = _today(household)
    await budget_service.set_total(
        household_id=household.id,
        year=today.year,
        month=today.month,
        amount=amount,
    )
    await message.answer(
        f"Місячний ліміт витрат встановлено: "
        f"{_currency_prefix(household.currency)}{amount:.2f}"
    )


async def set_category_budget_command(
    message: Message,
    household: Household,
    budget_service: BudgetService,
) -> None:
    parts = _command_arguments(message).split()
    amount = _parse_budget_amount(parts[1]) if len(parts) == 2 else None
    if len(parts) != 2 or amount is None:
        await message.answer("Формат: /set_category_budget groceries 600")
        return
    today = _today(household)
    budget = await budget_service.set_category(
        household_id=household.id,
        year=today.year,
        month=today.month,
        category_code=parts[0],
        amount=amount,
    )
    if budget is None:
        await message.answer("Категорію витрат з таким кодом не знайдено.")
        return
    await message.answer(
        f"Ліміт для {parts[0]} встановлено: {_currency_prefix(household.currency)}{amount:.2f}"
    )


def _percentage(value: Decimal | None) -> str:
    return "не визначено" if value is None else f"{value:.1f}%"


def _budget_text(status: BudgetStatus, currency: str) -> str:
    prefix = _currency_prefix(currency)
    lines = [
        "Фінанси цього місяця",
        f"Надходження: {prefix}{status.income:.2f}",
        f"Витрати: {prefix}{status.spent:.2f}",
        f"Баланс місяця: {_signed_amount(status.balance, currency)}",
        "",
    ]
    if status.total_limit is None:
        lines.append("Ліміт витрат не встановлено.")
    else:
        lines.extend(
            [
                f"Ліміт витрат: {prefix}{status.total_limit:.2f}",
                "Залишок за лімітом: "
                f"{_format_balance(status.remaining or Decimal('0.00'), currency)}",
                f"Використано: {_percentage(status.percentage)}",
            ]
        )
    if status.reserved != 0:
        safe = (
            f"{prefix}{status.safe_to_spend:.2f}"
            if status.safe_to_spend >= 0
            else _signed_amount(status.safe_to_spend, currency)
        )
        lines.extend(
            [
                f"Заплановані витрати: {prefix}{status.reserved:.2f}",
                f"Безпечно витратити: {safe}",
            ]
        )
    lines.append(f"Днів до кінця місяця: {status.days_remaining}")
    lines.extend(["", "Ліміти категорій:"])
    if not status.categories:
        lines.append("Не встановлено.")
    for item in status.categories:
        lines.append(
            f"• {item.name}: витрачено {prefix}{item.spent:.2f} з {prefix}{item.limit:.2f}; "
            f"залишилося {_format_balance(item.remaining, currency)}; "
            f"використано {_percentage(item.percentage)}"
        )
    return "\n".join(lines)


async def budget_command(
    message: Message,
    household: Household,
    budget_service: BudgetService,
) -> None:
    status = await budget_service.status(
        household_id=household.id,
        currency=household.currency,
        today=_today(household),
    )
    await message.answer(
        _budget_text(status, household.currency),
        reply_markup=budget_status_keyboard(
            can_set_from_income=status.total_limit is None and status.income > 0
        ),
    )


async def budget_callback(
    callback: CallbackQuery,
    callback_data: BudgetActionCallback,
    household: Household,
    budget_service: BudgetService,
) -> None:
    if callback_data.action != "set_from_income":
        await callback.answer("Невідома дія.", show_alert=True)
        return
    today = _today(household)
    status = await budget_service.status(
        household_id=household.id,
        currency=household.currency,
        today=today,
    )
    if status.total_limit is not None:
        await callback.answer("Ліміт витрат уже встановлено.", show_alert=True)
        return
    if status.income <= 0:
        await callback.answer("Цього місяця ще немає підтверджених доходів.", show_alert=True)
        return
    await budget_service.set_total(
        household_id=household.id,
        year=today.year,
        month=today.month,
        amount=status.income,
    )
    updated = await budget_service.status(
        household_id=household.id,
        currency=household.currency,
        today=today,
    )
    if callback.message is not None:
        await callback.message.edit_text(_budget_text(updated, household.currency))
    await callback.answer("Ліміт витрат встановлено.")


async def advice_command(
    message: Message,
    household: Household,
    advice_service: AdviceService,
    ai_rate_limiter: AIRateLimiter | None = None,
) -> None:
    if ai_rate_limiter is not None and not ai_rate_limiter.allow(household.id):
        await message.answer("Забагато запитів. Зачекайте хвилину та спробуйте ще раз.")
        return
    try:
        observations = await advice_service.generate(
            household_id=household.id,
            currency=household.currency,
            today=_today(household),
        )
    except Exception:
        logger.warning("AI advice unavailable: household_ref=%s", pseudonymize(household.id))
        await message.answer("Не вдалося сформувати надійну пораду. Спробуйте ще раз пізніше.")
        return
    text = "💡 Поради для вашого бюджету\n\n" + "\n".join(f"• {item}" for item in observations)
    await message.answer(text)


async def report_command(
    message: Message,
    household: Household,
    automatic_report_service: AutomaticReportService,
) -> None:
    today = _today(household)
    text = await automatic_report_service.render(
        household=household,
        report_type=ReportType.EVENING,
        local_date=today,
    )
    await message.answer(text)


async def _set_reports_enabled(
    message: Message,
    household: Household,
    household_settings_service: HouseholdSettingsService,
    *,
    enabled: bool,
) -> None:
    await household_settings_service.set_reports_enabled(household.id, enabled=enabled)
    state = "увімкнено" if enabled else "вимкнено"
    await message.answer(f"Автоматичні ранкові та вечірні звіти {state}.")


async def reports_on_command(
    message: Message,
    household: Household,
    household_settings_service: HouseholdSettingsService,
) -> None:
    await _set_reports_enabled(message, household, household_settings_service, enabled=True)


async def reports_off_command(
    message: Message,
    household: Household,
    household_settings_service: HouseholdSettingsService,
) -> None:
    await _set_reports_enabled(message, household, household_settings_service, enabled=False)


async def settings_command(message: Message, household: Household) -> None:
    reports = "увімкнені" if household.reports_enabled else "вимкнені"
    await message.answer(
        "Налаштування групи\n"
        f"Валюта: {household.currency}\n"
        f"Часовий пояс: {household.timezone}\n"
        f"Автоматичні звіти: {reports}",
        reply_markup=settings_keyboard(
            reports_enabled=household.reports_enabled, currency=household.currency
        ),
    )


def _amount_prefix(transaction_type: TransactionType) -> str:
    return "−" if transaction_type is TransactionType.EXPENSE else "+"


def _currency_prefix(currency: str) -> str:
    return currency_prefix(currency)


def _format_transaction_line(transaction: object) -> str:
    transaction_type = transaction.type  # type: ignore[attr-defined]
    color = "🔴" if transaction_type is TransactionType.EXPENSE else "🟢"
    description = transaction.description or "Без опису"  # type: ignore[attr-defined]
    currency = transaction.currency  # type: ignore[attr-defined]
    amount = transaction.amount  # type: ignore[attr-defined]
    line = (
        f"{color} {_amount_prefix(transaction_type)}{_currency_prefix(currency)}"
        f"{amount:.2f} · {description}"
    )
    member = getattr(transaction, "member", None)
    beneficiary = getattr(transaction, "beneficiary", None)
    if member is not None and beneficiary is not None and beneficiary.id != member.id:
        line += f" · для {beneficiary.display_name}"
    return line


def _operation_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "операцію"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return "операції"
    return "операцій"


def _format_balance(balance: Decimal, currency: str) -> str:
    sign = "−" if balance < 0 else ""
    return f"{sign}{_currency_prefix(currency)}{abs(balance):.2f}"


def _format_transactions(
    transactions: tuple[object, ...],
    household: Household,
    *,
    title: str | None = None,
    balance: Decimal | None = None,
) -> str:
    count = len(transactions)
    lines = [title or f"🧾 Знайдено {count} {_operation_word(count)}", ""]
    dates = {
        transaction.transaction_date  # type: ignore[attr-defined]
        for transaction in transactions
    }
    show_date_on_each_line = len(dates) > 1
    for transaction in transactions:
        line = _format_transaction_line(transaction)
        if show_date_on_each_line:
            line += f" · {transaction.transaction_date:%d.%m.%Y}"  # type: ignore[attr-defined]
        lines.append(line)
        category = getattr(transaction, "category", None)
        if category is not None:
            lines.append(f"🏷️ Категорія: {category.name}")
    if len(dates) == 1:
        transaction_date = next(iter(dates))
        lines.extend(["", f"Дата: {transaction_date:%d.%m.%Y}"])
    if balance is not None:
        lines.extend(["", f"Залишок: {_format_balance(balance, household.currency)}"])
    return "\n".join(lines)


def _format_planned_expense(
    plan: object, *, title: str | None = None, show_recurrence: bool = True
) -> str:
    currency = plan.currency  # type: ignore[attr-defined]
    recurrence = getattr(plan, "recurrence", None)
    lines = [title or f"📌 План #{plan.id}", ""]  # type: ignore[attr-defined]
    lines.extend(
        [
            f"💶 Сума: {_currency_prefix(currency)}{plan.amount:.2f}",  # type: ignore[attr-defined]
            f"🏷️ Категорія: {plan.category.name}",  # type: ignore[attr-defined]
            f"📝 {plan.description}",  # type: ignore[attr-defined]
            f"📅 Дата: {plan.due_date:%d.%m.%Y}",  # type: ignore[attr-defined]
        ]
    )
    if show_recurrence and recurrence is not None:
        lines.append("🔁 Повторення: щомісяця")
    return "\n".join(lines)


def _format_planned_expense_confirmation(plan: object) -> str:
    details = _format_planned_expense(
        plan,
        title="📌 Запланована витрата",
        show_recurrence=False,
    )
    return f"{details}\n\n🔁 Як запланувати: один раз чи щомісяця?"


def _planned_expense_keyboard(plan: object):
    if plan.status is PlannedExpenseStatus.PENDING:  # type: ignore[attr-defined]
        return planned_expense_confirmation_keyboard(plan.id)  # type: ignore[attr-defined]
    return planned_expenses_keyboard((plan,))


def _planned_expense_edit_text(plan: object) -> str:
    return (
        f"{_format_planned_expense(plan, title='✏️ Редагування плану')}"
        "\n\nЩо потрібно змінити?"
    )


async def planned_command(
    message: Message,
    household: Household,
    planned_expense_service: PlannedExpenseService,
) -> None:
    plans = await planned_expense_service.list_active(household.id)
    if not plans:
        await message.answer(
            "📌 Запланованих витрат немає.\n\n"
            "Напишіть, наприклад: «Заплануй страховку 100 євро на 28 жовтня»."
        )
        return

    await message.answer(
        _planned_expenses_text(plans, household.currency),
        reply_markup=planned_expenses_keyboard(plans),
    )


def _planned_expenses_text(plans: tuple[object, ...], currency: str) -> str:
    total = sum((plan.amount for plan in plans), Decimal("0.00"))
    lines = ["📌 Заплановані витрати", ""]
    for plan in plans:
        repeat = " · щомісяця" if plan.recurrence is not None else ""
        lines.append(
            f"#{plan.id} · {plan.due_date:%d.%m.%Y} · {plan.description} — "
            f"{_currency_prefix(plan.currency)}{plan.amount:.2f}{repeat}"
        )
    lines.extend(["", f"Усього зарезервовано: {_currency_prefix(currency)}{total:.2f}"])
    return "\n".join(lines)


async def planned_expense_callback(
    callback: CallbackQuery,
    callback_data: PlannedExpenseActionCallback,
    household: Household,
    member: Member,
    planned_expense_service: PlannedExpenseService,
    clarification_service: ClarificationService,
    category_service: CategoryService | None = None,
) -> None:
    plan = await planned_expense_service.get(
        household.id, callback_data.planned_expense_id
    )
    if plan is None:
        await callback.answer("Заплановану витрату не знайдено.", show_alert=True)
        return
    action = callback_data.action
    if action == "edit":
        if plan.status not in {PlannedExpenseStatus.PENDING, PlannedExpenseStatus.SCHEDULED}:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        if callback.message is not None:
            await callback.message.edit_text(
                _planned_expense_edit_text(plan),
                reply_markup=planned_expense_edit_keyboard(plan.id),
            )
        await callback.answer()
        return
    if action in {"edit_back", "edit_cancel"}:
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=plan.id,
        )
        if callback.message is not None:
            await callback.message.edit_text(
                _format_planned_expense_confirmation(plan)
                if plan.status is PlannedExpenseStatus.PENDING
                else _format_planned_expense(plan),
                reply_markup=_planned_expense_keyboard(plan),
            )
        await callback.answer()
        return
    if action in {"edit_amount", "edit_category", "edit_date", "edit_description"}:
        if plan.status not in {PlannedExpenseStatus.PENDING, PlannedExpenseStatus.SCHEDULED}:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        if callback.message is None:
            await callback.answer("Не вдалося почати редагування.", show_alert=True)
            return
        field = action.removeprefix("edit_")
        questions = {
            "amount": "💶 Яку нову суму встановити?",
            "category": f"🏷 Оберіть категорію для «{plan.description}».",
            "date": "📅 На яку дату запланувати оплату?",
            "description": "📝 Як змінити опис оплати?",
        }
        if field == "category" and category_service is None:
            await callback.answer("Сервіс категорій недоступний.", show_alert=True)
            return
        clarification = await clarification_service.begin_plan_edit(
            household_id=household.id,
            member_id=member.id,
            original_message_id=callback.message.message_id,
            planned_expense_id=plan.id,
            original_text=(
                f"Заплануй {plan.description}, {plan.amount} {plan.currency}, "
                f"на {plan.due_date.isoformat()}, категорія {plan.category.name}."
            ),
            field=field,
            question=questions[field],
            missing_category_type=(
                TransactionType.EXPENSE if field == "category" else None
            ),
        )
        if field == "category":
            categories = tuple(
                item
                for item in await category_service.list(household.id)
                if item.is_active and item.type is TransactionType.EXPENSE
            )
            await callback.message.edit_text(
                clarification.question,
                reply_markup=category_choice_keyboard(
                    clarification.id,
                    categories,
                    planned_expense_id=plan.id,
                ),
            )
        else:
            await callback.message.edit_text(
                clarification.question,
                reply_markup=planned_expense_edit_cancel_keyboard(plan.id),
            )
        await callback.answer()
        return
    if action in {"confirm_once", "confirm_monthly"}:
        recurrence = (
            PlannedExpenseRecurrence.MONTHLY if action == "confirm_monthly" else None
        )
        updated = await planned_expense_service.schedule(
            household_id=household.id,
            planned_expense_id=plan.id,
            recurrence=recurrence,
        )
        if updated is None:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=plan.id,
        )
        if callback.message is not None:
            title = (
                "✅ Щомісячну витрату заплановано"
                if recurrence is PlannedExpenseRecurrence.MONTHLY
                else "✅ Одноразову витрату заплановано"
            )
            await callback.message.edit_text(
                _format_planned_expense(updated, title=title)
            )
        await callback.answer()
        return
    if action == "cancel":
        updated = await planned_expense_service.set_status(
            household_id=household.id,
            planned_expense_id=plan.id,
            status=PlannedExpenseStatus.CANCELLED,
        )
        if updated is None:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=plan.id,
        )
        if callback.message is not None:
            await callback.message.edit_text(
                _format_planned_expense(updated, title="❌ Заплановану витрату скасовано")
            )
        await callback.answer()
        return
    if action == "paid":
        updated = await planned_expense_service.mark_paid(
            household=household,
            planned_expense_id=plan.id,
            paid_on=_today(household),
        )
        if updated is None:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=plan.id,
        )
        if callback.message is not None:
            await callback.message.edit_text(
                _format_planned_expense(updated, title="✅ Оплату записано у витрати")
            )
        await callback.answer()
        return
    if action == "postpone":
        if plan.status is not PlannedExpenseStatus.SCHEDULED:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        message_id = callback.message.message_id if callback.message is not None else 0
        await clarification_service.begin_plan_postpone(
            household_id=household.id,
            member_id=member.id,
            original_message_id=message_id,
            planned_expense_id=plan.id,
            original_text=(
                f"Заплануй витрату {plan.description} {plan.amount} {plan.currency} "
                f"у категорії {plan.category.name}"
            ),
        )
        if callback.message is not None:
            await callback.message.answer("📅 На яку дату перенести оплату?")
        await callback.answer()
        return
    await callback.answer("Невідома дія.", show_alert=True)


async def _resolve_plan_postpone(
    message: Message,
    *,
    answer_text: str,
    clarification: TransactionClarification,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    planned_expense_service: PlannedExpenseService,
    clarification_service: ClarificationService,
) -> None:
    result = await transaction_recognition_service.recognize(
        message=clarification.original_text,
        household=household,
        member=member,
        clarification_question=clarification.question,
        clarification_answer=answer_text,
    )
    if result.needs_clarification or not result.planned_expenses:
        if clarification.attempts + 1 >= MAX_CLARIFICATION_ATTEMPTS:
            await clarification_service.cancel(clarification.id)
            await message.answer("Не вдалося визначити дату. Перенесення скасовано.")
            return
        await clarification_service.advance(
            clarification.id,
            question=result.clarification_question or "Уточніть дату, будь ласка.",
        )
        await message.answer(result.clarification_question or "Уточніть дату, будь ласка.")
        return
    updated = await planned_expense_service.postpone(
        household_id=household.id,
        planned_expense_id=clarification.planned_expense_id or 0,
        due_date=result.planned_expenses[0].due_date,
    )
    await clarification_service.cancel(clarification.id)
    if updated is None:
        await message.answer("Заплановану витрату вже оброблено.")
        return
    await message.answer(_format_planned_expense(updated, title="📅 Оплату перенесено"))


async def _resolve_plan_edit(
    message: Message,
    *,
    answer_text: str,
    clarification: TransactionClarification,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    planned_expense_service: PlannedExpenseService,
    clarification_service: ClarificationService,
    category_service: CategoryService | None,
) -> None:
    field = clarification.kind.removeprefix("planned_edit_")
    result = await transaction_recognition_service.recognize(
        message=clarification.original_text,
        household=household,
        member=member,
        clarification_question=clarification.question,
        clarification_answer=answer_text,
    )
    if result.needs_clarification or not result.planned_expenses:
        if clarification.attempts + 1 >= MAX_CLARIFICATION_ATTEMPTS:
            await clarification_service.cancel(clarification.id)
            await message.answer("Не вдалося змінити план. Редагування скасовано.")
            return
        updated_context = await clarification_service.advance(
            clarification.id,
            question=result.clarification_question or clarification.question,
            missing_category_type=result.missing_category_type,
        )
        if updated_context is not None:
            await _answer_clarification_question(
                message,
                clarification=updated_context,
                household=household,
                category_service=category_service,
            )
        return
    recognized = result.planned_expenses[0]
    changes: dict[str, object] = {}
    if field == "amount":
        changes["amount"] = recognized.amount
    elif field == "date":
        changes["due_date"] = recognized.due_date
    elif field == "description":
        changes["description"] = recognized.description
    elif field == "category" and category_service is not None:
        category = next(
            (
                item
                for item in await category_service.list(household.id)
                if item.is_active
                and item.type is TransactionType.EXPENSE
                and item.code == recognized.category_code
            ),
            None,
        )
        if category is not None:
            changes["category_id"] = category.id
    if not changes:
        await message.answer("Не вдалося визначити нове значення. Спробуйте ще раз.")
        return
    updated = await planned_expense_service.update_details(
        household_id=household.id,
        planned_expense_id=clarification.planned_expense_id or 0,
        **changes,
    )
    await clarification_service.cancel(clarification.id)
    if updated is None:
        await message.answer("Цей план уже оброблено.")
        return
    await message.answer(
        _format_planned_expense_confirmation(updated)
        if updated.status is PlannedExpenseStatus.PENDING
        else _format_planned_expense(updated, title="✅ План оновлено"),
        reply_markup=_planned_expense_keyboard(updated),
    )


async def _answer_clarification_question(
    message: Message,
    *,
    clarification: TransactionClarification,
    household: Household,
    category_service: CategoryService | None,
) -> None:
    missing_category_type = getattr(clarification, "missing_category_type", None)
    if missing_category_type and category_service is not None:
        transaction_type = TransactionType(missing_category_type)
        categories = tuple(
            category
            for category in await category_service.list(household.id)
            if category.is_active and category.type is transaction_type
        )
        await message.answer(
            clarification.question,
            reply_markup=category_choice_keyboard(
                clarification.id,
                categories,
                    planned_expense_id=(
                        getattr(clarification, "planned_expense_id", None)
                        if getattr(clarification, "kind", None)
                        == "planned_edit_category"
                        else None
                    ),
            ),
        )
        return
    await message.answer(
        clarification.question,
        reply_markup=clarification_keyboard(
            clarification.id,
            has_suggestion=clarification.suggested_member_id is not None,
        ),
    )


async def recognize_plain_message(
    message: Message,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    transaction_service: TransactionService,
    ai_rate_limiter: AIRateLimiter | None = None,
    clarification_service: ClarificationService | None = None,
    category_service: CategoryService | None = None,
    max_message_length: int = 1000,
    store_original_text: bool = True,
    planned_expense_service: PlannedExpenseService | None = None,
    recognized_text: str | None = None,
) -> None:
    text = recognized_text if recognized_text is not None else message.text
    if not text:
        return
    if len(text) > max_message_length:
        await message.answer(
            f"Повідомлення надто довге. Скоротіть його до {max_message_length} символів."
        )
        return
    if ai_rate_limiter is not None and not ai_rate_limiter.allow(household.id):
        await message.answer("Забагато запитів. Зачекайте хвилину та спробуйте ще раз.")
        return
    if clarification_service is not None:
        clarification = await clarification_service.get(
            household_id=household.id, member_id=member.id
        )
        if clarification is not None:
            if clarification.expires_at <= datetime.now(UTC):
                await clarification_service.cancel(clarification.id)
                await message.answer(
                    "Час на уточнення минув. Надішліть список операцій ще раз."
                )
                return
            if clarification.kind == "planned_postpone":
                if planned_expense_service is None:
                    await message.answer("Сервіс запланованих витрат недоступний.")
                    return
                await _resolve_plan_postpone(
                    message,
                    answer_text=text,
                    clarification=clarification,
                    household=household,
                    member=member,
                    transaction_recognition_service=transaction_recognition_service,
                    planned_expense_service=planned_expense_service,
                    clarification_service=clarification_service,
                )
                return
            if clarification.awaiting_category_name:
                if category_service is None or not clarification.missing_category_type:
                    await message.answer("Не вдалося створити категорію. Скасуйте уточнення.")
                    return
                name = text.strip()[:255]
                if not name or name.startswith("/"):
                    await message.answer("Введіть назву категорії або скористайтеся /cancel.")
                    return
                category = await category_service.add(
                    household_id=household.id,
                    transaction_type=TransactionType(clarification.missing_category_type),
                    name=name,
                )
                if clarification.kind == "planned_edit_category":
                    if planned_expense_service is None:
                        await message.answer("Сервіс запланованих витрат недоступний.")
                        return
                    updated = await planned_expense_service.update_details(
                        household_id=household.id,
                        planned_expense_id=clarification.planned_expense_id or 0,
                        category_id=category.id,
                    )
                    await clarification_service.cancel(clarification.id)
                    if updated is None:
                        await message.answer("Цей план уже оброблено.")
                    else:
                        await message.answer(
                            _format_planned_expense_confirmation(updated)
                            if updated.status is PlannedExpenseStatus.PENDING
                            else _format_planned_expense(updated, title="✅ План оновлено"),
                            reply_markup=_planned_expense_keyboard(updated),
                        )
                    return
                await _resolve_clarification(
                    message,
                    answer=f"Створено й обрано категорію «{category.name}».",
                    clarification=clarification,
                    household=household,
                    member=member,
                    transaction_recognition_service=transaction_recognition_service,
                    transaction_service=transaction_service,
                    planned_expense_service=planned_expense_service,
                    clarification_service=clarification_service,
                    category_service=category_service,
                    store_original_text=store_original_text,
                )
                return
            if (clarification.kind or "transaction").startswith("planned_edit_"):
                if planned_expense_service is None:
                    await message.answer("Сервіс запланованих витрат недоступний.")
                    return
                await _resolve_plan_edit(
                    message,
                    answer_text=text,
                    clarification=clarification,
                    household=household,
                    member=member,
                    transaction_recognition_service=transaction_recognition_service,
                    planned_expense_service=planned_expense_service,
                    clarification_service=clarification_service,
                    category_service=category_service,
                )
                return
            await _resolve_clarification(
                message,
                answer=text,
                clarification=clarification,
                household=household,
                member=member,
                transaction_recognition_service=transaction_recognition_service,
                transaction_service=transaction_service,
                planned_expense_service=planned_expense_service,
                clarification_service=clarification_service,
                category_service=category_service,
                store_original_text=store_original_text,
            )
            return
    processing_message: Message | None = None
    try:
        processing_message = await message.reply("⏳ Обробляю…")
    except TelegramAPIError:
        logger.warning("Could not send processing indicator: message_id=%s", message.message_id)

    try:
        try:
            result = await transaction_recognition_service.recognize(
                message=text,
                household=household,
                member=member,
            )
        except Exception as error:
            logger.warning(
                "AI transaction recognition unavailable: error_type=%s",
                type(error).__name__,
            )
            await message.answer(AI_UNAVAILABLE_MESSAGE)
            return
        if result.intent is RecognitionIntent.NOT_A_TRANSACTION:
            return
        if result.needs_clarification:
            if clarification_service is None:
                await message.answer(result.clarification_question)
                return
            clarification = await clarification_service.begin(
                household_id=household.id,
                member_id=member.id,
                original_message_id=message.message_id,
                original_text=text,
                question=result.clarification_question or "Уточніть операцію, будь ласка.",
                ambiguous_member_name=result.ambiguous_member_name,
                suggested_member_id=result.suggested_member_id,
                missing_category_type=result.missing_category_type,
                **(
                    {"kind": "planned_create"}
                    if result.intent is RecognitionIntent.CREATE_PLANNED_EXPENSES
                    else {}
                ),
            )
            await _answer_clarification_question(
                message,
                clarification=clarification,
                household=household,
                category_service=category_service,
            )
            return
        if result.intent is RecognitionIntent.GET_STATISTICS:
            if category_service is None or result.statistics_query is None:
                await message.answer("Сервіс статистики недоступний.")
                return
            await _answer_statistics_query(
                message,
                household=household,
                category_service=category_service,
                query=result.statistics_query,
            )
            return
        if result.intent is RecognitionIntent.CREATE_PLANNED_EXPENSES:
            if planned_expense_service is None:
                await message.answer("Сервіс запланованих витрат недоступний.")
                return
            plans = await planned_expense_service.create_pending(
                household=household,
                member=member,
                telegram_message_id=message.message_id,
                original_text=text if store_original_text else "",
                recognition=result,
            )
            for plan in plans:
                await message.answer(
                    _format_planned_expense_confirmation(plan),
                    reply_markup=planned_expense_confirmation_keyboard(plan.id),
                )
            return
        pending = await transaction_service.create_pending(
            household=household,
            member=member,
            telegram_message_id=message.message_id,
            original_text=text if store_original_text else "",
            recognition=result,
        )
        await message.answer(
            _format_transactions(pending, household),
            reply_markup=pending_transactions_keyboard(message.message_id),
        )
    finally:
        if processing_message is not None:
            try:
                await processing_message.delete()
            except TelegramAPIError:
                logger.warning(
                    "Could not delete processing indicator: message_id=%s",
                    processing_message.message_id,
                )


def _is_affirmative(answer: str) -> bool:
    return answer.casefold().strip(" .!?") in {"так", "yes", "ага", "авжеж", "підтверджую"}


async def _resolve_clarification(
    message: Message,
    *,
    answer: str,
    clarification: TransactionClarification,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    transaction_service: TransactionService,
    planned_expense_service: PlannedExpenseService | None,
    clarification_service: ClarificationService,
    category_service: CategoryService | None,
    store_original_text: bool,
) -> None:
    processing_message: Message | None = None
    try:
        processing_message = await message.answer("⏳ Обробляю уточнення…")
        result = await transaction_recognition_service.recognize(
            message=clarification.original_text,
            household=household,
            member=member,
            clarification_question=clarification.question,
            clarification_answer=answer,
        )
        next_attempt = clarification.attempts + 1
        if result.intent is RecognitionIntent.NOT_A_TRANSACTION or result.needs_clarification:
            if next_attempt >= MAX_CLARIFICATION_ATTEMPTS:
                await clarification_service.cancel(clarification.id)
                await message.answer(
                    "Не вдалося завершити уточнення. Надішліть список операцій заново "
                    "або скористайтеся /cancel."
                )
                return
            question = (
                result.clarification_question
                if result.needs_clarification
                else "Не зрозумів відповідь. Уточніть, будь ласка, повним реченням."
            )
            updated = await clarification_service.advance(
                clarification.id,
                question=question,
                ambiguous_member_name=result.ambiguous_member_name,
                suggested_member_id=result.suggested_member_id,
                missing_category_type=result.missing_category_type,
            )
            if updated is not None:
                await _answer_clarification_question(
                    message,
                    clarification=updated,
                    household=household,
                    category_service=category_service,
                )
            return

        if result.intent is RecognitionIntent.GET_STATISTICS:
            if category_service is None or result.statistics_query is None:
                await message.answer("Сервіс статистики недоступний.")
                return
            await clarification_service.cancel(clarification.id)
            await _answer_statistics_query(
                message,
                household=household,
                category_service=category_service,
                query=result.statistics_query,
            )
            return

        if result.intent is RecognitionIntent.CREATE_PLANNED_EXPENSES:
            if planned_expense_service is None:
                await message.answer("Сервіс запланованих витрат недоступний.")
                return
            plans = await planned_expense_service.create_pending(
                household=household,
                member=member,
                telegram_message_id=clarification.original_message_id,
                original_text=clarification.original_text if store_original_text else "",
                recognition=result,
            )
            await clarification_service.cancel(clarification.id)
            for plan in plans:
                await message.answer(
                    _format_planned_expense_confirmation(plan),
                    reply_markup=planned_expense_confirmation_keyboard(plan.id),
                )
            return

        pending = await transaction_service.create_pending(
            household=household,
            member=member,
            telegram_message_id=clarification.original_message_id,
            original_text=clarification.original_text if store_original_text else "",
            recognition=result,
        )
        if (
            _is_affirmative(answer)
            and clarification.ambiguous_member_name
            and clarification.suggested_member_id
        ):
            await clarification_service.remember_alias(
                household_id=household.id,
                member_id=clarification.suggested_member_id,
                alias=clarification.ambiguous_member_name,
            )
        await clarification_service.cancel(clarification.id)
        await message.answer(
            _format_transactions(pending, household),
            reply_markup=pending_transactions_keyboard(
                clarification.original_message_id
            ),
        )
    except Exception as error:
        logger.warning("AI clarification unavailable: error_type=%s", type(error).__name__)
        await message.answer(AI_UNAVAILABLE_MESSAGE)
    finally:
        if processing_message is not None:
            try:
                await processing_message.delete()
            except TelegramAPIError:
                logger.warning(
                    "Could not delete clarification indicator: message_id=%s",
                    processing_message.message_id,
                )


async def cancel_clarification_command(
    message: Message,
    household: Household,
    member: Member,
    clarification_service: ClarificationService,
    state: FSMContext,
) -> None:
    cancelled = await clarification_service.cancel_for_member(
        household_id=household.id, member_id=member.id
    )
    had_state = await state.get_state() is not None
    await state.clear()
    await message.answer(
        "Уточнення скасовано." if cancelled or had_state else "Активного уточнення немає."
    )


async def clarification_callback(
    callback: CallbackQuery,
    callback_data: ClarificationCallback,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    transaction_service: TransactionService,
    clarification_service: ClarificationService,
    category_service: CategoryService | None = None,
    store_original_text: bool = True,
    planned_expense_service: PlannedExpenseService | None = None,
) -> None:
    clarification = await clarification_service.get_by_id(callback_data.clarification_id)
    if (
        clarification is None
        or clarification.household_id != household.id
        or clarification.member_id != member.id
    ):
        await callback.answer(
            "Це уточнення вже неактивне або належить іншому учаснику.",
            show_alert=True,
        )
        return
    if clarification.expires_at <= datetime.now(UTC):
        await clarification_service.cancel(clarification.id)
        await callback.answer("Час на уточнення минув.", show_alert=True)
        return
    if callback_data.action == "cancel":
        await clarification_service.cancel(clarification.id)
        if callback.message is not None:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Уточнення скасовано.")
        await callback.answer()
        return
    if callback_data.action == "no":
        updated = await clarification_service.advance(
            clarification.id,
            question="Кого саме ви мали на увазі? Напишіть точне ім’я.",
        )
        if callback.message is not None and updated is not None:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                updated.question,
                reply_markup=clarification_keyboard(updated.id, has_suggestion=False),
            )
        await callback.answer()
        return
    if callback_data.action != "yes" or clarification.suggested_member_id is None:
        await callback.answer("Невідома дія.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Не вдалося обробити відповідь.", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _resolve_clarification(
        callback.message,
        answer="так",
        clarification=clarification,
        household=household,
        member=member,
        transaction_recognition_service=transaction_recognition_service,
        transaction_service=transaction_service,
        planned_expense_service=planned_expense_service,
        clarification_service=clarification_service,
        category_service=category_service,
        store_original_text=store_original_text,
    )


async def category_choice_callback(
    callback: CallbackQuery,
    callback_data: CategoryChoiceCallback,
    household: Household,
    member: Member,
    transaction_recognition_service: TransactionRecognitionService,
    transaction_service: TransactionService,
    clarification_service: ClarificationService,
    category_service: CategoryService,
    store_original_text: bool = True,
    planned_expense_service: PlannedExpenseService | None = None,
) -> None:
    clarification = await clarification_service.get_by_id(callback_data.clarification_id)
    if (
        clarification is None
        or clarification.household_id != household.id
        or clarification.member_id != member.id
        or not clarification.missing_category_type
    ):
        await callback.answer("Це уточнення вже неактивне.", show_alert=True)
        return
    if clarification.expires_at <= datetime.now(UTC):
        await clarification_service.cancel(clarification.id)
        await callback.answer("Час на уточнення минув.", show_alert=True)
        return
    if callback_data.action == "new":
        updated = await clarification_service.request_category_name(clarification.id)
        if callback.message is not None and updated is not None:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                updated.question,
                reply_markup=(
                    planned_expense_edit_cancel_keyboard(
                        clarification.planned_expense_id
                    )
                    if clarification.kind == "planned_edit_category"
                    and clarification.planned_expense_id is not None
                    else None
                ),
            )
        await callback.answer()
        return
    if callback_data.action != "select":
        await callback.answer("Невідома дія.", show_alert=True)
        return
    transaction_type = TransactionType(clarification.missing_category_type)
    category = next(
        (
            item
            for item in await category_service.list(household.id)
            if item.id == callback_data.category_id
            and item.is_active
            and item.type is transaction_type
        ),
        None,
    )
    if category is None:
        await callback.answer("Категорія недоступна.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Не вдалося обробити відповідь.", show_alert=True)
        return
    if clarification.kind == "planned_edit_category":
        if planned_expense_service is None:
            await callback.answer(
                "Сервіс запланованих витрат недоступний.", show_alert=True
            )
            return
        updated = await planned_expense_service.update_details(
            household_id=household.id,
            planned_expense_id=clarification.planned_expense_id or 0,
            category_id=category.id,
        )
        await clarification_service.cancel(clarification.id)
        if updated is None:
            await callback.answer("Цей план уже оброблено.", show_alert=True)
            return
        await callback.message.edit_text(
            _format_planned_expense_confirmation(updated)
            if updated.status is PlannedExpenseStatus.PENDING
            else _format_planned_expense(updated, title="✅ План оновлено"),
            reply_markup=_planned_expense_keyboard(updated),
        )
        await callback.answer("Категорію змінено.")
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _resolve_clarification(
        callback.message,
        answer=f"Обрана категорія «{category.name}».",
        clarification=clarification,
        household=household,
        member=member,
        transaction_recognition_service=transaction_recognition_service,
        transaction_service=transaction_service,
        planned_expense_service=planned_expense_service,
        clarification_service=clarification_service,
        category_service=category_service,
        store_original_text=store_original_text,
    )


async def transaction_callback(
    callback: CallbackQuery,
    callback_data: TransactionActionCallback,
    household: Household,
    transaction_service: TransactionService,
) -> None:
    status_by_action = {
        "confirm": TransactionStatus.CONFIRMED,
        "cancel": TransactionStatus.CANCELLED,
    }
    status = status_by_action.get(callback_data.action)
    if status is None:
        await callback.answer("Невідома дія.", show_alert=True)
        return

    result = await transaction_service.transition_pending(
        household_id=household.id,
        telegram_message_id=callback_data.message_id,
        status=status,
    )
    if not result.transactions:
        await callback.answer("Операції не знайдено.", show_alert=True)
        return
    if not result.changed:
        await callback.answer("Ці операції вже оброблено.", show_alert=True)
        return

    label = "✅ Підтверджено" if status is TransactionStatus.CONFIRMED else "❌ Скасовано"
    count = len(result.transactions)
    title = f"{label} {count} {_operation_word(count)}"
    balance = None
    if status is TransactionStatus.CONFIRMED:
        balance = await transaction_service.confirmed_balance(
            household_id=household.id, currency=household.currency
        )
    if callback.message is not None:
        await callback.message.edit_text(
            _format_transactions(
                result.transactions,
                household,
                title=title,
                balance=balance,
            ),
            reply_markup=None,
        )
    if status is TransactionStatus.CONFIRMED:
        try:
            await callback.bot.set_message_reaction(
                chat_id=household.telegram_chat_id,
                message_id=callback_data.message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
                is_big=True,
            )
        except TelegramAPIError:
            logger.warning(
                "Could not react to confirmed transaction message: chat_ref=%s message_id=%s",
                pseudonymize(household.telegram_chat_id),
                callback_data.message_id,
            )
    await callback.answer(label)


async def last_command(
    message: Message,
    household: Household,
    transaction_service: TransactionService,
) -> None:
    transactions = await transaction_service.list_recent(
        household_id=household.id, currency=household.currency
    )
    if not transactions:
        await message.answer("Підтверджених операцій ще немає.")
        return
    lines = ["Останні підтверджені операції:"]
    for transaction in transactions:
        lines.append(
            f"{_format_transaction_line(transaction)} · {transaction.transaction_date:%d.%m.%Y}"
        )
    await message.answer("\n".join(lines))


async def undo_command(
    message: Message,
    household: Household,
    member: Member,
    transaction_service: TransactionService,
) -> None:
    transaction = await transaction_service.undo_last(
        household_id=household.id,
        member_id=member.id,
    )
    if transaction is None:
        await message.answer("Немає підтвердженої операції для скасування.")
        return
    await message.answer(f"Скасовано: {_format_transaction_line(transaction)}")


def _uncategorized_text(transactions: tuple[object, ...]) -> str:
    if not transactions:
        return "Усі операції мають категорії."
    lines = ["Операції без категорії:"]
    lines.extend(
        f"• #{item.id} {_format_transaction_line(item)} · {item.transaction_date:%d.%m.%Y}"  # type: ignore[attr-defined]
        for item in transactions
    )
    lines.append("\nОберіть операцію, щоб призначити категорію.")
    return "\n".join(lines)


async def uncategorized_command(
    message: Message,
    household: Household,
    transaction_service: TransactionService,
) -> None:
    transactions = await transaction_service.list_uncategorized(household_id=household.id)
    await message.answer(
        _uncategorized_text(transactions),
        reply_markup=(
            uncategorized_transactions_keyboard(transactions) if transactions else None
        ),
    )


async def uncategorized_callback(
    callback: CallbackQuery,
    callback_data: UncategorizedCallback,
    household: Household,
    transaction_service: TransactionService,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    transaction = await transaction_service.get(
        household_id=household.id, transaction_id=callback_data.transaction_id
    )
    if transaction is None or transaction.category_id is not None:
        await callback.answer("Операцію не знайдено або категорію вже призначено.", show_alert=True)
        return
    categories = tuple(
        item
        for item in await category_service.list(household.id)
        if item.is_active and item.type is transaction.type
    )
    if callback_data.action == "open":
        if callback.message is not None:
            await callback.message.edit_text(
                f"Оберіть категорію для:\n{_format_transaction_line(transaction)}",
                reply_markup=transaction_category_keyboard(transaction.id, categories),
            )
        await callback.answer()
        return
    if callback_data.action == "new":
        await state.set_state(TransactionCategoryStates.new_name)
        await state.update_data(transaction_id=transaction.id)
        if callback.message is not None:
            await callback.message.answer("Введіть назву нової категорії або /cancel.")
        await callback.answer()
        return
    if callback_data.action != "assign":
        await callback.answer("Невідома дія.", show_alert=True)
        return
    category = next((item for item in categories if item.id == callback_data.category_id), None)
    if category is None:
        await callback.answer("Категорія недоступна.", show_alert=True)
        return
    updated = await transaction_service.assign_category(
        household_id=household.id,
        transaction_id=transaction.id,
        category_id=category.id,
    )
    if updated is None:
        await callback.answer("Не вдалося оновити операцію.", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.edit_text(
            f"Категорію призначено: {category.name}\n{_format_transaction_line(updated)}"
        )
    await callback.answer("Категорію призначено.")


async def new_transaction_category_name(
    message: Message,
    household: Household,
    transaction_service: TransactionService,
    category_service: CategoryService,
    state: FSMContext,
) -> None:
    name = (message.text or "").strip()[:255]
    if not name or name.startswith("/"):
        await message.answer("Введіть назву категорії або /cancel.")
        return
    data = await state.get_data()
    transaction = await transaction_service.get(
        household_id=household.id, transaction_id=data["transaction_id"]
    )
    if transaction is None or transaction.category_id is not None:
        await state.clear()
        await message.answer("Операцію не знайдено або категорію вже призначено.")
        return
    category = await category_service.add(
        household_id=household.id,
        transaction_type=transaction.type,
        name=name,
    )
    await transaction_service.assign_category(
        household_id=household.id,
        transaction_id=transaction.id,
        category_id=category.id,
    )
    await state.clear()
    await message.answer(
        f"Створено категорію «{category.name}» і призначено операції "
        f"#{transaction.id}."
    )


def _command_arguments(message: Message) -> str:
    return (message.text or "").partition(" ")[2].strip()


async def categories_command(
    message: Message,
    household: Household,
    category_service: CategoryService,
) -> None:
    categories = await category_service.list(household.id)
    sections = {
        TransactionType.EXPENSE: ["Категорії витрат:"],
        TransactionType.INCOME: ["Категорії доходів:"],
    }
    for category in categories:
        status = "✅" if category.is_active else "⏸"
        sections[category.type].append(f"{status} {category.name}")
    for lines in sections.values():
        if len(lines) == 1:
            lines.append("Категорій ще немає.")
    await message.answer("\n\n".join("\n".join(sections[item]) for item in TransactionType))


async def category_add_command(
    message: Message,
    household: Household,
    category_service: CategoryService,
) -> None:
    parts = _command_arguments(message).split(maxsplit=1)
    type_aliases = {
        "expense": TransactionType.EXPENSE,
        "витрата": TransactionType.EXPENSE,
        "income": TransactionType.INCOME,
        "дохід": TransactionType.INCOME,
    }
    if len(parts) != 2 or parts[0].lower() not in type_aliases or not parts[1].strip():
        await message.answer("Формат: /category_add expense Назва категорії")
        return
    category = await category_service.add(
        household_id=household.id,
        transaction_type=type_aliases[parts[0].lower()],
        name=parts[1].strip()[:255],
    )
    await message.answer(f"Категорію створено: {category.name}")


async def category_rename_command(
    message: Message,
    household: Household,
    category_service: CategoryService,
) -> None:
    parts = _command_arguments(message).split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Формат: /category_rename code Нова назва")
        return
    category = await category_service.rename(
        household_id=household.id,
        code=parts[0],
        name=parts[1].strip()[:255],
    )
    if category is None:
        await message.answer("Категорію з таким кодом не знайдено.")
        return
    await message.answer(f"Категорію перейменовано: {category.name}")


async def _set_category_active(
    message: Message,
    household: Household,
    category_service: CategoryService,
    *,
    is_active: bool,
) -> None:
    code = _command_arguments(message)
    if not code or " " in code:
        command = "category_show" if is_active else "category_hide"
        await message.answer(f"Формат: /{command} code")
        return
    category = await category_service.set_active(
        household_id=household.id,
        code=code,
        is_active=is_active,
    )
    if category is None:
        await message.answer("Категорію з таким кодом не знайдено.")
        return
    action = "повернено" if is_active else "приховано"
    await message.answer(f"Категорію {action}: {category.name}")


async def category_hide_command(
    message: Message, household: Household, category_service: CategoryService
) -> None:
    await _set_category_active(message, household, category_service, is_active=False)


async def category_show_command(
    message: Message, household: Household, category_service: CategoryService
) -> None:
    await _set_category_active(message, household, category_service, is_active=True)


def _stats_period(argument: str, today: date) -> tuple[date, date, str] | None:
    if not argument:
        start = today.replace(day=1)
        title = f"Статистика за {start:%m.%Y}"
    elif argument == "today":
        return today, today + timedelta(days=1), f"Статистика за {today:%d.%m.%Y}"
    else:
        try:
            start = date.fromisoformat(f"{argument}-01")
        except ValueError:
            return None
        title = f"Статистика за {start:%m.%Y}"
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end, title


async def stats_command(
    message: Message,
    household: Household,
    category_service: CategoryService,
) -> None:
    period = _stats_period(_command_arguments(message), _today(household))
    if period is None:
        await message.answer("Формат: /stats, /stats today або /stats 2026-10")
        return
    start_date, end_date, title = period
    statistics = await category_service.statistics(
        household_id=household.id,
        start_date=start_date,
        end_date=end_date,
    )
    if not statistics:
        await message.answer(f"{title}\nПідтверджених операцій немає.")
        return
    lines = [title]
    for transaction_type, section in (
        (TransactionType.EXPENSE, "Витрати"),
        (TransactionType.INCOME, "Доходи"),
    ):
        items = [item for item in statistics if item.type is transaction_type]
        if not items:
            continue
        lines.extend(["", f"{section}:"])
        lines.extend(
            f"• {item.name}: {_currency_prefix(household.currency)}{item.amount:.2f}"
            for item in items
        )
        lines.append(
            f"Разом: {_currency_prefix(household.currency)}"
            f"{sum((item.amount for item in items), Decimal('0.00')):.2f}"
        )
    has_expenses = any(item.type is TransactionType.EXPENSE for item in statistics)
    await message.answer(
        "\n".join(lines),
        reply_markup=(
            statistics_result_keyboard(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            if has_expenses
            else None
        ),
    )


async def handle_error(event: ErrorEvent) -> bool:
    logger.error(
        "Telegram update failed: update_id=%s error_type=%s",
        event.update.update_id,
        type(event.exception).__name__,
    )
    message = event.update.message
    if message is not None:
        try:
            await message.answer("Сталася помилка. Спробуйте ще раз пізніше.")
        except Exception:  # Telegram may be unavailable; never mask the original failure.
            logger.error("Could not send the generic Telegram error response")
    return True


def create_commands_router() -> Router:
    router = Router(name="commands")
    router.message.register(help_command, Command("help"))
    router.message.register(today_command, Command("today"))
    router.message.register(month_command, Command("month"))
    router.message.register(budget_command, Command("budget"))
    router.message.register(advice_command, Command("advice"))
    router.message.register(report_command, Command("report"))
    router.message.register(reports_on_command, Command("reports_on"))
    router.message.register(reports_off_command, Command("reports_off"))
    router.message.register(set_budget_command, Command("set_budget"))
    router.message.register(set_category_budget_command, Command("set_category_budget"))
    router.message.register(last_command, Command("last"))
    router.message.register(planned_command, Command("planned"))
    router.message.register(uncategorized_command, Command("uncategorized"))
    router.message.register(undo_command, Command("undo"))
    router.message.register(categories_command, Command("categories"))
    router.message.register(category_add_command, Command("category_add"))
    router.message.register(category_rename_command, Command("category_rename"))
    router.message.register(category_hide_command, Command("category_hide"))
    router.message.register(category_show_command, Command("category_show"))
    router.message.register(stats_command, Command("stats"))
    router.message.register(settings_command, Command("settings"))
    router.message.register(cancel_clarification_command, Command("cancel"))
    router.message.register(
        new_transaction_category_name,
        StateFilter(TransactionCategoryStates.new_name),
    )
    router.message.register(recognize_plain_message)
    router.callback_query.register(transaction_callback, TransactionActionCallback.filter())
    router.callback_query.register(
        planned_expense_callback, PlannedExpenseActionCallback.filter()
    )
    router.callback_query.register(clarification_callback, ClarificationCallback.filter())
    router.callback_query.register(category_choice_callback, CategoryChoiceCallback.filter())
    router.callback_query.register(uncategorized_callback, UncategorizedCallback.filter())
    router.callback_query.register(budget_callback, BudgetActionCallback.filter())
    return router
