"""Create the initial family finance schema.

Revision ID: 20261006_01
Revises:
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261006_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRANSACTION_TYPES = ("expense", "income")
TRANSACTION_STATUSES = ("pending", "confirmed", "cancelled")

EXPENSE_CATEGORIES = (
    ("groceries", "Продукти"),
    ("cafes_restaurants", "Кафе та ресторани"),
    ("transport", "Транспорт"),
    ("car", "Автомобіль"),
    ("housing", "Житло"),
    ("utilities", "Комунальні послуги"),
    ("health", "Здоров’я"),
    ("children", "Діти"),
    ("clothing", "Одяг"),
    ("entertainment", "Розваги"),
    ("travel", "Подорожі"),
    ("subscriptions", "Підписки"),
    ("other", "Інше"),
)


def upgrade() -> None:
    transaction_type = postgresql.ENUM(
        *TRANSACTION_TYPES, name="transaction_type", create_type=False
    )
    transaction_status = postgresql.ENUM(
        *TRANSACTION_STATUSES, name="transaction_status", create_type=False
    )
    transaction_type.create(op.get_bind(), checkfirst=True)
    transaction_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "households",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="Europe/Paris", nullable=False),
        sa.Column("morning_report_time", sa.Time(), nullable=True),
        sa.Column("evening_report_time", sa.Time(), nullable=True),
        sa.Column("reports_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_households"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_households_telegram_chat_id"),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_members_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_members"),
        sa.UniqueConstraint("household_id", "telegram_user_id", name="uq_members_household_user"),
    )
    op.create_index("ix_members_household_id", "members", ["household_id"])

    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", transaction_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_categories_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
    )
    op.create_index("ix_categories_household_id", "categories", ["household_id"])
    op.create_index(
        "uq_categories_system_code",
        "categories",
        ["code"],
        unique=True,
        postgresql_where=sa.text("household_id IS NULL"),
    )
    op.create_index(
        "uq_categories_household_code",
        "categories",
        ["household_id", "code"],
        unique=True,
        postgresql_where=sa.text("household_id IS NOT NULL"),
    )

    categories_table = sa.table(
        "categories",
        sa.column("household_id", sa.BigInteger()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("type", transaction_type),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        categories_table,
        [
            {
                "household_id": None,
                "code": code,
                "name": name,
                "type": "expense",
                "is_active": True,
            }
            for code, name in EXPENSE_CATEGORIES
        ],
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("type", transaction_type, nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("status", transaction_status, server_default="pending", nullable=False),
        sa.Column(
            "ai_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_transactions_category_id_categories",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_transactions_household_id_households",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name="fk_transactions_member_id_members",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_transactions_telegram_message",
        ),
    )
    op.create_index("ix_transactions_category_id", "transactions", ["category_id"])
    op.create_index("ix_transactions_household_id", "transactions", ["household_id"])
    op.create_index("ix_transactions_member_id", "transactions", ["member_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_transaction_date", "transactions", ["transaction_date"])

    op.create_table(
        "budgets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budgets_amount_non_negative"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_budgets_valid_month"),
        sa.CheckConstraint("year >= 2000", name="ck_budgets_valid_year"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_budgets_category_id_categories",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_budgets_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_budgets"),
    )
    op.create_index("ix_budgets_category_id", "budgets", ["category_id"])
    op.create_index("ix_budgets_household_id", "budgets", ["household_id"])
    op.create_index(
        "uq_budgets_household_period_total",
        "budgets",
        ["household_id", "year", "month"],
        unique=True,
        postgresql_where=sa.text("category_id IS NULL"),
    )
    op.create_index(
        "uq_budgets_household_period_category",
        "budgets",
        ["household_id", "year", "month", "category_id"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("budgets")
    op.drop_table("transactions")
    op.drop_table("categories")
    op.drop_table("members")
    op.drop_table("households")
    postgresql.ENUM(name="transaction_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="transaction_type").drop(op.get_bind(), checkfirst=True)
