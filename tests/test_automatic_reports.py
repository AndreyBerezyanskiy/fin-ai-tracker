from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.report_scheduler import ReportScheduler
from application.services import AutomaticReportService, BudgetStatus, CategoryBudgetStatus
from domain.enums import ReportType
from infrastructure.database import Household


def household(*, timezone: str = "Europe/Paris") -> Household:
    return Household(
        id=7,
        telegram_chat_id=-100123,
        name="Сім'я",
        currency="EUR",
        timezone=timezone,
        morning_report_time=time(8),
        evening_report_time=time(20),
        reports_enabled=True,
    )


def service() -> AutomaticReportService:
    return AutomaticReportService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 8, 5, 0, tzinfo=UTC), None),
        (datetime(2026, 10, 8, 7, 0, tzinfo=UTC), ReportType.MORNING),
        (datetime(2026, 10, 8, 18, 0, tzinfo=UTC), ReportType.EVENING),
    ],
)
def test_active_slot_uses_household_timezone(now: datetime, expected: ReportType | None) -> None:
    slot = service().active_slot(household(), now)

    assert (slot[0] if slot else None) is expected
    assert (slot[1] if slot else None) == (date(2026, 10, 8) if expected else None)


@pytest.mark.asyncio
async def test_morning_report_contains_yesterday_budget_and_near_limits() -> None:
    report_service = SimpleNamespace(
        totals=AsyncMock(return_value=SimpleNamespace(expense=Decimal("42.50")))
    )
    budget_service = SimpleNamespace(
        status=AsyncMock(
            return_value=BudgetStatus(
                total_limit=Decimal("1000.00"),
                spent=Decimal("820.00"),
                days_remaining=23,
                categories=(
                    CategoryBudgetStatus(
                        code="groceries",
                        name="Продукти",
                        limit=Decimal("500.00"),
                        spent=Decimal("410.00"),
                    ),
                ),
            )
        )
    )
    automatic = AutomaticReportService(
        SimpleNamespace(), report_service, budget_service, SimpleNamespace()
    )

    text = await automatic.render(
        household=household(),
        report_type=ReportType.MORNING,
        local_date=date(2026, 10, 8),
    )

    assert "Витрати вчора: €42.50" in text
    assert "€820.00 з €1000.00 (82.0%)" in text
    assert "Продукти: 82.0%" in text
    report_service.totals.assert_awaited_once_with(
        household_id=7,
        start_date=date(2026, 10, 7),
        end_date=date(2026, 10, 8),
        currency="EUR",
    )


@pytest.mark.asyncio
async def test_evening_report_survives_temporary_openai_error() -> None:
    report_service = SimpleNamespace(
        totals=AsyncMock(return_value=SimpleNamespace(expense=Decimal("15.00")))
    )
    budget_service = SimpleNamespace(
        status=AsyncMock(
            return_value=BudgetStatus(
                total_limit=None,
                spent=Decimal("115.00"),
                days_remaining=23,
                categories=(),
            )
        )
    )
    advice_service = SimpleNamespace(generate=AsyncMock(side_effect=TimeoutError))
    automatic = AutomaticReportService(
        SimpleNamespace(), report_service, budget_service, advice_service
    )

    text = await automatic.render(
        household=household(),
        report_type=ReportType.EVENING,
        local_date=date(2026, 10, 8),
    )

    assert "Витрати сьогодні: €15.00" in text
    assert "спостереження тимчасово недоступне" in text


@pytest.mark.asyncio
async def test_delivery_key_prevents_duplicate_send() -> None:
    automatic = service()
    automatic._claim = AsyncMock(side_effect=[True, False])  # type: ignore[method-assign]
    automatic._release = AsyncMock()  # type: ignore[method-assign]
    automatic.render = AsyncMock(return_value="report")  # type: ignore[method-assign]
    sender = SimpleNamespace(send_message=AsyncMock())
    target = household()

    first = await automatic.deliver(
        sender=sender,
        household=target,
        report_type=ReportType.MORNING,
        local_date=date(2026, 10, 8),
    )
    second = await automatic.deliver(
        sender=sender,
        household=target,
        report_type=ReportType.MORNING,
        local_date=date(2026, 10, 8),
    )

    assert first is True
    assert second is False
    sender.send_message.assert_awaited_once_with(-100123, "report")
    automatic._release.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_failure_releases_key_for_next_tick() -> None:
    automatic = service()
    automatic._claim = AsyncMock(return_value=True)  # type: ignore[method-assign]
    automatic._release = AsyncMock()  # type: ignore[method-assign]
    automatic.render = AsyncMock(return_value="report")  # type: ignore[method-assign]
    sender = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError))

    with pytest.raises(TimeoutError):
        await automatic.deliver(
            sender=sender,
            household=household(),
            report_type=ReportType.MORNING,
            local_date=date(2026, 10, 8),
        )

    automatic._release.assert_awaited_once_with(7, ReportType.MORNING, date(2026, 10, 8))


@pytest.mark.asyncio
async def test_scheduler_processes_due_households_independently() -> None:
    first, second = household(), household(timezone="Europe/Kyiv")
    second.id = 8
    report_service = SimpleNamespace(
        enabled_households=AsyncMock(return_value=(first, second)),
        active_slot=lambda _household, _now: (ReportType.MORNING, date(2026, 10, 8)),
        deliver=AsyncMock(side_effect=[TimeoutError, True]),
    )
    scheduler = ReportScheduler(
        sender=SimpleNamespace(),
        report_service=report_service,
        now=lambda: datetime(2026, 10, 8, 7, 0, tzinfo=UTC),
    )

    await scheduler.run_due_reports()

    assert report_service.deliver.await_count == 2
