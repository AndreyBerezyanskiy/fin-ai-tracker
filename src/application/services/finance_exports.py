from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    TransactionType,
)
from infrastructure.database import PlannedExpense, Transaction
from infrastructure.repositories import PlannedExpenseRepository, TransactionRepository

MAX_EXPORT_OPERATIONS = 10_000

MONTH_NAMES = (
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


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def _month_from_index(value: int) -> date:
    year, zero_based_month = divmod(value, 12)
    return date(year, zero_based_month + 1, 1)


@dataclass(frozen=True, slots=True)
class ExportPeriod:
    start_date: date
    end_date: date
    label: str
    filename_suffix: str
    month_count: int

    @classmethod
    def months(cls, start_month: str, end_month: str) -> ExportPeriod:
        try:
            start_year, start_number = (int(part) for part in start_month.split("-"))
            end_year, end_number = (int(part) for part in end_month.split("-"))
            start = date(start_year, start_number, 1)
            end_start = date(end_year, end_number, 1)
        except (TypeError, ValueError):
            raise ValueError("invalid export month") from None
        start_index = _month_index(start.year, start.month)
        end_index = _month_index(end_start.year, end_start.month)
        if end_index < start_index:
            raise ValueError("export period ends before it starts")
        month_count = end_index - start_index + 1
        end = _month_from_index(end_index + 1)
        if month_count == 1:
            label = f"{MONTH_NAMES[start.month]} {start.year}"
            suffix = start.strftime("%Y-%m")
        elif start.month == 1 and end_start.month == 12 and start.year == end_start.year:
            label = str(start.year)
            suffix = str(start.year)
        else:
            label = (
                f"{MONTH_NAMES[start.month]} {start.year} — "
                f"{MONTH_NAMES[end_start.month]} {end_start.year}"
            )
            suffix = f"{start:%Y-%m}--{end_start:%Y-%m}"
        return cls(
            start_date=start,
            end_date=end,
            label=label,
            filename_suffix=suffix,
            month_count=month_count,
        )

    @classmethod
    def year(cls, year: int) -> ExportPeriod:
        return cls.months(f"{year:04d}-01", f"{year:04d}-12")

    @property
    def previous(self) -> ExportPeriod:
        start_index = _month_index(self.start_date.year, self.start_date.month)
        previous_start = _month_from_index(start_index - self.month_count)
        previous_end = _month_from_index(start_index - 1)
        return self.months(
            previous_start.strftime("%Y-%m"),
            previous_end.strftime("%Y-%m"),
        )


@dataclass(frozen=True, slots=True)
class ExportTransaction:
    transaction_date: date
    type: TransactionType
    category: str
    category_key: str
    description: str
    member: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ExportCategory:
    name: str
    type: TransactionType
    amount: Decimal
    percentage: Decimal


@dataclass(frozen=True, slots=True)
class ExportMonth:
    month: date
    income: Decimal
    expense: Decimal

    @property
    def balance(self) -> Decimal:
        return self.income - self.expense


@dataclass(frozen=True, slots=True)
class ExportPlannedExpense:
    due_date: date
    category: str
    description: str
    member: str
    amount: Decimal
    recurrence: PlannedExpenseRecurrence | None
    status: PlannedExpenseStatus


@dataclass(frozen=True, slots=True)
class FinanceExportReport:
    household_name: str
    currency: str
    period: ExportPeriod
    created_at: datetime
    transactions: tuple[ExportTransaction, ...]
    categories: tuple[ExportCategory, ...]
    months: tuple[ExportMonth, ...]
    planned_expenses: tuple[ExportPlannedExpense, ...]
    income: Decimal
    expense: Decimal
    planned_total: Decimal
    previous_income: Decimal
    previous_expense: Decimal

    @property
    def balance(self) -> Decimal:
        return self.income - self.expense

    @property
    def available_after_plans(self) -> Decimal:
        return self.balance - self.planned_total


@dataclass(frozen=True, slots=True)
class FinanceExportResult:
    report: FinanceExportReport
    content: bytes
    filename: str


class ExcelReportRenderer(Protocol):
    def render(self, report: FinanceExportReport) -> bytes: ...


class ExportTooLargeError(ValueError):
    pass


def _transaction_data(transaction: Transaction) -> ExportTransaction:
    category = transaction.category.name if transaction.category is not None else "Без категорії"
    category_key = (
        transaction.category.code if transaction.category is not None else "__uncategorized__"
    )
    return ExportTransaction(
        transaction_date=transaction.transaction_date,
        type=transaction.type,
        category=category,
        category_key=category_key,
        description=transaction.description,
        member=transaction.beneficiary.display_name,
        amount=transaction.amount,
    )


def _planned_data(plan: PlannedExpense) -> ExportPlannedExpense:
    return ExportPlannedExpense(
        due_date=plan.due_date,
        category=plan.category.name,
        description=plan.description,
        member=plan.member.display_name,
        amount=plan.amount,
        recurrence=plan.recurrence,
        status=plan.status,
    )


def _categories(transactions: tuple[ExportTransaction, ...]) -> tuple[ExportCategory, ...]:
    grouped: dict[tuple[str, TransactionType, str], Decimal] = {}
    totals: dict[TransactionType, Decimal] = {
        TransactionType.EXPENSE: Decimal("0"),
        TransactionType.INCOME: Decimal("0"),
    }
    for item in transactions:
        key = (item.category_key, item.type, item.category)
        grouped[key] = grouped.get(key, Decimal("0")) + item.amount
        totals[item.type] += item.amount
    return tuple(
        ExportCategory(
            name=name,
            type=transaction_type,
            amount=amount,
            percentage=(
                amount / totals[transaction_type]
                if totals[transaction_type]
                else Decimal("0")
            ),
        )
        for (_, transaction_type, name), amount in sorted(
            grouped.items(),
            key=lambda item: (item[0][1].value, -item[1], item[0][2]),
        )
    )


def _months(
    transactions: tuple[ExportTransaction, ...], period: ExportPeriod
) -> tuple[ExportMonth, ...]:
    grouped: dict[tuple[int, int], dict[TransactionType, Decimal]] = {}
    for item in transactions:
        values = grouped.setdefault(
            (item.transaction_date.year, item.transaction_date.month),
            {TransactionType.INCOME: Decimal("0"), TransactionType.EXPENSE: Decimal("0")},
        )
        values[item.type] += item.amount
    start_index = _month_index(period.start_date.year, period.start_date.month)
    return tuple(
        ExportMonth(
            month=month,
            income=grouped.get((month.year, month.month), {}).get(
                TransactionType.INCOME, Decimal("0")
            ),
            expense=grouped.get((month.year, month.month), {}).get(
                TransactionType.EXPENSE, Decimal("0")
            ),
        )
        for month in (
            _month_from_index(start_index + offset) for offset in range(period.month_count)
        )
    )


class FinanceExportService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        renderer: ExcelReportRenderer,
    ) -> None:
        self.session_factory = session_factory
        self.renderer = renderer

    async def available_years(self, *, household_id: int, current_year: int) -> tuple[int, ...]:
        async with self.session_factory() as session:
            oldest = await TransactionRepository(session).oldest_confirmed_date(household_id)
        first_year = min(oldest.year if oldest else current_year, current_year)
        return tuple(range(current_year, first_year - 1, -1))

    async def create(
        self,
        *,
        household_id: int,
        household_name: str,
        currency: str,
        period: ExportPeriod,
    ) -> FinanceExportResult | None:
        previous = period.previous
        async with self.session_factory() as session:
            transaction_repository = TransactionRepository(session)
            planned_repository = PlannedExpenseRepository(session)
            transactions = await transaction_repository.list_confirmed_for_period(
                household_id=household_id,
                start_date=period.start_date,
                end_date=period.end_date,
                currency=currency,
                limit=MAX_EXPORT_OPERATIONS + 1,
            )
            if len(transactions) > MAX_EXPORT_OPERATIONS:
                raise ExportTooLargeError
            plans = await planned_repository.list_for_period(
                household_id=household_id,
                start_date=period.start_date,
                end_date=period.end_date,
                currency=currency,
            )
            previous_totals = await transaction_repository.type_totals(
                household_id=household_id,
                start_date=previous.start_date,
                end_date=previous.end_date,
                currency=currency,
            )
        if not transactions and not plans:
            return None

        transaction_data = tuple(_transaction_data(item) for item in transactions)
        planned_data = tuple(_planned_data(item) for item in plans)
        income = sum(
            (item.amount for item in transaction_data if item.type is TransactionType.INCOME),
            Decimal("0"),
        )
        expense = sum(
            (item.amount for item in transaction_data if item.type is TransactionType.EXPENSE),
            Decimal("0"),
        )
        planned_total = sum(
            (
                item.amount
                for item in planned_data
                if item.status is PlannedExpenseStatus.SCHEDULED
            ),
            Decimal("0"),
        )
        report = FinanceExportReport(
            household_name=household_name,
            currency=currency,
            period=period,
            created_at=datetime.now(UTC),
            transactions=transaction_data,
            categories=_categories(transaction_data),
            months=_months(transaction_data, period),
            planned_expenses=planned_data,
            income=income,
            expense=expense,
            planned_total=planned_total,
            previous_income=previous_totals.get(TransactionType.INCOME, Decimal("0")),
            previous_expense=previous_totals.get(TransactionType.EXPENSE, Decimal("0")),
        )
        content = await asyncio.to_thread(self.renderer.render, report)
        return FinanceExportResult(
            report=report,
            content=content,
            filename=f"family-budget-{period.filename_suffix}.xlsx",
        )


__all__ = [
    "ExportCategory",
    "ExportMonth",
    "ExportPeriod",
    "ExportPlannedExpense",
    "ExportTooLargeError",
    "ExportTransaction",
    "FinanceExportReport",
    "FinanceExportResult",
    "FinanceExportService",
    "MAX_EXPORT_OPERATIONS",
]
