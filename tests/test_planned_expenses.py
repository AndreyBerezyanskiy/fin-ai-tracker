from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.report_scheduler import ReportScheduler
from application.services import BudgetStatus, PlannedExpenseService, ReminderCandidate
from bot.handlers.commands import (
    category_choice_callback,
    planned_expense_callback,
    recognize_plain_message,
)
from bot.keyboards import CategoryChoiceCallback, PlannedExpenseActionCallback
from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    ReminderKind,
    TransactionType,
)
from domain.models import RecognitionIntent, RecognitionResult, RecognizedPlannedExpense
from infrastructure.database import Household, Member, TransactionClarification
from infrastructure.repositories import PlannedExpenseRepository


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def plan(*, due_date: date = date(2026, 10, 28)) -> SimpleNamespace:
    household = SimpleNamespace(
        id=7,
        telegram_chat_id=-100123,
        timezone="Europe/Paris",
        morning_report_time=time(8),
    )
    return SimpleNamespace(
        id=42,
        household_id=7,
        household=household,
        member_id=2,
        category=SimpleNamespace(name="Страхування"),
        amount=Decimal("100.00"),
        currency="EUR",
        description="Страховка",
        due_date=due_date,
        recurrence=None,
        status=PlannedExpenseStatus.SCHEDULED,
    )


def test_safe_to_spend_uses_limit_or_cashflow_and_reservations() -> None:
    with_limit = BudgetStatus(
        total_limit=Decimal("500"),
        spent=Decimal("264"),
        income=Decimal("1000"),
        reserved=Decimal("130"),
        days_remaining=10,
        categories=(),
    )
    without_limit = BudgetStatus(
        total_limit=None,
        spent=Decimal("764"),
        income=Decimal("1000"),
        reserved=Decimal("130"),
        days_remaining=10,
        categories=(),
    )

    assert with_limit.safe_to_spend == Decimal("106")
    assert without_limit.safe_to_spend == Decimal("106")


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (date(2026, 1, 31), date(2026, 2, 28)),
        (date(2028, 1, 31), date(2028, 2, 29)),
        (date(2026, 12, 15), date(2027, 1, 15)),
    ],
)
def test_monthly_recurrence_clamps_to_valid_day(current: date, expected: date) -> None:
    assert PlannedExpenseService._next_month(current) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("due_date", "expected"),
    [
        (date(2026, 10, 13), ReminderKind.DAY_BEFORE),
        (date(2026, 10, 12), ReminderKind.DUE),
        (date(2026, 10, 11), ReminderKind.OVERDUE),
        (date(2026, 10, 14), None),
    ],
)
async def test_due_reminders_respect_local_date(
    monkeypatch: pytest.MonkeyPatch, due_date: date, expected: ReminderKind | None
) -> None:
    monkeypatch.setattr(
        PlannedExpenseRepository,
        "list_scheduled",
        AsyncMock(return_value=[plan(due_date=due_date)]),
    )
    service = PlannedExpenseService(lambda: FakeSession())  # type: ignore[arg-type]

    reminders = await service.due_reminders(datetime(2026, 10, 12, 7, tzinfo=UTC))

    assert (reminders[0].kind if reminders else None) is expected


@pytest.mark.asyncio
async def test_scheduler_sends_reminder_once_with_actions() -> None:
    target = plan(due_date=date(2026, 10, 12))
    planned_service = SimpleNamespace(
        due_reminders=AsyncMock(return_value=(ReminderCandidate(target, ReminderKind.DUE),)),
        claim_reminder=AsyncMock(side_effect=[True, False]),
        release_reminder=AsyncMock(),
    )
    sender = SimpleNamespace(send_message=AsyncMock())
    scheduler = ReportScheduler(
        sender=sender,
        report_service=SimpleNamespace(),
        planned_expense_service=planned_service,
    )
    now = datetime(2026, 10, 12, 8, tzinfo=UTC)

    await scheduler.run_due_reminders(now)
    await scheduler.run_due_reminders(now)

    sender.send_message.assert_awaited_once()
    text = sender.send_message.await_args.args[1]
    assert "Запланована оплата сьогодні" in text
    assert "Страховка" in text
    assert sender.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    planned_service.release_reminder.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_releases_failed_reminder_for_retry() -> None:
    target = plan(due_date=date(2026, 10, 12))
    planned_service = SimpleNamespace(
        due_reminders=AsyncMock(return_value=(ReminderCandidate(target, ReminderKind.DUE),)),
        claim_reminder=AsyncMock(return_value=True),
        release_reminder=AsyncMock(),
    )
    scheduler = ReportScheduler(
        sender=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError)),
        report_service=SimpleNamespace(),
        planned_expense_service=planned_service,
    )

    await scheduler.run_due_reminders(datetime(2026, 10, 12, 8, tzinfo=UTC))

    planned_service.release_reminder.assert_awaited_once_with(42, ReminderKind.DUE)


