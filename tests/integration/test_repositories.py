from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services import (
    AccessService,
    AIUsageData,
    AIUsageService,
    BudgetService,
    CategoryService,
    ClarificationService,
    HouseholdSettingsService,
    PlannedExpenseService,
    ReportService,
    TransactionService,
    VoiceFeatureService,
)
from domain.category_templates import CATEGORY_TEMPLATES
from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    ReminderKind,
    ReportType,
    TransactionStatus,
    TransactionType,
)
from domain.models import (
    RecognitionIntent,
    RecognitionResult,
    RecognizedPlannedExpense,
    RecognizedTransaction,
)
from infrastructure.database import (
    AccessInvitation,
    Category,
    FeatureFlag,
    Household,
    MemberAlias,
    ProcessedTelegramUpdate,
    Transaction,
    TransactionClarification,
    create_engine,
    create_session_factory,
    session_scope,
)
from infrastructure.repositories import (
    BudgetRepository,
    CategoryRepository,
    HouseholdRepository,
    MemberRepository,
    PlannedExpenseRepository,
    ProcessedTelegramUpdateRepository,
    ReportDeliveryRepository,
    TransactionRepository,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="set TEST_DATABASE_URL to a disposable PostgreSQL database",
    ),
]


async def add_template_categories(
    session: AsyncSession, household_id: int, *codes: str
) -> dict[str, Category]:
    repository = CategoryRepository(session)
    templates = {item.code: item for item in CATEGORY_TEMPLATES}
    result: dict[str, Category] = {}
    for code in codes:
        category = await repository.get_effective_by_code(household_id, code)
        if category is None:
            template = templates[code]
            category = await repository.create(
                household_id=household_id,
                code=template.code,
                name=template.name,
                transaction_type=template.type,
            )
        result[code] = category
    return result


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    yield TEST_DATABASE_URL
    command.downgrade(config, "base")


