from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from domain.enums import TransactionType
from domain.models import (
    AIRecognitionResponse,
    AITransactionCandidate,
    CategoryDefinition,
    MemberDefinition,
    RecognitionIntent,
)
from infrastructure.openai.recognizer import (
    MISSING_AMOUNT_OR_TYPE_QUESTION,
    OpenAITransactionRecognizer,
)

CATEGORIES = (
    CategoryDefinition("groceries", "Продукти", TransactionType.EXPENSE),
    CategoryDefinition("cafes_restaurants", "Кафе та ресторани", TransactionType.EXPENSE),
    CategoryDefinition("transport", "Транспорт", TransactionType.EXPENSE),
    CategoryDefinition("car", "Автомобіль", TransactionType.EXPENSE),
    CategoryDefinition("housing", "Житло", TransactionType.EXPENSE),
    CategoryDefinition("utilities", "Комунальні послуги", TransactionType.EXPENSE),
    CategoryDefinition("health", "Здоров’я", TransactionType.EXPENSE),
    CategoryDefinition("children", "Діти", TransactionType.EXPENSE),
    CategoryDefinition("clothing", "Одяг", TransactionType.EXPENSE),
    CategoryDefinition("entertainment", "Розваги", TransactionType.EXPENSE),
    CategoryDefinition("travel", "Подорожі", TransactionType.EXPENSE),
    CategoryDefinition("subscriptions", "Підписки", TransactionType.EXPENSE),
    CategoryDefinition("other", "Інше", TransactionType.EXPENSE),
    CategoryDefinition("salary", "Зарплата", TransactionType.INCOME),
    CategoryDefinition("other_income", "Інший дохід", TransactionType.INCOME),
)


@dataclass(frozen=True)
class Example:
    message: str
    transactions: tuple[dict[str, str | None], ...]
    expected: tuple[tuple[str, str, str, str], ...]


def transaction(
    transaction_type: str,
    amount: str,
    category: str,
    description: str,
    transaction_date: str = "2026-10-07",
    currency: str = "EUR",
) -> dict[str, str | None]:
    return {
        "type": transaction_type,
        "amount": amount,
        "currency": currency,
        "category_code": category,
        "description": description,
        "beneficiary_member_id": None,
        "date": transaction_date,
    }


