"""Domain enumerations shared by persistence and application layers."""

from enum import StrEnum


class TransactionType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


__all__ = ["TransactionStatus", "TransactionType"]