@pytest_asyncio.fixture
async def session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    engine = create_engine(migrated_database)
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = create_session_factory(engine)
    async with factory(bind=connection) as db_session:
        yield db_session
    await transaction.rollback()
    await connection.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def service_session_factory(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(migrated_database)
    factory = create_session_factory(engine)
    async with session_scope(factory) as db_session:
        await db_session.execute(delete(FeatureFlag))
        await db_session.execute(
            delete(Transaction).where(
                Transaction.telegram_chat_id.in_((-100999, -100998, -100997, -100996))
            )
        )
        await db_session.execute(
            delete(Household).where(
                Household.telegram_chat_id.in_((-100999, -100998, -100997, -100996))
            )
        )
    try:
        yield factory
    finally:
        async with session_scope(factory) as db_session:
            await db_session.execute(delete(FeatureFlag))
            await db_session.execute(
                delete(Transaction).where(
                    Transaction.telegram_chat_id.in_((-100999, -100998, -100997, -100996))
                )
            )
            await db_session.execute(
                delete(Household).where(
                    Household.telegram_chat_id.in_((-100999, -100998, -100997, -100996))
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_household_and_member_are_created_idempotently(session: AsyncSession) -> None:
    households = HouseholdRepository(session)
    household = await households.get_or_create(telegram_chat_id=-100123, name="Сім'я")
    same_household = await households.get_or_create(telegram_chat_id=-100123, name="Інша назва")

    members = MemberRepository(session)
    member = await members.get_or_create(
        household_id=household.id,
        telegram_user_id=42,
        display_name="Олена",
        username="olena",
    )
    same_member = await members.get_or_create(
        household_id=household.id,
        telegram_user_id=42,
        display_name="Олена Нова",
        username="olena_new",
    )

    assert same_household.id == household.id
    assert same_member.id == member.id
    assert same_member.display_name == "Олена Нова"
    assert household.currency == "EUR"
    assert household.timezone == "Europe/Paris"


@pytest.mark.asyncio
async def test_household_currency_can_be_changed(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Currency settings"
        )
        household_id = household.id

    service = HouseholdSettingsService(service_session_factory)
    updated = await service.set_currency(household_id=household_id, currency="uah")

    assert updated.currency == "UAH"
    assert updated.auto_confirm_transactions is True
    updated = await service.toggle_auto_confirm_transactions(household_id)
    assert updated.auto_confirm_transactions is False
    with pytest.raises(ValueError, match="unsupported currency"):
        await service.set_currency(household_id=household_id, currency="BTC")


@pytest.mark.asyncio
async def test_transaction_action_messages_are_persisted_until_due(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100996, name="Temporary transaction actions"
        )
        household_id = household.id

    service = TransactionService(service_session_factory)
    listener = Mock()
    service.set_schedule_listener(listener)
    expires_at = datetime(2026, 10, 24, 14, 15, tzinfo=UTC)
    await service.remember_action_message(
        household_id=household_id,
        telegram_chat_id=-100996,
        telegram_message_id=50,
        expires_at=expires_at,
    )
    listener.action_message_changed.assert_called_once()

    assert await service.due_action_messages(expires_at - timedelta(seconds=1)) == ()
    due = await service.due_action_messages(expires_at)
    assert len(due) == 1
    assert due[0].telegram_message_id == 50

    await service.complete_action_message(due[0].id)
    assert await service.due_action_messages(expires_at) == ()


@pytest.mark.asyncio
async def test_ai_usage_is_deduplicated_and_aggregated_per_household(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100997, name="AI usage household"
        )
        household_id = household.id

    service = AIUsageService(service_session_factory)
    text_usage = AIUsageData(
        model="gpt-6-luna",
        response_id="resp_usage_test",
        input_tokens=1000,
        output_tokens=100,
    )
    assert await service.record(
        household_id=household_id,
        request_kind="transaction_recognition",
        usage=text_usage,
    )
    assert not await service.record(
        household_id=household_id,
        request_kind="transaction_recognition",
        usage=text_usage,
    )
    assert await service.record(
        household_id=household_id,
        request_kind="voice_transcription",
        usage=AIUsageData(
            model="gpt-transcribe",
            input_tokens=14,
            output_tokens=5,
            audio_input_tokens=14,
        ),
        duration_seconds=7,
    )

    report = await service.report("30d", now=datetime.now(UTC))
    row = next(item for item in report.rows if item.household_id == household_id)

    assert row.request_count == 2
    assert row.input_tokens == 1014
    assert row.output_tokens == 105
    assert row.audio_input_tokens == 14
    assert row.estimated_cost_usd == Decimal("0.0006750000")


@pytest.mark.asyncio
async def test_voice_feature_flag_persists_and_respects_environment_switch(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enabled_service = VoiceFeatureService(service_session_factory, configured=True)
    assert (await enabled_service.status()).enabled is True

    disabled = await enabled_service.set_admin_enabled(enabled=False)
    assert disabled.enabled is False
    persisted = await VoiceFeatureService(
        service_session_factory, configured=True
    ).status()
    assert persisted.enabled is False

    await enabled_service.set_admin_enabled(enabled=True)
    env_disabled = VoiceFeatureService(service_session_factory, configured=False)
    status = await env_disabled.status()
    assert status.admin_enabled is True
    assert status.enabled is False


@pytest.mark.asyncio
async def test_new_household_starts_without_categories(
    session: AsyncSession,
) -> None:
    household = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100126, name="Категорії"
    )
    categories = await CategoryRepository(session).list_available(household.id)
    assert categories == []


@pytest.mark.asyncio
async def test_categories_are_isolated_by_household(session: AsyncSession) -> None:
    first = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100127, name="Перша сім’я"
    )
    second = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100128, name="Друга сім’я"
    )
    repository = CategoryRepository(session)
    first_category = await repository.create(
        household_id=first.id,
        code="pets",
        name="Домашні тварини",
        transaction_type=TransactionType.EXPENSE,
    )

    assert await repository.list_available(second.id) == []
    assert await repository.get_for_household(second.id, first_category.id) is None
    assert await repository.get_for_household(first.id, first_category.id) == first_category


@pytest.mark.asyncio
async def test_access_invitations_requests_and_revocation(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AccessService(service_session_factory)
    invitation = await service.create_invitation(
        created_by_user_id=42, target_type="private"
    )

    wrong_target = await service.redeem_invitation(
        token=invitation.token,
        telegram_chat_id=-100700,
        chat_type="supergroup",
        title="Wrong group",
        username=None,
    )
    assert wrong_target.status == "wrong_target"

    redeemed = await service.redeem_invitation(
        token=invitation.token,
        telegram_chat_id=700,
        chat_type="private",
        title="Олена",
        username="olena",
    )
    assert redeemed.status == "granted"
    assert await service.is_allowed(700) is True
    assert (
        await service.redeem_invitation(
            token=invitation.token,
            telegram_chat_id=701,
            chat_type="private",
            title="Інший користувач",
            username=None,
        )
    ).status == "used"

    request = await service.request_access(
        telegram_chat_id=-100701,
        requester_user_id=43,
        chat_type="supergroup",
        title="Сімейна група",
        username=None,
    )
    assert [item.id for item in await service.list_pending_requests()] == [request.id]
    approved = await service.approve_request(request_id=request.id, reviewed_by_user_id=42)
    assert approved is not None
    assert await service.is_allowed(-100701) is True
    assert await service.approve_request(request_id=request.id, reviewed_by_user_id=42) is None

    revoked = await service.revoke(-100701)
    assert revoked is not None
    assert await service.is_allowed(-100701) is False

    expiring = await service.create_invitation(created_by_user_id=42, target_type="private")
    async with session_scope(service_session_factory) as db_session:
        await db_session.execute(
            update(AccessInvitation)
            .where(AccessInvitation.used_at.is_(None))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    expired = await service.redeem_invitation(
        token=expiring.token,
        telegram_chat_id=702,
        chat_type="private",
        title="Expired",
        username=None,
    )
    assert expired.status == "expired"

    rejected_request = await service.request_access(
        telegram_chat_id=703,
        requester_user_id=44,
        chat_type="private",
        title="Rejected",
        username=None,
    )
    rejected = await service.reject_request(
        request_id=rejected_request.id, reviewed_by_user_id=42
    )
    assert rejected is not None
    assert rejected.status == "rejected"
    assert await service.is_allowed(703) is False


@pytest.mark.asyncio
async def test_category_templates_deletion_and_assignment_are_household_safe(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        first = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Перша сім’я"
        )
        second = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100999, name="Друга сім’я"
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=first.id,
            telegram_user_id=42,
            display_name="Автор",
        )

    categories = CategoryService(service_session_factory)
    available_templates = await categories.list_templates(
        household_id=first.id, transaction_type=TransactionType.EXPENSE
    )
    assert "groceries" in {item.code for item in available_templates}

    first_groceries = await categories.add_template(household_id=first.id, code="groceries")
    second_groceries = await categories.add_template(household_id=second.id, code="groceries")
    bonus = await categories.add_template(household_id=first.id, code="bonus")
    assert first_groceries is not None
    assert second_groceries is not None
    assert bonus is not None
    assert first_groceries.id != second_groceries.id
    assert await categories.add_template(household_id=first.id, code="groceries") is None
    assert await categories.delete_unused(household_id=first.id, code="bonus") is True

    async with session_scope(service_session_factory) as db_session:
        transaction = Transaction(
            household_id=first.id,
            member_id=member.id,
            beneficiary_member_id=member.id,
            category_id=None,
            type=TransactionType.EXPENSE,
            amount=Decimal("10.00"),
            currency="EUR",
            description="Продукти",
            transaction_date=date(2026, 10, 13),
            original_text="продукти 10",
            telegram_chat_id=first.telegram_chat_id,
            telegram_message_id=991,
            status=TransactionStatus.CONFIRMED,
        )
        db_session.add(transaction)
        await db_session.flush()
        transaction_id = transaction.id

    transactions = TransactionService(service_session_factory)
    assert (
        await transactions.assign_category(
            household_id=first.id,
            transaction_id=transaction_id,
            category_id=second_groceries.id,
        )
        is None
    )
    assigned = await transactions.assign_category(
        household_id=first.id,
        transaction_id=transaction_id,
        category_id=first_groceries.id,
    )
    assert assigned is not None
    assert assigned.category_id == first_groceries.id
    assert await categories.delete_unused(household_id=first.id, code="groceries") is False

    renamed = await categories.rename(household_id=first.id, code="groceries", name="Їжа додому")
    assert renamed is not None
    assert renamed.name == "Їжа додому"
    second_list = await categories.list(second.id)
    assert [(item.code, item.name) for item in second_list] == [("groceries", "Продукти")]


@pytest.mark.asyncio
async def test_telegram_update_is_claimed_once_and_can_be_released(session: AsyncSession) -> None:
    repository = ProcessedTelegramUpdateRepository(session)

    assert await repository.claim(12345) is True
    assert await repository.claim(12345) is False

    await repository.release(12345)
    await session.flush()

    assert await session.get(ProcessedTelegramUpdate, 12345) is None
    assert await repository.claim(12345) is True


@pytest.mark.asyncio
async def test_clarification_and_alias_survive_separate_database_sessions(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Сім'я уточнень"
        )
        author = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=41,
            display_name="Андрій",
        )
        beneficiary = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=42,
            display_name="Yanina",
        )
        household_id, author_id, beneficiary_id = household.id, author.id, beneficiary.id

    service = ClarificationService(service_session_factory)
    created = await service.begin(
        household_id=household_id,
        member_id=author_id,
        original_message_id=100,
        original_text="Яна навчання 120",
        question="Чи Яна — це Yanina?",
        ambiguous_member_name="Яна",
        suggested_member_id=beneficiary_id,
    )
    loaded = await service.get(household_id=household_id, member_id=author_id)

    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.original_text == "Яна навчання 120"

    await service.remember_alias(
        household_id=household_id, member_id=beneficiary_id, alias="  ЯНА  "
    )
    await service.cancel(created.id)

    async with service_session_factory() as db_session:
        alias = await db_session.scalar(
            select(MemberAlias).where(MemberAlias.household_id == household_id)
        )
        clarification_count = await db_session.scalar(
            select(func.count()).select_from(TransactionClarification).where(
                TransactionClarification.household_id == household_id
            )
        )
    assert alias is not None
    assert alias.alias == "ЯНА"
    assert alias.normalized_alias == "яна"
    assert alias.member_id == beneficiary_id
    assert clarification_count == 0


