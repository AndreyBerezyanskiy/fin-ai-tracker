"""Store the member a transaction belongs to.

Revision ID: 20261007_05
Revises: 20261007_04
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_05"
down_revision: str | None = "20261007_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("beneficiary_member_id", sa.BigInteger()))
    op.execute("UPDATE transactions SET beneficiary_member_id = member_id")
    op.alter_column("transactions", "beneficiary_member_id", nullable=False)
    op.create_foreign_key(
        "fk_transactions_beneficiary_member_id_members",
        "transactions",
        "members",
        ["beneficiary_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_transactions_beneficiary_member_id",
        "transactions",
        ["beneficiary_member_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_beneficiary_member_id", table_name="transactions")
    op.drop_constraint(
        "fk_transactions_beneficiary_member_id_members",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "beneficiary_member_id")
