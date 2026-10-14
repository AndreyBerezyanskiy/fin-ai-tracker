from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest

import application.services.finance_exports as export_module
from application.services.finance_exports import (
    MAX_EXPORT_OPERATIONS,
    ExportCategory,
    ExportMonth,
    ExportPeriod,
    ExportPlannedExpense,
    ExportTooLargeError,
    ExportTransaction,
    FinanceExportReport,
    FinanceExportService,
)
from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    TransactionType,
)
from infrastructure.excel_report_renderer import XlsxFinanceReportRenderer


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


def _session_factory() -> _SessionContext:
    return _SessionContext()


def _report(*, description: str = "Кава", month_count: int = 2) -> FinanceExportReport:
    period = ExportPeriod.months("2026-09", "2026-10" if month_count == 2 else "2026-09")
    transaction = ExportTransaction(
        transaction_date=date(2026, 9, 14),
        type=TransactionType.EXPENSE,
        category="Кафе та ресторани",
        category_key="cafes_restaurants",
        description=description,
        member="Андрій",
        amount=Decimal("12.50"),
    )
    return FinanceExportReport(
        household_name="Сімейний бюджет",
        currency="EUR",
        period=period,
        created_at=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        transactions=(transaction,),
        categories=(
            ExportCategory(
                name="Кафе та ресторани",
                type=TransactionType.EXPENSE,
                amount=Decimal("12.50"),
                percentage=Decimal("1"),
            ),
        ),
        months=tuple(
            ExportMonth(
                month=date(2026, 9 + index, 1),
                income=Decimal("100"),
                expense=Decimal("12.50"),
            )
            for index in range(month_count)
        ),
        planned_expenses=(
            ExportPlannedExpense(
                due_date=date(2026, 10, 25),
                category="Страхування",
                description="Авто",
                member="Андрій",
                amount=Decimal("100"),
                recurrence=PlannedExpenseRecurrence.MONTHLY,
                status=PlannedExpenseStatus.SCHEDULED,
            ),
        ),
        income=Decimal("200"),
        expense=Decimal("12.50"),
        planned_total=Decimal("100"),
        previous_income=Decimal("180"),
        previous_expense=Decimal("20"),
    )


def test_export_period_supports_month_year_and_cross_year_range() -> None:
    month = ExportPeriod.months("2026-10", "2026-10")
    year = ExportPeriod.year(2024)
    period = ExportPeriod.months("2025-11", "2026-02")

    assert month.start_date == date(2026, 10, 1)
    assert month.end_date == date(2026, 11, 1)
    assert month.label == "Жовтень 2026"
    assert month.filename_suffix == "2026-10"
    assert year.end_date == date(2025, 1, 1)
    assert year.label == "2024"
    assert period.month_count == 4
    assert period.end_date == date(2026, 3, 1)
    assert period.previous.start_date == date(2025, 7, 1)
    assert period.previous.end_date == date(2025, 11, 1)


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-13", "2026-13"), ("2026-10", "2026-09"), ("bad", "2026-10")],
)
def test_export_period_rejects_invalid_months(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        ExportPeriod.months(start, end)


def test_excel_renderer_creates_expected_sheets_charts_and_safe_strings() -> None:
    rendered = XlsxFinanceReportRenderer().render(_report(description="=2+2"))

    assert rendered.startswith(b"PK")
    with ZipFile(BytesIO(rendered)) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        worksheets = b"".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet")
        )
        shared_strings = archive.read("xl/sharedStrings.xml")
        assert "Підсумок" in workbook
        assert "Операції" in workbook
        assert "Категорії" in workbook
        assert "По місяцях" in workbook
        assert "Заплановані витрати" in workbook
        assert any(name.startswith("xl/charts/chart") for name in archive.namelist())
        assert b"<f>" not in worksheets
        assert "=2+2" in shared_strings.decode()


def test_single_month_excel_omits_monthly_sheet() -> None:
    rendered = XlsxFinanceReportRenderer().render(_report(month_count=1))

    with ZipFile(BytesIO(rendered)) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        assert "По місяцях" not in workbook