@pytest.mark.asyncio
async def test_uncategorized_transaction_can_be_reassigned(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(service_session_factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Перекласифікація"
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=77,
            display_name="Автор",
        )
        education = (await add_template_categories(db_session, household.id, "education"))[
            "education"
        ]
        transaction = Transaction(
            household_id=household.id,
            member_id=member.id,
            beneficiary_member_id=member.id,
            category_id=None,
            type=TransactionType.EXPENSE,
            amount=Decimal("120.00"),
            currency="EUR",
            description="Навчання французької",
            transaction_date=date(2026, 10, 10),
            original_text="Яна навчання французької 120",
            telegram_chat_id=household.telegram_chat_id,
            telegram_message_id=701,
            status=TransactionStatus.CONFIRMED,
        )
        db_session.add(transaction)
        await db_session.flush()
        household_id = household.id
        transaction_id = transaction.id
        education_id = education.id

    service = TransactionService(service_session_factory)
    uncategorized = await service.list_uncategorized(household_id=household_id)
    assert [item.id for item in uncategorized] == [transaction_id]

    updated = await service.assign_category(
        household_id=household_id,
        transaction_id=transaction_id,
        category_id=education_id,
    )
    assert updated is not None
    assert updated.category_id == education_id
    assert await service.list_uncategorized(household_id=household_id) == ()


@pytest.mark.asyncio
async def test_planned_expense_reserve_reminder_payment_and_recurrence(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = service_session_factory
    async with session_scope(factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Заплановані витрати"
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=77,
            display_name="Автор",
        )
        await add_template_categories(db_session, household.id, "taxes_documents", "communications")

    recognition = RecognitionResult(
        intent=RecognitionIntent.CREATE_PLANNED_EXPENSES,
        transactions=(),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={},
        planned_expenses=(
            RecognizedPlannedExpense(
                amount=Decimal("100.00"),
                currency="EUR",
                category_code="taxes_documents",
                description="Страховка",
                due_date=date(2026, 10, 28),
                recurrence="monthly",
            ),
        ),
    )
    service = PlannedExpenseService(factory)
    schedule_listener = Mock()
    service.set_schedule_listener(schedule_listener)
    pending = await service.create_pending(
        household=household,
        member=member,
        telegram_message_id=800,
        original_text="Щомісяця 28 числа страховка 100",
        recognition=recognition,
    )
    assert len(pending) == 1
    assert pending[0].status is PlannedExpenseStatus.PENDING

    clarification_service = ClarificationService(factory)
    edit_context = await clarification_service.begin_plan_edit(
        household_id=household.id,
        member_id=member.id,
        original_message_id=801,
        planned_expense_id=pending[0].id,
        original_text="Заплануй страховку 100",
        field="amount",
        question="Яку нову суму встановити?",
    )
    assert (
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=pending[0].id + 1,
        )
        is False
    )
    assert await clarification_service.get_by_id(edit_context.id) is not None
    assert (
        await clarification_service.cancel_plan_context(
            household_id=household.id,
            member_id=member.id,
            planned_expense_id=pending[0].id,
        )
        is True
    )

    async with factory() as db_session:
        communications = await CategoryRepository(db_session).get_effective_by_code(
            household.id, "communications"
        )
        assert communications is not None
    edited = await service.update_details(
        household_id=household.id,
        planned_expense_id=pending[0].id,
        amount=Decimal("110.00"),
        category_id=communications.id,
        description="Мобільний зв’язок",
    )
    assert edited is not None
    assert edited.amount == Decimal("110.00")
    assert edited.category.code == "communications"
    assert edited.description == "Мобільний зв’язок"

    scheduled = await service.schedule(
        household_id=household.id,
        planned_expense_id=pending[0].id,
        recurrence=PlannedExpenseRecurrence.MONTHLY,
    )
    assert scheduled is not None
    schedule_listener.planned_expense_changed.assert_called_with(scheduled)
    budget = await BudgetService(factory).status(
        household_id=household.id,
        currency="EUR",
        today=date(2026, 10, 27),
    )
    assert budget.reserved == Decimal("110.00")

    reminders = await service.due_reminders(datetime(2026, 10, 27, 8, tzinfo=UTC))
    assert [(item.planned_expense.id, item.kind) for item in reminders] == [
        (scheduled.id, ReminderKind.DAY_BEFORE)
    ]
    assert await service.claim_reminder(scheduled.id, ReminderKind.DAY_BEFORE) is True
    assert await service.claim_reminder(scheduled.id, ReminderKind.DAY_BEFORE) is False

    paid = await service.mark_paid(
        household=household,
        planned_expense_id=scheduled.id,
        paid_on=date(2026, 10, 28),
    )
    assert paid is not None
    assert paid.status is PlannedExpenseStatus.PAID
    active = await service.list_active(household.id)
    assert [(item.amount, item.due_date) for item in active] == [
        (Decimal("110.00"), date(2026, 11, 28))
    ]
    totals = await ReportService(factory).totals(
        household_id=household.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
    )
    assert totals.expense == Decimal("110.00")


@pytest.mark.asyncio
async def test_report_delivery_slot_is_claimed_once_and_can_be_retried(
    session: AsyncSession,
) -> None:
    household = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100124, name="Звіти"
    )
    repository = ReportDeliveryRepository(session)
    parameters = {
        "household_id": household.id,
        "report_type": ReportType.MORNING,
        "local_date": date(2026, 10, 8),
    }

    assert await repository.claim(**parameters) is True
    assert await repository.claim(**parameters) is False

    await repository.release(**parameters)
    assert await repository.claim(**parameters) is True


