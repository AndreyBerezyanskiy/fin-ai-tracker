from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from application.services import (
    BudgetStatus,
    CategoryBudgetStatus,
    MonthReport,
    PeriodTotals,
    ReportCategoryTotal,
    TelegramContext,
)
from bot.handlers.commands import (
    budget_callback,
    budget_command,
    categories_command,
    category_add_command,
    help_command,
    last_command,
    month_command,
    recognize_plain_message,
    report_command,
    reports_off_command,
    reports_on_command,
    set_budget_command,
    set_category_budget_command,
    settings_command,
    stats_command,
    today_command,
    transaction_callback,
    undo_command,
)
from bot.keyboards import BudgetActionCallback, TransactionActionCallback
from bot.middlewares import (
    AllowedCallbackQueryMiddleware,
    AllowedMessageMiddleware,
    UpdateLoggingMiddleware,
)
from domain.enums import ReportType, TransactionStatus, TransactionType
from domain.models import (
    RecognitionIntent,
    RecognitionResult,
    RecognizedStatisticsQuery,
    RecognizedTransaction,
)
from infrastructure.database import Household, Member, TransactionClarification


def make_message(
    *,
    chat_id: int = -100123,
    chat_type: str = "supergroup",
    is_bot: bool = False,
    text: str = "/start",
) -> tuple[Message, Update]:
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Сімейні фінанси"),
        from_user=User(id=42, is_bot=is_bot, first_name="Олена", username="olena"),
        text=text,
    )
    return message, Update(update_id=99, message=message)


@pytest.mark.asyncio
async def test_allowed_message_bootstraps_context_and_calls_handler() -> None:
    message, update = make_message()
    household = Household(id=1, telegram_chat_id=message.chat.id, name=message.chat.title)
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    bootstrap = AsyncMock(return_value=TelegramContext(household=household, member=member))
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock(return_value="handled")
    data = {"event_update": update}

    result = await middleware(handler, message, data)

    assert result == "handled"
    assert data["household"] is household
    assert data["member"] is member
    handler.assert_awaited_once()
    bootstrap.assert_awaited_once_with(
        update_id=99,
        chat_id=-100123,
        chat_name="Сімейні фінанси",
        user_id=42,
        display_name="Олена",
        username="olena",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_id", "chat_type", "is_bot"),
    [(-100999, "supergroup", False), (-100123, "supergroup", True)],
)
async def test_non_allowed_chats_and_bots_are_ignored(
    chat_id: int, chat_type: str, is_bot: bool
) -> None:
    message, update = make_message(
        chat_id=chat_id, chat_type=chat_type, is_bot=is_bot, text="звичайне повідомлення"
    )
    bootstrap = AsyncMock()
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({-100123}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock()

    result = await middleware(handler, message, {"event_update": update})

    assert result is None
    bootstrap.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_unauthorized_start_is_forwarded_only_to_access_handler() -> None:
    message, update = make_message(chat_id=123456, chat_type="private", text="/start")
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({-100123}),
        bootstrap_service=SimpleNamespace(bootstrap=AsyncMock()),
        access_service=SimpleNamespace(is_allowed=AsyncMock(return_value=False)),
    )
    handler = AsyncMock(return_value="access")
    data = {"event_update": update}

    result = await middleware(handler, message, data)

    assert result == "access"
    assert data["access_bootstrap"] is True
    handler.assert_awaited_once()
    middleware.bootstrap_service.bootstrap.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_grant_allows_private_chat() -> None:
    message, update = make_message(chat_id=123456, chat_type="private", text="/menu")
    household = Household(id=1, telegram_chat_id=message.chat.id, name="Олена")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    bootstrap = AsyncMock(return_value=TelegramContext(household=household, member=member))
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({-100123}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
        access_service=SimpleNamespace(is_allowed=AsyncMock(return_value=True)),
    )
    handler = AsyncMock(return_value="handled")

    result = await middleware(handler, message, {"event_update": update})

    assert result == "handled"
    bootstrap.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_update_is_ignored() -> None:
    message, update = make_message()
    bootstrap = AsyncMock(return_value=None)
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock()

    result = await middleware(handler, message, {"event_update": update})

    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_handler_releases_update_for_retry() -> None:
    message, update = make_message()
    context = TelegramContext(
        household=Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси"),
        member=Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
    )
    bootstrap_service = SimpleNamespace(
        bootstrap=AsyncMock(return_value=context),
        release=AsyncMock(),
    )
    middleware = AllowedMessageMiddleware(
        allowed_chat_ids=frozenset({message.chat.id}),
        bootstrap_service=bootstrap_service,
    )
    handler = AsyncMock(side_effect=RuntimeError("failed"))

    with pytest.raises(RuntimeError, match="failed"):
        await middleware(handler, message, {"event_update": update})

    bootstrap_service.release.assert_awaited_once_with(99)


