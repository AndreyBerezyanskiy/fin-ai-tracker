from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.enums import (
    PlannedExpenseRecurrence,
    PlannedExpenseStatus,
    ReminderKind,
    ReportType,
    TransactionStatus,
    TransactionType,
)
from infrastructure.database.base import Base, TimestampMixin

transaction_type_enum = Enum(
    TransactionType,
    name="transaction_type",
    values_callable=lambda enum: [item.value for item in enum],
)
transaction_status_enum = Enum(
    TransactionStatus,
    name="transaction_status",
    values_callable=lambda enum: [item.value for item in enum],
)


class Household(TimestampMixin, Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Europe/Paris", server_default="Europe/Paris"
    )
    morning_report_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(8), server_default="08:00:00"
    )
    evening_report_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(20), server_default="20:00:00"
    )
    reports_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    auto_confirm_transactions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    members: Mapped[list[Member]] = relationship(back_populates="household")
    categories: Mapped[list[Category]] = relationship(back_populates="household")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="household")
    budgets: Mapped[list[Budget]] = relationship(back_populates="household")
    planned_expenses: Mapped[list[PlannedExpense]] = relationship(back_populates="household")


class ReportDelivery(TimestampMixin, Base):
    __tablename__ = "report_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "household_id", "report_type", "local_date", name="uq_report_delivery_slot"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)

    @property
    def type(self) -> ReportType:
        return ReportType(self.report_type)


class Member(TimestampMixin, Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("household_id", "telegram_user_id", name="uq_members_household_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    household: Mapped[Household] = relationship(back_populates="members")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="member", foreign_keys="Transaction.member_id"
    )
    beneficiary_transactions: Mapped[list[Transaction]] = relationship(
        back_populates="beneficiary", foreign_keys="Transaction.beneficiary_member_id"
    )


class MemberAlias(TimestampMixin, Base):
    __tablename__ = "member_aliases"
    __table_args__ = (
        UniqueConstraint("household_id", "normalized_alias", name="uq_member_alias_household"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)


class TransactionClarification(TimestampMixin, Base):
    __tablename__ = "transaction_clarifications"
    __table_args__ = (
        UniqueConstraint(
            "household_id", "member_id", name="uq_transaction_clarification_member"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ambiguous_member_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suggested_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    missing_category_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    awaiting_category_name: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="transaction", server_default="transaction"
    )
    planned_expense_id: Mapped[int | None] = mapped_column(
        ForeignKey("planned_expenses.id", ondelete="SET NULL"), nullable=True
    )
    replacement_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ProcessedTelegramUpdate(Base):
    __tablename__ = "processed_telegram_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


class AccessGrant(TimestampMixin, Base):
    __tablename__ = "access_grants"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granted_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class AccessInvitation(TimestampMixin, Base):
    __tablename__ = "access_invitations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AccessRequest(TimestampMixin, Base):
    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    requester_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    reviewed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FeatureFlag(TimestampMixin, Base):
    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AIUsageEvent(TimestampMixin, Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_events_created_at", "created_at"),
        UniqueConstraint("response_id", name="uq_ai_usage_events_response_id"),
        CheckConstraint("input_tokens >= 0", name="ai_usage_input_tokens_non_negative"),
        CheckConstraint(
            "cached_input_tokens >= 0", name="ai_usage_cached_tokens_non_negative"
        ),
        CheckConstraint("output_tokens >= 0", name="ai_usage_output_tokens_non_negative"),
        CheckConstraint(
            "audio_input_tokens >= 0", name="ai_usage_audio_tokens_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    service_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    audio_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    input_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    cached_input_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    output_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    price_per_minute: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (Index("uq_categories_household_code", "household_id", "code", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[TransactionType] = mapped_column(transaction_type_enum, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    household: Mapped[Household] = relationship(back_populates="categories")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="category")
    budgets: Mapped[list[Budget]] = relationship(back_populates="category")


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "message_transaction_index",
            name="uq_transactions_telegram_message_item",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("message_transaction_index >= 0", name="message_transaction_index_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    beneficiary_member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    type: Mapped[TransactionType] = mapped_column(transaction_type_enum, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_transaction_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[TransactionStatus] = mapped_column(
        transaction_status_enum,
        nullable=False,
        default=TransactionStatus.PENDING,
        server_default=TransactionStatus.PENDING.value,
        index=True,
    )
    ai_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    household: Mapped[Household] = relationship(back_populates="transactions")
    member: Mapped[Member] = relationship(back_populates="transactions", foreign_keys=[member_id])
    beneficiary: Mapped[Member] = relationship(
        back_populates="beneficiary_transactions", foreign_keys=[beneficiary_member_id]
    )
    category: Mapped[Category | None] = relationship(back_populates="transactions")


class PlannedExpense(TimestampMixin, Base):
    __tablename__ = "planned_expenses"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "message_item_index",
            name="uq_planned_expenses_telegram_message_item",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("message_item_index >= 0", name="message_item_index_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[PlannedExpenseStatus] = mapped_column(
        Enum(
            PlannedExpenseStatus,
            name="planned_expense_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=PlannedExpenseStatus.PENDING,
        server_default=PlannedExpenseStatus.PENDING.value,
        index=True,
    )
    recurrence: Mapped[PlannedExpenseRecurrence | None] = mapped_column(
        Enum(
            PlannedExpenseRecurrence,
            name="planned_expense_recurrence",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=True,
    )
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_item_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    household: Mapped[Household] = relationship(back_populates="planned_expenses")
    member: Mapped[Member] = relationship()
    category: Mapped[Category] = relationship()
    transaction: Mapped[Transaction | None] = relationship()
    deliveries: Mapped[list[PlannedExpenseDelivery]] = relationship(
        back_populates="planned_expense", cascade="all, delete-orphan"
    )


class PlannedExpenseDelivery(TimestampMixin, Base):
    __tablename__ = "planned_expense_deliveries"
    __table_args__ = (
        UniqueConstraint("planned_expense_id", "kind", name="uq_planned_expense_delivery_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    planned_expense_id: Mapped[int] = mapped_column(
        ForeignKey("planned_expenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ReminderKind] = mapped_column(
        Enum(
            ReminderKind,
            name="planned_expense_reminder_kind",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )

    planned_expense: Mapped[PlannedExpense] = relationship(back_populates="deliveries")


class Budget(TimestampMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint("year >= 2000", name="valid_year"),
        CheckConstraint("month BETWEEN 1 AND 12", name="valid_month"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index(
            "uq_budgets_household_period_total",
            "household_id",
            "year",
            "month",
            unique=True,
            postgresql_where=text("category_id IS NULL"),
        ),
        Index(
            "uq_budgets_household_period_category",
            "household_id",
            "year",
            "month",
            "category_id",
            unique=True,
            postgresql_where=text("category_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    household: Mapped[Household] = relationship(back_populates="budgets")
    category: Mapped[Category | None] = relationship(back_populates="budgets")


__all__ = [
    "AccessGrant",
    "AccessInvitation",
    "AccessRequest",
    "Budget",
    "Category",
    "Household",
    "Member",
    "MemberAlias",
    "PlannedExpense",
    "PlannedExpenseDelivery",
    "ProcessedTelegramUpdate",
    "ReportDelivery",
    "Transaction",
    "TransactionClarification",
]