@pytest.mark.asyncio
async def test_transaction_insert_is_decimal_and_idempotent(session: AsyncSession) -> None:
    household = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100456, name="Сім'я"
    )
    member = await MemberRepository(session).get_or_create(
        household_id=household.id,
        telegram_user_id=43,
        display_name="Андрій",
    )
    category = (await add_template_categories(session, household.id, "groceries"))["groceries"]

    repository = TransactionRepository(session)
    parameters = {
        "household_id": household.id,
        "member_id": member.id,
        "category_id": category.id,
        "transaction_type": TransactionType.EXPENSE,
        "amount": Decimal("12.34"),
        "currency": "EUR",
        "description": "Продукти",
        "transaction_date": date(2026, 10, 6),
        "original_text": "продукти 12,34",
        "telegram_chat_id": household.telegram_chat_id,
        "telegram_message_id": 777,
        "ai_metadata": {"model": "test"},
    }
    first = await repository.create_idempotent(**parameters)
    duplicate = await repository.create_idempotent(**parameters)

    count = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.household_id == household.id)
    )
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.transaction.id == first.transaction.id
    assert first.transaction.amount == Decimal("12.34")
    assert first.transaction.status is TransactionStatus.PENDING
    assert count == 1

    await repository.set_status(first.transaction, TransactionStatus.CONFIRMED)
    assert first.transaction.status is TransactionStatus.CONFIRMED