@pytest.mark.asyncio
async def test_update_logging_excludes_message_text(caplog: pytest.LogCaptureFixture) -> None:
    _message, update = make_message(text="secret payment details")
    middleware = UpdateLoggingMiddleware()

    with caplog.at_level("INFO"):
        await middleware(AsyncMock(), update, {})

    assert "update_id=99" in caplog.text
    assert "secret payment details" not in caplog.text


@pytest.mark.asyncio
async def test_help_and_settings_commands_answer_in_ukrainian() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
        reports_enabled=True,
    )

    await help_command(message)
    await settings_command(message, household)

    assert "/today" in message.answer.await_args_list[0].args[0]
    assert "Europe/Paris" in message.answer.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_manual_report_and_report_switch_commands() -> None:
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    report_message = SimpleNamespace(answer=AsyncMock())
    report_service = SimpleNamespace(render=AsyncMock(return_value="Поточний звіт"))
    settings_service = SimpleNamespace(set_reports_enabled=AsyncMock())

    await report_command(report_message, household, report_service)
    await reports_on_command(report_message, household, settings_service)
    await reports_off_command(report_message, household, settings_service)

    render_call = report_service.render.await_args.kwargs
    assert render_call["report_type"] is ReportType.EVENING
    assert render_call["household"] is household
    settings_service.set_reports_enabled.assert_has_awaits(
        [
            call(1, enabled=True),
            call(1, enabled=False),
        ]
    )


@pytest.mark.asyncio
async def test_today_command_uses_deterministic_report_totals() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    report_service = SimpleNamespace(
        totals=AsyncMock(
            return_value=PeriodTotals(income=Decimal("100.00"), expense=Decimal("25.50"))
        ),
        recent_transactions=AsyncMock(return_value=()),
    )

    await today_command(message, household, report_service)

    report_service.totals.assert_awaited_once()
    answer = message.answer.await_args.args[0]
    assert "100.00 EUR" in answer
    assert "25.50 EUR" in answer
    assert "74.50 EUR" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("totals", "present", "absent"),
    [
        (
            PeriodTotals(income=Decimal("0.00"), expense=Decimal("117.00")),
            "Витрати: 117.00 EUR",
            "Доходи:",
        ),
        (
            PeriodTotals(income=Decimal("2500.00"), expense=Decimal("0.00")),
            "Доходи: 2500.00 EUR",
            "Витрати:",
        ),
    ],
)
async def test_today_command_hides_empty_income_or_expense(
    totals: PeriodTotals,
    present: str,
    absent: str,
) -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    report_service = SimpleNamespace(
        totals=AsyncMock(return_value=totals),
        recent_transactions=AsyncMock(return_value=()),
    )

    await today_command(message, household, report_service)

    answer = message.answer.await_args.args[0]
    assert present in answer
    assert absent not in answer
    assert "Баланс:" in answer


