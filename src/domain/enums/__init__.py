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


__all__ = ["ReportType", "TransactionStatus", "TransactionType"]
