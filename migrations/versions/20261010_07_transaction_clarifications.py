"""Persist transaction clarifications and confirmed member aliases.

Revision ID: 20261010_07
Revises: 20261008_06
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_07"
down_revision: str | None = "20261008_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_aliases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_member_aliases"),
        sa.UniqueConstraint("household_id", "normalized_alias", name="uq_member_alias_household"),
    )
    op.create_index("ix_member_aliases_household_id", "member_aliases", ["household_id"])
    op.create_index("ix_member_aliases_member_id", "member_aliases", ["member_id"])

    op.create_table(
        "transaction_clarifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("original_message_id", sa.BigInteger(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("question", sa.String(length=500), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ambiguous_member_name", sa.String(length=255), nullable=True),
        sa.Column("suggested_member_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_transaction_clarifications_attempts_non_negative"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suggested_member_id"], ["members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_transaction_clarifications"),
        sa.UniqueConstraint(
            "household_id", "member_id", name="uq_transaction_clarification_member"
        ),
    )
    op.create_index(
        "ix_transaction_clarifications_household_id",
        "transaction_clarifications",
        ["household_id"],
    )
    op.create_index(
        "ix_transaction_clarifications_member_id",
        "transaction_clarifications",
        ["member_id"],
    )
    op.create_index(
        "ix_transaction_clarifications_expires_at",
        "transaction_clarifications",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("transaction_clarifications")
    op.drop_table("member_aliases")
