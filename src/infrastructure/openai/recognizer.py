from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from domain.enums import TransactionType
from domain.models import (
    AIRecognitionResponse,
    AITransactionCandidate,
    CategoryDefinition,
    MemberDefinition,
    RecognitionIntent,
    RecognitionResult,
    RecognizedTransaction,
)

MISSING_AMOUNT_OR_TYPE_QUESTION = "Не вдалося визначити суму. Скільки коштувала покупка?"
INVALID_AMOUNT_QUESTION = "Сума має бути більшою за нуль. Уточніть суму, будь ласка."
INVALID_DATE_QUESTION = "Не вдалося визначити дату. Уточніть дату операції, будь ласка."
GENERIC_CLARIFICATION_QUESTION = "Уточніть, будь ласка, суму та тип операції."

SYSTEM_PROMPT = """Ти розпізнаєш сімейні фінансові операції з коротких повідомлень.
Поверни лише дані за наданою схемою. Не роби фінансових розрахунків і не виконуй дій.

Правила:
- Одне повідомлення може містити кілька операцій; поверни кожну окремо.
- type може бути лише expense або income.
- amount поверни десятковим рядком із крапкою, без назви валюти.
- Якщо валюта не вказана, використай основну валюту.
- Не конвертуй валюти.
- Для «сьогодні» та «вчора» обчисли календарну дату з переданих local_date і timezone.
- date поверни у форматі YYYY-MM-DD.
- Використовуй лише передані category_code. Якщо категорія витрати незрозуміла,
  використовуй other; для незрозумілого доходу використовуй other_income.
- description має бути коротким змістовним описом без суми, дати та валюти.
- Якщо явно вказано, для кого операція, поверни id цієї людини з allowed_members у
  beneficiary_member_id. Якщо людину не вказано, поверни null. Не вигадуй учасників.
- Якщо вказане ім’я не можна однозначно зіставити з allowed_members, попроси уточнення.
- Якщо повідомлення не описує фінансову операцію, intent=not_a_transaction і transactions=[].
- Якщо сума або тип відсутні, залиш відповідне поле null і попроси коротке уточнення.
"""