@pytest.mark.asyncio
async def test_category_and_budget_crud(session: AsyncSession) -> None:
    household = await HouseholdRepository(session).get_or_create(
        telegram_chat_id=-100789, name="Сім'я"
    )
    category_repository = CategoryRepository(session)
    category = await category_repository.create(
        household_id=household.id,
        code="pets",
        name="Домашні тварини",
        transaction_type=TransactionType.EXPENSE,
    )
    available = await category_repository.list_available(
        household.id, transaction_type=TransactionType.EXPENSE
    )
    income_categories = await category_repository.list_available(
        household.id, transaction_type=TransactionType.INCOME
    )

    budgets = BudgetRepository(session)
    budget = await budgets.upsert(
        household_id=household.id,
        year=2026,
        month=10,
        category_id=category.id,
        amount=Decimal("100.00"),
    )
    updated = await budgets.upsert(
        household_id=household.id,
        year=2026,
        month=10,
        category_id=category.id,
        amount=Decimal("125.00"),
    )

    assert category in available
    assert income_categories == []
    assert updated.id == budget.id
    assert updated.amount == Decimal("125.00")


@pytest.mark.asyncio
async def test_pending_batch_confirm_last_and_undo_flow(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = service_session_factory
    async with session_scope(factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100999, name="Flow test"
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=99,
            display_name="Тест",
        )
        beneficiary = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=100,
            display_name="Олена",
        )
        await add_template_categories(db_session, household.id, "health", "groceries")

    recognition = RecognitionResult(
        intent=RecognitionIntent.CREATE_TRANSACTIONS,
        transactions=(
            RecognizedTransaction(
                type=TransactionType.EXPENSE,
                amount=Decimal("18.00"),
                currency="EUR",
                category_code="health",
                description="Аптека",
                beneficiary_member_id=beneficiary.id,
                date="2026-10-07",
            ),
            RecognizedTransaction(
                type=TransactionType.EXPENSE,
                amount=Decimal("12.00"),
                currency="EUR",
                category_code="groceries",
                description="Продукти",
                date="2026-10-07",
            ),
        ),
        needs_clarification=False,
        clarification_question=None,
        ai_metadata={"response_id": "resp_flow"},
    )
    service = TransactionService(factory)

    first = await service.create_pending(
        household=household,
        member=member,
        telegram_message_id=555,
        original_text="аптека 18 і продукти 12",
        recognition=recognition,
    )
    duplicate = await service.create_pending(
        household=household,
        member=member,
        telegram_message_id=555,
        original_text="аптека 18 і продукти 12",
        recognition=recognition,
    )

    assert len(first) == 2
    assert [item.beneficiary_member_id for item in first] == [beneficiary.id, member.id]
    assert [item.category.name for item in first] == ["Здоров’я", "Продукти"]
    assert [item.id for item in duplicate] == [item.id for item in first]
    assert all(item.status is TransactionStatus.PENDING for item in first)

    confirmed = await service.transition_pending(
        household_id=household.id,
        telegram_message_id=555,
        status=TransactionStatus.CONFIRMED,
    )
    repeated = await service.transition_pending(
        household_id=household.id,
        telegram_message_id=555,
        status=TransactionStatus.CANCELLED,
    )

    assert confirmed.changed is True
    assert all(item.status is TransactionStatus.CONFIRMED for item in confirmed.transactions)
    assert repeated.changed is False
    assert len(await service.list_recent(household_id=household.id)) == 2
    assert await service.confirmed_balance(household_id=household.id) == Decimal("-30.00")

    category_service = CategoryService(factory)
    renamed = await category_service.rename(
        household_id=household.id,
        code="health",
        name="Медицина",
    )
    assert renamed is not None
    statistics = await category_service.statistics(
        household_id=household.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
    )
    assert {(item.name, item.amount) for item in statistics} == {
        ("Медицина", Decimal("18.00")),
        ("Продукти", Decimal("12.00")),
    }
    hidden = await category_service.set_active(
        household_id=household.id,
        code="health",
        is_active=False,
    )
    assert hidden is not None and hidden.is_active is False

    settings_service = HouseholdSettingsService(factory)
    settings_listener = Mock()
    settings_service.set_schedule_listener(settings_listener)
    updated_household = await settings_service.set_timezone(
        household_id=household.id, timezone="Europe/Kyiv"
    )
    assert updated_household.timezone == "Europe/Kyiv"
    settings_listener.household_changed.assert_called_with(updated_household)
    settings_listener.reset_mock()
    toggled_household = await settings_service.toggle_reports(household.id)
    assert toggled_household.reports_enabled is False
    settings_listener.household_changed.assert_called_with(toggled_household)

    undone = await service.undo_last(household_id=household.id, member_id=member.id)
    assert len(undone) == 2
    assert all(item.status is TransactionStatus.CANCELLED for item in undone)
    assert len(await service.list_recent(household_id=household.id)) == 0
    assert await service.confirmed_balance(household_id=household.id) == Decimal("0.00")


