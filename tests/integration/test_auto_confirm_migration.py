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


def test_existing_and_new_households_get_auto_confirmation() -> None:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "20261024_15")
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO households (
                        id, telegram_chat_id, name, auto_confirm_transactions
                    ) VALUES (910001, -910001, 'Existing household', false)
                    """
                )
            )

        command.upgrade(config, "head")

        with engine.begin() as connection:
            existing = connection.execute(
                text("SELECT auto_confirm_transactions FROM households WHERE id = 910001")
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO households (id, telegram_chat_id, name)
                    VALUES (910002, -910002, 'New household')
                    """
                )
            )
            created = connection.execute(
                text("SELECT auto_confirm_transactions FROM households WHERE id = 910002")
            ).scalar_one()

        assert existing is True
        assert created is True
    finally:
        engine.dispose()
        command.downgrade(config, "base")
