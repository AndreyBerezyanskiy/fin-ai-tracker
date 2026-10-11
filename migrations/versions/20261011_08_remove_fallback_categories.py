"""Remove fallback categories and preserve their transactions for reassignment.

Revision ID: 20261011_08
Revises: 20261010_07
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261011_08"
down_revision: str | None = "20261010_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transaction_clarifications",
        sa.Column("missing_category_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "transaction_clarifications",
        sa.Column(
            "awaiting_category_name", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.alter_column(
        "transactions", "category_id", existing_type=sa.BigInteger(), nullable=True
    )
    op.execute(
        sa.text(
            "UPDATE transactions SET category_id = NULL "
            "WHERE category_id IN "
            "(SELECT id FROM categories WHERE code IN ('other', 'other_income'))"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM budgets "
            "WHERE category_id IN "
            "(SELECT id FROM categories WHERE code IN ('other', 'other_income'))"
        )
    )
    op.execute(sa.text("DELETE FROM categories WHERE code IN ('other', 'other_income')"))
    transaction_type = postgresql.ENUM(
        "expense", "income", name="transaction_type", create_type=False
    )
    categories = sa.table(
        "categories",
        sa.column("household_id", sa.BigInteger()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("type", transaction_type),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        categories,
        [
            {
                "household_id": None,
                "code": code,
                "name": name,
                "type": kind,
                "is_active": True,
            }
            for code, name, kind in (
                ("education", "Освіта", "expense"),
                ("taxes_documents", "Податки та документи", "expense"),
                ("gifts", "Подарунки", "expense"),
                ("refunds", "Повернення коштів", "income"),
                ("bonus", "Бонуси", "income"),
            )
        ],
    )


def downgrade() -> None:
    new_codes = "('education', 'taxes_documents', 'gifts', 'refunds', 'bonus')"
    op.execute(
        sa.text(
            "UPDATE transactions SET category_id = NULL WHERE category_id IN "
            f"(SELECT id FROM categories WHERE household_id IS NULL AND code IN {new_codes})"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM budgets WHERE category_id IN "
            f"(SELECT id FROM categories WHERE household_id IS NULL AND code IN {new_codes})"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM categories WHERE household_id IS NULL AND code IN "
            f"{new_codes}"
        )
    )
    transaction_type = postgresql.ENUM(
        "expense", "income", name="transaction_type", create_type=False
    )
    categories = sa.table(
        "categories",
        sa.column("household_id", sa.BigInteger()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("type", transaction_type),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        categories,
        [
            {
                "household_id": None,
                "code": "other",
                "name": "Інше",
                "type": "expense",
                "is_active": True,
            },
            {
                "household_id": None,
                "code": "other_income",
                "name": "Інший дохід",
                "type": "income",
                "is_active": True,
            },
        ],
    )
    op.execute(
        sa.text(
            "UPDATE transactions SET category_id = categories.id FROM categories "
            "WHERE transactions.category_id IS NULL AND categories.household_id IS NULL "
            "AND categories.code = CASE WHEN transactions.type = 'expense' "
            "THEN 'other' ELSE 'other_income' END"
        )
    )
    op.alter_column(
        "transactions", "category_id", existing_type=sa.BigInteger(), nullable=False
    )
    op.drop_column("transaction_clarifications", "awaiting_category_name")
    op.drop_column("transaction_clarifications", "missing_category_type")
