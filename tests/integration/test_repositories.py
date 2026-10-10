from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services import (
    BudgetService,
    CategoryService,
    ClarificationService,
    HouseholdSettingsService,
    ReportService,
    TransactionService,
)
from domain.enums import ReportType, TransactionStatus, TransactionType
from domain.models import RecognitionIntent, RecognitionResult, RecognizedTransaction
from infrastructure.database import (
    Category,
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
        await db_session.execute(
            delete(Transaction).where(Transaction.telegram_chat_id.in_((-100999, -100998)))
        )
        await db_session.execute(
            delete(Household).where(Household.telegram_chat_id.in_((-100999, -100998)))
        )
    try:
        yield factory
    finally:
        async with session_scope(factory) as db_session:
            await db_session.execute(
                delete(Transaction).where(Transaction.telegram_chat_id.in_((-100999, -100998)))
            )
            await db_session.execute(
                delete(Household).where(Household.telegram_chat_id.in_((-100999, -100998)))
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
    category = await session.scalar(select(Category).where(Category.code == "groceries"))
    assert category is not None

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
    assert {item.code for item in income_categories} == {"salary", "other_income"}
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
    updated_household = await settings_service.set_timezone(
        household_id=household.id, timezone="Europe/Kyiv"
    )
    assert updated_household.timezone == "Europe/Kyiv"
    toggled_household = await settings_service.toggle_reports(household.id)
    assert toggled_household.reports_enabled is False

    undone = await service.undo_last(household_id=household.id, member_id=member.id)
    assert undone is not None
    assert undone.status is TransactionStatus.CANCELLED
    assert len(await service.list_recent(household_id=household.id)) == 1
    assert await service.confirmed_balance(household_id=household.id) == Decimal("-18.00")


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
