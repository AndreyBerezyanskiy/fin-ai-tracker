from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.services import (
    AdviceCategory,
    AdviceGeneration,
    AdviceReport,
    AdviceService,
    AIUsageData,
    BudgetStatus,
    CategoryBudgetStatus,
    MonthReport,
    PeriodTotals,
    ReportCategoryTotal,
)
from application.services.advice import INSUFFICIENT_DATA_MESSAGE
from domain.models import AIAdviceResponse
from infrastructure.openai import InvalidAdviceError, OpenAIAdviceGenerator
from infrastructure.openai.advisor import SYSTEM_PROMPT


def advice_report() -> AdviceReport:
    return AdviceReport(
        currency="EUR",
        budget=Decimal("2000.00"),
        spent=Decimal("750.00"),
        budget_used_percent=Decimal("37.50"),
        top_categories=(
            AdviceCategory(
                name="Продукти",
                spent=Decimal("450.00"),
                budget=Decimal("600.00"),
                budget_used_percent=Decimal("75.00"),
            ),
        ),
        previous_month_spent=Decimal("850.00"),
        expense_change=Decimal("-100.00"),
        expense_change_percent=Decimal("-11.7647"),
        days_remaining=12,
    )


def generator_with(observations: list[str]) -> tuple[OpenAIAdviceGenerator, AsyncMock]:
    parse = AsyncMock(
        return_value=SimpleNamespace(
            id="resp_advice",
            model="gpt-6-luna",
            output_parsed=AIAdviceResponse(observations=observations),
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=30,
                input_tokens_details=SimpleNamespace(cached_tokens=20),
            ),
        )
    )
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return OpenAIAdviceGenerator(client, model="test-model"), parse


@pytest.mark.asyncio
async def test_advice_uses_only_aggregate_report_and_structured_output() -> None:
    generator, parse = generator_with(
        [
            "Використано 37,5% загального бюджету; до кінця місяця 12 днів.",
            "На продукти витрачено €450 із €600.",
        ]
    )

    generation = await generator.generate(advice_report())

    assert len(generation.observations) == 2
    assert generation.usage.input_tokens == 120
    assert generation.usage.cached_input_tokens == 20
    request = parse.await_args.kwargs
    assert request["text_format"] is AIAdviceResponse
    assert request["store"] is False
    assert request["instructions"] == SYSTEM_PROMPT
    assert "цікавих і практичних" in request["instructions"]
    assert "тепло, доброзичливо та позитивно" in request["instructions"]
    payload = json.loads(request["input"])
    assert payload["budget"] == "2000.00"
    assert payload["top_categories"] == [
        {
            "name": "Продукти",
            "spent": "450.00",
            "budget": "600.00",
            "budget_used_percent": "75.00",
        }
    ]
    assert "member" not in request["input"]
    assert "transaction" not in request["input"]


@pytest.mark.asyncio
async def test_advice_rejects_numbers_absent_from_report() -> None:
    generator, _parse = generator_with(["Можна заощадити приблизно €110."])

    with pytest.raises(InvalidAdviceError, match="number absent") as raised:
        await generator.generate(advice_report())

    assert raised.value.usage is not None
    assert raised.value.usage.response_id == "resp_advice"


@pytest.mark.asyncio
async def test_advice_rejects_forbidden_financial_recommendations() -> None:
    generator, _parse = generator_with(["Розгляньте кредит на €600."])

    with pytest.raises(InvalidAdviceError, match="forbidden"):
        await generator.generate(advice_report())


@pytest.mark.asyncio
async def test_advice_service_builds_deterministic_report() -> None:
    report_service = SimpleNamespace(
        month_report=AsyncMock(
            return_value=MonthReport(
                totals=PeriodTotals(expense=Decimal("750.00")),
                expense_categories=(
                    ReportCategoryTotal("groceries", "Продукти", Decimal("450.00")),
                    ReportCategoryTotal("transport", "Транспорт", Decimal("200.00")),
                ),
                previous_totals=PeriodTotals(expense=Decimal("850.00")),
            )
        )
    )
    budget_service = SimpleNamespace(
        status=AsyncMock(
            return_value=BudgetStatus(
                total_limit=Decimal("2000.00"),
                spent=Decimal("750.00"),
                days_remaining=12,
                categories=(
                    CategoryBudgetStatus(
                        code="groceries",
                        name="Продукти",
                        limit=Decimal("600.00"),
                        spent=Decimal("450.00"),
                    ),
                ),
            )
        )
    )
    usage = AIUsageData(model="gpt-6-luna", response_id="resp_advice")
    advice_generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=AdviceGeneration(observations=("Спостереження",), usage=usage)
        )
    )
    usage_service = SimpleNamespace(record=AsyncMock())
    service = AdviceService(
        report_service, budget_service, advice_generator, usage_service
    )

    result = await service.generate(household_id=7, currency="EUR", today=date(2026, 10, 19))

    assert result == ("Спостереження",)
    aggregate = advice_generator.generate.await_args.args[0]
    assert aggregate.spent == Decimal("750.00")
    assert aggregate.expense_change == Decimal("-100.00")
    assert aggregate.expense_change_percent.quantize(Decimal("0.01")) == Decimal("-11.76")
    assert aggregate.top_categories[0].budget == Decimal("600.00")
    assert aggregate.top_categories[1].budget is None
    usage_service.record.assert_awaited_once()
    assert usage_service.record.await_args.kwargs["household_id"] == 7


@pytest.mark.asyncio
async def test_advice_service_skips_ai_when_there_are_no_expenses() -> None:
    report_service = SimpleNamespace(
        month_report=AsyncMock(
            return_value=MonthReport(
                totals=PeriodTotals(), expense_categories=(), previous_totals=None
            )
        )
    )
    budget_service = SimpleNamespace(
        status=AsyncMock(
            return_value=BudgetStatus(
                total_limit=None, spent=Decimal("0.00"), days_remaining=12, categories=()
            )
        )
    )
    advice_generator = SimpleNamespace(generate=AsyncMock())
    service = AdviceService(report_service, budget_service, advice_generator)

    result = await service.generate(household_id=7, currency="EUR", today=date(2026, 10, 19))

    assert result == (INSUFFICIENT_DATA_MESSAGE,)
    advice_generator.generate.assert_not_awaited()
    budget_service.status.assert_not_awaited()
