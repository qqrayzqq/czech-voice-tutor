"""A single paid synthesis and its recoverable Telegram delivery."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class ReplyAudio(Base):
    __tablename__ = "reply_audio"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generating', 'ready', 'failed', 'delivered')", name="ck_reply_audio_status"
        ),
    )
    reply_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(Text, server_default="generating")
    model: Mapped[str] = mapped_column(Text)
    voice: Mapped[str] = mapped_column(Text)
    mp3: Mapped[bytes | None] = mapped_column(LargeBinary)
    telegram_file_id: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivery_token: Mapped[str | None] = mapped_column(Text)
    delivery_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