class OpenAITransactionRecognizer:
    """Extract and validate transactions without persisting them."""

    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        self.client = client
        self.model = model

    async def recognize(
        self,
        *,
        message: str,
        local_date: date,
        timezone: str,
        base_currency: str,
        allowed_categories: Iterable[CategoryDefinition],
        allowed_members: Iterable[MemberDefinition] = (),
        default_member_id: int | None = None,
    ) -> RecognitionResult:
        categories = tuple(allowed_categories)
        members = tuple(allowed_members)
        request_data = {
            "original_message": message,
            "current_local_date": local_date.isoformat(),
            "timezone": timezone,
            "base_currency": base_currency.upper(),
            "allowed_categories": [
                {"code": item.code, "name": item.name, "type": item.type.value}
                for item in categories
            ],
            "allowed_members": [
                {"id": item.id, "display_name": item.display_name, "username": item.username}
                for item in members
            ],
        }
        response = await self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(request_data, ensure_ascii=False),
            text_format=AIRecognitionResponse,
            reasoning={"effort": "low"},
            store=False,
        )
        metadata = self._metadata(response)
        parsed = response.output_parsed
        if parsed is None:
            return self._clarification(GENERIC_CLARIFICATION_QUESTION, metadata)
        if parsed.intent is RecognitionIntent.NOT_A_TRANSACTION:
            return RecognitionResult(
                intent=RecognitionIntent.NOT_A_TRANSACTION,
                transactions=(),
                needs_clarification=False,
                clarification_question=None,
                ai_metadata=metadata,
            )

        category_by_code = {item.code: item for item in categories}
        allowed_member_ids = {item.id for item in members}
        validated: list[RecognizedTransaction] = []
        for candidate in parsed.transactions:
            transaction_or_question = self._validate_candidate(
                candidate,
                local_date=local_date,
                base_currency=base_currency.upper(),
                category_by_code=category_by_code,
                allowed_member_ids=allowed_member_ids,
                default_member_id=default_member_id,
            )
            if isinstance(transaction_or_question, str):
                return self._clarification(transaction_or_question, metadata)
            validated.append(transaction_or_question)

        if not validated:
            question = self._safe_question(parsed.clarification_question)
            return self._clarification(question, metadata)
        if parsed.needs_clarification:
            question = self._safe_question(parsed.clarification_question)
            return self._clarification(question, metadata)
        return RecognitionResult(
            intent=RecognitionIntent.CREATE_TRANSACTIONS,
            transactions=tuple(validated),
            needs_clarification=False,
            clarification_question=None,
            ai_metadata=metadata,
        )

    def _validate_candidate(
        self,
        candidate: AITransactionCandidate,
        *,
        local_date: date,
        base_currency: str,
        category_by_code: dict[str, CategoryDefinition],
        allowed_member_ids: set[int],
        default_member_id: int | None,
    ) -> RecognizedTransaction | str:
        if candidate.amount is None or candidate.type is None:
            return MISSING_AMOUNT_OR_TYPE_QUESTION

        try:
            amount = Decimal(candidate.amount.strip().replace(",", "."))
        except (InvalidOperation, AttributeError):
            return MISSING_AMOUNT_OR_TYPE_QUESTION
        if not amount.is_finite() or amount <= 0:
            return INVALID_AMOUNT_QUESTION
        try:
            amount = amount.quantize(Decimal("0.01"))
        except InvalidOperation:
            return INVALID_AMOUNT_QUESTION

        currency = (candidate.currency or base_currency).upper()
        if currency != base_currency:
            return f"Поки що підтримується лише {base_currency}. Вкажіть суму в {base_currency}."

        try:
            transaction_date = (
                date.fromisoformat(candidate.transaction_date)
                if candidate.transaction_date
                else local_date
            )
        except ValueError:
            return INVALID_DATE_QUESTION

        category_code = candidate.category_code or self._fallback_category(candidate.type)
        category = category_by_code.get(category_code)
        if category is None or category.type is not candidate.type:
            category_code = self._fallback_category(candidate.type)
            category = category_by_code.get(category_code)
        if category is None or category.type is not candidate.type:
            return "Не вдалося визначити доступну категорію операції."

        beneficiary_member_id = candidate.beneficiary_member_id or default_member_id
        if beneficiary_member_id is not None and beneficiary_member_id not in allowed_member_ids:
            return "Не вдалося визначити члена сім’ї. Уточніть ім’я, будь ласка."

        try:
            return RecognizedTransaction(
                type=candidate.type,
                amount=amount,
                currency=currency,
                category_code=category.code,
                description=(candidate.description or "").strip()[:500],
                beneficiary_member_id=beneficiary_member_id,
                date=transaction_date,
            )
        except ValidationError:
            return INVALID_AMOUNT_QUESTION

    @staticmethod
    def _fallback_category(transaction_type: TransactionType) -> str:
        return "other" if transaction_type is TransactionType.EXPENSE else "other_income"

    @staticmethod
    def _safe_question(question: str | None) -> str:
        if question and question.strip():
            return question.strip()[:200]
        return GENERIC_CLARIFICATION_QUESTION

    @staticmethod
    def _clarification(question: str, metadata: dict[str, Any]) -> RecognitionResult:
        return RecognitionResult(
            intent=RecognitionIntent.CREATE_TRANSACTIONS,
            transactions=(),
            needs_clarification=True,
            clarification_question=question,
            ai_metadata=metadata,
        )

    @staticmethod
    def _metadata(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        metadata: dict[str, Any] = {
            "response_id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
        }
        if usage is not None:
            metadata["input_tokens"] = getattr(usage, "input_tokens", None)
            metadata["output_tokens"] = getattr(usage, "output_tokens", None)
        return {key: value for key, value in metadata.items() if value is not None}
