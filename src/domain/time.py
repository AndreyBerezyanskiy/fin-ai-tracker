from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def as_utc(value: datetime) -> datetime:
    """Normalize an aware datetime before persistence."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def in_household_timezone(value: datetime, timezone_name: str) -> datetime:
    """Convert a stored aware datetime for display in the household timezone."""

    return as_utc(value).astimezone(ZoneInfo(timezone_name))
