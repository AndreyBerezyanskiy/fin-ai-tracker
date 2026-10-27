from __future__ import annotations

from typing import Protocol

from infrastructure.database import Household, PlannedExpense, TransactionActionMessage


class ScheduleChangeListener(Protocol):
    """Receive committed state changes that affect in-memory scheduled jobs."""

    def household_changed(self, household: Household) -> None: ...

    def planned_expense_changed(self, planned_expense: PlannedExpense) -> None: ...

    def action_message_changed(self, action_message: TransactionActionMessage) -> None: ...

    def action_message_removed(self, telegram_chat_id: int, telegram_message_id: int) -> None: ...


__all__ = ["ScheduleChangeListener"]