@pytest.mark.asyncio
async def test_month_command_shows_categories_and_previous_month_comparison() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    report = MonthReport(
        totals=PeriodTotals(income=Decimal("2500.00"), expense=Decimal("700.00")),
        expense_categories=(
            ReportCategoryTotal("groceries", "Продукти", Decimal("500.00")),
            ReportCategoryTotal("transport", "Транспорт", Decimal("200.00")),
        ),
        previous_totals=PeriodTotals(income=Decimal("2400.00"), expense=Decimal("800.00")),
    )
    service = SimpleNamespace(month_report=AsyncMock(return_value=report))

    await month_command(message, household, service)

    answer = message.answer.await_args.args[0]
    assert "Доходи: 2500.00 EUR" in answer
    assert "Продукти: €500.00" in answer
    assert "доходи: +€100.00" in answer
    assert "витрати: −€100.00" in answer
    service.month_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_commands_set_and_show_total_and_category_limits() -> None:
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    set_total_message = SimpleNamespace(text="/set_budget 2000", answer=AsyncMock())
    set_category_message = SimpleNamespace(
        text="/set_category_budget groceries 600", answer=AsyncMock()
    )
    report_message = SimpleNamespace(text="/budget", answer=AsyncMock())
    service = SimpleNamespace(
        set_total=AsyncMock(return_value=object()),
        set_category=AsyncMock(return_value=object()),
        status=AsyncMock(
            return_value=BudgetStatus(
                total_limit=Decimal("2000.00"),
                spent=Decimal("500.00"),
                income=Decimal("1100.00"),
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
        ),
    )

    await set_budget_command(set_total_message, household, service)
    await set_category_budget_command(set_category_message, household, service)
    await budget_command(report_message, household, service)

    assert service.set_total.await_args.kwargs["amount"] == Decimal("2000.00")
    assert service.set_category.await_args.kwargs["category_code"] == "groceries"
    answer = report_message.answer.await_args.args[0]
    assert "Надходження: €1100.00" in answer
    assert "Витрати: €500.00" in answer
    assert "Баланс місяця: +€600.00" in answer
    assert "Залишок за лімітом: €1500.00" in answer
    assert "Використано: 25.0%" in answer
    assert "Продукти: витрачено €450.00 з €600.00" in answer
    assert "залишилося €150.00; використано 75.0%" in answer


@pytest.mark.asyncio
async def test_budget_without_limit_offers_explicit_income_based_limit() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    status = BudgetStatus(
        total_limit=None,
        spent=Decimal("250.00"),
        income=Decimal("1100.00"),
        days_remaining=12,
        categories=(),
    )

    await budget_command(
        message,
        household,
        SimpleNamespace(status=AsyncMock(return_value=status)),
    )

    answer = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "Баланс місяця: +€850.00" in answer
    assert "Ліміт витрат не встановлено" in answer
    assert keyboard.inline_keyboard[0][0].text == "Встановити ліміт із доходу"


@pytest.mark.asyncio
async def test_budget_callback_sets_limit_to_confirmed_monthly_income() -> None:
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    before = BudgetStatus(
        total_limit=None,
        spent=Decimal("250.00"),
        income=Decimal("1100.00"),
        days_remaining=12,
        categories=(),
    )
    after = BudgetStatus(
        total_limit=Decimal("1100.00"),
        spent=Decimal("250.00"),
        income=Decimal("1100.00"),
        days_remaining=12,
        categories=(),
    )
    service = SimpleNamespace(
        status=AsyncMock(side_effect=[before, after]),
        set_total=AsyncMock(),
    )
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await budget_callback(
        callback,
        BudgetActionCallback(action="set_from_income"),
        household,
        service,
    )

    assert service.set_total.await_args.kwargs["amount"] == Decimal("1100.00")
    assert "Ліміт витрат: €1100.00" in callback.message.edit_text.await_args.args[0]
    callback.answer.assert_awaited_once_with("Ліміт витрат встановлено.")


@pytest.mark.asyncio
async def test_plain_message_returns_recognized_transactions_in_ukrainian() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="кава 4,50",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(
            RecognizedTransaction(
                type=TransactionType.EXPENSE,
                amount=Decimal("4.50"),
                currency="EUR",
                category_code="cafes_restaurants",
                description="Кава",
                date="2026-10-07",
            ),
        ),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={"response_id": "resp_test"},
    )
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    service = SimpleNamespace(recognize=AsyncMock(return_value=result))
    pending_transaction = SimpleNamespace(
        type=TransactionType.EXPENSE,
        amount=Decimal("4.50"),
        currency="EUR",
        description="Кава",
        transaction_date=datetime(2026, 10, 7).date(),
        category=SimpleNamespace(name="Кафе та ресторани"),
    )
    transaction_service = SimpleNamespace(
        create_pending=AsyncMock(return_value=(pending_transaction,))
    )

    await recognize_plain_message(message, household, member, service, transaction_service)

    service.recognize.assert_awaited_once_with(
        message="кава 4,50", household=household, member=member
    )
    transaction_service.create_pending.assert_awaited_once_with(
        household=household,
        member=member,
        telegram_message_id=10,
        original_text="кава 4,50",
        recognition=result,
    )
    answer = message.answer.await_args.args[0]
    assert "Знайдено 1 операцію" in answer
    assert "−€4.50" in answer
    assert "🏷️ Категорія: Кафе та ресторани" in answer
    assert "Дата: 07.10.2026" in answer
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    message.reply.assert_awaited_once_with("⏳ Обробляю…")
    processing_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_message_returns_filtered_statistics_for_requested_period() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="Дай статистику доходів за останні 2 місяці",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    result = RecognitionResult(
        intent=RecognitionIntent.GET_STATISTICS,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        statistics_query=RecognizedStatisticsQuery(
            transaction_type=TransactionType.INCOME,
            start_date=date(2026, 8, 12),
            end_date=date(2026, 10, 12),
        ),
    )
    category_service = SimpleNamespace(
        statistics=AsyncMock(
            return_value=(
                SimpleNamespace(
                    name="Зарплата",
                    type=TransactionType.INCOME,
                    amount=Decimal("2200.00"),
                ),
                SimpleNamespace(
                    name="Продукти",
                    type=TransactionType.EXPENSE,
                    amount=Decimal("400.00"),
                ),
            )
        )
    )
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(
        message,
        household,
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        transaction_service,
        category_service=category_service,
    )

    category_service.statistics.assert_awaited_once_with(
        household_id=1,
        start_date=date(2026, 8, 12),
        end_date=date(2026, 10, 13),
    )
    answer = message.answer.await_args.args[0]
    assert "Статистика доходів" in answer
    assert "12.08.2026 — 12.10.2026" in answer
    assert "Зарплата: €2200.00" in answer
    assert "Продукти" not in answer
    transaction_service.create_pending.assert_not_awaited()
    processing_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_message_statistics_reports_empty_period() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="Статистика витрат за останні 40 днів",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    result = RecognitionResult(
        intent=RecognitionIntent.GET_STATISTICS,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        statistics_query=RecognizedStatisticsQuery(
            transaction_type=TransactionType.EXPENSE,
            start_date=date(2026, 9, 3),
            end_date=date(2026, 10, 12),
        ),
    )
    category_service = SimpleNamespace(statistics=AsyncMock(return_value=()))

    await recognize_plain_message(
        message,
        Household(id=1, telegram_chat_id=-100123, name="Сім’я", currency="EUR"),
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        SimpleNamespace(create_pending=AsyncMock()),
        category_service=category_service,
    )

    answer = message.answer.await_args.args[0]
    assert "Статистика витрат" in answer
    assert "03.09.2026 — 12.10.2026" in answer
    assert "Підтверджених операцій за цей період немає" in answer


