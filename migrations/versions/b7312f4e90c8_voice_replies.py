"""Add recoverable speech replies and enable voice output by default.

Revision ID: b7312f4e90c8
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "b7312f4e90c8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_audio",
        sa.Column(
            "reply_id",
            sa.BigInteger(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="generating"),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("voice", sa.Text(), nullable=False),
        sa.Column("mp3", sa.LargeBinary()),
        sa.Column("telegram_file_id", sa.Text()),
        sa.Column("telegram_chat_id", sa.BigInteger()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("delivery_token", sa.Text()),
        sa.Column("delivery_lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'ready', 'failed', 'delivered')", name="ck_reply_audio_status"
        ),
    )
    # Before this feature response_mode was not user-editable: all old text
    # values represent the previous default rather than explicit preferences.
    op.execute("UPDATE users SET response_mode = 'voice' WHERE response_mode = 'text'")
    op.alter_column("users", "response_mode", server_default="voice")


def downgrade() -> None:
    op.alter_column("users", "response_mode", server_default="text")
    op.execute("UPDATE users SET response_mode = 'text' WHERE response_mode = 'voice'")
    op.drop_table("reply_audio")