@pytest.mark.asyncio
async def test_plain_text_plan_is_shown_for_confirmation() -> None:
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сім’я",
        currency="EUR",
        timezone="Europe/Paris",
    )
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    recognized = RecognizedPlannedExpense(
        amount=Decimal("100"),
        currency="EUR",
        category_code="taxes_documents",
        description="Страховка",
        due_date=date(2026, 10, 28),
        recurrence=PlannedExpenseRecurrence.MONTHLY,
    )
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_PLANNED_EXPENSES,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        planned_expenses=(recognized,),
    )
    processing = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="Щомісяця 28 числа страховка 100",
        reply=AsyncMock(return_value=processing),
        answer=AsyncMock(),
    )
    created = plan()
    recognition_service = SimpleNamespace(recognize=AsyncMock(return_value=result))
    planned_service = SimpleNamespace(create_pending=AsyncMock(return_value=(created,)))

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        SimpleNamespace(),
        planned_expense_service=planned_service,
    )

    planned_service.create_pending.assert_awaited_once()
    answer = message.answer.await_args.args[0]
    assert "📌 Запланована витрата" in answer
    assert "Страховка" in answer
    assert "один раз чи щомісяця?" in answer
    keyboard = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [button.text for button in keyboard[0]] == ["1️⃣ Один раз", "🔁 Щомісяця"]


