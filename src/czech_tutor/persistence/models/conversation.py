from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, func
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class Conversation(Base):
    """One continuing conversation per Telegram user."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "history_reset_after_id >= 0", name="ck_conversations_history_reset_after_id"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    history_reset_after_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
