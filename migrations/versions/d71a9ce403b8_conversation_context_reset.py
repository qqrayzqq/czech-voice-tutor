"""Add a non-destructive conversation history boundary.

Revision ID: d71a9ce403b8
Revises: c9427f6a21d0
"""

import sqlalchemy as sa
from alembic import op

revision = "d71a9ce403b8"
down_revision = "c9427f6a21d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("history_reset_after_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_conversations_history_reset_after_id", "conversations", "history_reset_after_id >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_conversations_history_reset_after_id", "conversations", type_="check")
    op.drop_column("conversations", "history_reset_after_id")
