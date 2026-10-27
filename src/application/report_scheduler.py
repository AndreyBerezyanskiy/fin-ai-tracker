from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from application.services.automatic_reports import AutomaticReportService, ReportSender
from application.services.planned_expenses import PlannedExpenseService, ReminderCandidate
from application.services.transactions import TransactionService
from bot.keyboards import planned_expenses_keyboard
from domain.currencies import currency_prefix
from domain.enums import PlannedExpenseStatus, ReminderKind, ReportType
from infrastructure.database import Household, PlannedExpense, TransactionActionMessage
from infrastructure.observability import pseudonymize

logger = logging.getLogger(__name__)

RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))


class SchedulerSender(ReportSender, Protocol):
    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup: object | None
    ) -> object: ...


class ReportScheduler:
    """Schedule database work only when a user-visible event is actually due."""

    def __init__(
        self,
        *,
        sender: SchedulerSender,
        report_service: AutomaticReportService,
        planned_expense_service: PlannedExpenseService | None = None,
        transaction_service: TransactionService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.sender = sender
        self.report_service = report_service
        self.planned_expense_service = planned_expense_service
        self.transaction_service = transaction_service
        self.now = now or (lambda: datetime.now(UTC))
        self.scheduler = AsyncIOScheduler(timezone=UTC)
        self._households: dict[int, Household] = {}
        self._planned_expenses: dict[int, PlannedExpense] = {}

    async def start(self) -> None:
        """Restore durable jobs once, then remain idle until their exact run times."""

        if self.scheduler.running:
            return
        households = await self.report_service.enabled_households()
        plans = (
            await self.planned_expense_service.scheduled()
            if self.planned_expense_service is not None
            else ()
        )
        action_messages = (
            await self.transaction_service.pending_action_messages()
            if self.transaction_service is not None
            else ()
        )

        for household in households:
            self._schedule_household(household)
        for plan in plans:
            self._schedule_planned_expense(plan)
        for action_message in action_messages:
            self.action_message_changed(action_message)

        self.scheduler.start()
        await self._recover_due_reports(households)
        logger.info(
            "event_scheduler_restored households=%s planned_expenses=%s action_messages=%s",
            len(households),
            len(plans),
            len(action_messages),
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def household_changed(self, household: Household) -> None:
        self._households[household.id] = household
        self._remove_jobs(f"report:{household.id}:")
        if household.reports_enabled:
            self._schedule_household(household)
        for plan in tuple(self._planned_expenses.values()):
            if plan.household_id != household.id:
                continue
            plan.household.timezone = household.timezone
            plan.household.morning_report_time = household.morning_report_time
            self.planned_expense_changed(plan)

    def planned_expense_changed(self, planned_expense: PlannedExpense) -> None:
        self._remove_jobs(f"reminder:{planned_expense.id}:")
        if planned_expense.status is PlannedExpenseStatus.SCHEDULED:
            self._planned_expenses[planned_expense.id] = planned_expense
            self._schedule_planned_expense(planned_expense)
        else:
            self._planned_expenses.pop(planned_expense.id, None)

    def action_message_changed(self, action_message: TransactionActionMessage) -> None:
        job_id = self._action_job_id(
            action_message.telegram_chat_id,
            action_message.telegram_message_id,
        )
        self.scheduler.add_job(
            self.run_action_cleanup,
            trigger=DateTrigger(
                run_date=max(action_message.expires_at, self.now()),
                timezone=UTC,
            ),
            args=(
                action_message.id,
                action_message.telegram_chat_id,
                action_message.telegram_message_id,
            ),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )

    def action_message_removed(self, telegram_chat_id: int, telegram_message_id: int) -> None:
        self._remove_jobs(self._action_job_id(telegram_chat_id, telegram_message_id))

    def _schedule_household(self, household: Household) -> None:
        self._households[household.id] = household
        timezone = ZoneInfo(household.timezone)
        for report_type, report_time in (
            (ReportType.MORNING, household.morning_report_time),
            (ReportType.EVENING, household.evening_report_time),
        ):
            self.scheduler.add_job(
                self.run_report,
                trigger=CronTrigger(
                    hour=report_time.hour,
                    minute=report_time.minute,
                    timezone=timezone,
                ),
                args=(household.id, report_type),
                id=f"report:{household.id}:{report_type.value}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )

    def _schedule_planned_expense(self, plan: PlannedExpense) -> None:
        self._planned_expenses[plan.id] = plan
        now = self.now()
        future_job_added = False
        for kind in ReminderKind:
            run_at = self._reminder_run_at(plan, kind)
            if run_at <= now:
                continue
            future_job_added = True
            self._add_reminder_job(plan.id, kind, run_at)

        current_kind = self._current_reminder_kind(plan, now)
        if current_kind is not None:
            self._add_reminder_job(plan.id, current_kind, now)
        elif (
            not future_job_added
            and plan.due_date < now.astimezone(ZoneInfo(plan.household.timezone)).date()
        ):
            local_today = now.astimezone(ZoneInfo(plan.household.timezone)).date()
            next_morning = datetime.combine(
                local_today,
                plan.household.morning_report_time,
                tzinfo=ZoneInfo(plan.household.timezone),
            ).astimezone(UTC)
            self._add_reminder_job(plan.id, ReminderKind.OVERDUE, max(now, next_morning))
        elif not future_job_added:
            logger.warning("No reminder slot available: plan_ref=%s", pseudonymize(plan.id))

    def _add_reminder_job(
        self,
        planned_expense_id: int,
        kind: ReminderKind,
        run_at: datetime,
        *,
        retry: int = 0,
    ) -> None:
        suffix = f":retry:{retry}" if retry else ""
        self.scheduler.add_job(
            self.run_reminder,
            trigger=DateTrigger(run_date=run_at, timezone=UTC),
            args=(planned_expense_id, kind, retry),
            id=f"reminder:{planned_expense_id}:{kind.value}{suffix}",
            replace_existing=True,
            misfire_grace_time=300,
        )

    async def _recover_due_reports(self, households: tuple[Household, ...]) -> None:
        now = self.now()
        for household in households:
            slot = self.report_service.active_slot(household, now)
            if slot is not None:
                await self._deliver_report(household, slot[0], slot[1])

    async def run_report(
        self,
        household_id: int,
        report_type: ReportType,
        local_date: date | None = None,
        retry: int = 0,
    ) -> None:
        household = await self.report_service.household(household_id)
        if household is None or not household.reports_enabled:
            return
        target_date = local_date or self.now().astimezone(ZoneInfo(household.timezone)).date()
        await self._deliver_report(household, report_type, target_date, retry)

    async def _deliver_report(
        self,
        household: Household,
        report_type: ReportType,
        local_date: date,
        retry: int = 0,
    ) -> None:
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
                "retry=%s error_type=%s",
                pseudonymize(household.id),
                report_type.value,
                retry,
                type(error).__name__,
            )
            self._schedule_report_retry(household.id, report_type, local_date, retry)

    def _schedule_report_retry(
        self,
        household_id: int,
        report_type: ReportType,
        local_date: date,
        retry: int,
    ) -> None:
        if retry >= len(RETRY_DELAYS):
            return
        next_retry = retry + 1
        self.scheduler.add_job(
            self.run_report,
            trigger=DateTrigger(run_date=self.now() + RETRY_DELAYS[retry], timezone=UTC),
            args=(household_id, report_type, local_date, next_retry),
            id=f"report:{household_id}:retry:{report_type.value}:{local_date}:{next_retry}",
            replace_existing=True,
            misfire_grace_time=300,
        )

    async def run_reminder(
        self,
        planned_expense_id: int,
        kind: ReminderKind,
        retry: int = 0,
    ) -> None:
        if self.planned_expense_service is None:
            return
        plan = await self.planned_expense_service.get_scheduled(planned_expense_id)
        if plan is None:
            return
        await self._deliver_reminder(plan, kind, retry)

    async def _deliver_reminder(
        self,
        plan: PlannedExpense,
        kind: ReminderKind,
        retry: int = 0,
    ) -> None:
        if self.planned_expense_service is None:
            return
        if not await self.planned_expense_service.claim_reminder(plan.id, kind):
            return
        reminder = ReminderCandidate(plan, kind)
        try:
            await self.sender.send_message(
                plan.household.telegram_chat_id,
                self._reminder_text(reminder),
                reply_markup=planned_expenses_keyboard((plan,)),
            )
        except Exception as error:
            await self.planned_expense_service.release_reminder(plan.id, kind)
            logger.warning(
                "Planned expense reminder failed: household_ref=%s plan_ref=%s retry=%s "
                "error_type=%s",
                pseudonymize(plan.household_id),
                pseudonymize(plan.id),
                retry,
                type(error).__name__,
            )
            if retry < len(RETRY_DELAYS):
                self._add_reminder_job(
                    plan.id,
                    kind,
                    self.now() + RETRY_DELAYS[retry],
                    retry=retry + 1,
                )

    async def run_action_cleanup(
        self,
        action_message_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        retry: int = 0,
    ) -> None:
        if self.transaction_service is None:
            return
        try:
            await self.sender.edit_message_reply_markup(
                chat_id=telegram_chat_id,
                message_id=telegram_message_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            await self.transaction_service.complete_action_message(action_message_id)
        except Exception as error:
            logger.warning(
                "Transaction action cleanup failed: chat_ref=%s retry=%s error_type=%s",
                pseudonymize(telegram_chat_id),
                retry,
                type(error).__name__,
            )
            if retry < len(RETRY_DELAYS):
                next_retry = retry + 1
                self.scheduler.add_job(
                    self.run_action_cleanup,
                    trigger=DateTrigger(
                        run_date=self.now() + RETRY_DELAYS[retry],
                        timezone=UTC,
                    ),
                    args=(
                        action_message_id,
                        telegram_chat_id,
                        telegram_message_id,
                        next_retry,
                    ),
                    id=f"{self._action_job_id(telegram_chat_id, telegram_message_id)}:retry",
                    replace_existing=True,
                    misfire_grace_time=300,
                )
        else:
            await self.transaction_service.complete_action_message(action_message_id)

    async def run_due_reports(self) -> None:
        """Compatibility helper for an explicit recovery run; never scheduled periodically."""

        now = self.now()
        households = await self.report_service.enabled_households()
        for household in households:
            slot = self.report_service.active_slot(household, now)
            if slot is not None:
                await self._deliver_report(household, slot[0], slot[1])
        await self.run_due_reminders(now)

    async def run_due_reminders(self, now: datetime | None = None) -> None:
        if self.planned_expense_service is None:
            return
        for reminder in await self.planned_expense_service.due_reminders(now or self.now()):
            await self._deliver_reminder(reminder.planned_expense, reminder.kind)

    async def run_due_action_cleanups(self, now: datetime | None = None) -> None:
        if self.transaction_service is None:
            return
        for action_message in await self.transaction_service.due_action_messages(now or self.now()):
            await self.run_action_cleanup(
                action_message.id,
                action_message.telegram_chat_id,
                action_message.telegram_message_id,
            )

    def _remove_jobs(self, prefix: str) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                self.scheduler.remove_job(job.id)

    @staticmethod
    def _action_job_id(telegram_chat_id: int, telegram_message_id: int) -> str:
        return f"action:{telegram_chat_id}:{telegram_message_id}"

    @staticmethod
    def _reminder_run_at(plan: PlannedExpense, kind: ReminderKind) -> datetime:
        day_offset = {
            ReminderKind.DAY_BEFORE: -1,
            ReminderKind.DUE: 0,
            ReminderKind.OVERDUE: 1,
        }[kind]
        local_date = plan.due_date + timedelta(days=day_offset)
        local_datetime = datetime.combine(
            local_date,
            plan.household.morning_report_time,
            tzinfo=ZoneInfo(plan.household.timezone),
        )
        return local_datetime.astimezone(UTC)

    @staticmethod
    def _current_reminder_kind(plan: PlannedExpense, now: datetime) -> ReminderKind | None:
        local_now = now.astimezone(ZoneInfo(plan.household.timezone))
        if local_now.time() < plan.household.morning_report_time:
            return None
        difference = (plan.due_date - local_now.date()).days
        if difference == 1:
            return ReminderKind.DAY_BEFORE
        if difference == 0:
            return ReminderKind.DUE
        if difference < 0:
            return ReminderKind.OVERDUE
        return None

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
