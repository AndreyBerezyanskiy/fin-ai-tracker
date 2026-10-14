"""Track AI token usage and estimated cost per household.

Revision ID: 20261014_14
Revises: 20261013_13
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_14"
down_revision: str | None = "20261013_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("request_kind", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("service_tier", sa.String(length=32), nullable=True),
        sa.Column("response_id", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("audio_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("input_price_per_million", sa.Numeric(20, 8), nullable=True),
        sa.Column("cached_input_price_per_million", sa.Numeric(20, 8), nullable=True),
        sa.Column("output_price_per_million", sa.Numeric(20, 8), nullable=True),
        sa.Column("price_per_minute", sa.Numeric(20, 8), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "audio_input_tokens >= 0", name="ck_ai_usage_events_ai_usage_audio_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "cached_input_tokens >= 0",
            name="ck_ai_usage_events_ai_usage_cached_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0", name="ck_ai_usage_events_ai_usage_input_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "output_tokens >= 0", name="ck_ai_usage_events_ai_usage_output_tokens_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_ai_usage_events_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_usage_events"),
        sa.UniqueConstraint("response_id", name="uq_ai_usage_events_response_id"),
    )
    op.create_index(
        op.f("ix_ai_usage_events_household_id"),
        "ai_usage_events",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_usage_events_request_kind"),
        "ai_usage_events",
        ["request_kind"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_events_created_at", "ai_usage_events", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_created_at", table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_request_kind"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_household_id"), table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
