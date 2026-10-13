from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="set TEST_DATABASE_URL to a disposable PostgreSQL database",
    ),
]


def test_used_global_category_is_localized_without_losing_references() -> None:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "20261012_10")
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO households (id, telegram_chat_id, name)
                    VALUES (900001, -900001, 'Migration test');
                    INSERT INTO members (
                        id, household_id, telegram_user_id, display_name, is_active
                    ) VALUES (900001, 900001, 900001, 'Tester', true);
                    INSERT INTO transactions (
                        household_id, member_id, beneficiary_member_id, category_id,
                        type, amount, currency, description, transaction_date,
                        original_text, telegram_chat_id, telegram_message_id,
                        message_transaction_index, status, ai_metadata
                    ) SELECT
                        900001, 900001, 900001, categories.id,
                        'expense', 12.00, 'EUR', 'Food', DATE '2026-10-13',
                        'food 12', -900001, 1, 0, 'confirmed', '{}'::jsonb
                    FROM categories
                    WHERE household_id IS NULL AND code = 'groceries';
                    INSERT INTO planned_expenses (
                        household_id, member_id, category_id, amount, currency,
                        description, due_date, status, original_text,
                        telegram_chat_id, telegram_message_id, message_item_index
                    ) SELECT
                        900001, 900001, categories.id, 20.00, 'EUR',
                        'Future food', DATE '2026-10-30', 'scheduled', 'plan food',
                        -900001, 2, 0
                    FROM categories
                    WHERE household_id IS NULL AND code = 'groceries';
                    INSERT INTO budgets (household_id, year, month, category_id, amount)
                    SELECT 900001, 2026, 10, categories.id, 100.00
                    FROM categories
                    WHERE household_id IS NULL AND code = 'groceries';
                    """
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            local = connection.execute(
                text("SELECT id FROM categories WHERE household_id = 900001 AND code = 'groceries'")
            ).scalar_one()
            assert (
                connection.execute(
                    text("SELECT count(*) FROM categories WHERE household_id IS NULL")
                ).scalar_one()
                == 0
            )
            for table in ("transactions", "planned_expenses", "budgets"):
                assert (
                    connection.execute(
                        text(f"SELECT category_id FROM {table} WHERE household_id = 900001")
                    ).scalar_one()
                    == local
                )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
