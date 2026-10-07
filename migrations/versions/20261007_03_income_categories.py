"""Add system income categories used by transaction recognition.

Revision ID: 20261007_03
Revises: 20261007_02
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261007_03"
down_revision: str | None = "20261007_02"
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
                "code": "salary",
                "name": "Зарплата",
                "type": "income",
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


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM categories "
            "WHERE household_id IS NULL AND code IN ('salary', 'other_income')"
        )
    )
