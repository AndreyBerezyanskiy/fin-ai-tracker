from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from openai import AsyncOpenAI

from application.services.advice import AdviceReport
from domain.models import AIAdviceResponse

SYSTEM_PROMPT = """Сформулюй українською від одного до трьох коротких спостережень про бюджет.
Використовуй лише готові значення з агрегованого звіту. Не обчислюй нових сум,
відсотків чи прогнозів. Не вигадуй причин витрат або відсутні дані. Не згадуй учасників.
Не називай текст професійною фінансовою консультацією. Не рекомендуй кредити,
інвестиції чи конкретні фінансові продукти. Не нумеруй спостереження всередині тексту.
Якщо певне поле має значення null, не роби висновків на його основі.
"""

_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?![\w])")
_FORBIDDEN_TERMS = (
    "кредит",
    "позик",
    "інвест",
    "акці",
    "облігац",
    "депозит",
    "фінансовий продукт",
    "професійн",
    "консультац",
)


class InvalidAdviceError(ValueError):
    pass


class OpenAIAdviceGenerator:
    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        self.client = client
        self.model = model

    async def generate(self, report: AdviceReport) -> tuple[str, ...]:
        payload = self._payload(report)
        response = await self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(payload, ensure_ascii=False),
            text_format=AIAdviceResponse,
            reasoning={"effort": "low"},
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise InvalidAdviceError("OpenAI returned no structured advice")
        observations = tuple(parsed.observations)
        self._validate(observations, payload)
        return observations

    @staticmethod
    def _decimal(value: Decimal | None) -> str | None:
        return None if value is None else f"{value:.2f}"

    @classmethod
    def _payload(cls, report: AdviceReport) -> dict[str, Any]:
        return {
            "currency": report.currency,
            "budget": cls._decimal(report.budget),
            "spent": cls._decimal(report.spent),
            "budget_used_percent": cls._decimal(report.budget_used_percent),
            "top_categories": [
                {
                    "name": item.name,
                    "spent": cls._decimal(item.spent),
                    "budget": cls._decimal(item.budget),
                    "budget_used_percent": cls._decimal(item.budget_used_percent),
                }
                for item in report.top_categories
            ],
            "dynamics": {
                "previous_month_spent": cls._decimal(report.previous_month_spent),
                "expense_change": cls._decimal(report.expense_change),
                "expense_change_percent": cls._decimal(report.expense_change_percent),
            },
            "days_remaining": report.days_remaining,
        }

    @classmethod
    def _validate(cls, observations: tuple[str, ...], payload: dict[str, Any]) -> None:
        source = json.dumps(payload, ensure_ascii=False)
        allowed_numbers = cls._numbers(source)
        allowed_numbers.update(abs(value) for value in tuple(allowed_numbers))
        for observation in observations:
            lowered = observation.casefold()
            if any(term in lowered for term in _FORBIDDEN_TERMS):
                raise InvalidAdviceError("advice contains a forbidden recommendation")
            if not cls._numbers(observation).issubset(allowed_numbers):
                raise InvalidAdviceError("advice contains a number absent from the report")

    @staticmethod
    def _numbers(text: str) -> set[Decimal]:
        result: set[Decimal] = set()
        for raw in _NUMBER.findall(text):
            try:
                result.add(Decimal(raw.replace(",", ".")))
            except InvalidOperation:
                continue
        return result


__all__ = ["InvalidAdviceError", "OpenAIAdviceGenerator"]
