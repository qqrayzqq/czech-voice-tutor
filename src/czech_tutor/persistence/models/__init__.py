"""Import all models here so Alembic can discover the complete schema."""

from czech_tutor.persistence.models.base import Base
from czech_tutor.persistence.models.conversation import Conversation
from czech_tutor.persistence.models.correction import MessageCorrection
from czech_tutor.persistence.models.message import Message
from czech_tutor.persistence.models.user import User

__all__ = ["Base", "Conversation", "Message", "MessageCorrection", "User"]
