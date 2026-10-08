from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import TransactionStatus, TransactionType
from infrastructure.database import Budget, Transaction, session_scope
from infrastructure.repositories import BudgetRepository, CategoryRepository, TransactionRepository


@dataclass(frozen=True, slots=True)
class CategoryBudgetStatus:
    code: str
    name: str
    limit: Decimal
    spent: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.limit - self.spent

    @property
    def percentage(self) -> Decimal | None:
        if self.limit == 0:
            return None
        return self.spent * Decimal("100") / self.limit


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    total_limit: Decimal | None
    spent: Decimal
    days_remaining: int
    categories: tuple[CategoryBudgetStatus, ...]

    @property
    def remaining(self) -> Decimal | None:
        if self.total_limit is None:
            return None
        return self.total_limit - self.spent

    @property
    def percentage(self) -> Decimal | None:
        if self.total_limit in (None, Decimal("0")):
            return None
        return self.spent * Decimal("100") / self.total_limit


class BudgetService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def set_total(
        self, *, household_id: int, year: int, month: int, amount: Decimal
    ) -> Budget:
        self._validate_amount(amount)
        async with session_scope(self.session_factory) as session:
            return await BudgetRepository(session).upsert(
                household_id=household_id,
                year=year,
                month=month,
                amount=amount,
            )

    async def set_category(
        self,
        *,
        household_id: int,
        year: int,
        month: int,
        category_code: str,
        amount: Decimal,
    ) -> Budget | None:
        self._validate_amount(amount)
        async with session_scope(self.session_factory) as session:
            category = await CategoryRepository(session).get_effective_by_code(
                household_id, category_code
            )
            if category is None or category.type is not TransactionType.EXPENSE:
                return None
            return await BudgetRepository(session).upsert(
                household_id=household_id,
                year=year,
                month=month,
                category_id=category.id,
                amount=amount,
            )

    async def status(
        self,
        *,
        household_id: int,
        currency: str,
        today: date,
    ) -> BudgetStatus:
        start_date = today.replace(day=1)
        end_date = (
            date(today.year + 1, 1, 1)
            if today.month == 12
            else date(today.year, today.month + 1, 1)
        )
        async with self.session_factory() as session:
            budgets = await BudgetRepository(session).list_for_period(
                household_id=household_id,
                year=today.year,
                month=today.month,
            )
            effective_categories = {
                item.code: item
                for item in await CategoryRepository(session).list_for_management(household_id)
            }
            spent = await session.scalar(
                select(func.sum(Transaction.amount)).where(
                    Transaction.household_id == household_id,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.type == TransactionType.EXPENSE,
                    Transaction.currency == currency.upper(),
                    Transaction.transaction_date >= start_date,
                    Transaction.transaction_date < end_date,
                )
            )
            category_totals = await TransactionRepository(session).category_totals(
                household_id=household_id,
                start_date=start_date,
                end_date=end_date,
                currency=currency,
            )

        expense_by_code = {
            item.code: item.amount
            for item in category_totals
            if item.type is TransactionType.EXPENSE
        }
        total_budget = next((item for item in budgets if item.category_id is None), None)
        category_statuses = tuple(
            CategoryBudgetStatus(
                code=budget.category.code,
                name=effective_categories.get(budget.category.code, budget.category).name,
                limit=budget.amount,
                spent=expense_by_code.get(budget.category.code, Decimal("0.00")),
            )
            for budget in budgets
            if budget.category is not None
        )
        return BudgetStatus(
            total_limit=total_budget.amount if total_budget is not None else None,
            spent=spent or Decimal("0.00"),
            days_remaining=(end_date - today).days - 1,
            categories=category_statuses,
        )

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999999999.99"):
            raise ValueError("invalid budget amount")


__all__ = ["BudgetService", "BudgetStatus", "CategoryBudgetStatus"]
