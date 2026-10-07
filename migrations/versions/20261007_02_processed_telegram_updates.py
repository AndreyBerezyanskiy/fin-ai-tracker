"""Track handled Telegram updates.

Revision ID: 20261007_02
Revises: 20261006_01
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_02"
down_revision: str | None = "20261006_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_telegram_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint("update_id", name="pk_processed_telegram_updates"),
    )


def downgrade() -> None:
    op.drop_table("processed_telegram_updates")
