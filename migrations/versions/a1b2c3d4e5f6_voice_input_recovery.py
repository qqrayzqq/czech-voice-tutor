"""Persist voice editing, ASR recovery and delivery claims.

Revision ID: a1b2c3d4e5f6
Revises: fffd62c31a76
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "fffd62c31a76"
branch_labels = None
depends_on = None

ACTIVE = "status IN ('received', 'transcribing', 'awaiting_confirmation', 'awaiting_edit')"
STATUSES = "'received', 'transcribing', 'awaiting_confirmation', 'confirmed', 'cancelled', 'failed'"


def upgrade() -> None:
    op.drop_constraint("ck_voice_jobs_status", "voice_jobs", type_="check")
    op.create_check_constraint(
        "ck_voice_jobs_status", "voice_jobs", f"status IN ({STATUSES}, 'awaiting_edit')"
    )
    for column in (
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("soniox_file_id", sa.Text()),
        sa.Column("soniox_transcription_id", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("source_message_id", sa.BigInteger()),
        sa.Column("delivery_token", sa.Text()),
        sa.Column("delivery_lease_until", sa.DateTime(timezone=True)),
    ):
        op.add_column("voice_jobs", column)
    op.execute(
        sa.text("""
        UPDATE voice_jobs AS v SET source_message_id=m.id FROM messages AS m
        WHERE v.status='confirmed' AND m.role='user'
          AND m.telegram_chat_id=v.telegram_chat_id
          AND m.telegram_message_id=v.telegram_message_id
    """)
    )
    op.create_foreign_key(
        "fk_voice_jobs_source_message",
        "voice_jobs",
        "messages",
        ["source_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_voice_jobs_source_message", "voice_jobs", ["source_message_id"])
    # Preserve the newest active job before installing the database invariant.
    op.execute(
        sa.text(f"""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY telegram_user_id ORDER BY created_at DESC, id DESC
            ) AS position FROM voice_jobs WHERE {ACTIVE}
        ) UPDATE voice_jobs SET status='cancelled'
          WHERE id IN (SELECT id FROM ranked WHERE position > 1)
    """)
    )
    op.create_index(
        "uq_voice_jobs_active_user",
        "voice_jobs",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_table(
        "voice_edit_inputs",
        sa.Column("telegram_chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_message_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("voice_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_voice_edit_inputs_job_id", "voice_edit_inputs", ["job_id"])


def downgrade() -> None:
    op.drop_table("voice_edit_inputs")
    op.drop_index("uq_voice_jobs_active_user", table_name="voice_jobs")
    op.execute("UPDATE voice_jobs SET status='awaiting_confirmation' WHERE status='awaiting_edit'")
    op.drop_constraint("uq_voice_jobs_source_message", "voice_jobs", type_="unique")
    op.drop_constraint("fk_voice_jobs_source_message", "voice_jobs", type_="foreignkey")
    for name in (
        "revision",
        "expires_at",
        "soniox_file_id",
        "soniox_transcription_id",
        "failure_reason",
        "source_message_id",
        "delivery_token",
        "delivery_lease_until",
    ):
        op.drop_column("voice_jobs", name)
    op.drop_constraint("ck_voice_jobs_status", "voice_jobs", type_="check")
    op.create_check_constraint("ck_voice_jobs_status", "voice_jobs", f"status IN ({STATUSES})")
