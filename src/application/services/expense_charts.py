from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from application.services.categories import CategoryService, CategoryStatistic
from domain.enums import TransactionType


@dataclass(frozen=True, slots=True)
class ExpenseChartCategory:
    code: str
    name: str
    amount: Decimal
    percentage: Decimal


@dataclass(frozen=True, slots=True)
class ExpenseChartData:
    period_label: str
    start_date: date
    end_date: date
    currency: str
    total: Decimal
    categories: tuple[ExpenseChartCategory, ...]
    previous_total: Decimal
    difference: Decimal
    difference_percentage: Decimal | None


@dataclass(frozen=True, slots=True)
class ExpenseChartResult:
    data: ExpenseChartData
    image: bytes


class ExpenseChartRenderer(Protocol):
    def render(self, data: ExpenseChartData) -> bytes: ...


UKRAINIAN_MONTHS = (
    "",
    "Січень",
    "Лютий",
    "Березень",
    "Квітень",
    "Травень",
    "Червень",
    "Липень",
    "Серпень",
    "Вересень",
    "Жовтень",
    "Листопад",
    "Грудень",
)


def expense_period_label(start_date: date, end_date: date) -> str:
    next_month = (
        date(start_date.year + 1, 1, 1)
        if start_date.month == 12
        else date(start_date.year, start_date.month + 1, 1)
    )
    if start_date.day == 1 and end_date == next_month:
        return f"{UKRAINIAN_MONTHS[start_date.month]} {start_date.year}"
    if end_date == start_date + timedelta(days=1):
        return f"{start_date:%d.%m.%Y}"
    return f"{start_date:%d.%m.%Y} — {(end_date - timedelta(days=1)):%d.%m.%Y}"


def prepare_expense_chart_data(
    statistics: tuple[CategoryStatistic, ...],
    *,
    previous_statistics: tuple[CategoryStatistic, ...] = (),
    start_date: date,
    end_date: date,
    currency: str,
    max_categories: int = 5,
) -> ExpenseChartData | None:
    expenses = sorted(
        (
            item
            for item in statistics
            if item.type is TransactionType.EXPENSE and item.amount > 0
        ),
        key=lambda item: item.amount,
        reverse=True,
    )
    total = sum((item.amount for item in expenses), Decimal("0"))
    if total <= 0:
        return None

    visible = expenses[:max_categories]
    remainder = expenses[max_categories:]
    chart_categories = [
        ExpenseChartCategory(
            code=item.code,
            name=item.name,
            amount=item.amount,
            percentage=item.amount / total * 100,
        )
        for item in visible
    ]
    if remainder:
        remainder_total = sum((item.amount for item in remainder), Decimal("0"))
        chart_categories.append(
            ExpenseChartCategory(
                code="__other__",
                name="Інше",
                amount=remainder_total,
                percentage=remainder_total / total * 100,
            )
        )

    previous_total = sum(
        (
            item.amount
            for item in previous_statistics
            if item.type is TransactionType.EXPENSE and item.amount > 0
        ),
        Decimal("0"),
    )
    difference = total - previous_total
    difference_percentage = (
        difference / previous_total * 100 if previous_total > 0 else None
    )
    return ExpenseChartData(
        period_label=expense_period_label(start_date, end_date),
        start_date=start_date,
        end_date=end_date,
        currency=currency,
        total=total,
        categories=tuple(chart_categories),
        previous_total=previous_total,
        difference=difference,
        difference_percentage=difference_percentage,
    )


class ExpenseChartService:
    def __init__(
        self,
        category_service: CategoryService,
        renderer: ExpenseChartRenderer,
        *,
        max_categories: int = 5,
    ) -> None:
        self.category_service = category_service
        self.renderer = renderer
        self.max_categories = max_categories

    async def create(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str,
    ) -> ExpenseChartResult | None:
        period_length = end_date - start_date
        previous_start = start_date - period_length
        statistics, previous_statistics = await asyncio.gather(
            self.category_service.statistics(
                household_id=household_id,
                start_date=start_date,
                end_date=end_date,
            ),
            self.category_service.statistics(
                household_id=household_id,
                start_date=previous_start,
                end_date=start_date,
            ),
        )
        data = prepare_expense_chart_data(
            statistics,
            previous_statistics=previous_statistics,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            max_categories=self.max_categories,
        )
        if data is None:
            return None
        image = await asyncio.to_thread(self.renderer.render, data)
        return ExpenseChartResult(data=data, image=image)


__all__ = [
    "ExpenseChartCategory",
    "ExpenseChartData",
    "ExpenseChartRenderer",
    "ExpenseChartResult",
    "ExpenseChartService",
    "expense_period_label",
    "prepare_expense_chart_data",
]
