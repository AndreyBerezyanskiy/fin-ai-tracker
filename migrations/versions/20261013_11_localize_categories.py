"""Make every category belong to exactly one household.

Revision ID: 20261013_11
Revises: 20261012_10
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261013_11"
down_revision: str | None = "20261012_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SYSTEM_CATEGORIES = (
    ("groceries", "Продукти", "expense"),
    ("cafes_restaurants", "Кафе та ресторани", "expense"),
    ("transport", "Транспорт", "expense"),
    ("car", "Автомобіль", "expense"),
    ("housing", "Житло", "expense"),
    ("utilities", "Комунальні послуги", "expense"),
    ("communications", "Мобільний зв’язок та інтернет", "expense"),
    ("health", "Здоров’я", "expense"),
    ("children", "Діти", "expense"),
    ("education", "Освіта", "expense"),
    ("clothing", "Одяг", "expense"),
    ("entertainment", "Розваги", "expense"),
    ("travel", "Подорожі", "expense"),
    ("subscriptions", "Підписки", "expense"),
    ("taxes_documents", "Податки та документи", "expense"),
    ("gifts", "Подарунки", "expense"),
    ("salary", "Зарплата", "income"),
    ("refunds", "Повернення коштів", "income"),
    ("bonus", "Бонуси", "income"),
)


def upgrade() -> None:
    # Materialize only global categories that a household has actually used.
    op.execute(
        sa.text(
            """
            INSERT INTO categories (household_id, code, name, type, is_active)
            SELECT used.household_id, global.code, global.name, global.type, global.is_active
            FROM (
                SELECT transactions.household_id, transactions.category_id FROM transactions
                WHERE transactions.category_id IS NOT NULL
                UNION
                SELECT planned_expenses.household_id, planned_expenses.category_id
                FROM planned_expenses
                UNION
                SELECT budgets.household_id, budgets.category_id FROM budgets
                WHERE budgets.category_id IS NOT NULL
            ) AS used
            JOIN categories AS global ON global.id = used.category_id
            WHERE global.household_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM categories AS local
                  WHERE local.household_id = used.household_id
                    AND local.code = global.code
              )
            ON CONFLICT DO NOTHING
            """
        )
    )

    for table in ("transactions", "planned_expenses", "budgets"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS target
                SET category_id = local.id
                FROM categories AS global, categories AS local
                WHERE target.category_id = global.id
                  AND global.household_id IS NULL
                  AND local.household_id = target.household_id
                  AND local.code = global.code
                """
            )
        )

    op.execute(sa.text("DELETE FROM categories WHERE household_id IS NULL"))
    op.drop_index("uq_categories_system_code", table_name="categories")
    op.drop_index("uq_categories_household_code", table_name="categories")
    op.alter_column("categories", "household_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_index(
        "uq_categories_household_code",
        "categories",
        ["household_id", "code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_categories_household_code", table_name="categories")
    op.alter_column("categories", "household_id", existing_type=sa.BigInteger(), nullable=True)
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
            for code, name, kind in SYSTEM_CATEGORIES
        ],
    )
    system_codes = ", ".join(f"'{code}'" for code, _, _ in SYSTEM_CATEGORIES)
    for table in ("transactions", "planned_expenses", "budgets"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS target
                SET category_id = global.id
                FROM categories AS local, categories AS global
                WHERE target.category_id = local.id
                  AND local.household_id = target.household_id
                  AND local.code IN ({system_codes})
                  AND global.household_id IS NULL
                  AND global.code = local.code
                """
            )
        )
