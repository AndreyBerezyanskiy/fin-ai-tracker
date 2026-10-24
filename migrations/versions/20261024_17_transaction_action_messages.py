"""Track temporary transaction action keyboards.

Revision ID: 20261024_17
Revises: 20261024_16
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_17"
down_revision: str | None = "20261024_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_action_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            name="fk_transaction_action_messages_household_id_households",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transaction_action_messages"),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_transaction_action_message",
        ),
    )
    op.create_index(
        op.f("ix_transaction_action_messages_household_id"),
        "transaction_action_messages",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transaction_action_messages_expires_at"),
        "transaction_action_messages",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_transaction_action_messages_expires_at"),
        table_name="transaction_action_messages",
    )
    op.drop_index(
        op.f("ix_transaction_action_messages_household_id"),
        table_name="transaction_action_messages",
    )
    op.drop_table("transaction_action_messages")
