from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services.advice import AdviceService
from application.services.budgets import BudgetService, BudgetStatus
from application.services.telegram import ReportService
from domain.currencies import currency_prefix
from domain.enums import ReportType
from infrastructure.database import Household, session_scope
from infrastructure.observability import pseudonymize
from infrastructure.repositories import HouseholdRepository, ReportDeliveryRepository

logger = logging.getLogger(__name__)


class ReportSender(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object: ...


class AutomaticReportService:
    """Build and deliver deterministic reports, with AI used only for evening advice."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        report_service: ReportService,
        budget_service: BudgetService,
        advice_service: AdviceService,
    ) -> None:
        self.session_factory = session_factory
        self.report_service = report_service
        self.budget_service = budget_service
        self.advice_service = advice_service

    async def enabled_households(self) -> tuple[Household, ...]:
        async with self.session_factory() as session:
            return tuple(await HouseholdRepository(session).list_with_reports_enabled())

    @staticmethod
    def active_slot(household: Household, now: datetime) -> tuple[ReportType, date] | None:
        local_now = now.astimezone(ZoneInfo(household.timezone))
        due = [
            (household.morning_report_time, ReportType.MORNING),
            (household.evening_report_time, ReportType.EVENING),
        ]
        available = [
            (report_time, kind) for report_time, kind in due if report_time <= local_now.time()
        ]
        if not available:
            return None
        _report_time, report_type = max(available, key=lambda item: item[0])
        return report_type, local_now.date()

    async def deliver(
        self,
        *,
        sender: ReportSender,
        household: Household,
        report_type: ReportType,
        local_date: date,
    ) -> bool:
        if not await self._claim(household.id, report_type, local_date):
            return False
        try:
            text = await self.render(
                household=household,
                report_type=report_type,
                local_date=local_date,
            )
            await sender.send_message(household.telegram_chat_id, text)
        except Exception:
            # An explicit failure means Telegram did not acknowledge delivery. Releasing the
            # key lets the next scheduler tick retry while the local slot is still active.
            await self._release(household.id, report_type, local_date)
            raise
        return True

    async def render(
        self,
        *,
        household: Household,
        report_type: ReportType,
        local_date: date,
    ) -> str:
        budget = await self.budget_service.status(
            household_id=household.id,
            currency=household.currency,
            today=local_date,
        )
        if report_type is ReportType.MORNING:
            report_date = local_date - timedelta(days=1)
            totals = await self.report_service.totals(
                household_id=household.id,
                start_date=report_date,
                end_date=local_date,
                currency=household.currency,
            )
            return self._morning_text(
                expense=totals.expense,
                budget=budget,
                currency=household.currency,
            )

        totals = await self.report_service.totals(
            household_id=household.id,
            start_date=local_date,
            end_date=local_date + timedelta(days=1),
            currency=household.currency,
        )
        advice = await self._advice(household, local_date)
        return self._evening_text(
            expense=totals.expense,
            budget=budget,
            currency=household.currency,
            advice=advice,
        )

    async def _advice(self, household: Household, local_date: date) -> str:
        try:
            observations = await self.advice_service.generate(
                household_id=household.id,
                currency=household.currency,
                today=local_date,
            )
        except Exception as error:
            logger.warning(
                "Evening AI observation unavailable: household_ref=%s error_type=%s",
                pseudonymize(household.id),
                type(error).__name__,
            )
            return "Персональна порада тимчасово недоступна."
        if observations:
            return observations[0]
        return "Продовжуйте фіксувати витрати — скоро тут з’явиться персональна порада."

    async def _claim(self, household_id: int, report_type: ReportType, local_date: date) -> bool:
        async with session_scope(self.session_factory) as session:
            return await ReportDeliveryRepository(session).claim(
                household_id=household_id,
                report_type=report_type,
                local_date=local_date,
            )

    async def _release(self, household_id: int, report_type: ReportType, local_date: date) -> None:
        async with session_scope(self.session_factory) as session:
            await ReportDeliveryRepository(session).release(
                household_id=household_id,
                report_type=report_type,
                local_date=local_date,
            )

    @classmethod
    def _morning_text(cls, *, expense: Decimal, budget: BudgetStatus, currency: str) -> str:
        lines = [
            "☀️ Доброго ранку!",
            "",
            f"Учора витрачено {cls._money(expense, currency)}.",
            "",
            "📅 Цього місяця",
            *cls._month_summary_lines(budget, currency),
            "",
            cls._limit_line(budget, currency),
            *cls._reserve_lines(budget, currency),
            "",
        ]
        approaching = [
            item
            for item in budget.categories
            if item.percentage is not None and item.percentage >= 80
        ]
        if approaching:
            lines.append("⚠️ Наближаються до ліміту")
            lines.extend(f"• {item.name}: {item.percentage:.1f}%" for item in approaching)
        else:
            lines.append("✅ Категорій біля ліміту немає.")
        return "\n".join(lines)

    @classmethod
    def _evening_text(
        cls,
        *,
        expense: Decimal,
        budget: BudgetStatus,
        currency: str,
        advice: str,
    ) -> str:
        return "\n".join(
            [
                "🌙 Підсумки дня",
                "",
                f"Сьогодні витрачено {cls._money(expense, currency)}.",
                "",
                "📅 Цього місяця",
                *cls._month_summary_lines(budget, currency),
                "",
                f"💡 Порада: {advice}",
                "",
                cls._limit_line(budget, currency),
                *cls._reserve_lines(budget, currency),
            ]
        )

    @classmethod
    def _month_summary_lines(cls, budget: BudgetStatus, currency: str) -> list[str]:
        balance_icon = "📈" if budget.balance >= 0 else "📉"
        balance_status = "🟢" if budget.balance > 0 else "🔴" if budget.balance < 0 else "⚪"
        return [
            f"💰 Надійшло: {cls._money(budget.income, currency)}",
            f"💸 Витрачено: {cls._money(budget.spent, currency)}",
            (
                f"{balance_icon} Баланс: "
                f"{cls._signed_money(budget.balance, currency)} {balance_status}"
            ),
        ]

    @classmethod
    def _limit_line(cls, budget: BudgetStatus, currency: str) -> str:
        if budget.total_limit is None:
            return "⚙️ Ліміт витрат не встановлено."
        percentage = "не визначено" if budget.percentage is None else f"{budget.percentage:.1f}%"
        return (
            f"⚙️ Ліміт витрат: {cls._money(budget.spent, currency)} з "
            f"{cls._money(budget.total_limit, currency)} ({percentage})."
        )

    @classmethod
    def _reserve_lines(cls, budget: BudgetStatus, currency: str) -> list[str]:
        if budget.reserved == 0:
            return []
        safe = (
            cls._money(budget.safe_to_spend, currency)
            if budget.safe_to_spend >= 0
            else cls._signed_money(budget.safe_to_spend, currency)
        )
        return [
            f"📌 Заплановано: {cls._money(budget.reserved, currency)}",
            f"🛡️ Безпечно витратити: {safe}",
        ]

    @staticmethod
    def _money(value: Decimal, currency: str) -> str:
        return f"{currency_prefix(currency)}{value:.2f}"

    @classmethod
    def _signed_money(cls, value: Decimal, currency: str) -> str:
        sign = "+" if value > 0 else "−" if value < 0 else ""
        return f"{sign}{cls._money(abs(value), currency)}"


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["AutomaticReportService", "ReportSender", "utc_now"]
