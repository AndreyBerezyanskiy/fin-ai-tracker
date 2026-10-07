from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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

from domain.enums import TransactionStatus, TransactionType
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
    morning_report_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    evening_report_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reports_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    members: Mapped[list[Member]] = relationship(back_populates="household")
    categories: Mapped[list[Category]] = relationship(back_populates="household")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="household")
    budgets: Mapped[list[Budget]] = relationship(back_populates="household")


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
    transactions: Mapped[list[Transaction]] = relationship(back_populates="member")


class ProcessedTelegramUpdate(Base):
    __tablename__ = "processed_telegram_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index(
            "uq_categories_system_code",
            "code",
            unique=True,
            postgresql_where=text("household_id IS NULL"),
        ),
        Index(
            "uq_categories_household_code",
            "household_id",
            "code",
            unique=True,
            postgresql_where=text("household_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[TransactionType] = mapped_column(transaction_type_enum, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    household: Mapped[Household | None] = relationship(back_populates="categories")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="category")
    budgets: Mapped[list[Budget]] = relationship(back_populates="category")


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id", "telegram_message_id", name="uq_transactions_telegram_message"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
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
    member: Mapped[Member] = relationship(back_populates="transactions")
    category: Mapped[Category] = relationship(back_populates="transactions")


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
    "Budget",
    "Category",
    "Household",
    "Member",
    "ProcessedTelegramUpdate",
    "Transaction",
]
