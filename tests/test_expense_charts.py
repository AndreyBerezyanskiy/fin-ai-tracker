from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from application.services import CategoryStatistic
from application.services.expense_charts import (
    ExpenseChartService,
    expense_period_label,
    prepare_expense_chart_data,
)
from domain.enums import TransactionType
from infrastructure.expense_chart_renderer import PillowExpenseChartRenderer


def _stat(
    code: str,
    name: str,
    amount: str,
    transaction_type: TransactionType = TransactionType.EXPENSE,
) -> CategoryStatistic:
    return CategoryStatistic(
        code=code,
        name=name,
        type=transaction_type,
        amount=Decimal(amount),
    )


def test_prepare_expense_chart_groups_smaller_categories_and_ignores_income() -> None:
    statistics = tuple(
        _stat(f"category_{index}", f"Категорія {index}", str(amount))
        for index, amount in enumerate((60, 50, 40, 30, 20, 10, 5), start=1)
    ) + (_stat("salary", "Зарплата", "1000", TransactionType.INCOME),)

    data = prepare_expense_chart_data(
        statistics,
        previous_statistics=(_stat("old", "Старі витрати", "180"),),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
    )

    assert data is not None
    assert data.total == Decimal("215")
    assert len(data.categories) == 6
    assert data.categories[-1].name == "Інше"
    assert data.categories[-1].amount == Decimal("15")
    assert sum(item.percentage for item in data.categories).quantize(Decimal("0.001")) == Decimal(
        "100.000"
    )
    assert data.previous_total == Decimal("180")
    assert data.difference == Decimal("35")
    assert data.period_label == "Жовтень 2026"


def test_prepare_expense_chart_returns_none_without_expenses() -> None:
    assert (
        prepare_expense_chart_data(
            (_stat("salary", "Зарплата", "1000", TransactionType.INCOME),),
            start_date=date(2026, 10, 1),
            end_date=date(2026, 11, 1),
            currency="UAH",
        )
        is None
    )


@pytest.mark.parametrize(
    ("start_date", "end_date", "expected"),
    [
        (date(2026, 10, 1), date(2026, 11, 1), "Жовтень 2026"),
        (date(2026, 10, 14), date(2026, 10, 15), "14.10.2026"),
        (date(2026, 9, 5), date(2026, 10, 15), "05.09.2026 — 14.10.2026"),
    ],
)
def test_expense_period_label(start_date: date, end_date: date, expected: str) -> None:
    assert expense_period_label(start_date, end_date) == expected


@pytest.mark.asyncio
async def test_expense_chart_service_loads_matching_previous_period() -> None:
    current = (_stat("food", "Продукти", "120"),)
    previous = (_stat("food", "Продукти", "150"),)
    category_service = SimpleNamespace(
        statistics=AsyncMock(side_effect=(current, previous)),
    )
    renderer = Mock()
    renderer.render.return_value = b"png"
    service = ExpenseChartService(category_service, renderer)

    result = await service.create(
        household_id=7,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 10, 15),
        currency="USD",
    )

    assert result is not None
    assert result.image == b"png"
    assert result.data.difference == Decimal("-30")
    assert category_service.statistics.await_args_list[0].kwargs == {
        "household_id": 7,
        "start_date": date(2026, 9, 5),
        "end_date": date(2026, 10, 15),
    }
    assert category_service.statistics.await_args_list[1].kwargs == {
        "household_id": 7,
        "start_date": date(2026, 7, 27),
        "end_date": date(2026, 9, 5),
    }
    renderer.render.assert_called_once_with(result.data)


@pytest.mark.parametrize("currency", ["EUR", "UAH", "USD"])
def test_pillow_renderer_creates_square_png_with_ukrainian_text(currency: str) -> None:
    data = prepare_expense_chart_data(
        (
            _stat("groceries", "Продукти", "312.40"),
            _stat("housing", "Житло", "220"),
            _stat("cafes", "Кафе та ресторани", "126.50"),
            _stat("transport", "Транспорт", "84"),
            _stat("custom", "Мобільний зв’язок та інтернет", "117"),
        ),
        previous_statistics=(_stat("old", "Минулі витрати", "933.90"),),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency=currency,
    )
    assert data is not None

    rendered = PillowExpenseChartRenderer().render(data)

    assert rendered.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (1080, 1080)
        assert image.mode == "RGB"
