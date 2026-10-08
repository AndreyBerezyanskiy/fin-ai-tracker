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

from application.services import CategoryService, HouseholdSettingsService, TransactionService
from domain.enums import TransactionStatus, TransactionType
from domain.models import RecognitionIntent, RecognitionResult, RecognizedTransaction
from infrastructure.database import (
    Category,
    ProcessedTelegramUpdate,
    Transaction,
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
        await db_session.execute(delete(Transaction).where(Transaction.telegram_chat_id == -100999))
    try:
        yield factory
    finally:
        async with session_scope(factory) as db_session:
            await db_session.execute(
                delete(Transaction).where(Transaction.telegram_chat_id == -100999)
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

    count = await session.scalar(select(func.count()).select_from(Transaction))
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
