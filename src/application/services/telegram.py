from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import TransactionStatus, TransactionType
from infrastructure.database import Household, Member, Transaction, session_scope
from infrastructure.repositories import (
    CategoryRepository,
    HouseholdRepository,
    MemberRepository,
    ProcessedTelegramUpdateRepository,
    TransactionRepository,
)


@dataclass(frozen=True, slots=True)
class TelegramContext:
    household: Household
    member: Member


@dataclass(frozen=True, slots=True)
class PeriodTotals:
    income: Decimal = Decimal("0.00")
    expense: Decimal = Decimal("0.00")

    @property
    def balance(self) -> Decimal:
        return self.income - self.expense


@dataclass(frozen=True, slots=True)
class ReportCategoryTotal:
    code: str
    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class MonthReport:
    totals: PeriodTotals
    expense_categories: tuple[ReportCategoryTotal, ...]
    previous_totals: PeriodTotals | None


class TelegramBootstrapService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def bootstrap(
        self,
        *,
        update_id: int,
        chat_id: int,
        chat_name: str,
        user_id: int,
        display_name: str,
        username: str | None,
    ) -> TelegramContext | None:
        async with session_scope(self.session_factory) as session:
            if not await ProcessedTelegramUpdateRepository(session).claim(update_id):
                return None

            household = await HouseholdRepository(session).get_or_create(
                telegram_chat_id=chat_id,
                name=chat_name,
            )
            member = await MemberRepository(session).get_or_create(
                household_id=household.id,
                telegram_user_id=user_id,
                display_name=display_name,
                username=username,
            )
            return TelegramContext(household=household, member=member)

    async def release(self, update_id: int) -> None:
        """Allow Telegram to retry an update whose handler failed."""

        async with session_scope(self.session_factory) as session:
            await ProcessedTelegramUpdateRepository(session).release(update_id)


class ReportService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def totals(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str | None = None,
    ) -> PeriodTotals:
        async with self.session_factory() as session:
            filters = [
                Transaction.household_id == household_id,
                Transaction.status == TransactionStatus.CONFIRMED,
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date < end_date,
            ]
            if currency is not None:
                filters.append(Transaction.currency == currency.upper())
            statement = (
                select(Transaction.type, func.sum(Transaction.amount))
                .where(*filters)
                .group_by(Transaction.type)
            )
            amounts = dict((await session.execute(statement)).all())

        return PeriodTotals(
            income=amounts.get(TransactionType.INCOME, Decimal("0.00")),
            expense=amounts.get(TransactionType.EXPENSE, Decimal("0.00")),
        )

    async def recent_transactions(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str,
        limit: int = 5,
    ) -> tuple[Transaction, ...]:
        async with self.session_factory() as session:
            return tuple(
                await TransactionRepository(session).list_recent_confirmed(
                    household_id,
                    start_date=start_date,
                    end_date=end_date,
                    currency=currency,
                    limit=limit,
                )
            )

    async def month_report(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        previous_start_date: date,
        currency: str,
    ) -> MonthReport:
        totals = await self.totals(
            household_id=household_id,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
        )
        previous = await self.totals(
            household_id=household_id,
            start_date=previous_start_date,
            end_date=start_date,
            currency=currency,
        )
        async with self.session_factory() as session:
            categories = {
                category.code: category
                for category in await CategoryRepository(session).list_for_management(household_id)
            }
            category_totals = await TransactionRepository(session).category_totals(
                household_id=household_id,
                start_date=start_date,
                end_date=end_date,
                currency=currency,
            )
        expense_categories = tuple(
            ReportCategoryTotal(item.code, categories[item.code].name, item.amount)
            for item in category_totals
            if item.type is TransactionType.EXPENSE and item.code in categories
        )
        return MonthReport(
            totals=totals,
            expense_categories=expense_categories,
            previous_totals=(previous if previous.income != 0 or previous.expense != 0 else None),
        )