@pytest.mark.asyncio
async def test_plain_message_asks_clarification() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="купив продукти",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
    )
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(),
        needs_clarification=True,
        clarification_question="Не вдалося визначити суму. Скільки коштувала покупка?",
        ai_metadata={},
    )
    service = SimpleNamespace(recognize=AsyncMock(return_value=result))
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(message, household, member, service, transaction_service)

    message.answer.assert_awaited_once_with(result.clarification_question)
    transaction_service.create_pending.assert_not_awaited()
    processing_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_message_persists_member_clarification_with_buttons() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="Яна навчання французької 120",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім'я", currency="EUR")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Андрій")
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(),
        needs_clarification=True,
        clarification_question="Чи «Яна» — це учасниця Yanina?",
        ai_metadata={},
        ambiguous_member_name="Яна",
        suggested_member_id=3,
    )
    stored = SimpleNamespace(
        id=7,
        question=result.clarification_question,
        suggested_member_id=3,
        missing_category_type=None,
    )
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=None), begin=AsyncMock(return_value=stored)
    )

    await recognize_plain_message(
        message,
        household,
        member,
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        SimpleNamespace(create_pending=AsyncMock()),
        clarification_service=clarification_service,
    )

    clarification_service.begin.assert_awaited_once_with(
        household_id=1,
        member_id=2,
        original_message_id=10,
        original_text=message.text,
        question=result.clarification_question,
        ambiguous_member_name="Яна",
        suggested_member_id=3,
        missing_category_type=None,
    )
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "Так"


