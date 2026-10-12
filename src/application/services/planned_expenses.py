from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    ReminderKind,
    TransactionStatus,
    TransactionType,
)
from domain.models import RecognitionResult
from infrastructure.database import Household, Member, PlannedExpense, Transaction, session_scope
from infrastructure.repositories import CategoryRepository, PlannedExpenseRepository


@dataclass(frozen=True, slots=True)
class ReminderCandidate:
    planned_expense: PlannedExpense
    kind: ReminderKind


class PlannedExpenseService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_pending(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
    ) -> tuple[PlannedExpense, ...]:
        async with session_scope(self.session_factory) as session:
            categories = {
                item.code: item
                for item in await CategoryRepository(session).list_available(
                    household.id, transaction_type=TransactionType.EXPENSE
                )
            }
            repository = PlannedExpenseRepository(session)
            existing = await repository.list_by_telegram_message(
                household.telegram_chat_id, telegram_message_id
            )
            if existing:
                return tuple(existing)
            plans: list[PlannedExpense] = []
            for index, recognized in enumerate(recognition.planned_expenses):
                category = categories.get(recognized.category_code)
                if category is None:
                    raise ValueError("planned expense category is unavailable")
                recurrence = (
                    PlannedExpenseRecurrence(recognized.recurrence)
                    if recognized.recurrence
                    else None
                )
                plans.append(
                    await repository.create(
                        household_id=household.id,
                        member_id=member.id,
                        category_id=category.id,
                        amount=recognized.amount,
                        currency=recognized.currency,
                        description=recognized.description,
                        due_date=recognized.due_date,
                        recurrence=recurrence,
                        original_text=original_text,
                        telegram_chat_id=household.telegram_chat_id,
                        telegram_message_id=telegram_message_id,
                        message_item_index=index,
                    )
                )
            return tuple(plans)

    async def list_active(self, household_id: int) -> tuple[PlannedExpense, ...]:
        async with self.session_factory() as session:
            return tuple(await PlannedExpenseRepository(session).list_active(household_id))

    async def get(self, household_id: int, planned_expense_id: int) -> PlannedExpense | None:
        async with self.session_factory() as session:
            return await PlannedExpenseRepository(session).get_for_household(
                household_id=household_id, planned_expense_id=planned_expense_id
            )

    async def set_status(
        self,
        *,
        household_id: int,
        planned_expense_id: int,
        status: PlannedExpenseStatus,
    ) -> PlannedExpense | None:
        if status not in {PlannedExpenseStatus.SCHEDULED, PlannedExpenseStatus.CANCELLED}:
            raise ValueError("unsupported planned expense transition")
        async with session_scope(self.session_factory) as session:
            plan = await PlannedExpenseRepository(session).get_for_household(
                household_id=household_id, planned_expense_id=planned_expense_id, lock=True
            )
            allowed_current = (
                {PlannedExpenseStatus.PENDING}
                if status is PlannedExpenseStatus.SCHEDULED
                else {PlannedExpenseStatus.PENDING, PlannedExpenseStatus.SCHEDULED}
            )
            if plan is None or plan.status not in allowed_current:
                return None
            plan.status = status
            await session.flush()
            return plan

    async def schedule(
        self,
        *,
        household_id: int,
        planned_expense_id: int,
        recurrence: PlannedExpenseRecurrence | None,
    ) -> PlannedExpense | None:
        async with session_scope(self.session_factory) as session:
            plan = await PlannedExpenseRepository(session).get_for_household(
                household_id=household_id, planned_expense_id=planned_expense_id, lock=True
            )
            if plan is None or plan.status is not PlannedExpenseStatus.PENDING:
                return None
            plan.recurrence = recurrence
            plan.status = PlannedExpenseStatus.SCHEDULED
            await session.flush()
            return plan

    async def postpone(
        self, *, household_id: int, planned_expense_id: int, due_date: date
    ) -> PlannedExpense | None:
        async with session_scope(self.session_factory) as session:
            repository = PlannedExpenseRepository(session)
            plan = await repository.get_for_household(
                household_id=household_id, planned_expense_id=planned_expense_id, lock=True
            )
            if plan is None or plan.status is not PlannedExpenseStatus.SCHEDULED:
                return None
            plan.due_date = due_date
            await repository.clear_deliveries(plan.id)
            await session.flush()
            return plan

    async def mark_paid(
        self, *, household: Household, planned_expense_id: int, paid_on: date
    ) -> PlannedExpense | None:
        async with session_scope(self.session_factory) as session:
            repository = PlannedExpenseRepository(session)
            plan = await repository.get_for_household(
                household_id=household.id, planned_expense_id=planned_expense_id, lock=True
            )
            if plan is None or plan.status is not PlannedExpenseStatus.SCHEDULED:
                return None
            transaction = Transaction(
                household_id=plan.household_id,
                member_id=plan.member_id,
                beneficiary_member_id=plan.member_id,
                category_id=plan.category_id,
                type=TransactionType.EXPENSE,
                amount=plan.amount,
                currency=plan.currency,
                description=plan.description,
                transaction_date=paid_on,
                original_text=f"Запланована оплата: {plan.description}",
                telegram_chat_id=household.telegram_chat_id,
                telegram_message_id=-plan.id,
                message_transaction_index=0,
                status=TransactionStatus.CONFIRMED,
                ai_metadata={"planned_expense_id": plan.id},
            )
            session.add(transaction)
            await session.flush()
            plan.transaction_id = transaction.id
            plan.status = PlannedExpenseStatus.PAID
            if plan.recurrence is PlannedExpenseRecurrence.MONTHLY:
                await repository.create(
                    household_id=plan.household_id,
                    member_id=plan.member_id,
                    category_id=plan.category_id,
                    amount=plan.amount,
                    currency=plan.currency,
                    description=plan.description,
                    due_date=self._next_month(plan.due_date),
                    recurrence=plan.recurrence,
                    original_text=plan.original_text,
                    telegram_chat_id=None,
                    telegram_message_id=None,
                    status=PlannedExpenseStatus.SCHEDULED,
                )
            await session.flush()
            return plan

    async def due_reminders(self, now: datetime) -> tuple[ReminderCandidate, ...]:
        async with self.session_factory() as session:
            plans = await PlannedExpenseRepository(session).list_scheduled()
        reminders: list[ReminderCandidate] = []
        for plan in plans:
            local_now = now.astimezone(ZoneInfo(plan.household.timezone))
            if local_now.time() < plan.household.morning_report_time:
                continue
            difference = (plan.due_date - local_now.date()).days
            if difference == 1:
                kind = ReminderKind.DAY_BEFORE
            elif difference == 0:
                kind = ReminderKind.DUE
            elif difference < 0:
                kind = ReminderKind.OVERDUE
            else:
                continue
            reminders.append(ReminderCandidate(plan, kind))
        return tuple(reminders)

    async def claim_reminder(self, planned_expense_id: int, kind: ReminderKind) -> bool:
        async with session_scope(self.session_factory) as session:
            return await PlannedExpenseRepository(session).claim_delivery(planned_expense_id, kind)

    async def release_reminder(self, planned_expense_id: int, kind: ReminderKind) -> None:
        async with session_scope(self.session_factory) as session:
            await PlannedExpenseRepository(session).release_delivery(planned_expense_id, kind)

    @staticmethod
    def _next_month(value: date) -> date:
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        return date(year, month, min(value.day, monthrange(year, month)[1]))


__all__ = ["PlannedExpenseService", "ReminderCandidate"]
