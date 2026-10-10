from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from application.services.automatic_reports import AutomaticReportService, ReportSender

logger = logging.getLogger(__name__)


class ReportScheduler:
    def __init__(
        self,
        *,
        sender: ReportSender,
        report_service: AutomaticReportService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.sender = sender
        self.report_service = report_service
        self.now = now or (lambda: datetime.now(UTC))
        self.scheduler = AsyncIOScheduler(timezone=UTC)

    def start(self) -> None:
        self.scheduler.add_job(
            self.run_due_reports,
            trigger=IntervalTrigger(minutes=1, timezone=UTC),
            id="automatic-reports",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
            next_run_time=self.now(),
        )
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def run_due_reports(self) -> None:
        now = self.now()
        households = await self.report_service.enabled_households()
        for household in households:
            slot = self.report_service.active_slot(household, now)
            if slot is None:
                continue
            report_type, local_date = slot
            try:
                await self.report_service.deliver(
                    sender=self.sender,
                    household=household,
                    report_type=report_type,
                    local_date=local_date,
                )
            except Exception as error:
                logger.warning(
                    "Automatic report delivery failed: household_id=%s report_type=%s "
                    "error_type=%s",
                    household.id,
                    report_type.value,
                    type(error).__name__,
                )


__all__ = ["ReportScheduler"]