@pytest.mark.asyncio
async def test_missing_plan_date_is_saved_as_persistent_clarification() -> None:
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_PLANNED_EXPENSES,
        transactions=(),
        needs_clarification=True,
        clarification_question="На яку дату запланувати оплату?",
        ai_metadata={},
    )
    stored = SimpleNamespace(
        id=8,
        question=result.clarification_question,
        suggested_member_id=None,
        missing_category_type=None,
    )
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=None), begin=AsyncMock(return_value=stored)
    )
    processing = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="Заплануй страховку 100",
        reply=AsyncMock(return_value=processing),
        answer=AsyncMock(),
    )

    await recognize_plain_message(
        message,
        household,
        member,
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        SimpleNamespace(),
        clarification_service=clarification_service,
        planned_expense_service=SimpleNamespace(),
    )

    assert clarification_service.begin.await_args.kwargs["kind"] == "planned_create"
    assert "дату" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_plan_confirmation_schedules_expense() -> None:
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    target = plan()
    pending = SimpleNamespace(**target.__dict__)
    pending.status = PlannedExpenseStatus.PENDING
    pending.recurrence = PlannedExpenseRecurrence.MONTHLY
    service = SimpleNamespace(
        get=AsyncMock(return_value=pending),
        schedule=AsyncMock(return_value=target),
    )
    clarification_service = SimpleNamespace(cancel_plan_context=AsyncMock(return_value=False))
    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

    await planned_expense_callback(
        callback,
        PlannedExpenseActionCallback(action="confirm_once", planned_expense_id=42),
        household,
        member,
        service,
        clarification_service,
    )

    service.schedule.assert_awaited_once_with(
        household_id=1,
        planned_expense_id=42,
        recurrence=None,
    )
    assert "Одноразову витрату заплановано" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_monthly_plan_requires_explicit_monthly_confirmation() -> None:
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    target = plan()
    target.recurrence = PlannedExpenseRecurrence.MONTHLY
    pending = SimpleNamespace(**target.__dict__)
    pending.status = PlannedExpenseStatus.PENDING
    service = SimpleNamespace(
        get=AsyncMock(return_value=pending),
        schedule=AsyncMock(return_value=target),
    )
    clarification_service = SimpleNamespace(cancel_plan_context=AsyncMock(return_value=False))
    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

    await planned_expense_callback(
        callback,
        PlannedExpenseActionCallback(action="confirm_monthly", planned_expense_id=42),
        household,
        member,
        service,
        clarification_service,
    )

    service.schedule.assert_awaited_once_with(
        household_id=1,
        planned_expense_id=42,
        recurrence=PlannedExpenseRecurrence.MONTHLY,
    )
    assert "Щомісячну витрату заплановано" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_plan_edit_amount_starts_correlated_clarification() -> None:
    target = plan()
    stored = SimpleNamespace(id=9, question="💶 Яку нову суму встановити?")
    clarification_service = SimpleNamespace(
        begin_plan_edit=AsyncMock(return_value=stored)
    )
    callback = SimpleNamespace(
        message=SimpleNamespace(message_id=77, edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await planned_expense_callback(
        callback,
        PlannedExpenseActionCallback(action="edit_amount", planned_expense_id=42),
        Household(id=1, telegram_chat_id=-100123, name="Сім’я"),
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
        SimpleNamespace(get=AsyncMock(return_value=target)),
        clarification_service,
    )

    clarification_service.begin_plan_edit.assert_awaited_once()
    call = clarification_service.begin_plan_edit.await_args.kwargs
    assert call["planned_expense_id"] == 42
    assert call["field"] == "amount"
    assert "100.00 EUR" in call["original_text"]
    assert callback.message.edit_text.await_args.args[0] == stored.question


@pytest.mark.asyncio
async def test_plan_category_can_be_changed_without_losing_other_fields() -> None:
    target = plan()
    target.status = PlannedExpenseStatus.PENDING
    communications = SimpleNamespace(
        id=15,
        code="communications",
        name="Мобільний зв’язок та інтернет",
        type=TransactionType.EXPENSE,
        is_active=True,
    )
    updated = SimpleNamespace(**target.__dict__)
    updated.category = communications
    clarification = SimpleNamespace(
        id=9,
        household_id=1,
        member_id=2,
        missing_category_type="expense",
        expires_at=datetime.now(UTC).replace(year=2027),
        kind="planned_edit_category",
        planned_expense_id=42,
    )
    clarification_service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=clarification),
        cancel=AsyncMock(return_value=True),
    )
    planned_service = SimpleNamespace(update_details=AsyncMock(return_value=updated))
    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

    await category_choice_callback(
        callback,
        CategoryChoiceCallback(action="select", clarification_id=9, category_id=15),
        Household(id=1, telegram_chat_id=-100123, name="Сім’я"),
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
        SimpleNamespace(),
        SimpleNamespace(),
        clarification_service,
        SimpleNamespace(list=AsyncMock(return_value=(communications,))),
        planned_expense_service=planned_service,
    )

    planned_service.update_details.assert_awaited_once_with(
        household_id=1,
        planned_expense_id=42,
        category_id=15,
    )
    assert updated.amount == Decimal("100.00")
    assert updated.due_date == date(2026, 10, 28)
    assert "Мобільний зв’язок та інтернет" in callback.message.edit_text.await_args.args[0]
    clarification_service.cancel.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_plan_amount_edit_updates_only_amount_from_followup() -> None:
    target = plan()
    target.amount = Decimal("11.00")
    clarification = TransactionClarification(
        id=9,
        household_id=1,
        member_id=2,
        original_message_id=77,
        original_text="Заплануй мобільний зв’язок, 100 EUR, на 2026-10-28.",
        question="💶 Яку нову суму встановити?",
        attempts=0,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        kind="planned_edit_amount",
        planned_expense_id=42,
    )
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_PLANNED_EXPENSES,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        planned_expenses=(
            RecognizedPlannedExpense(
                amount=Decimal("11.00"),
                currency="EUR",
                category_code="communications",
                description="Мобільний зв’язок",
                due_date=date(2026, 10, 28),
            ),
        ),
    )
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=clarification),
        cancel=AsyncMock(return_value=True),
    )
    planned_service = SimpleNamespace(update_details=AsyncMock(return_value=target))
    message = SimpleNamespace(message_id=78, text="11 євро", answer=AsyncMock())
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(
        message,
        Household(id=1, telegram_chat_id=-100123, name="Сім’я", currency="EUR"),
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        transaction_service,
        clarification_service=clarification_service,
        planned_expense_service=planned_service,
    )

    planned_service.update_details.assert_awaited_once_with(
        household_id=1,
        planned_expense_id=42,
        amount=Decimal("11.00"),
    )
    assert "€11.00" in message.answer.await_args.args[0]
    clarification_service.cancel.assert_awaited_once_with(9)
    transaction_service.create_pending.assert_not_awaited()
