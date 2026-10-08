from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.enums import TransactionStatus, TransactionType
from infrastructure.database.models import (
    Budget,
    Category,
    Household,
    Member,
    ProcessedTelegramUpdate,
    Transaction,
)


class InvalidTransactionReferenceError(ValueError):
    """Raised when transaction references do not belong to its household."""


@dataclass(frozen=True, slots=True)
class TransactionCreateResult:
    transaction: Transaction
    created: bool


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    code: str
    type: TransactionType
    amount: Decimal


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

    async def list_active(self, household_id: int) -> list[Member]:
        return list(
            await self.session.scalars(
                select(Member)
                .where(Member.household_id == household_id, Member.is_active.is_(True))
                .order_by(Member.display_name, Member.id)
            )
        )


class ProcessedTelegramUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, update_id: int) -> bool:
        statement = (
            insert(ProcessedTelegramUpdate)
            .values(update_id=update_id)
            .on_conflict_do_nothing(index_elements=[ProcessedTelegramUpdate.update_id])
            .returning(ProcessedTelegramUpdate.update_id)
        )
        return (await self.session.scalar(statement)) is not None

    async def release(self, update_id: int) -> None:
        await self.session.execute(
            delete(ProcessedTelegramUpdate).where(ProcessedTelegramUpdate.update_id == update_id)
        )


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def list_available(
        self, household_id: int, *, transaction_type: TransactionType | None = None
    ) -> list[Category]:
        categories = await self.list_for_management(household_id)
        return [
            category
            for category in categories
            if category.is_active
            and (transaction_type is None or category.type is transaction_type)
        ]

    async def list_for_management(self, household_id: int) -> list[Category]:
        statement = select(Category).where(
            Category.household_id.is_(None) | (Category.household_id == household_id)
        )
        categories = list(await self.session.scalars(statement))
        effective: dict[str, Category] = {}
        for category in sorted(categories, key=lambda item: item.household_id is not None):
            effective[category.code] = category
        return sorted(effective.values(), key=lambda item: (item.type.value, item.name.casefold()))

    async def get_effective_by_code(self, household_id: int, code: str) -> Category | None:
        categories = await self.list_for_management(household_id)
        return next((category for category in categories if category.code == code), None)

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

    async def rename_effective(self, *, household_id: int, code: str, name: str) -> Category | None:
        category = await self.get_effective_by_code(household_id, code)
        if category is None:
            return None
        if category.household_id == household_id:
            category.name = name
        else:
            category = Category(
                household_id=household_id,
                code=category.code,
                name=name,
                type=category.type,
                is_active=category.is_active,
            )
            self.session.add(category)
        await self.session.flush()
        return category

    async def set_effective_active(
        self, *, household_id: int, code: str, is_active: bool
    ) -> Category | None:
        category = await self.get_effective_by_code(household_id, code)
        if category is None:
            return None
        if category.household_id == household_id:
            category.is_active = is_active
        else:
            category = Category(
                household_id=household_id,
                code=category.code,
                name=category.name,
                type=category.type,
                is_active=is_active,
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
    ) -> list[Transaction]:
        return list(
            await self.session.scalars(
                select(Transaction)
                .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
                .where(
                    Transaction.telegram_chat_id == telegram_chat_id,
                    Transaction.telegram_message_id == telegram_message_id,
                )
                .order_by(Transaction.message_transaction_index)
            )
        )

    async def lock_by_telegram_message(
        self, *, household_id: int, telegram_message_id: int
    ) -> list[Transaction]:
        statement = (
            select(Transaction)
            .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
            .where(
                Transaction.household_id == household_id,
                Transaction.telegram_message_id == telegram_message_id,
            )
            .order_by(Transaction.message_transaction_index)
            .with_for_update()
        )
        return list(await self.session.scalars(statement))

    async def create_idempotent(
        self,
        *,
        household_id: int,
        member_id: int,
        beneficiary_member_id: int | None = None,
        category_id: int,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: str,
        description: str,
        transaction_date: date,
        original_text: str,
        telegram_chat_id: int,
        telegram_message_id: int,
        message_transaction_index: int = 0,
        status: TransactionStatus = TransactionStatus.PENDING,
        ai_metadata: dict[str, Any] | None = None,
    ) -> TransactionCreateResult:
        await self._validate_references(
            household_id=household_id,
            member_id=member_id,
            beneficiary_member_id=beneficiary_member_id or member_id,
            category_id=category_id,
            transaction_type=transaction_type,
            telegram_chat_id=telegram_chat_id,
        )
        statement = (
            insert(Transaction)
            .values(
                household_id=household_id,
                member_id=member_id,
                beneficiary_member_id=beneficiary_member_id or member_id,
                category_id=category_id,
                type=transaction_type,
                amount=amount,
                currency=currency.upper(),
                description=description,
                transaction_date=transaction_date,
                original_text=original_text,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                message_transaction_index=message_transaction_index,
                status=status,
                ai_metadata=ai_metadata or {},
            )
            .on_conflict_do_nothing(constraint="uq_transactions_telegram_message_item")
            .returning(Transaction)
        )
        transaction = (await self.session.scalars(statement)).one_or_none()
        if transaction is not None:
            return TransactionCreateResult(transaction=transaction, created=True)

        existing = await self.session.scalar(
            select(Transaction).where(
                Transaction.telegram_chat_id == telegram_chat_id,
                Transaction.telegram_message_id == telegram_message_id,
                Transaction.message_transaction_index == message_transaction_index,
            )
        )
        if existing is None:  # Defensive: conflict target should make this unreachable.
            raise RuntimeError("transaction conflict occurred but row was not found")
        return TransactionCreateResult(transaction=existing, created=False)

    async def list_recent_confirmed(
        self,
        household_id: int,
        *,
        limit: int = 5,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
    ) -> list[Transaction]:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
        ]
        if start_date is not None:
            filters.append(Transaction.transaction_date >= start_date)
        if end_date is not None:
            filters.append(Transaction.transaction_date < end_date)
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Transaction)
            .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
            .where(*filters)
            .order_by(
                Transaction.transaction_date.desc(),
                Transaction.updated_at.desc(),
                Transaction.id.desc(),
            )
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def confirmed_balance(self, household_id: int, *, currency: str | None = None) -> Decimal:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
        ]
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Transaction.type, func.sum(Transaction.amount))
            .where(*filters)
            .group_by(Transaction.type)
        )
        amounts = dict((await self.session.execute(statement)).all())
        return amounts.get(TransactionType.INCOME, Decimal("0.00")) - amounts.get(
            TransactionType.EXPENSE, Decimal("0.00")
        )

    async def category_totals(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str | None = None,
    ) -> list[CategoryTotal]:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date < end_date,
        ]
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Category.code, Transaction.type, func.sum(Transaction.amount))
            .join(Category, Category.id == Transaction.category_id)
            .where(*filters)
            .group_by(Category.code, Transaction.type)
            .order_by(Transaction.type, func.sum(Transaction.amount).desc())
        )
        rows = await self.session.execute(statement)
        return [
            CategoryTotal(code, transaction_type, amount) for code, transaction_type, amount in rows
        ]

    async def cancel_last_confirmed(
        self, *, household_id: int, member_id: int
    ) -> Transaction | None:
        transaction = await self.session.scalar(
            select(Transaction)
            .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
            .where(
                Transaction.household_id == household_id,
                Transaction.member_id == member_id,
                Transaction.status == TransactionStatus.CONFIRMED,
            )
            .order_by(Transaction.updated_at.desc(), Transaction.id.desc())
            .limit(1)
            .with_for_update()
        )
        if transaction is None:
            return None
        return await self.set_status(transaction, TransactionStatus.CANCELLED)

    async def set_status(self, transaction: Transaction, status: TransactionStatus) -> Transaction:
        transaction.status = status
        await self.session.flush()
        return transaction

    async def _validate_references(
        self,
        *,
        household_id: int,
        member_id: int,
        beneficiary_member_id: int,
        category_id: int,
        transaction_type: TransactionType,
        telegram_chat_id: int,
    ) -> None:
        household = await self.session.get(Household, household_id)
        member = await self.session.get(Member, member_id)
        beneficiary = await self.session.get(Member, beneficiary_member_id)
        category = await self.session.get(Category, category_id)
        if household is None or household.telegram_chat_id != telegram_chat_id:
            raise InvalidTransactionReferenceError("Telegram chat does not match household")
        if member is None or member.household_id != household_id:
            raise InvalidTransactionReferenceError("Member does not belong to household")
        if beneficiary is None or beneficiary.household_id != household_id:
            raise InvalidTransactionReferenceError("Beneficiary does not belong to household")
        if category is None or category.household_id not in (None, household_id):
            raise InvalidTransactionReferenceError("Category is not available to household")
        if category.type != transaction_type:
            raise InvalidTransactionReferenceError("Category type does not match transaction type")


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, budget_id: int) -> Budget | None:
        return await self.session.get(Budget, budget_id)

    async def list_for_period(self, *, household_id: int, year: int, month: int) -> list[Budget]:
        return list(
            await self.session.scalars(
                select(Budget)
                .options(selectinload(Budget.category))
                .where(
                    Budget.household_id == household_id,
                    Budget.year == year,
                    Budget.month == month,
                )
                .order_by(Budget.category_id.asc().nulls_first())
            )
        )

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
