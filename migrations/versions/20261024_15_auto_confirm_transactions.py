"""Add automatic transaction confirmation settings.

Revision ID: 20261024_15
Revises: 20261014_14
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_15"
down_revision: str | None = "20261014_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column(
            "auto_confirm_transactions",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "transaction_clarifications",
        sa.Column("replacement_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction_clarifications", "replacement_message_id")
    op.drop_column("households", "auto_confirm_transactions")
