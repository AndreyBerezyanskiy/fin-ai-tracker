"""Add a dedicated communications expense category.

Revision ID: 20261012_10
Revises: 20261012_09
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_10"
down_revision: str | None = "20261012_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
                "code": "communications",
                "name": "Мобільний зв’язок та інтернет",
                "type": "expense",
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    for table in ("transactions", "planned_expenses", "budgets"):
        op.execute(
            sa.text(
                f"UPDATE {table} AS target SET category_id = utilities.id "
                "FROM categories AS communications, categories AS utilities "
                "WHERE target.category_id = communications.id "
                "AND communications.household_id IS NULL "
                "AND communications.code = 'communications' "
                "AND utilities.household_id IS NULL "
                "AND utilities.code = 'utilities'"
            )
        )
    op.execute(
        sa.text(
            "DELETE FROM categories "
            "WHERE household_id IS NULL AND code = 'communications'"
        )
    )
