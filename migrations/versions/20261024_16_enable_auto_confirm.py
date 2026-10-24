"""Enable automatic transaction confirmation by default.

Revision ID: 20261024_16
Revises: 20261024_15
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_16"
down_revision: str | None = "20261024_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "households",
        "auto_confirm_transactions",
        server_default=sa.text("true"),
    )
    op.execute(sa.text("UPDATE households SET auto_confirm_transactions = true"))


def downgrade() -> None:
    op.alter_column(
        "households",
        "auto_confirm_transactions",
        server_default=sa.text("false"),
    )
