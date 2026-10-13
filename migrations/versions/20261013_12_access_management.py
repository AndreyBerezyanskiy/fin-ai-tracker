"""Add database-backed Telegram access management.

Revision ID: 20261013_12
Revises: 20261013_11
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_12"
down_revision: str | None = "20261013_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("granted_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_access_grants"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_access_grants_telegram_chat_id"),
    )
    op.create_table(
        "access_invitations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_access_invitations"),
        sa.UniqueConstraint("token_digest", name="uq_access_invitations_token_digest"),
    )
    op.create_index(
        "ix_access_invitations_expires_at", "access_invitations", ["expires_at"]
    )
    op.create_table(
        "access_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_access_requests"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_access_requests_telegram_chat_id"),
    )


def downgrade() -> None:
    op.drop_table("access_requests")
    op.drop_index("ix_access_invitations_expires_at", table_name="access_invitations")
    op.drop_table("access_invitations")
    op.drop_table("access_grants")
