from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import TransactionStatus, TransactionType
from infrastructure.database import Household, Member, Transaction, session_scope
from infrastructure.repositories import (
    HouseholdRepository,
    MemberRepository,
    ProcessedTelegramUpdateRepository,
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
    ) -> PeriodTotals:
        async with self.session_factory() as session:
            statement = (
                select(Transaction.type, func.sum(Transaction.amount))
                .where(
                    Transaction.household_id == household_id,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.transaction_date >= start_date,
                    Transaction.transaction_date < end_date,
                )
                .group_by(Transaction.type)
            )
            amounts = dict((await session.execute(statement)).all())

        return PeriodTotals(
            income=amounts.get(TransactionType.INCOME, Decimal("0.00")),
            expense=amounts.get(TransactionType.EXPENSE, Decimal("0.00")),
        )
