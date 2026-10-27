from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.schedule_events import ScheduleChangeListener
from domain.currencies import SUPPORTED_CURRENCIES
from domain.enums import ReportType
from infrastructure.database import Household, session_scope
from infrastructure.repositories import HouseholdRepository


class HouseholdSettingsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.schedule_listener: ScheduleChangeListener | None = None

    def set_schedule_listener(self, listener: ScheduleChangeListener) -> None:
        self.schedule_listener = listener

    def _notify_schedule_changed(self, household: Household) -> None:
        if self.schedule_listener is not None:
            self.schedule_listener.household_changed(household)

    async def toggle_reports(self, household_id: int) -> Household:
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.reports_enabled = not household.reports_enabled
            await session.flush()
        self._notify_schedule_changed(household)
        return household

    async def set_reports_enabled(self, household_id: int, *, enabled: bool) -> Household:
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.reports_enabled = enabled
            await session.flush()
        self._notify_schedule_changed(household)
        return household

    async def set_report_time(
        self,
        *,
        household_id: int,
        report_type: ReportType,
        report_time: time,
    ) -> Household:
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            if report_type is ReportType.MORNING:
                household.morning_report_time = report_time
            elif report_type is ReportType.EVENING:
                household.evening_report_time = report_time
            else:  # pragma: no cover - exhaustive guard for future enum members
                raise ValueError("unsupported report type")
            await session.flush()
        self._notify_schedule_changed(household)
        return household

    async def toggle_auto_confirm_transactions(self, household_id: int) -> Household:
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.auto_confirm_transactions = not household.auto_confirm_transactions
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
        self._notify_schedule_changed(household)
        return household

    async def set_currency(self, *, household_id: int, currency: str) -> Household:
        normalized = currency.strip().upper()
        if normalized not in SUPPORTED_CURRENCIES:
            raise ValueError("unsupported currency")
        async with session_scope(self.session_factory) as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            household.currency = normalized
            await session.flush()
            return household


__all__ = ["HouseholdSettingsService"]
