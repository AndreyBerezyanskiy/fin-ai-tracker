from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from domain.enums import TransactionType


class RecognitionIntent(StrEnum):
    CREATE_TRANSACTIONS = "create_transactions"
    CREATE_PLANNED_EXPENSES = "create_planned_expenses"
    GET_STATISTICS = "get_statistics"
    NOT_A_TRANSACTION = "not_a_transaction"


class AITransactionCandidate(BaseModel):
    """Schema returned by OpenAI before application-side validation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: TransactionType | None
    amount: str | None
    currency: str | None
    category_code: str | None
    description: str | None
    beneficiary_member_id: int | None
    transaction_date: str | None = Field(alias="date")
    recurrence: str | None


class AIStatisticsQuery(BaseModel):
    """Date range and optional transaction type returned by OpenAI."""

    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType | None
    start_date: str | None
    end_date: str | None


class AIRecognitionResponse(BaseModel):
    """Strict Structured Outputs schema used at the API boundary."""

    model_config = ConfigDict(extra="forbid")

    intent: RecognitionIntent
    transactions: list[AITransactionCandidate] = Field(max_length=10)
    needs_clarification: bool
    clarification_question: str | None
    ambiguous_member_name: str | None = None
    suggested_member_id: int | None = None
    statistics_query: AIStatisticsQuery | None = None


class RecognizedTransaction(BaseModel):
    """Validated transaction data safe for deterministic application logic."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: TransactionType
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    category_code: str = Field(min_length=1, max_length=64)
    description: str = Field(max_length=500)
    beneficiary_member_id: int | None = Field(default=None, gt=0)
    transaction_date: date = Field(alias="date")

    @field_serializer("amount")
    def serialize_amount(self, amount: Decimal) -> str:
        return f"{amount:.2f}"


class RecognizedPlannedExpense(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    category_code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    due_date: date
    recurrence: str | None = None


class RecognizedStatisticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType | None = None
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    code: str
    name: str
    type: TransactionType


@dataclass(frozen=True, slots=True)
class MemberDefinition:
    id: int
    display_name: str
    username: str | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    intent: RecognitionIntent
    transactions: tuple[RecognizedTransaction, ...]
    needs_clarification: bool
    clarification_question: str | None
    ai_metadata: dict[str, Any]
    ambiguous_member_name: str | None = None
    suggested_member_id: int | None = None
    missing_category_type: TransactionType | None = None
    planned_expenses: tuple[RecognizedPlannedExpense, ...] = ()
    statistics_query: RecognizedStatisticsQuery | None = None


__all__ = [
    "AIRecognitionResponse",
    "AIStatisticsQuery",
    "AITransactionCandidate",
    "CategoryDefinition",
    "MemberDefinition",
    "RecognitionIntent",
    "RecognitionResult",
    "RecognizedPlannedExpense",
    "RecognizedStatisticsQuery",
    "RecognizedTransaction",
]