@pytest.mark.parametrize("currency", ["EUR", "UAH", "USD"])
def test_excel_renderer_supports_household_currencies(currency: str) -> None:
    rendered = XlsxFinanceReportRenderer().render(replace(_report(), currency=currency))

    assert rendered.startswith(b"PK")


@pytest.mark.asyncio
async def test_finance_export_service_collects_report_and_previous_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    category = SimpleNamespace(code="groceries", name="Продукти")
    beneficiary = SimpleNamespace(display_name="Олена")
    transaction = SimpleNamespace(
        transaction_date=date(2026, 10, 10),
        type=TransactionType.EXPENSE,
        category=category,
        description="Супермаркет",
        beneficiary=beneficiary,
        amount=Decimal("50"),
    )
    plan = SimpleNamespace(
        due_date=date(2026, 10, 25),
        category=SimpleNamespace(name="Страхування"),
        description="Авто",
        member=SimpleNamespace(display_name="Олена"),
        amount=Decimal("100"),
        recurrence=None,
        status=PlannedExpenseStatus.SCHEDULED,
    )
    transaction_repository = SimpleNamespace(
        list_confirmed_for_period=AsyncMock(return_value=[transaction]),
        type_totals=AsyncMock(
            return_value={TransactionType.EXPENSE: Decimal("75")}
        ),
    )
    planned_repository = SimpleNamespace(list_for_period=AsyncMock(return_value=[plan]))
    monkeypatch.setattr(
        export_module, "TransactionRepository", lambda _session: transaction_repository
    )
    monkeypatch.setattr(
        export_module, "PlannedExpenseRepository", lambda _session: planned_repository
    )
    renderer = Mock(return_value=b"xlsx")
    renderer.render.return_value = b"xlsx"
    service = FinanceExportService(_session_factory, renderer)
    period = ExportPeriod.months("2026-10", "2026-10")

    result = await service.create(
        household_id=4,
        household_name="Сім’я",
        currency="EUR",
        period=period,
    )

    assert result is not None
    assert result.filename == "family-budget-2026-10.xlsx"
    assert result.report.expense == Decimal("50")
    assert result.report.planned_total == Decimal("100")
    assert result.report.available_after_plans == Decimal("-150")
    assert result.report.previous_expense == Decimal("75")
    transaction_repository.list_confirmed_for_period.assert_awaited_once_with(
        household_id=4,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
        limit=MAX_EXPORT_OPERATIONS + 1,
    )
    transaction_repository.type_totals.assert_awaited_once_with(
        household_id=4,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 10, 1),
        currency="EUR",
    )


@pytest.mark.asyncio
async def test_finance_export_service_rejects_too_many_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_repository = SimpleNamespace(
        list_confirmed_for_period=AsyncMock(
            return_value=[SimpleNamespace()] * (MAX_EXPORT_OPERATIONS + 1)
        )
    )
    planned_repository = SimpleNamespace()
    monkeypatch.setattr(
        export_module, "TransactionRepository", lambda _session: transaction_repository
    )
    monkeypatch.setattr(
        export_module, "PlannedExpenseRepository", lambda _session: planned_repository
    )
    service = FinanceExportService(_session_factory, Mock())

    with pytest.raises(ExportTooLargeError):
        await service.create(
            household_id=4,
            household_name="Сім’я",
            currency="EUR",
            period=ExportPeriod.months("2026-10", "2026-10"),
        )


@pytest.mark.asyncio
async def test_available_years_starts_at_oldest_household_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(oldest_confirmed_date=AsyncMock(return_value=date(2023, 4, 1)))
    monkeypatch.setattr(export_module, "TransactionRepository", lambda _session: repository)
    service = FinanceExportService(_session_factory, Mock())

    assert await service.available_years(household_id=4, current_year=2026) == (
        2026,
        2025,
        2024,
        2023,
    )
