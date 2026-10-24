from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.enums import (
    PlannedExpenseStatus,
    ReminderKind,
    ReportType,
    TransactionStatus,
    TransactionType,
)
from infrastructure.database.models import (
    AccessGrant,
    AccessInvitation,
    AccessRequest,
    AIUsageEvent,
    Budget,
    Category,
    FeatureFlag,
    Household,
    Member,
    MemberAlias,
    PlannedExpense,
    PlannedExpenseDelivery,
    ProcessedTelegramUpdate,
    ReportDelivery,
    Transaction,
    TransactionActionMessage,
    TransactionClarification,
)


class InvalidTransactionReferenceError(ValueError):
    """Raised when transaction references do not belong to its household."""


@dataclass(frozen=True, slots=True)
class TransactionCreateResult:
    transaction: Transaction
    created: bool


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    code: str
    type: TransactionType
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AIUsageAggregate:
    household_id: int
    household_name: str
    request_kind: str | None
    request_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    audio_input_tokens: int
    duration_seconds: Decimal
    estimated_cost_usd: Decimal
    unpriced_requests: int


class AccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_allowed(self, telegram_chat_id: int) -> bool:
        return (
            await self.session.scalar(
                select(AccessGrant.id).where(
                    AccessGrant.telegram_chat_id == telegram_chat_id,
                    AccessGrant.is_active.is_(True),
                )
            )
            is not None
        )

    async def grant(
        self,
        *,
        telegram_chat_id: int,
        chat_type: str,
        title: str,
        username: str | None,
        granted_by_user_id: int,
    ) -> AccessGrant:
        statement = (
            insert(AccessGrant)
            .values(
                telegram_chat_id=telegram_chat_id,
                chat_type=chat_type,
                title=title,
                username=username,
                granted_by_user_id=granted_by_user_id,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[AccessGrant.telegram_chat_id],
                set_={
                    "chat_type": chat_type,
                    "title": title,
                    "username": username,
                    "granted_by_user_id": granted_by_user_id,
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
            .returning(AccessGrant)
        )
        return (await self.session.scalars(statement)).one()

    async def list_active_grants(self) -> list[AccessGrant]:
        return list(
            await self.session.scalars(
                select(AccessGrant)
                .where(AccessGrant.is_active.is_(True))
                .order_by(AccessGrant.title, AccessGrant.id)
            )
        )

    async def revoke(self, telegram_chat_id: int) -> AccessGrant | None:
        grant = await self.session.scalar(
            select(AccessGrant).where(AccessGrant.telegram_chat_id == telegram_chat_id)
        )
        if grant is None or not grant.is_active:
            return None
        grant.is_active = False
        await self.session.flush()
        return grant

    async def create_invitation(
        self,
        *,
        token_digest: str,
        target_type: str,
        created_by_user_id: int,
        expires_at: datetime,
    ) -> AccessInvitation:
        invitation = AccessInvitation(
            token_digest=token_digest,
            target_type=target_type,
            created_by_user_id=created_by_user_id,
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation


    async def get_invitation_for_update(self, token_digest: str) -> AccessInvitation | None:
        return await self.session.scalar(
            select(AccessInvitation)
            .where(AccessInvitation.token_digest == token_digest)
            .with_for_update()
        )

    async def request_access(
        self,
        *,
        telegram_chat_id: int,
        requester_user_id: int,
        chat_type: str,
        title: str,
        username: str | None,
    ) -> AccessRequest:
        statement = (
            insert(AccessRequest)
            .values(
                telegram_chat_id=telegram_chat_id,
                requester_user_id=requester_user_id,
                chat_type=chat_type,
                title=title,
                username=username,
                status="pending",
                reviewed_by_user_id=None,
            )
            .on_conflict_do_update(
                index_elements=[AccessRequest.telegram_chat_id],
                set_={
                    "requester_user_id": requester_user_id,
                    "chat_type": chat_type,
                    "title": title,
                    "username": username,
                    "status": "pending",
                    "reviewed_by_user_id": None,
                    "updated_at": func.now(),
                },
            )
            .returning(AccessRequest)
        )
        return (await self.session.scalars(statement)).one()

    async def list_pending_requests(self) -> list[AccessRequest]:
        return list(
            await self.session.scalars(
                select(AccessRequest)
                .where(AccessRequest.status == "pending")
                .order_by(AccessRequest.created_at, AccessRequest.id)
            )
        )

    async def get_request_for_update(self, request_id: int) -> AccessRequest | None:
        return await self.session.scalar(
            select(AccessRequest).where(AccessRequest.id == request_id).with_for_update()
        )


class AIUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        household_id: int,
        request_kind: str,
        model: str,
        service_tier: str | None,
        response_id: str | None,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        audio_input_tokens: int,
        duration_seconds: Decimal | None,
        input_price_per_million: Decimal | None,
        cached_input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
        price_per_minute: Decimal | None,
        estimated_cost_usd: Decimal | None,
    ) -> bool:
        statement = (
            insert(AIUsageEvent)
            .values(
                household_id=household_id,
                request_kind=request_kind,
                model=model,
                service_tier=service_tier,
                response_id=response_id,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                audio_input_tokens=audio_input_tokens,
                duration_seconds=duration_seconds,
                input_price_per_million=input_price_per_million,
                cached_input_price_per_million=cached_input_price_per_million,
                output_price_per_million=output_price_per_million,
                price_per_minute=price_per_minute,
                estimated_cost_usd=estimated_cost_usd,
            )
            .on_conflict_do_nothing(constraint="uq_ai_usage_events_response_id")
            .returning(AIUsageEvent.id)
        )
        return (await self.session.scalar(statement)) is not None

    async def aggregate(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        household_id: int | None = None,
        by_kind: bool = False,
    ) -> list[AIUsageAggregate]:
        dimensions: list[Any] = [Household.id, Household.name]
        if by_kind:
            dimensions.append(AIUsageEvent.request_kind)
        statement = (
            select(
                *dimensions,
                func.count(AIUsageEvent.id),
                func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.cached_input_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.output_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.audio_input_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.duration_seconds), 0),
                func.coalesce(func.sum(AIUsageEvent.estimated_cost_usd), 0),
                func.count(AIUsageEvent.id).filter(
                    AIUsageEvent.estimated_cost_usd.is_(None)
                ),
            )
            .join(Household, Household.id == AIUsageEvent.household_id)
            .where(
                AIUsageEvent.created_at >= start_at,
                AIUsageEvent.created_at < end_at,
            )
        )
        if household_id is not None:
            statement = statement.where(AIUsageEvent.household_id == household_id)
        statement = statement.group_by(*dimensions).order_by(Household.name, Household.id)
        rows = (await self.session.execute(statement)).all()
        offset = 1 if by_kind else 0
        return [
            AIUsageAggregate(
                household_id=row[0],
                household_name=row[1],
                request_kind=row[2] if by_kind else None,
                request_count=row[2 + offset],
                input_tokens=row[3 + offset],
                cached_input_tokens=row[4 + offset],
                output_tokens=row[5 + offset],
                audio_input_tokens=row[6 + offset],
                duration_seconds=row[7 + offset],
                estimated_cost_usd=row[8 + offset],
                unpriced_requests=row[9 + offset],
            )
            for row in rows
        ]


class FeatureFlagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_enabled(self, name: str, *, default: bool = True) -> bool:
        value = await self.session.scalar(
            select(FeatureFlag.enabled).where(FeatureFlag.name == name)
        )
        return default if value is None else value

    async def set_enabled(self, name: str, *, enabled: bool) -> FeatureFlag:
        statement = (
            insert(FeatureFlag)
            .values(name=name, enabled=enabled)
            .on_conflict_do_update(
                index_elements=[FeatureFlag.name],
                set_={"enabled": enabled, "updated_at": func.now()},
            )
            .returning(FeatureFlag)
        )
        return (await self.session.scalars(statement)).one()


class HouseholdRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, household_id: int) -> Household | None:
        return await self.session.get(Household, household_id)

    async def get_by_telegram_chat_id(self, telegram_chat_id: int) -> Household | None:
        return await self.session.scalar(
            select(Household).where(Household.telegram_chat_id == telegram_chat_id)
        )

    async def get_or_create(
        self,
        *,
        telegram_chat_id: int,
        name: str,
        currency: str = "EUR",
        timezone: str = "Europe/Paris",
    ) -> Household:
        statement = (
            insert(Household)
            .values(
                telegram_chat_id=telegram_chat_id,
                name=name,
                currency=currency.upper(),
                timezone=timezone,
            )
            .on_conflict_do_nothing(index_elements=[Household.telegram_chat_id])
            .returning(Household)
        )
        household = (await self.session.scalars(statement)).one_or_none()
        if household is not None:
            return household

        existing = await self.get_by_telegram_chat_id(telegram_chat_id)
        if existing is None:  # Defensive: conflict target should make this unreachable.
            raise RuntimeError("household conflict occurred but row was not found")
        return existing

    async def update_report_settings(
        self,
        household: Household,
        *,
        morning_report_time: time,
        evening_report_time: time,
        reports_enabled: bool,
    ) -> Household:
        household.morning_report_time = morning_report_time
        household.evening_report_time = evening_report_time
        household.reports_enabled = reports_enabled
        await self.session.flush()
        return household

    async def list_with_reports_enabled(self) -> list[Household]:
        return list(
            await self.session.scalars(
                select(Household).where(Household.reports_enabled.is_(True)).order_by(Household.id)
            )
        )


class ReportDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, *, household_id: int, report_type: ReportType, local_date: date) -> bool:
        statement = (
            insert(ReportDelivery)
            .values(
                household_id=household_id,
                report_type=report_type.value,
                local_date=local_date,
            )
            .on_conflict_do_nothing(constraint="uq_report_delivery_slot")
            .returning(ReportDelivery.id)
        )
        return (await self.session.scalar(statement)) is not None

    async def release(
        self, *, household_id: int, report_type: ReportType, local_date: date
    ) -> None:
        await self.session.execute(
            delete(ReportDelivery).where(
                ReportDelivery.household_id == household_id,
                ReportDelivery.report_type == report_type.value,
                ReportDelivery.local_date == local_date,
            )
        )


class TransactionActionMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        household_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        expires_at: datetime,
    ) -> None:
        await self.session.execute(
            insert(TransactionActionMessage)
            .values(
                household_id=household_id,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                constraint="uq_transaction_action_message",
                set_={"expires_at": expires_at},
            )
        )

    async def list_due(
        self, now: datetime, *, limit: int = 100
    ) -> list[TransactionActionMessage]:
        return list(
            await self.session.scalars(
                select(TransactionActionMessage)
                .where(TransactionActionMessage.expires_at <= now)
                .order_by(TransactionActionMessage.expires_at, TransactionActionMessage.id)
                .limit(limit)
            )
        )

    async def delete(self, action_message_id: int) -> None:
        await self.session.execute(
            delete(TransactionActionMessage).where(
                TransactionActionMessage.id == action_message_id
            )
        )

    async def delete_by_message(
        self, *, telegram_chat_id: int, telegram_message_id: int
    ) -> None:
        await self.session.execute(
            delete(TransactionActionMessage).where(
                TransactionActionMessage.telegram_chat_id == telegram_chat_id,
                TransactionActionMessage.telegram_message_id == telegram_message_id,
            )
        )


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, member_id: int) -> Member | None:
        return await self.session.get(Member, member_id)

    async def get_by_telegram_user_id(
        self, household_id: int, telegram_user_id: int
    ) -> Member | None:
        return await self.session.scalar(
            select(Member).where(
                Member.household_id == household_id,
                Member.telegram_user_id == telegram_user_id,
            )
        )

    async def get_or_create(
        self,
        *,
        household_id: int,
        telegram_user_id: int,
        display_name: str,
        username: str | None = None,
    ) -> Member:
        statement = (
            insert(Member)
            .values(
                household_id=household_id,
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                username=username,
            )
            .on_conflict_do_update(
                constraint="uq_members_household_user",
                set_={
                    "display_name": display_name,
                    "username": username,
                    "is_active": True,
                    "updated_at": func.now(),
                },
            )
            .returning(Member)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()

    async def set_active(self, member: Member, *, is_active: bool) -> Member:
        member.is_active = is_active
        await self.session.flush()
        return member

    async def list_active(self, household_id: int) -> list[Member]:
        return list(
            await self.session.scalars(
                select(Member)
                .where(Member.household_id == household_id, Member.is_active.is_(True))
                .order_by(Member.display_name, Member.id)
            )
        )


class MemberAliasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_household(self, household_id: int) -> list[MemberAlias]:
        return list(
            await self.session.scalars(
                select(MemberAlias)
                .where(MemberAlias.household_id == household_id)
                .order_by(MemberAlias.id)
            )
        )

    async def upsert(
        self, *, household_id: int, member_id: int, alias: str, normalized_alias: str
    ) -> MemberAlias:
        statement = (
            insert(MemberAlias)
            .values(
                household_id=household_id,
                member_id=member_id,
                alias=alias,
                normalized_alias=normalized_alias,
            )
            .on_conflict_do_update(
                constraint="uq_member_alias_household",
                set_={"member_id": member_id, "alias": alias, "updated_at": func.now()},
            )
            .returning(MemberAlias)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()


class TransactionClarificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_member(
        self, *, household_id: int, member_id: int
    ) -> TransactionClarification | None:
        return await self.session.scalar(
            select(TransactionClarification).where(
                TransactionClarification.household_id == household_id,
                TransactionClarification.member_id == member_id,
            )
        )

    async def get_by_id(self, clarification_id: int) -> TransactionClarification | None:
        return await self.session.get(TransactionClarification, clarification_id)

    async def upsert(
        self,
        *,
        household_id: int,
        member_id: int,
        original_message_id: int,
        original_text: str,
        question: str,
        expires_at: datetime,
        ambiguous_member_name: str | None,
        suggested_member_id: int | None,
        missing_category_type: str | None,
        kind: str = "transaction",
        planned_expense_id: int | None = None,
        replacement_message_id: int | None = None,
    ) -> TransactionClarification:
        values = {
            "household_id": household_id,
            "member_id": member_id,
            "original_message_id": original_message_id,
            "original_text": original_text,
            "question": question,
            "attempts": 0,
            "expires_at": expires_at,
            "ambiguous_member_name": ambiguous_member_name,
            "suggested_member_id": suggested_member_id,
            "missing_category_type": missing_category_type,
            "awaiting_category_name": False,
            "kind": kind,
            "planned_expense_id": planned_expense_id,
            "replacement_message_id": replacement_message_id,
        }
        statement = (
            insert(TransactionClarification)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_transaction_clarification_member",
                set_={**values, "updated_at": func.now()},
            )
            .returning(TransactionClarification)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()

    async def update(
        self,
        clarification: TransactionClarification,
        *,
        question: str,
        attempts: int,
        expires_at: datetime,
        ambiguous_member_name: str | None,
        suggested_member_id: int | None,
        missing_category_type: str | None,
        awaiting_category_name: bool = False,
        kind: str | None = None,
        planned_expense_id: int | None = None,
        original_text: str | None = None,
        replacement_message_id: int | None = None,
    ) -> TransactionClarification:
        clarification.question = question
        clarification.attempts = attempts
        clarification.expires_at = expires_at
        clarification.ambiguous_member_name = ambiguous_member_name
        clarification.suggested_member_id = suggested_member_id
        clarification.missing_category_type = missing_category_type
        clarification.awaiting_category_name = awaiting_category_name
        if kind is not None:
            clarification.kind = kind
        if planned_expense_id is not None:
            clarification.planned_expense_id = planned_expense_id
        if original_text is not None:
            clarification.original_text = original_text
        if replacement_message_id is not None:
            clarification.replacement_message_id = replacement_message_id
        await self.session.flush()
        return clarification

    async def delete(self, clarification: TransactionClarification) -> None:
        await self.session.delete(clarification)
        await self.session.flush()


class ProcessedTelegramUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, update_id: int) -> bool:
        statement = (
            insert(ProcessedTelegramUpdate)
            .values(update_id=update_id)
            .on_conflict_do_nothing(index_elements=[ProcessedTelegramUpdate.update_id])
            .returning(ProcessedTelegramUpdate.update_id)
        )
        return (await self.session.scalar(statement)) is not None

    async def release(self, update_id: int) -> None:
        await self.session.execute(
            delete(ProcessedTelegramUpdate).where(ProcessedTelegramUpdate.update_id == update_id)
        )


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_for_household(self, household_id: int, category_id: int) -> Category | None:
        return await self.session.scalar(
            select(Category).where(
                Category.id == category_id,
                Category.household_id == household_id,
            )
        )

    async def list_available(
        self, household_id: int, *, transaction_type: TransactionType | None = None
    ) -> list[Category]:
        categories = await self.list_for_management(household_id)
        return [
            category
            for category in categories
            if category.is_active
            and (transaction_type is None or category.type is transaction_type)
        ]

    async def list_for_management(self, household_id: int) -> list[Category]:
        statement = select(Category).where(Category.household_id == household_id)
        categories = list(await self.session.scalars(statement))
        return sorted(categories, key=lambda item: (item.type.value, item.name.casefold()))

    async def get_effective_by_code(self, household_id: int, code: str) -> Category | None:
        categories = await self.list_for_management(household_id)
        return next((category for category in categories if category.code == code), None)

    async def create(
        self,
        *,
        household_id: int,
        code: str,
        name: str,
        transaction_type: TransactionType,
    ) -> Category:
        category = Category(
            household_id=household_id,
            code=code,
            name=name,
            type=transaction_type,
        )
        self.session.add(category)
        await self.session.flush()
        return category

    async def create_if_absent(
        self,
        *,
        household_id: int,
        code: str,
        name: str,
        transaction_type: TransactionType,
    ) -> Category | None:
        statement = (
            insert(Category)
            .values(
                household_id=household_id,
                code=code,
                name=name,
                type=transaction_type,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=[Category.household_id, Category.code])
            .returning(Category.id)
        )
        category_id = await self.session.scalar(statement)
        return await self.get(category_id) if category_id is not None else None

    async def rename_effective(self, *, household_id: int, code: str, name: str) -> Category | None:
        category = await self.get_effective_by_code(household_id, code)
        if category is None:
            return None
        category.name = name
        await self.session.flush()
        return category

    async def set_effective_active(
        self, *, household_id: int, code: str, is_active: bool
    ) -> Category | None:
        category = await self.get_effective_by_code(household_id, code)
        if category is None:
            return None
        category.is_active = is_active
        await self.session.flush()
        return category

    async def delete_if_unused(self, category: Category) -> bool:
        statement = (
            delete(Category)
            .where(
                Category.id == category.id,
                ~select(Transaction.id).where(Transaction.category_id == category.id).exists(),
                ~select(PlannedExpense.id)
                .where(PlannedExpense.category_id == category.id)
                .exists(),
                ~select(Budget.id).where(Budget.category_id == category.id).exists(),
            )
            .returning(Category.id)
        )
        return await self.session.scalar(statement) is not None


class TransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, transaction_id: int) -> Transaction | None:
        return await self.session.get(Transaction, transaction_id)

    async def get_for_household(
        self, *, household_id: int, transaction_id: int
    ) -> Transaction | None:
        return await self.session.scalar(
            select(Transaction)
            .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
            .where(
                Transaction.id == transaction_id,
                Transaction.household_id == household_id,
            )
        )

    async def list_uncategorized(
        self, *, household_id: int, limit: int = 20
    ) -> list[Transaction]:
        return list(
            await self.session.scalars(
                select(Transaction)
                .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
                .where(
                    Transaction.household_id == household_id,
                    Transaction.category_id.is_(None),
                    Transaction.status.in_(
                        (TransactionStatus.PENDING, TransactionStatus.CONFIRMED)
                    ),
                )
                .order_by(
                    Transaction.transaction_date.desc(),
                    Transaction.id.desc(),
                )
                .limit(limit)
            )
        )

    async def assign_category(
        self, *, transaction: Transaction, category: Category
    ) -> Transaction:
        if category.household_id != transaction.household_id:
            raise InvalidTransactionReferenceError("Category is not available to household")
        if category.type is not transaction.type:
            raise InvalidTransactionReferenceError("Category type does not match transaction type")
        transaction.category_id = category.id
        transaction.category = category
        await self.session.flush()
        return transaction

    async def get_by_telegram_message(
        self, telegram_chat_id: int, telegram_message_id: int
    ) -> list[Transaction]:
        return list(
            await self.session.scalars(
                select(Transaction)
                .options(
                    selectinload(Transaction.member),
                    selectinload(Transaction.beneficiary),
                    selectinload(Transaction.category),
                )
                .where(
                    Transaction.telegram_chat_id == telegram_chat_id,
                    Transaction.telegram_message_id == telegram_message_id,
                )
                .order_by(Transaction.message_transaction_index)
            )
        )

    async def lock_by_telegram_message(
        self, *, household_id: int, telegram_message_id: int
    ) -> list[Transaction]:
        statement = (
            select(Transaction)
            .options(
                selectinload(Transaction.member),
                selectinload(Transaction.beneficiary),
                selectinload(Transaction.category),
            )
            .where(
                Transaction.household_id == household_id,
                Transaction.telegram_message_id == telegram_message_id,
            )
            .order_by(Transaction.message_transaction_index)
            .with_for_update()
        )
        return list(await self.session.scalars(statement))

    async def create_idempotent(
        self,
        *,
        household_id: int,
        member_id: int,
        beneficiary_member_id: int | None = None,
        category_id: int,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: str,
        description: str,
        transaction_date: date,
        original_text: str,
        telegram_chat_id: int,
        telegram_message_id: int,
        message_transaction_index: int = 0,
        status: TransactionStatus = TransactionStatus.PENDING,
        ai_metadata: dict[str, Any] | None = None,
    ) -> TransactionCreateResult:
        await self._validate_references(
            household_id=household_id,
            member_id=member_id,
            beneficiary_member_id=beneficiary_member_id or member_id,
            category_id=category_id,
            transaction_type=transaction_type,
            telegram_chat_id=telegram_chat_id,
        )
        statement = (
            insert(Transaction)
            .values(
                household_id=household_id,
                member_id=member_id,
                beneficiary_member_id=beneficiary_member_id or member_id,
                category_id=category_id,
                type=transaction_type,
                amount=amount,
                currency=currency.upper(),
                description=description,
                transaction_date=transaction_date,
                original_text=original_text,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                message_transaction_index=message_transaction_index,
                status=status,
                ai_metadata=ai_metadata or {},
            )
            .on_conflict_do_nothing(constraint="uq_transactions_telegram_message_item")
            .returning(Transaction)
        )
        transaction = (await self.session.scalars(statement)).one_or_none()
        if transaction is not None:
            return TransactionCreateResult(transaction=transaction, created=True)

        existing = await self.session.scalar(
            select(Transaction).where(
                Transaction.telegram_chat_id == telegram_chat_id,
                Transaction.telegram_message_id == telegram_message_id,
                Transaction.message_transaction_index == message_transaction_index,
            )
        )
        if existing is None:  # Defensive: conflict target should make this unreachable.
            raise RuntimeError("transaction conflict occurred but row was not found")
        return TransactionCreateResult(transaction=existing, created=False)

    async def list_recent_confirmed(
        self,
        household_id: int,
        *,
        limit: int = 5,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
    ) -> list[Transaction]:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
        ]
        if start_date is not None:
            filters.append(Transaction.transaction_date >= start_date)
        if end_date is not None:
            filters.append(Transaction.transaction_date < end_date)
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Transaction)
            .options(selectinload(Transaction.member), selectinload(Transaction.beneficiary))
            .where(*filters)
            .order_by(
                Transaction.transaction_date.desc(),
                Transaction.updated_at.desc(),
                Transaction.id.desc(),
            )
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def list_confirmed_for_period(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str,
        limit: int,
    ) -> list[Transaction]:
        statement = (
            select(Transaction)
            .options(
                selectinload(Transaction.member),
                selectinload(Transaction.beneficiary),
                selectinload(Transaction.category),
            )
            .where(
                Transaction.household_id == household_id,
                Transaction.status == TransactionStatus.CONFIRMED,
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date < end_date,
                Transaction.currency == currency.upper(),
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def oldest_confirmed_date(self, household_id: int) -> date | None:
        return await self.session.scalar(
            select(func.min(Transaction.transaction_date)).where(
                Transaction.household_id == household_id,
                Transaction.status == TransactionStatus.CONFIRMED,
            )
        )

    async def confirmed_balance(self, household_id: int, *, currency: str | None = None) -> Decimal:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
        ]
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Transaction.type, func.sum(Transaction.amount))
            .where(*filters)
            .group_by(Transaction.type)
        )
        amounts = dict((await self.session.execute(statement)).all())
        return amounts.get(TransactionType.INCOME, Decimal("0.00")) - amounts.get(
            TransactionType.EXPENSE, Decimal("0.00")
        )

    async def category_totals(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str | None = None,
    ) -> list[CategoryTotal]:
        filters = [
            Transaction.household_id == household_id,
            Transaction.status == TransactionStatus.CONFIRMED,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date < end_date,
        ]
        if currency is not None:
            filters.append(Transaction.currency == currency.upper())
        statement = (
            select(Category.code, Transaction.type, func.sum(Transaction.amount))
            .join(Category, Category.id == Transaction.category_id)
            .where(*filters)
            .group_by(Category.code, Transaction.type)
            .order_by(Transaction.type, func.sum(Transaction.amount).desc())
        )
        rows = await self.session.execute(statement)
        return [
            CategoryTotal(code, transaction_type, amount) for code, transaction_type, amount in rows
        ]

    async def type_totals(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str,
    ) -> dict[TransactionType, Decimal]:
        rows = await self.session.execute(
            select(Transaction.type, func.sum(Transaction.amount))
            .where(
                Transaction.household_id == household_id,
                Transaction.status == TransactionStatus.CONFIRMED,
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date < end_date,
                Transaction.currency == currency.upper(),
            )
            .group_by(Transaction.type)
        )
        return dict(rows.all())

    async def latest_confirmed_message_id(
        self, *, household_id: int, member_id: int
    ) -> int | None:
        return await self.session.scalar(
            select(Transaction.telegram_message_id)
            .where(
                Transaction.household_id == household_id,
                Transaction.member_id == member_id,
                Transaction.status == TransactionStatus.CONFIRMED,
            )
            .order_by(Transaction.updated_at.desc(), Transaction.id.desc())
            .limit(1)
        )

    async def set_status(self, transaction: Transaction, status: TransactionStatus) -> Transaction:
        transaction.status = status
        await self.session.flush()
        return transaction

    async def _validate_references(
        self,
        *,
        household_id: int,
        member_id: int,
        beneficiary_member_id: int,
        category_id: int,
        transaction_type: TransactionType,
        telegram_chat_id: int,
    ) -> None:
        household = await self.session.get(Household, household_id)
        member = await self.session.get(Member, member_id)
        beneficiary = await self.session.get(Member, beneficiary_member_id)
        category = await self.session.get(Category, category_id)
        if household is None or household.telegram_chat_id != telegram_chat_id:
            raise InvalidTransactionReferenceError("Telegram chat does not match household")
        if member is None or member.household_id != household_id:
            raise InvalidTransactionReferenceError("Member does not belong to household")
        if beneficiary is None or beneficiary.household_id != household_id:
            raise InvalidTransactionReferenceError("Beneficiary does not belong to household")
        if category is None or category.household_id != household_id:
            raise InvalidTransactionReferenceError("Category is not available to household")
        if category.type != transaction_type:
            raise InvalidTransactionReferenceError("Category type does not match transaction type")


class PlannedExpenseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _options():
        return (
            selectinload(PlannedExpense.category),
            selectinload(PlannedExpense.member),
            selectinload(PlannedExpense.household),
        )

    async def create(
        self,
        *,
        household_id: int,
        member_id: int,
        category_id: int,
        amount: Decimal,
        currency: str,
        description: str,
        due_date: date,
        recurrence: str | None,
        original_text: str,
        telegram_chat_id: int | None,
        telegram_message_id: int | None,
        message_item_index: int = 0,
        status: PlannedExpenseStatus = PlannedExpenseStatus.PENDING,
    ) -> PlannedExpense:
        plan = PlannedExpense(
            household_id=household_id,
            member_id=member_id,
            category_id=category_id,
            amount=amount,
            currency=currency.upper(),
            description=description,
            due_date=due_date,
            recurrence=recurrence,
            original_text=original_text,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            message_item_index=message_item_index,
            status=status,
        )
        self.session.add(plan)
        await self.session.flush()
        plan.category = await self.session.get(Category, category_id)  # type: ignore[assignment]
        return plan

    async def get_for_household(
        self, *, household_id: int, planned_expense_id: int, lock: bool = False
    ) -> PlannedExpense | None:
        statement = (
            select(PlannedExpense)
            .options(*self._options())
            .where(
                PlannedExpense.id == planned_expense_id,
                PlannedExpense.household_id == household_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_active(self, household_id: int) -> list[PlannedExpense]:
        return list(
            await self.session.scalars(
                select(PlannedExpense)
                .options(*self._options())
                .where(
                    PlannedExpense.household_id == household_id,
                    PlannedExpense.status == PlannedExpenseStatus.SCHEDULED,
                )
                .order_by(PlannedExpense.due_date, PlannedExpense.id)
            )
        )

    async def list_for_period(
        self,
        *,
        household_id: int,
        start_date: date,
        end_date: date,
        currency: str,
    ) -> list[PlannedExpense]:
        return list(
            await self.session.scalars(
                select(PlannedExpense)
                .options(*self._options())
                .where(
                    PlannedExpense.household_id == household_id,
                    PlannedExpense.due_date >= start_date,
                    PlannedExpense.due_date < end_date,
                    PlannedExpense.currency == currency.upper(),
                )
                .order_by(PlannedExpense.due_date, PlannedExpense.id)
            )
        )

    async def list_by_telegram_message(
        self, telegram_chat_id: int, telegram_message_id: int
    ) -> list[PlannedExpense]:
        return list(
            await self.session.scalars(
                select(PlannedExpense)
                .options(*self._options())
                .where(
                    PlannedExpense.telegram_chat_id == telegram_chat_id,
                    PlannedExpense.telegram_message_id == telegram_message_id,
                )
                .order_by(PlannedExpense.message_item_index)
            )
        )

    async def list_scheduled(self) -> list[PlannedExpense]:
        return list(
            await self.session.scalars(
                select(PlannedExpense)
                .options(*self._options())
                .where(PlannedExpense.status == PlannedExpenseStatus.SCHEDULED)
                .order_by(PlannedExpense.due_date, PlannedExpense.id)
            )
        )

    async def claim_delivery(self, planned_expense_id: int, kind: ReminderKind) -> bool:
        statement = (
            insert(PlannedExpenseDelivery)
            .values(planned_expense_id=planned_expense_id, kind=kind)
            .on_conflict_do_nothing(constraint="uq_planned_expense_delivery_kind")
            .returning(PlannedExpenseDelivery.id)
        )
        return (await self.session.scalar(statement)) is not None

    async def release_delivery(self, planned_expense_id: int, kind: ReminderKind) -> None:
        await self.session.execute(
            delete(PlannedExpenseDelivery).where(
                PlannedExpenseDelivery.planned_expense_id == planned_expense_id,
                PlannedExpenseDelivery.kind == kind,
            )
        )

    async def clear_deliveries(self, planned_expense_id: int) -> None:
        await self.session.execute(
            delete(PlannedExpenseDelivery).where(
                PlannedExpenseDelivery.planned_expense_id == planned_expense_id
            )
        )


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, budget_id: int) -> Budget | None:
        return await self.session.get(Budget, budget_id)

    async def list_for_period(self, *, household_id: int, year: int, month: int) -> list[Budget]:
        return list(
            await self.session.scalars(
                select(Budget)
                .options(selectinload(Budget.category))
                .where(
                    Budget.household_id == household_id,
                    Budget.year == year,
                    Budget.month == month,
                )
                .order_by(Budget.category_id.asc().nulls_first())
            )
        )

    async def upsert(
        self,
        *,
        household_id: int,
        year: int,
        month: int,
        amount: Decimal,
        category_id: int | None = None,
    ) -> Budget:
        values = {
            "household_id": household_id,
            "year": year,
            "month": month,
            "category_id": category_id,
            "amount": amount,
        }
        statement = insert(Budget).values(**values)
        if category_id is None:
            statement = statement.on_conflict_do_update(
                index_elements=[Budget.household_id, Budget.year, Budget.month],
                index_where=Budget.category_id.is_(None),
                set_={"amount": amount, "updated_at": func.now()},
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[Budget.household_id, Budget.year, Budget.month, Budget.category_id],
                index_where=Budget.category_id.is_not(None),
                set_={"amount": amount, "updated_at": func.now()},
            )
        statement = statement.returning(Budget).execution_options(populate_existing=True)
        return (await self.session.scalars(statement)).one()