EXAMPLES = (
    Example(
        "32 євро продукти в Lidl",
        (transaction("expense", "32.00", "groceries", "Lidl"),),
        (("32.00", "groceries", "2026-10-07", "expense"),),
    ),
    Example(
        "вчора заправка 64.20",
        (transaction("expense", "64.20", "car", "Заправка", "2026-10-06"),),
        (("64.20", "car", "2026-10-06", "expense"),),
    ),
    Example(
        "отримав зарплату 2500",
        (transaction("income", "2500", "salary", "Зарплата"),),
        (("2500.00", "salary", "2026-10-07", "income"),),
    ),
    Example(
        "кава 4,50",
        (transaction("expense", "4,50", "cafes_restaurants", "Кава"),),
        (("4.50", "cafes_restaurants", "2026-10-07", "expense"),),
    ),
    Example(
        "сьогодні 18 аптека і 12 продукти",
        (
            transaction("expense", "18", "health", "Аптека"),
            transaction("expense", "12", "groceries", "Продукти"),
        ),
        (
            ("18.00", "health", "2026-10-07", "expense"),
            ("12.00", "groceries", "2026-10-07", "expense"),
        ),
    ),
    Example(
        "courses chez Carrefour 45,90 €",
        (transaction("expense", "45.90", "groceries", "Carrefour"),),
        (("45.90", "groceries", "2026-10-07", "expense"),),
    ),
    Example(
        "hier essence 70.10",
        (transaction("expense", "70.10", "car", "Essence", "2026-10-06"),),
        (("70.10", "car", "2026-10-06", "expense"),),
    ),
    Example(
        "salaire reçu 2800 euros",
        (transaction("income", "2800", "salary", "Salaire"),),
        (("2800.00", "salary", "2026-10-07", "income"),),
    ),
    Example(
        "café 3,20 et métro 2.15",
        (
            transaction("expense", "3.20", "cafes_restaurants", "Café"),
            transaction("expense", "2.15", "transport", "Métro"),
        ),
        (
            ("3.20", "cafes_restaurants", "2026-10-07", "expense"),
            ("2.15", "transport", "2026-10-07", "expense"),
        ),
    ),
    Example(
        "loyer 1200 le 01/10",
        (transaction("expense", "1200", "housing", "Loyer", "2026-10-01"),),
        (("1200.00", "housing", "2026-10-01", "expense"),),
    ),
    Example(
        "Lidl groceries 23.40 вчора",
        (transaction("expense", "23.40", "groceries", "Lidl", "2026-10-06"),),
        (("23.40", "groceries", "2026-10-06", "expense"),),
    ),
    Example(
        "Uber 14,80 сьогодні",
        (transaction("expense", "14.80", "transport", "Uber"),),
        (("14.80", "transport", "2026-10-07", "expense"),),
    ),
    Example(
        "internet Orange 39.99",
        (transaction("expense", "39.99", "utilities", "Orange Internet"),),
        (("39.99", "utilities", "2026-10-07", "expense"),),
    ),
    Example(
        "лікар 60 євро 05.10.2026",
        (transaction("expense", "60", "health", "Лікар", "2026-10-05"),),
        (("60.00", "health", "2026-10-05", "expense"),),
    ),
    Example(
        "école 25,00",
        (transaction("expense", "25.00", "children", "École"),),
        (("25.00", "children", "2026-10-07", "expense"),),
    ),
    Example(
        "куртка 89.95",
        (transaction("expense", "89.95", "clothing", "Куртка"),),
        (("89.95", "clothing", "2026-10-07", "expense"),),
    ),
    Example(
        "cinéma 22",
        (transaction("expense", "22", "entertainment", "Cinéma"),),
        (("22.00", "entertainment", "2026-10-07", "expense"),),
    ),
    Example(
        "train Paris 110,50",
        (transaction("expense", "110.50", "travel", "Train Paris"),),
        (("110.50", "travel", "2026-10-07", "expense"),),
    ),
    Example(
        "Netflix 13.49",
        (transaction("expense", "13.49", "subscriptions", "Netflix"),),
        (("13.49", "subscriptions", "2026-10-07", "expense"),),
    ),
    Example(
        "подарунок 30",
        (transaction("expense", "30", "other", "Подарунок"),),
        (("30.00", "other", "2026-10-07", "expense"),),
    ),
    Example(
        "повернули 15 євро",
        (transaction("income", "15", "other_income", "Повернення"),),
        (("15.00", "other_income", "2026-10-07", "income"),),
    ),
    Example(
        "remboursement 42,75",
        (transaction("income", "42.75", "other_income", "Remboursement"),),
        (("42.75", "other_income", "2026-10-07", "income"),),
    ),
    Example(
        "06.10 продукти 19.80",
        (transaction("expense", "19.80", "groceries", "Продукти", "2026-10-06"),),
        (("19.80", "groceries", "2026-10-06", "expense"),),
    ),
    Example(
        "вчора boulangerie 8,30",
        (transaction("expense", "8.30", "groceries", "Boulangerie", "2026-10-06"),),
        (("8.30", "groceries", "2026-10-06", "expense"),),
    ),
    Example(
        "зарплата 2500 і Netflix 13,49",
        (
            transaction("income", "2500", "salary", "Зарплата"),
            transaction("expense", "13.49", "subscriptions", "Netflix"),
        ),
        (
            ("2500.00", "salary", "2026-10-07", "income"),
            ("13.49", "subscriptions", "2026-10-07", "expense"),
        ),
    ),
    Example(
        "déjeuner 16.5",
        (transaction("expense", "16.5", "cafes_restaurants", "Déjeuner"),),
        (("16.50", "cafes_restaurants", "2026-10-07", "expense"),),
    ),
    Example(
        "parking 6 EUR",
        (transaction("expense", "6", "car", "Parking"),),
        (("6.00", "car", "2026-10-07", "expense"),),
    ),
    Example(
        "EDF 84,00",
        (transaction("expense", "84.00", "utilities", "EDF"),),
        (("84.00", "utilities", "2026-10-07", "expense"),),
    ),
    Example(
        "книги дітям 27.30",
        (transaction("expense", "27.30", "children", "Книги"),),
        (("27.30", "children", "2026-10-07", "expense"),),
    ),
    Example(
        "bonus 300 reçu aujourd'hui",
        (transaction("income", "300", "other_income", "Bonus"),),
        (("300.00", "other_income", "2026-10-07", "income"),),
    ),
)