@pytest.mark.asyncio
async def test_unknown_category_offers_existing_categories_and_creation() -> None:
    processing_message = SimpleNamespace(message_id=11, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=10,
        text="незрозуміла витрата 20",
        answer=AsyncMock(),
        reply=AsyncMock(return_value=processing_message),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім'я", currency="EUR")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Андрій")
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(),
        needs_clarification=True,
        clarification_question="Оберіть категорію для «Незрозуміла витрата».",
        ai_metadata={},
        missing_category_type=TransactionType.EXPENSE,
    )
    stored = SimpleNamespace(
        id=7,
        question=result.clarification_question,
        suggested_member_id=None,
        missing_category_type="expense",
    )
    category = SimpleNamespace(
        id=5,
        name="Освіта",
        code="education",
        type=TransactionType.EXPENSE,
        is_active=True,
    )
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=None), begin=AsyncMock(return_value=stored)
    )

    await recognize_plain_message(
        message,
        household,
        member,
        SimpleNamespace(recognize=AsyncMock(return_value=result)),
        SimpleNamespace(create_pending=AsyncMock()),
        clarification_service=clarification_service,
        category_service=SimpleNamespace(list=AsyncMock(return_value=(category,))),
    )

    keyboard = message.answer.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["Освіта", "➕ Створити нову категорію", "Скасувати"]
    clarification_service.begin.assert_awaited_once_with(
        household_id=1,
        member_id=2,
        original_message_id=10,
        original_text=message.text,
        question=result.clarification_question,
        ambiguous_member_name=None,
        suggested_member_id=None,
        missing_category_type=TransactionType.EXPENSE,
    )


@pytest.mark.asyncio
async def test_followup_answer_resumes_original_message_and_remembers_alias() -> None:
    indicator = SimpleNamespace(message_id=12, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=11,
        text="так",
        answer=AsyncMock(side_effect=[indicator, None]),
        reply=AsyncMock(),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім'я", currency="EUR")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Андрій")
    clarification = TransactionClarification(
        id=7,
        household_id=1,
        member_id=2,
        original_message_id=10,
        original_text="Яна навчання французької 120",
        question="Чи «Яна» — це учасниця Yanina?",
        attempts=0,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ambiguous_member_name="Яна",
        suggested_member_id=3,
    )
    recognized = RecognizedTransaction(
        type=TransactionType.EXPENSE,
        amount=Decimal("120"),
        currency="EUR",
        category_code="education",
        description="Навчання французької",
        beneficiary_member_id=3,
        date="2026-10-10",
    )
    result = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(recognized,),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
    )
    recognition_service = SimpleNamespace(recognize=AsyncMock(return_value=result))
    transaction_service = SimpleNamespace(create_pending=AsyncMock(return_value=(recognized,)))
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=clarification),
        cancel=AsyncMock(return_value=True),
        remember_alias=AsyncMock(),
    )

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        transaction_service,
        clarification_service=clarification_service,
    )

    recognition_service.recognize.assert_awaited_once_with(
        message=clarification.original_text,
        household=household,
        member=member,
        clarification_question=clarification.question,
        clarification_answer="так",
    )
    transaction_service.create_pending.assert_awaited_once_with(
        household=household,
        member=member,
        telegram_message_id=10,
        original_text=clarification.original_text,
        recognition=result,
    )
    clarification_service.remember_alias.assert_awaited_once_with(
        household_id=1, member_id=3, alias="Яна"
    )
    clarification_service.cancel.assert_awaited_once_with(7)
    indicator.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_statistics_period_clarification_keeps_original_context() -> None:
    indicator = SimpleNamespace(message_id=12, delete=AsyncMock())
    message = SimpleNamespace(
        message_id=11,
        text="за останні 40 днів",
        answer=AsyncMock(side_effect=[indicator, None]),
        reply=AsyncMock(),
    )
    household = Household(id=1, telegram_chat_id=-100123, name="Сім’я", currency="EUR")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    clarification = TransactionClarification(
        id=7,
        household_id=1,
        member_id=2,
        original_message_id=10,
        original_text="Покажи статистику витрат",
        question="За який період показати статистику?",
        attempts=0,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    result = RecognitionResult(
        intent=RecognitionIntent.GET_STATISTICS,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        statistics_query=RecognizedStatisticsQuery(
            transaction_type=TransactionType.EXPENSE,
            start_date=date(2026, 9, 3),
            end_date=date(2026, 10, 12),
        ),
    )
    recognition_service = SimpleNamespace(recognize=AsyncMock(return_value=result))
    clarification_service = SimpleNamespace(
        get=AsyncMock(return_value=clarification),
        cancel=AsyncMock(return_value=True),
    )
    category_service = SimpleNamespace(
        statistics=AsyncMock(
            return_value=(
                SimpleNamespace(
                    name="Продукти",
                    type=TransactionType.EXPENSE,
                    amount=Decimal("85.40"),
                ),
            )
        )
    )
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        transaction_service,
        clarification_service=clarification_service,
        category_service=category_service,
    )

    recognition_service.recognize.assert_awaited_once_with(
        message="Покажи статистику витрат",
        household=household,
        member=member,
        clarification_question="За який період показати статистику?",
        clarification_answer="за останні 40 днів",
    )
    clarification_service.cancel.assert_awaited_once_with(7)
    category_service.statistics.assert_awaited_once_with(
        household_id=1,
        start_date=date(2026, 9, 3),
        end_date=date(2026, 10, 13),
    )
    assert "Продукти: €85.40" in message.answer.await_args_list[-1].args[0]
    transaction_service.create_pending.assert_not_awaited()
    indicator.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_transaction_callback_confirms_pending_batch() -> None:
    bot = SimpleNamespace(set_message_reaction=AsyncMock())
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        bot=bot,
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
    )
    transaction = SimpleNamespace(
        status=TransactionStatus.CONFIRMED,
        type=TransactionType.EXPENSE,
        amount=Decimal("40.00"),
        currency="EUR",
        description="Інтернет",
        transaction_date=datetime(2026, 10, 7).date(),
    )
    service = SimpleNamespace(
        transition_pending=AsyncMock(
            return_value=SimpleNamespace(transactions=(transaction,), changed=True)
        ),
        confirmed_balance=AsyncMock(return_value=Decimal("62.00")),
    )

    await transaction_callback(
        callback,
        TransactionActionCallback(action="confirm", message_id=10),
        household,
        service,
    )

    service.transition_pending.assert_awaited_once_with(
        household_id=1,
        telegram_message_id=10,
        status=TransactionStatus.CONFIRMED,
    )
    edited_text = callback.message.edit_text.await_args.args[0]
    assert "✅ Підтверджено 1 операцію" in edited_text
    assert "Залишок: €62.00" in edited_text
    callback.message.edit_text.assert_awaited_once()
    service.confirmed_balance.assert_awaited_once_with(household_id=1, currency="EUR")
    reaction_call = bot.set_message_reaction.await_args
    assert reaction_call.kwargs["chat_id"] == -100123
    assert reaction_call.kwargs["message_id"] == 10
    assert reaction_call.kwargs["reaction"][0].emoji == "👍"
    assert reaction_call.kwargs["is_big"] is True
    callback.answer.assert_awaited_once_with("✅ Підтверджено")


