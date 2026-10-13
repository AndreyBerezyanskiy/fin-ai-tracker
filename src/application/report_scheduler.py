from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from application.services.automatic_reports import AutomaticReportService, ReportSender
from application.services.planned_expenses import PlannedExpenseService, ReminderCandidate
from bot.keyboards import planned_expenses_keyboard
from domain.currencies import currency_prefix
from domain.enums import ReminderKind
from infrastructure.observability import pseudonymize

logger = logging.getLogger(__name__)


class ReportScheduler:
    def __init__(
        self,
        *,
        sender: ReportSender,
        report_service: AutomaticReportService,
        planned_expense_service: PlannedExpenseService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.sender = sender
        self.report_service = report_service
        self.planned_expense_service = planned_expense_service
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
                    "Automatic report delivery failed: household_ref=%s report_type=%s "
                    "error_type=%s",
                    pseudonymize(household.id),
                    report_type.value,
                    type(error).__name__,
                )
        await self.run_due_reminders(now)

    async def run_due_reminders(self, now: datetime | None = None) -> None:
        if self.planned_expense_service is None:
            return
        for reminder in await self.planned_expense_service.due_reminders(now or self.now()):
            plan = reminder.planned_expense
            if not await self.planned_expense_service.claim_reminder(plan.id, reminder.kind):
                continue
            try:
                await self.sender.send_message(
                    plan.household.telegram_chat_id,
                    self._reminder_text(reminder),
                    reply_markup=planned_expenses_keyboard((plan,)),
                )
            except Exception as error:
                await self.planned_expense_service.release_reminder(plan.id, reminder.kind)
                logger.warning(
                    "Planned expense reminder failed: household_ref=%s plan_ref=%s error_type=%s",
                    pseudonymize(plan.household_id),
                    pseudonymize(plan.id),
                    type(error).__name__,
                )

    @staticmethod
    def _reminder_text(reminder: ReminderCandidate) -> str:
        plan = reminder.planned_expense
        title = {
            ReminderKind.DAY_BEFORE: "🔔 Завтра запланована оплата",
            ReminderKind.DUE: "⏰ Запланована оплата сьогодні",
            ReminderKind.OVERDUE: "⚠️ Запланована оплата прострочена",
        }[reminder.kind]
        return "\n".join(
            [
                title,
                "",
                plan.description,
                f"💶 {currency_prefix(plan.currency)}{plan.amount:.2f}",
                f"🏷️ {plan.category.name}",
                f"📅 {plan.due_date:%d.%m.%Y}",
                "",
                "Сума вже врахована в резерві бюджету.",
            ]
        )


__all__ = ["ReportScheduler"]
