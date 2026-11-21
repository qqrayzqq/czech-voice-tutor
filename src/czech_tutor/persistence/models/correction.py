from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from czech_tutor.persistence.models.base import Base


class MessageCorrection(Base):
    """A saved correction of a fragment in the original user message."""

    __tablename__ = "corrections"
    __table_args__ = (
        Index("ix_corrections_message_id_id", "message_id", "id"),
        CheckConstraint("kind IN ('error', 'advice')", name="ck_corrections_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE")
    )
    original: Mapped[str] = mapped_column(Text)
    corrected: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="error")
    explanation_ru: Mapped[str] = mapped_column(Text)
