from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database import Household, session_scope
from infrastructure.repositories import HouseholdRepository


class HouseholdSettingsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def toggle_reports(self, household_id: int) -> Household:
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.reports_enabled = not household.reports_enabled
            await session.flush()
            return household

    async def set_timezone(self, *, household_id: int, timezone: str) -> Household:
        ZoneInfo(timezone)
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.timezone = timezone
            await session.flush()
            return household


__all__ = ["HouseholdSettingsService"]