@pytest.mark.asyncio
async def test_transaction_callback_reports_already_processed() -> None:
    callback = SimpleNamespace(message=SimpleNamespace(), answer=AsyncMock())
    household = Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси")
    service = SimpleNamespace(
        transition_pending=AsyncMock(
            return_value=SimpleNamespace(transactions=(object(),), changed=False)
        )
    )

    await transaction_callback(
        callback,
        TransactionActionCallback(action="cancel", message_id=10),
        household,
        service,
    )

    callback.answer.assert_awaited_once_with("Ці операції вже оброблено.", show_alert=True)


@pytest.mark.asyncio
async def test_transaction_callback_changes_message_when_cancelled() -> None:
    bot = SimpleNamespace(set_message_reaction=AsyncMock())
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        bot=bot,
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
    )
    transaction = SimpleNamespace(
        status=TransactionStatus.CANCELLED,
        type=TransactionType.EXPENSE,
        amount=Decimal("40.00"),
        currency="EUR",
        description="Інтернет",
        transaction_date=datetime(2026, 10, 7).date(),
    )
    service = SimpleNamespace(
        transition_pending=AsyncMock(
            return_value=SimpleNamespace(transactions=(transaction,), changed=True)
        ),
        confirmed_balance=AsyncMock(),
    )

    await transaction_callback(
        callback,
        TransactionActionCallback(action="cancel", message_id=10),
        household,
        service,
    )

    edited_text = callback.message.edit_text.await_args.args[0]
    assert "❌ Скасовано 1 операцію" in edited_text
    assert "Дата: 07.10.2026" in edited_text
    assert "Залишок:" not in edited_text
    service.confirmed_balance.assert_not_awaited()
    bot.set_message_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_last_and_undo_commands_use_transaction_service() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    household = Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси")
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    transaction = SimpleNamespace(
        type=TransactionType.EXPENSE,
        amount=Decimal("18.00"),
        currency="EUR",
        description="Аптека",
        transaction_date=datetime(2026, 10, 7).date(),
        member=member,
        beneficiary=Member(
            id=3,
            household_id=1,
            telegram_user_id=43,
            display_name="Олена",
        ),
    )
    service = SimpleNamespace(
        list_recent=AsyncMock(return_value=(transaction,)),
        undo_last=AsyncMock(return_value=transaction),
    )

    await last_command(message, household, service)
    await undo_command(message, household, member, service)

    assert "Аптека" in message.answer.await_args_list[0].args[0]
    assert "для Олена" in message.answer.await_args_list[0].args[0]
    assert "Скасовано" in message.answer.await_args_list[1].args[0]
    service.undo_last.assert_awaited_once_with(household_id=1, member_id=2)


