from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class User(Base):
    """Telegram identity and the learner's conversation preferences."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("interface_language IN ('ru', 'uk')", name="ck_users_interface_language"),
        CheckConstraint(
            "language_level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name="ck_users_language_level",
        ),
        CheckConstraint(
            "response_mode IN ('text', 'voice')",
            name="ck_users_response_mode",
        ),
        CheckConstraint(
            "answer_length IN ('short', 'medium', 'long')",
            name="ck_users_answer_length",
        ),
        CheckConstraint(
            "(processing_token IS NULL) = (processing_lease_until IS NULL)",
            name="ck_users_processing_lease",
        ),
    )
    interface_language: Mapped[str] = mapped_column(Text, server_default="ru")

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(Text)
    language_level: Mapped[str | None] = mapped_column(Text)
    response_mode: Mapped[str] = mapped_column(Text, server_default="voice")
    answer_length: Mapped[str] = mapped_column(Text, server_default="short")
    processing_token: Mapped[str | None] = mapped_column(Text)
    processing_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