@pytest.mark.asyncio
async def test_confirmed_batch_can_be_replaced_atomically(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = service_session_factory
    async with session_scope(factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100997, name="Replacement test"
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=97,
            display_name="Тест",
        )
        other_member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=96,
            display_name="Інший учасник",
        )
        await add_template_categories(db_session, household.id, "health")

    def recognition(amount: str) -> RecognitionResult:
        return RecognitionResult(
            intent=RecognitionIntent.CREATE_TRANSACTIONS,
            transactions=(
                RecognizedTransaction(
                    type=TransactionType.EXPENSE,
                    amount=Decimal(amount),
                    currency="EUR",
                    category_code="health",
                    description="Аптека",
                    date="2026-10-07",
                ),
            ),
            needs_clarification=False,
            clarification_question=None,
            ai_metadata={},
        )

    service = TransactionService(factory)
    original = await service.create_confirmed(
        household=household,
        member=member,
        telegram_message_id=700,
        original_text="аптека 18",
        recognition=recognition("18.00"),
    )
    denied = await service.cancel_confirmed_batch(
        household_id=household.id,
        member_id=other_member.id,
        telegram_message_id=700,
    )
    assert denied.changed is False
    assert await service.confirmed_balance(household_id=household.id) == Decimal("-18.00")

    replaced = await service.replace_confirmed_batch(
        household=household,
        member=member,
        telegram_message_id=700,
        replacement_message_id=701,
        original_text="аптека 21",
        recognition=recognition("21.00"),
    )

    assert replaced.changed is True
    assert replaced.transactions[0].amount == Decimal("21.00")
    assert original[0].id != replaced.transactions[0].id
    assert await service.confirmed_balance(household_id=household.id) == Decimal("-21.00")
    assert not await service.get_confirmed_batch(
        household_id=household.id,
        member_id=member.id,
        telegram_message_id=700,
    )


