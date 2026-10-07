from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Numeric

from domain.time import as_utc, in_household_timezone
from infrastructure.database.models import Household, Transaction


def test_money_is_mapped_as_fixed_precision_numeric() -> None:
    amount_type = Transaction.__table__.c.amount.type

    assert isinstance(amount_type, Numeric)
    assert amount_type.precision == 18
    assert amount_type.scale == 2
    assert amount_type.python_type is Decimal


def test_household_defaults_match_mvp() -> None:
    household = Household(telegram_chat_id=-100123, name="Родина")

    assert household.currency is None  # SQLAlchemy applies column defaults during INSERT.
    assert Household.__table__.c.currency.default.arg == "EUR"
    assert Household.__table__.c.timezone.default.arg == "Europe/Paris"


def test_datetime_conversion_uses_household_timezone() -> None:
    stored = datetime(2026, 1, 15, 18, 30, tzinfo=UTC)

    displayed = in_household_timezone(stored, "Europe/Paris")

    assert displayed.hour == 19
    assert displayed.tzname() == "CET"


def test_naive_datetime_cannot_be_persisted_or_displayed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        as_utc(datetime(2026, 1, 15, 18, 30))
