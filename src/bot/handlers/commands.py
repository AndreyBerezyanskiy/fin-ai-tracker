from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message

from application.services import PeriodTotals, ReportService, TransactionRecognitionService
from domain.enums import TransactionType
from domain.models import RecognitionIntent, RecognitionResult
from infrastructure.database import Household

logger = logging.getLogger(__name__)

HELP_TEXT = """Доступні команди:
/start — підключити цю сімейну групу
/help — показати цю інструкцію
/today — підсумок за сьогодні
/month — підсумок за поточний місяць
/settings — налаштування групи

Надсилайте витрати й доходи звичайним текстом, наприклад: «кава 4,50».
Щоб бот надалі бачив такі повідомлення, вимкніть Privacy Mode через @BotFather."""


def _format_totals(title: str, totals: PeriodTotals, currency: str) -> str:
    def amount(value: Decimal) -> str:
        return f"{value:.2f} {currency}"

    return (
        f"{title}\n"
        f"Доходи: {amount(totals.income)}\n"
        f"Витрати: {amount(totals.expense)}\n"
        f"Баланс: {amount(totals.balance)}"
    )


def _today(household: Household) -> date:
    return datetime.now(ZoneInfo(household.timezone)).date()


async def start_command(message: Message, household: Household) -> None:
    await message.answer(
        f"Групу «{household.name}» підключено до сімейного бюджету.\n\n{HELP_TEXT}"
    )


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
    )
    await message.answer(_format_totals("Сьогодні", totals, household.currency))


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
    totals = await report_service.totals(
        household_id=household.id,
        start_date=start_date,
        end_date=end_date,
    )
    await message.answer(_format_totals("Цього місяця", totals, household.currency))


async def settings_command(message: Message, household: Household) -> None:
    reports = "увімкнені" if household.reports_enabled else "вимкнені"
    await message.answer(
        "Налаштування групи\n"
        f"Валюта: {household.currency}\n"
        f"Часовий пояс: {household.timezone}\n"
        f"Автоматичні звіти: {reports}"
    )


def _format_recognition(result: RecognitionResult) -> str:
    lines = ["Розпізнано (ще не збережено):"]
    for transaction in result.transactions:
        operation = "витрата" if transaction.type is TransactionType.EXPENSE else "дохід"
        description = f" — {transaction.description}" if transaction.description else ""
        lines.append(
            f"• {operation}: {transaction.amount:.2f} {transaction.currency}, "
            f"{transaction.category_code}, {transaction.transaction_date.isoformat()}"
            f"{description}"
        )
    return "\n".join(lines)


async def recognize_plain_message(
    message: Message,
    household: Household,
    transaction_recognition_service: TransactionRecognitionService,
) -> None:
    if not message.text:
        return
    result = await transaction_recognition_service.recognize(
        message=message.text,
        household=household,
    )
    if result.intent is RecognitionIntent.NOT_A_TRANSACTION:
        return
    if result.needs_clarification:
        await message.answer(result.clarification_question)
        return
    await message.answer(_format_recognition(result))


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
    router.message.register(start_command, Command("start"))
    router.message.register(help_command, Command("help"))
    router.message.register(today_command, Command("today"))
    router.message.register(month_command, Command("month"))
    router.message.register(settings_command, Command("settings"))
    router.message.register(recognize_plain_message)
    return router