@pytest.mark.asyncio
async def test_export_queries_are_limited_to_household_period_and_currency(
    session: AsyncSession,
) -> None:
    households = HouseholdRepository(session)
    members = MemberRepository(session)
    transactions = TransactionRepository(session)
    plans = PlannedExpenseRepository(session)
    first = await households.get_or_create(telegram_chat_id=-100140, name="Перша сім’я")
    second = await households.get_or_create(telegram_chat_id=-100141, name="Друга сім’я")
    first_member = await members.get_or_create(
        household_id=first.id, telegram_user_id=140, display_name="Андрій"
    )
    second_member = await members.get_or_create(
        household_id=second.id, telegram_user_id=141, display_name="Олена"
    )
    first_category = (
        await add_template_categories(session, first.id, "groceries")
    )["groceries"]
    second_category = (
        await add_template_categories(session, second.id, "groceries")
    )["groceries"]

    first_transaction = await transactions.create_idempotent(
        household_id=first.id,
        member_id=first_member.id,
        category_id=first_category.id,
        transaction_type=TransactionType.EXPENSE,
        amount=Decimal("42.00"),
        currency="EUR",
        description="Продукти",
        transaction_date=date(2026, 10, 12),
        original_text="продукти 42",
        telegram_chat_id=first.telegram_chat_id,
        telegram_message_id=1401,
    )
    await transactions.set_status(first_transaction.transaction, TransactionStatus.CONFIRMED)
    second_transaction = await transactions.create_idempotent(
        household_id=second.id,
        member_id=second_member.id,
        category_id=second_category.id,
        transaction_type=TransactionType.EXPENSE,
        amount=Decimal("500.00"),
        currency="EUR",
        description="Чужі витрати",
        transaction_date=date(2026, 10, 12),
        original_text="чужі витрати 500",
        telegram_chat_id=second.telegram_chat_id,
        telegram_message_id=1411,
    )
    await transactions.set_status(second_transaction.transaction, TransactionStatus.CONFIRMED)
    await plans.create(
        household_id=first.id,
        member_id=first_member.id,
        category_id=first_category.id,
        amount=Decimal("100.00"),
        currency="EUR",
        description="Страхування",
        due_date=date(2026, 10, 25),
        recurrence=None,
        original_text="страхування 100",
        telegram_chat_id=first.telegram_chat_id,
        telegram_message_id=1402,
        status=PlannedExpenseStatus.SCHEDULED,
    )

    exported = await transactions.list_confirmed_for_period(
        household_id=first.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
        limit=100,
    )
    exported_plans = await plans.list_for_period(
        household_id=first.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
    )

    assert [(item.description, item.category.name) for item in exported] == [
        ("Продукти", "Продукти")
    ]
    assert exported[0].beneficiary.display_name == "Андрій"
    assert [item.description for item in exported_plans] == ["Страхування"]
    assert await transactions.oldest_confirmed_date(first.id) == date(2026, 10, 12)
    assert await transactions.type_totals(
        household_id=first.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        currency="EUR",
    ) == {TransactionType.EXPENSE: Decimal("42.00")}


