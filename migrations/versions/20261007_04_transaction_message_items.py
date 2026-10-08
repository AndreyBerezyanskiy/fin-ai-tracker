"""Allow multiple transactions in one Telegram message.

Revision ID: 20261007_04
Revises: 20261007_03
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_04"
down_revision: str | None = "20261007_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("message_transaction_index", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_transactions_message_transaction_index_valid",
        "transactions",
        "message_transaction_index >= 0",
    )
    op.drop_constraint("uq_transactions_telegram_message", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_transactions_telegram_message_item",
        "transactions",
        ["telegram_chat_id", "telegram_message_id", "message_transaction_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_transactions_telegram_message_item", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_transactions_telegram_message",
        "transactions",
        ["telegram_chat_id", "telegram_message_id"],
    )
    op.drop_constraint(
        "ck_transactions_message_transaction_index_valid", "transactions", type_="check"
    )
    op.drop_column("transactions", "message_transaction_index")
