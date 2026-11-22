"""Start a fresh model context using only a short local transaction."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.persistence.context_reset import reset_history
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.users import upsert_user
from czech_tutor.tutor.errors import StorageError


async def reset_conversation_context(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
    username: str | None,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> str | None:
    """Return the saved UI language for a new reset; skip duplicate commands."""
    try:
        async with session_factory.begin() as session:
            learner = await upsert_user(session, telegram_user_id, username)
            conversation = await get_or_create_conversation(session, telegram_user_id)
            if not await reset_history(
                session, conversation, telegram_user_id, telegram_chat_id, telegram_message_id
            ):
                return None
            return learner.interface_language
    except SQLAlchemyError:
        raise StorageError("Failed to reset conversation context.") from None