def make_recognizer(
    parsed: AIRecognitionResponse | None,
) -> tuple[OpenAITransactionRecognizer, AsyncMock]:
    parse = AsyncMock(
        return_value=SimpleNamespace(
            id="resp_test",
            model="test-model",
            output_parsed=parsed,
            usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        )
    )
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return OpenAITransactionRecognizer(client, model="test-model"), parse


async def run_recognition(parsed: AIRecognitionResponse | None):
    recognizer, parse = make_recognizer(parsed)
    result = await recognizer.recognize(
        message="test message",
        local_date=date(2026, 10, 7),
        timezone="Europe/Paris",
        base_currency="EUR",
        allowed_categories=CATEGORIES,
    )
    return result, parse


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example.message)
async def test_recognizes_multilingual_examples_with_mocked_ai(example: Example) -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[AITransactionCandidate.model_validate(item) for item in example.transactions],
        needs_clarification=False,
        clarification_question=None,
    )
    recognizer, parse = make_recognizer(parsed)

    result = await recognizer.recognize(
        message=example.message,
        local_date=date(2026, 10, 7),
        timezone="Europe/Paris",
        base_currency="EUR",
        allowed_categories=CATEGORIES,
    )

    actual = tuple(
        (
            f"{item.amount:.2f}",
            item.category_code,
            item.transaction_date.isoformat(),
            item.type.value,
        )
        for item in result.transactions
    )
    assert actual == example.expected
    assert result.needs_clarification is False
    request = parse.await_args.kwargs
    assert request["text_format"] is AIRecognitionResponse
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    request_data = json.loads(request["input"])
    assert request_data["original_message"] == example.message
    assert request_data["current_local_date"] == "2026-10-07"
    assert request_data["timezone"] == "Europe/Paris"
    assert request_data["base_currency"] == "EUR"
    assert {item["code"] for item in request_data["allowed_categories"]} >= {
        "groceries",
        "salary",
    }


async def test_non_transaction_message_is_ignored() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.NOT_A_TRANSACTION,
        transactions=[],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.intent is RecognitionIntent.NOT_A_TRANSACTION
    assert result.transactions == ()
    assert result.needs_clarification is False


@pytest.mark.parametrize("missing_field", ["amount", "type"])
async def test_missing_amount_or_type_uses_required_question(missing_field: str) -> None:
    raw = transaction("expense", "12", "groceries", "Lidl")
    raw[missing_field] = None
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[AITransactionCandidate.model_validate(raw)],
        needs_clarification=True,
        clarification_question="AI wording must not override server validation",
    )

    result, _parse = await run_recognition(parsed)

    assert result.transactions == ()
    assert result.needs_clarification is True
    assert result.clarification_question == MISSING_AMOUNT_OR_TYPE_QUESTION


@pytest.mark.parametrize("amount", ["0", "-2", "not-a-number", "NaN", "999999999999999999999"])
async def test_invalid_amount_is_rejected(amount: str) -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[
            AITransactionCandidate.model_validate(
                transaction("expense", amount, "groceries", "Lidl")
            )
        ],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.needs_clarification is True
    assert result.transactions == ()


async def test_unsupported_currency_is_rejected_without_conversion() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[
            AITransactionCandidate.model_validate(
                transaction("expense", "20", "groceries", "Lidl", currency="USD")
            )
        ],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.needs_clarification is True
    assert "EUR" in (result.clarification_question or "")
    assert result.transactions == ()


