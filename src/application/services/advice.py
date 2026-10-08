from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from application.services.budgets import BudgetService
from application.services.telegram import ReportService

INSUFFICIENT_DATA_MESSAGE = "Недостатньо даних для поради: цього місяця ще немає витрат."


@dataclass(frozen=True, slots=True)
class AdviceCategory:
    name: str
    spent: Decimal
    budget: Decimal | None
    budget_used_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class AdviceReport:
    currency: str
    budget: Decimal | None
    spent: Decimal
    budget_used_percent: Decimal | None
    top_categories: tuple[AdviceCategory, ...]
    previous_month_spent: Decimal | None
    expense_change: Decimal | None
    expense_change_percent: Decimal | None
    days_remaining: int


class AdviceGenerator(Protocol):
    async def generate(self, report: AdviceReport) -> tuple[str, ...]: ...


class AdviceService:
    """Build an aggregate report in Python, then ask AI only to phrase observations."""

    def __init__(
        self,
        report_service: ReportService,
        budget_service: BudgetService,
        generator: AdviceGenerator,
    ) -> None:
        self.report_service = report_service
        self.budget_service = budget_service
        self.generator = generator

    async def generate(self, *, household_id: int, currency: str, today: date) -> tuple[str, ...]:
        start_date = today.replace(day=1)
        end_date = (
            date(today.year + 1, 1, 1)
            if today.month == 12
            else date(today.year, today.month + 1, 1)
        )
        previous_start = (
            date(start_date.year - 1, 12, 1)
            if start_date.month == 1
            else date(start_date.year, start_date.month - 1, 1)
        )
        month_report = await self.report_service.month_report(
            household_id=household_id,
            start_date=start_date,
            end_date=end_date,
            previous_start_date=previous_start,
            currency=currency,
        )
        if month_report.totals.expense == 0:
            return (INSUFFICIENT_DATA_MESSAGE,)
        budget_status = await self.budget_service.status(
            household_id=household_id,
            currency=currency,
            today=today,
        )

        category_budgets = {item.code: item for item in budget_status.categories}
        top_categories = tuple(
            AdviceCategory(
                name=item.name,
                spent=item.amount,
                budget=(
                    category_budgets[item.code].limit if item.code in category_budgets else None
                ),
                budget_used_percent=(
                    category_budgets[item.code].percentage
                    if item.code in category_budgets
                    else None
                ),
            )
            for item in month_report.expense_categories[:3]
        )
        previous_spent = (
            month_report.previous_totals.expense
            if month_report.previous_totals is not None
            else None
        )
        expense_change = (
            month_report.totals.expense - previous_spent if previous_spent is not None else None
        )
        expense_change_percent = (
            expense_change * Decimal("100") / previous_spent
            if expense_change is not None and previous_spent != 0
            else None
        )
        report = AdviceReport(
            currency=currency.upper(),
            budget=budget_status.total_limit,
            spent=month_report.totals.expense,
            budget_used_percent=budget_status.percentage,
            top_categories=top_categories,
            previous_month_spent=previous_spent,
            expense_change=expense_change,
            expense_change_percent=expense_change_percent,
            days_remaining=budget_status.days_remaining,
        )
        return await self.generator.generate(report)


__all__ = [
    "AdviceCategory",
    "AdviceGenerator",
    "AdviceReport",
    "AdviceService",
    "INSUFFICIENT_DATA_MESSAGE",
]
