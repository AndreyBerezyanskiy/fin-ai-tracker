from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.enums import TransactionStatus, TransactionType
from infrastructure.database.models import Budget, Category, Household, Member, Transaction


class InvalidTransactionReferenceError(ValueError):
    """Raised when transaction references do not belong to its household."""


@dataclass(frozen=True, slots=True)
class TransactionCreateResult:
    transaction: Transaction
    created: bool


class HouseholdRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, household_id: int) -> Household | None:
        return await self.session.get(Household, household_id)

    async def get_by_telegram_chat_id(self, telegram_chat_id: int) -> Household | None:
        return await self.session.scalar(
            select(Household).where(Household.telegram_chat_id == telegram_chat_id)
        )

    async def get_or_create(
        self,
        *,
        telegram_chat_id: int,
        name: str,
        currency: str = "EUR",
        timezone: str = "Europe/Paris",
    ) -> Household:
        statement = (
            insert(Household)
            .values(
                telegram_chat_id=telegram_chat_id,
                name=name,
                currency=currency.upper(),
                timezone=timezone,
            )
            .on_conflict_do_nothing(index_elements=[Household.telegram_chat_id])
            .returning(Household)
        )
        household = (await self.session.scalars(statement)).one_or_none()
        if household is not None:
            return household

        existing = await self.get_by_telegram_chat_id(telegram_chat_id)
        if existing is None:  # Defensive: conflict target should make this unreachable.
            raise RuntimeError("household conflict occurred but row was not found")
        return existing

    async def update_report_settings(
        self,
        household: Household,
        *,
        morning_report_time: time | None,
        evening_report_time: time | None,
        reports_enabled: bool,
    ) -> Household:
        household.morning_report_time = morning_report_time
        household.evening_report_time = evening_report_time
        household.reports_enabled = reports_enabled
        await self.session.flush()
        return household


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, member_id: int) -> Member | None:
        return await self.session.get(Member, member_id)

    async def get_by_telegram_user_id(
        self, household_id: int, telegram_user_id: int
    ) -> Member | None:
        return await self.session.scalar(
            select(Member).where(
                Member.household_id == household_id,
                Member.telegram_user_id == telegram_user_id,
            )
        )

    async def get_or_create(
        self,
        *,
        household_id: int,
        telegram_user_id: int,
        display_name: str,
        username: str | None = None,
    ) -> Member:
        statement = (
            insert(Member)
            .values(
                household_id=household_id,
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                username=username,
            )
            .on_conflict_do_update(
                constraint="uq_members_household_user",
                set_={
                    "display_name": display_name,
                    "username": username,
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
            .returning(Member)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()

    async def set_active(self, member: Member, *, is_active: bool) -> Member:
        member.is_active = is_active
        await self.session.flush()
        return member


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def list_available(
        self, household_id: int, *, transaction_type: TransactionType | None = None
    ) -> list[Category]:
        statement = select(Category).where(
            Category.is_active.is_(True),
            (Category.household_id.is_(None) | (Category.household_id == household_id)),
        )
        if transaction_type is not None:
            statement = statement.where(Category.type == transaction_type)
        statement = statement.order_by(Category.name)
        return list(await self.session.scalars(statement))

    async def create(
        self,
        *,
        household_id: int,
        code: str,
        name: str,
        transaction_type: TransactionType,
    ) -> Category:
        category = Category(
            household_id=household_id,
            code=code,
            name=name,
            type=transaction_type,
        )
        self.session.add(category)
        await self.session.flush()
        return category


class TransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, transaction_id: int) -> Transaction | None:
        return await self.session.get(Transaction, transaction_id)

    async def get_by_telegram_message(
        self, telegram_chat_id: int, telegram_message_id: int
    ) -> Transaction | None:
        return await self.session.scalar(
            select(Transaction).where(
                Transaction.telegram_chat_id == telegram_chat_id,
                Transaction.telegram_message_id == telegram_message_id,
            )
        )

    async def create_idempotent(
        self,
        *,
        household_id: int,
        member_id: int,
        category_id: int,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: str,
        description: str,
        transaction_date: date,
        original_text: str,
        telegram_chat_id: int,
        telegram_message_id: int,
        status: TransactionStatus = TransactionStatus.PENDING,
        ai_metadata: dict[str, Any] | None = None,
    ) -> TransactionCreateResult:
        await self._validate_references(
            household_id=household_id,
            member_id=member_id,
            category_id=category_id,
            transaction_type=transaction_type,
            telegram_chat_id=telegram_chat_id,
        )
        statement = (
            insert(Transaction)
            .values(
                household_id=household_id,
                member_id=member_id,
                category_id=category_id,
                type=transaction_type,
                amount=amount,
                currency=currency.upper(),
                description=description,
                transaction_date=transaction_date,
                original_text=original_text,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                status=status,
                ai_metadata=ai_metadata or {},
            )
            .on_conflict_do_nothing(constraint="uq_transactions_telegram_message")
            .returning(Transaction)
        )
        transaction = (await self.session.scalars(statement)).one_or_none()
        if transaction is not None:
            return TransactionCreateResult(transaction=transaction, created=True)

        existing = await self.get_by_telegram_message(telegram_chat_id, telegram_message_id)
        if existing is None:  # Defensive: conflict target should make this unreachable.
            raise RuntimeError("transaction conflict occurred but row was not found")
        return TransactionCreateResult(transaction=existing, created=False)

    async def set_status(self, transaction: Transaction, status: TransactionStatus) -> Transaction:
        transaction.status = status
        await self.session.flush()
        return transaction

    async def _validate_references(
        self,
        *,
        household_id: int,
        member_id: int,
        category_id: int,
        transaction_type: TransactionType,
        telegram_chat_id: int,
    ) -> None:
        household = await self.session.get(Household, household_id)
        member = await self.session.get(Member, member_id)
        category = await self.session.get(Category, category_id)
        if household is None or household.telegram_chat_id != telegram_chat_id:
            raise InvalidTransactionReferenceError("Telegram chat does not match household")
        if member is None or member.household_id != household_id:
            raise InvalidTransactionReferenceError("Member does not belong to household")
        if category is None or category.household_id not in (None, household_id):
            raise InvalidTransactionReferenceError("Category is not available to household")
        if category.type != transaction_type:
            raise InvalidTransactionReferenceError("Category type does not match transaction type")


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, budget_id: int) -> Budget | None:
        return await self.session.get(Budget, budget_id)

    async def upsert(
        self,
        *,
        household_id: int,
        year: int,
        month: int,
        amount: Decimal,
        category_id: int | None = None,
    ) -> Budget:
        values = {
            "household_id": household_id,
            "year": year,
            "month": month,
            "category_id": category_id,
            "amount": amount,
        }
        statement = insert(Budget).values(**values)
        if category_id is None:
            statement = statement.on_conflict_do_update(
                index_elements=[Budget.household_id, Budget.year, Budget.month],
                index_where=Budget.category_id.is_(None),
                set_={"amount": amount, "updated_at": func.now()},
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[Budget.household_id, Budget.year, Budget.month, Budget.category_id],
                index_where=Budget.category_id.is_not(None),
                set_={"amount": amount, "updated_at": func.now()},
            )
        statement = statement.returning(Budget).execution_options(populate_existing=True)
        return (await self.session.scalars(statement)).one()