async def test_invalid_date_is_rejected() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[
            AITransactionCandidate.model_validate(
                transaction("expense", "20", "groceries", "Lidl", "2026-02-30")
            )
        ],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.needs_clarification is True
    assert "дату" in (result.clarification_question or "")


async def test_unknown_category_falls_back_to_other() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[
            AITransactionCandidate.model_validate(
                transaction("expense", "20", "invented", "Невідоме")
            )
        ],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.transactions[0].category_code == "other"
    assert result.needs_clarification is False


async def test_metadata_is_minimal_and_excludes_message() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[
            AITransactionCandidate.model_validate(
                transaction("expense", "20", "groceries", "Private Lidl details")
            )
        ],
        needs_clarification=False,
        clarification_question=None,
    )

    result, _parse = await run_recognition(parsed)

    assert result.ai_metadata == {
        "response_id": "resp_test",
        "model": "test-model",
        "input_tokens": 100,
        "output_tokens": 30,
    }
    assert "Private Lidl details" not in repr(result.ai_metadata)


@pytest.mark.asyncio
async def test_beneficiary_is_validated_and_defaults_to_author() -> None:
    explicit = transaction("expense", "20", "groceries", "Для Олени")
    explicit["beneficiary_member_id"] = 2
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[AITransactionCandidate.model_validate(explicit)],
        needs_clarification=False,
        clarification_question=None,
    )
    recognizer, _parse = make_recognizer(parsed)
    members = (
        MemberDefinition(id=1, display_name="Андрій", username="andrii"),
        MemberDefinition(id=2, display_name="Олена", username="olena"),
    )

    result = await recognizer.recognize(
        message="продукти для Олени 20",
        local_date=date(2026, 10, 7),
        timezone="Europe/Paris",
        base_currency="EUR",
        allowed_categories=CATEGORIES,
        allowed_members=members,
        default_member_id=1,
    )
    assert result.transactions[0].beneficiary_member_id == 2

    parsed.transactions[0].beneficiary_member_id = None
    defaulted = await recognizer.recognize(
        message="продукти 20",
        local_date=date(2026, 10, 7),
        timezone="Europe/Paris",
        base_currency="EUR",
        allowed_categories=CATEGORIES,
        allowed_members=members,
        default_member_id=1,
    )
    assert defaulted.transactions[0].beneficiary_member_id == 1


async def test_clarification_context_and_member_aliases_are_sent_to_ai() -> None:
    parsed = AIRecognitionResponse(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=[],
        needs_clarification=True,
        clarification_question="Чи Яна — це Yanina?",
        ambiguous_member_name="Яна",
        suggested_member_id=2,
    )
    recognizer, parse = make_recognizer(parsed)

    result = await recognizer.recognize(
        message="Яна навчання 120",
        local_date=date(2026, 10, 10),
        timezone="Europe/Paris",
        base_currency="EUR",
        allowed_categories=CATEGORIES,
        allowed_members=(
            MemberDefinition(
                id=2, display_name="Yanina", username="yanina", aliases=("Яна",)
            ),
        ),
        clarification_question="Чи Яна — це Yanina?",
        clarification_answer="так",
    )

    request_data = json.loads(parse.await_args.kwargs["input"])
    assert request_data["allowed_members"][0]["aliases"] == ["Яна"]
    assert request_data["clarification_context"] == {
        "question": "Чи Яна — це Yanina?",
        "answer": "так",
    }
    assert result.ambiguous_member_name == "Яна"
    assert result.suggested_member_id == 2


def test_ai_schema_rejects_unsupported_transaction_type() -> None:
    with pytest.raises(ValidationError):
        AITransactionCandidate.model_validate(transaction("transfer", "20", "other", "Transfer"))


def test_example_set_has_at_least_thirty_messages() -> None:
    assert len(EXAMPLES) >= 30
    assert any(len(item.transactions) > 1 for item in EXAMPLES)
    assert any("," in str(item.transactions) for item in EXAMPLES)
    assert any("hier" in item.message or "вчора" in item.message for item in EXAMPLES)
