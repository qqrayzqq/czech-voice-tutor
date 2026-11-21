from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class VoiceJob(Base):
    """A voice input awaiting transcription and learner confirmation."""

    __tablename__ = "voice_jobs"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id", "telegram_message_id", name="uq_voice_jobs_telegram_message"
        ),
        CheckConstraint(
            "status IN ('received', 'transcribing', 'awaiting_confirmation', "
            "'awaiting_edit', 'confirmed', 'cancelled', 'failed')",
            name="ck_voice_jobs_status",
        ),
        Index(
            "uq_voice_jobs_active_user",
            "telegram_user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('received', 'transcribing', 'awaiting_confirmation', 'awaiting_edit')"
            ),
            sqlite_where=text(
                "status IN ('received', 'transcribing', 'awaiting_confirmation', 'awaiting_edit')"
            ),
        ),
    )

    card_message_id: Mapped[int | None] = mapped_column(BigInteger)
    card_token: Mapped[str | None] = mapped_column(Text)
    card_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    asr_metadata: Mapped[dict | None] = mapped_column(JSON)
    clarification_count: Mapped[int] = mapped_column(Integer, server_default="0")

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE")
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    telegram_file_id: Mapped[str] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    confirmed_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revision: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    soniox_file_id: Mapped[str | None] = mapped_column(Text)
    soniox_transcription_id: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL"), unique=True
    )
    delivery_token: Mapped[str | None] = mapped_column(Text)
    delivery_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceEditInput(Base):
    """Deduplicate edit messages without adding rejected ASR to conversation history."""

    __tablename__ = "voice_edit_inputs"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("voice_jobs.id", ondelete="CASCADE"), index=True
    )
