"""Add planned expenses and reminder deliveries.

Revision ID: 20261012_09
Revises: 20261011_08
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_09"
down_revision: str | None = "20261011_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending",
        "scheduled",
        "paid",
        "cancelled",
        name="planned_expense_status",
        create_type=False,
    )
    recurrence = postgresql.ENUM("monthly", name="planned_expense_recurrence", create_type=False)
    reminder = postgresql.ENUM(
        "day_before",
        "due",
        "overdue",
        name="planned_expense_reminder_kind",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    recurrence.create(op.get_bind(), checkfirst=True)
    reminder.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "transaction_clarifications",
        sa.Column("kind", sa.String(32), server_default="transaction", nullable=False),
    )
    op.add_column(
        "transaction_clarifications",
        sa.Column("planned_expense_id", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "planned_expenses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", status, server_default="pending", nullable=False),
        sa.Column("recurrence", recurrence, nullable=True),
        sa.Column("transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("original_text", sa.Text(), server_default="", nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("message_item_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount > 0", name="ck_planned_expenses_amount_positive"),
        sa.CheckConstraint(
            "message_item_index >= 0", name="ck_planned_expenses_message_item_index_valid"
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_planned_expenses"),
        sa.UniqueConstraint("transaction_id", name="uq_planned_expenses_transaction_id"),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "message_item_index",
            name="uq_planned_expenses_telegram_message_item",
        ),
    )
    for column in ("household_id", "member_id", "category_id", "due_date", "status"):
        op.create_index(f"ix_planned_expenses_{column}", "planned_expenses", [column])
    op.create_table(
        "planned_expense_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("planned_expense_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", reminder, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["planned_expense_id"], ["planned_expenses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planned_expense_deliveries"),
        sa.UniqueConstraint("planned_expense_id", "kind", name="uq_planned_expense_delivery_kind"),
    )
    op.create_index(
        "ix_planned_expense_deliveries_planned_expense_id",
        "planned_expense_deliveries",
        ["planned_expense_id"],
    )
    op.create_foreign_key(
        "fk_tx_clarifications_planned_expense",
        "transaction_clarifications",
        "planned_expenses",
        ["planned_expense_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tx_clarifications_planned_expense",
        "transaction_clarifications",
        type_="foreignkey",
    )
    op.drop_column("transaction_clarifications", "planned_expense_id")
    op.drop_column("transaction_clarifications", "kind")
    op.drop_table("planned_expense_deliveries")
    op.drop_table("planned_expenses")
    sa.Enum(name="planned_expense_reminder_kind").drop(op.get_bind())
    sa.Enum(name="planned_expense_recurrence").drop(op.get_bind())
    sa.Enum(name="planned_expense_status").drop(op.get_bind())
