from sqlalchemy import BigInteger, CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class User(Base):
    """Telegram identity and the learner's conversation preferences."""

    __tablename__ = "users"
    __table_args__ = (
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
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(Text)
    language_level: Mapped[str | None] = mapped_column(Text)
    response_mode: Mapped[str] = mapped_column(Text, server_default="text")
    answer_length: Mapped[str] = mapped_column(Text, server_default="short")