@pytest.mark.asyncio
async def test_category_commands_list_and_add_categories() -> None:
    household = Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси")
    listed_message = SimpleNamespace(text="/categories", answer=AsyncMock())
    add_message = SimpleNamespace(text="/category_add expense Домашні тварини", answer=AsyncMock())
    expense = SimpleNamespace(
        code="groceries",
        name="Продукти",
        type=TransactionType.EXPENSE,
        is_active=True,
    )
    income = SimpleNamespace(
        code="salary",
        name="Зарплата",
        type=TransactionType.INCOME,
        is_active=False,
    )
    created = SimpleNamespace(code="custom_12345678", name="Домашні тварини")
    service = SimpleNamespace(
        list=AsyncMock(return_value=(expense, income)),
        add=AsyncMock(return_value=created),
    )

    await categories_command(listed_message, household, service)
    await category_add_command(add_message, household, service)

    listed = listed_message.answer.await_args.args[0]
    assert "✅ groceries — Продукти" in listed
    assert "⏸ salary — Зарплата" in listed
    service.add.assert_awaited_once_with(
        household_id=1,
        transaction_type=TransactionType.EXPENSE,
        name="Домашні тварини",
    )
    assert "custom_12345678" in add_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_stats_command_groups_income_and_expenses() -> None:
    message = SimpleNamespace(text="/stats 2026-10", answer=AsyncMock())
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    service = SimpleNamespace(
        statistics=AsyncMock(
            return_value=(
                SimpleNamespace(
                    name="Продукти",
                    type=TransactionType.EXPENSE,
                    amount=Decimal("117.00"),
                ),
                SimpleNamespace(
                    name="Зарплата",
                    type=TransactionType.INCOME,
                    amount=Decimal("2500.00"),
                ),
            )
        )
    )

    await stats_command(message, household, service)

    answer = message.answer.await_args.args[0]
    assert "Статистика за 10.2026" in answer
    assert "Продукти: €117.00" in answer
    assert "Зарплата: €2500.00" in answer
    service.statistics.assert_awaited_once_with(
        household_id=1,
        start_date=datetime(2026, 10, 1).date(),
        end_date=datetime(2026, 11, 1).date(),
    )


@pytest.mark.asyncio
async def test_allowed_callback_bootstraps_household_member() -> None:
    message, _update = make_message(text="confirmation")
    callback = CallbackQuery(
        id="callback-1",
        from_user=message.from_user,
        chat_instance="chat-instance",
        message=message,
        data="transaction:confirm:10",
    )
    update = Update(update_id=100, callback_query=callback)
    context = TelegramContext(
        household=Household(id=1, telegram_chat_id=-100123, name="Сімейні фінанси"),
        member=Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена"),
    )
    bootstrap = AsyncMock(return_value=context)
    middleware = AllowedCallbackQueryMiddleware(
        allowed_chat_ids=frozenset({-100123}),
        bootstrap_service=SimpleNamespace(bootstrap=bootstrap),
    )
    handler = AsyncMock(return_value="handled")
    data = {"event_update": update}

    result = await middleware(handler, callback, data)

    assert result == "handled"
    assert data["household"] is context.household
    assert data["member"] is context.member
    bootstrap.assert_awaited_once_with(
        update_id=100,
        chat_id=-100123,
        chat_name="Сімейні фінанси",
        user_id=42,
        display_name="Олена",
        username="olena",
    )
