"""Add automatic report delivery slots and default report times.

Revision ID: 20261008_06
Revises: 20261007_05
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_06"
down_revision: str | None = "20261007_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE households SET morning_report_time = '08:00:00' WHERE morning_report_time IS NULL"
    )
    op.execute(
        "UPDATE households SET evening_report_time = '20:00:00' WHERE evening_report_time IS NULL"
    )
    op.alter_column(
        "households",
        "morning_report_time",
        existing_type=sa.Time(),
        nullable=False,
        server_default="08:00:00",
    )
    op.alter_column(
        "households",
        "evening_report_time",
        existing_type=sa.Time(),
        nullable=False,
        server_default="20:00:00",
    )

    op.create_table(
        "report_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("report_type", sa.String(length=16), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
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
            name="fk_report_deliveries_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_deliveries"),
        sa.UniqueConstraint(
            "household_id",
            "report_type",
            "local_date",
            name="uq_report_delivery_slot",
        ),
    )
    op.create_index("ix_report_deliveries_household_id", "report_deliveries", ["household_id"])


def downgrade() -> None:
    op.drop_table("report_deliveries")
    op.alter_column(
        "households",
        "evening_report_time",
        existing_type=sa.Time(),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "households",
        "morning_report_time",
        existing_type=sa.Time(),
        nullable=True,
        server_default=None,
    )
