"""Domain enumerations shared by persistence and application layers."""

from enum import StrEnum


class TransactionType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ReportType(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


class PlannedExpenseStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PAID = "paid"
    CANCELLED = "cancelled"


class PlannedExpenseRecurrence(StrEnum):
    MONTHLY = "monthly"


class ReminderKind(StrEnum):
    DAY_BEFORE = "day_before"
    DUE = "due"
    OVERDUE = "overdue"


__all__ = [
    "PlannedExpenseRecurrence",
    "PlannedExpenseStatus",
    "ReminderKind",
    "ReportType",
    "TransactionStatus",
    "TransactionType",
]
