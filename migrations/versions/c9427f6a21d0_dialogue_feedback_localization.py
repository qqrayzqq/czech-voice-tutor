"""Persist localized assessment, voice repair revisions and presentation receipts.

Revision ID: c9427f6a21d0
Revises: b7312f4e90c8
"""

import sqlalchemy as sa
from alembic import op

revision = "c9427f6a21d0"
down_revision = "b7312f4e90c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("interface_language", sa.Text(), nullable=False, server_default="ru")
    )
    op.create_check_constraint(
        "ck_users_interface_language", "users", "interface_language IN ('ru', 'uk')"
    )
    op.add_column(
        "messages", sa.Column("input_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "messages",
        sa.Column("feedback_status", sa.Text(), nullable=False, server_default="evaluated"),
    )
    op.add_column(
        "messages", sa.Column("feedback_language", sa.Text(), nullable=False, server_default="ru")
    )
    op.add_column(
        "messages", sa.Column("notice_state", sa.Text(), nullable=False, server_default="pending")
    )
    op.add_column("messages", sa.Column("notice_message_id", sa.BigInteger()))
    op.create_check_constraint("ck_messages_input_revision", "messages", "input_revision >= 0")
    op.create_check_constraint(
        "ck_messages_feedback_status",
        "messages",
        "feedback_status IN ('evaluated', 'not_evaluated', 'clarification', 'superseded')",
    )
    op.create_check_constraint(
        "ck_messages_feedback_language", "messages", "feedback_language IN ('ru', 'uk')"
    )
    op.create_check_constraint(
        "ck_messages_notice_state",
        "messages",
        "notice_state IN ('pending', 'sending', 'sent', 'skipped')",
    )
    # Existing IDs remain revision zero. Deliberate manual repairs reserve their
    # own revision without paying again for the same Telegram input/revision.
    op.drop_constraint("uq_messages_telegram_message", "messages", type_="unique")
    op.create_unique_constraint(
        "uq_messages_telegram_message",
        "messages",
        ["telegram_chat_id", "telegram_message_id", "input_revision"],
    )
    op.add_column(
        "corrections", sa.Column("kind", sa.Text(), nullable=False, server_default="error")
    )
    op.create_check_constraint("ck_corrections_kind", "corrections", "kind IN ('error', 'advice')")
    op.add_column("voice_jobs", sa.Column("card_message_id", sa.BigInteger()))
    op.add_column("voice_jobs", sa.Column("card_token", sa.Text()))
    op.add_column("voice_jobs", sa.Column("card_lease_until", sa.DateTime(timezone=True)))
    op.add_column("voice_jobs", sa.Column("asr_metadata", sa.JSON()))
    op.add_column(
        "voice_jobs",
        sa.Column("clarification_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Do not retrospectively send notices for already delivered pre-upgrade replies.
    op.execute(
        "UPDATE messages SET notice_state = 'skipped' "
        "WHERE role = 'assistant' AND telegram_message_id IS NOT NULL"
    )


def downgrade() -> None:
    # Downgrade cannot safely collapse deliberate repairs with the same original
    # Telegram identity. Fail explicitly rather than discard conversation data.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM messages WHERE input_revision <> 0) "
        "THEN RAISE EXCEPTION 'Cannot downgrade after manually revised inputs'; "
        "END IF; END $$"
    )
    for name in (
        "clarification_count",
        "asr_metadata",
        "card_lease_until",
        "card_token",
        "card_message_id",
    ):
        op.drop_column("voice_jobs", name)
    op.drop_constraint("ck_corrections_kind", "corrections", type_="check")
    op.drop_column("corrections", "kind")
    op.drop_constraint("uq_messages_telegram_message", "messages", type_="unique")
    op.create_unique_constraint(
        "uq_messages_telegram_message", "messages", ["telegram_chat_id", "telegram_message_id"]
    )
    for name in (
        "ck_messages_notice_state",
        "ck_messages_feedback_language",
        "ck_messages_feedback_status",
        "ck_messages_input_revision",
    ):
        op.drop_constraint(name, "messages", type_="check")
    for name in (
        "notice_message_id",
        "notice_state",
        "feedback_language",
        "feedback_status",
        "input_revision",
    ):
        op.drop_column("messages", name)
    op.drop_constraint("ck_users_interface_language", "users", type_="check")
    op.drop_column("users", "interface_language")
