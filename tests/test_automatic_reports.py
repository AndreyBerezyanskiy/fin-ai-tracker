from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

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
                income=Decimal("1200.00"),
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

    assert text.startswith("☀️ Доброго ранку!\n\nУчора витрачено €42.50.")
    assert "📅 Цього місяця" in text
    assert "💰 Надійшло: €1200.00" in text
    assert "💸 Витрачено: €820.00" in text
    assert "📈 Баланс: +€380.00 🟢" in text
    assert "⚙️ Ліміт витрат: €820.00 з €1000.00 (82.0%)." in text
    assert "⚠️ Наближаються до ліміту\n• Продукти: 82.0%" in text
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
                income=Decimal("500.00"),
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

    assert text.startswith("🌙 Підсумки дня\n\nСьогодні витрачено €15.00.")
    assert "📅 Цього місяця" in text
    assert "💰 Надійшло: €500.00" in text
    assert "💸 Витрачено: €115.00" in text
    assert "📈 Баланс: +€385.00 🟢" in text
    assert "⚙️ Ліміт витрат не встановлено." in text
    assert "💡 Порада: Персональна порада тимчасово недоступна." in text


def test_reports_show_reserved_and_safe_to_spend_amounts() -> None:
    budget = BudgetStatus(
        total_limit=None,
        spent=Decimal("764.00"),
        income=Decimal("1000.00"),
        reserved=Decimal("130.00"),
        days_remaining=10,
        categories=(),
    )

    text = service()._evening_text(
        expense=Decimal("14.00"),
        budget=budget,
        currency="EUR",
        advice="Продовжуйте в тому ж дусі.",
    )

    assert "📌 Заплановано: €130.00" in text
    assert "🛡️ Безпечно витратити: €106.00" in text


@pytest.mark.asyncio
async def test_scheduler_start_restores_only_event_driven_jobs() -> None:
    target = household()
    report_service = SimpleNamespace(
        enabled_households=AsyncMock(return_value=(target,)),
        active_slot=lambda _household, _now: None,
    )
    planned_service = SimpleNamespace(scheduled=AsyncMock(return_value=()))
    transaction_service = SimpleNamespace(pending_action_messages=AsyncMock(return_value=()))
    scheduler = ReportScheduler(
        sender=SimpleNamespace(),
        report_service=report_service,
        planned_expense_service=planned_service,
        transaction_service=transaction_service,
        now=lambda: datetime(2026, 10, 8, 5, 0, tzinfo=UTC),
    )

    await scheduler.start()
    try:
        jobs = scheduler.scheduler.get_jobs()
        assert {job.id for job in jobs} == {"report:7:morning", "report:7:evening"}
        assert all(isinstance(job.trigger, CronTrigger) for job in jobs)
    finally:
        scheduler.shutdown()


def test_household_time_change_immediately_reschedules_report_job() -> None:
    target = household()
    scheduler = ReportScheduler(
        sender=SimpleNamespace(),
        report_service=SimpleNamespace(),
    )
    scheduler.household_changed(target)

    target.evening_report_time = time(21, 30)
    scheduler.household_changed(target)

    job = scheduler.scheduler.get_job("report:7:evening")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert fields["hour"] == "21"
    assert fields["minute"] == "30"


def test_action_cleanup_is_scheduled_once_at_expiration() -> None:
    scheduler = ReportScheduler(
        sender=SimpleNamespace(),
        report_service=SimpleNamespace(),
        now=lambda: datetime(2026, 10, 24, 14, 0, tzinfo=UTC),
    )
    action = SimpleNamespace(
        id=9,
        telegram_chat_id=-100123,
        telegram_message_id=50,
        expires_at=datetime(2026, 10, 24, 14, 15, tzinfo=UTC),
    )

    scheduler.action_message_changed(action)

    job = scheduler.scheduler.get_job("action:-100123:50")
    assert job is not None
    assert isinstance(job.trigger, DateTrigger)


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


@pytest.mark.asyncio
async def test_scheduler_removes_expired_transaction_buttons() -> None:
    now = datetime(2026, 10, 24, 14, 15, tzinfo=UTC)
    action_message = SimpleNamespace(
        id=9,
        household_id=7,
        telegram_chat_id=-100123,
        telegram_message_id=50,
    )
    transaction_service = SimpleNamespace(
        due_action_messages=AsyncMock(return_value=(action_message,)),
        complete_action_message=AsyncMock(),
    )
    sender = SimpleNamespace(edit_message_reply_markup=AsyncMock())
    scheduler = ReportScheduler(
        sender=sender,
        report_service=SimpleNamespace(),
        transaction_service=transaction_service,
    )

    await scheduler.run_due_action_cleanups(now)

    transaction_service.due_action_messages.assert_awaited_once_with(now)
    sender.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=-100123,
        message_id=50,
        reply_markup=None,
    )
    transaction_service.complete_action_message.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_scheduler_retries_transaction_button_cleanup_after_failure() -> None:
    action_message = SimpleNamespace(
        id=9,
        household_id=7,
        telegram_chat_id=-100123,
        telegram_message_id=50,
    )
    transaction_service = SimpleNamespace(
        due_action_messages=AsyncMock(return_value=(action_message,)),
        complete_action_message=AsyncMock(),
    )
    scheduler = ReportScheduler(
        sender=SimpleNamespace(edit_message_reply_markup=AsyncMock(side_effect=TimeoutError)),
        report_service=SimpleNamespace(),
        transaction_service=transaction_service,
    )

    await scheduler.run_due_action_cleanups(datetime(2026, 10, 24, 14, 15, tzinfo=UTC))

    transaction_service.complete_action_message.assert_not_awaited()
