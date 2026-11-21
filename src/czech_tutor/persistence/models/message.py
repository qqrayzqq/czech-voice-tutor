from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class Message(Base):
    """A user input or a saved assistant reply in a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("input_revision >= 0", name="ck_messages_input_revision"),
        CheckConstraint(
            "feedback_status IN ('evaluated', 'not_evaluated', 'clarification', 'superseded')",
            name="ck_messages_feedback_status",
        ),
        CheckConstraint("feedback_language IN ('ru', 'uk')", name="ck_messages_feedback_language"),
        CheckConstraint(
            "notice_state IN ('pending', 'sending', 'sent', 'skipped')",
            name="ck_messages_notice_state",
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        CheckConstraint(
            "generation_status IN ('processing', 'completed', 'failed')",
            name="ck_messages_generation_status",
        ),
        # A sent Telegram message has both identifiers; an unsent reply has neither.
        CheckConstraint(
            "(telegram_chat_id IS NULL AND telegram_message_id IS NULL) OR "
            "(telegram_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL)",
            name="ck_messages_telegram_ids",
        ),
        # Input rows own generation status; reply rows reference their input.
        CheckConstraint(
            "(role = 'user' AND reply_to_message_id IS NULL AND note_ru IS NULL "
            "AND generation_status IS NOT NULL AND telegram_chat_id IS NOT NULL "
            "AND telegram_message_id IS NOT NULL) OR "
            "(role = 'assistant' AND reply_to_message_id IS NOT NULL "
            "AND generation_status IS NULL)",
            name="ck_messages_role_fields",
        ),
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "input_revision",
            name="uq_messages_telegram_message",
        ),
        Index("ix_messages_conversation_created_id", "conversation_id", "created_at", "id"),
    )

    input_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    feedback_status: Mapped[str] = mapped_column(Text, server_default="evaluated")
    feedback_language: Mapped[str] = mapped_column(Text, server_default="ru")
    notice_state: Mapped[str] = mapped_column(Text, server_default="pending")
    notice_message_id: Mapped[int | None] = mapped_column(BigInteger)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    note_ru: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    generation_status: Mapped[str | None] = mapped_column(Text)