@pytest.mark.asyncio
async def test_reports_and_budgets_use_confirmed_base_currency_transactions(
    service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = service_session_factory
    async with session_scope(factory) as db_session:
        household = await HouseholdRepository(db_session).get_or_create(
            telegram_chat_id=-100998, name="Report test"
        )
        await db_session.execute(
            delete(Transaction).where(Transaction.household_id == household.id)
        )
        member = await MemberRepository(db_session).get_or_create(
            household_id=household.id,
            telegram_user_id=98,
            display_name="Звіт",
        )
        await add_template_categories(db_session, household.id, "salary", "groceries", "transport")
        categories = {
            item.code: item
            for item in await CategoryRepository(db_session).list_available(household.id)
        }
        repository = TransactionRepository(db_session)
        prepared = (
            (
                1,
                TransactionType.INCOME,
                "salary",
                "200.00",
                "EUR",
                date(2026, 9, 30),
                TransactionStatus.CONFIRMED,
            ),
            (
                2,
                TransactionType.INCOME,
                "salary",
                "1000.00",
                "EUR",
                date(2026, 10, 2),
                TransactionStatus.CONFIRMED,
            ),
            (
                3,
                TransactionType.EXPENSE,
                "groceries",
                "120.00",
                "EUR",
                date(2026, 10, 3),
                TransactionStatus.CONFIRMED,
            ),
            (
                4,
                TransactionType.EXPENSE,
                "transport",
                "30.00",
                "EUR",
                date(2026, 10, 4),
                TransactionStatus.CONFIRMED,
            ),
            (
                5,
                TransactionType.EXPENSE,
                "groceries",
                "50.00",
                "EUR",
                date(2026, 10, 5),
                TransactionStatus.PENDING,
            ),
            (
                6,
                TransactionType.EXPENSE,
                "groceries",
                "40.00",
                "EUR",
                date(2026, 10, 5),
                TransactionStatus.CANCELLED,
            ),
            (
                7,
                TransactionType.EXPENSE,
                "groceries",
                "999.00",
                "USD",
                date(2026, 10, 3),
                TransactionStatus.CONFIRMED,
            ),
        )
        for message_id, kind, code, amount, currency, transaction_date, status in prepared:
            await repository.create_idempotent(
                household_id=household.id,
                member_id=member.id,
                category_id=categories[code].id,
                transaction_type=kind,
                amount=Decimal(amount),
                currency=currency,
                description=code,
                transaction_date=transaction_date,
                original_text=code,
                telegram_chat_id=household.telegram_chat_id,
                telegram_message_id=message_id,
                status=status,
            )

    report = await ReportService(factory).month_report(
        household_id=household.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        previous_start_date=date(2026, 9, 1),
        currency="EUR",
    )
    assert report.totals.income == Decimal("1000.00")
    assert report.totals.expense == Decimal("150.00")
    assert report.totals.balance == Decimal("850.00")
    assert {(item.code, item.amount) for item in report.expense_categories} == {
        ("groceries", Decimal("120.00")),
        ("transport", Decimal("30.00")),
    }
    assert report.previous_totals is not None
    assert report.previous_totals.income == Decimal("200.00")

    budget_service = BudgetService(factory)
    await budget_service.set_total(
        household_id=household.id,
        year=2026,
        month=10,
        amount=Decimal("500.00"),
    )
    await budget_service.set_category(
        household_id=household.id,
        year=2026,
        month=10,
        category_code="groceries",
        amount=Decimal("200.00"),
    )
    await budget_service.set_category(
        household_id=household.id,
        year=2026,
        month=10,
        category_code="transport",
        amount=Decimal("50.00"),
    )
    status = await budget_service.status(
        household_id=household.id,
        currency="EUR",
        today=date(2026, 10, 8),
    )
    assert status.total_limit == Decimal("500.00")
    assert status.income == Decimal("1000.00")
    assert status.spent == Decimal("150.00")
    assert status.balance == Decimal("850.00")
    assert status.remaining == Decimal("350.00")
    assert status.percentage == Decimal("30.0")
    assert status.days_remaining == 23
    assert {item.code: (item.spent, item.remaining) for item in status.categories} == {
        "groceries": (Decimal("120.00"), Decimal("80.00")),
        "transport": (Decimal("30.00"), Decimal("20.00")),
    }
