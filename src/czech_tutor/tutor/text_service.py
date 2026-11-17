import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.openrouter import generate_reply
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.history import get_recent_turns
from czech_tutor.persistence.messages import (
    mark_generation_failed,
    reserve_user_message,
    save_tutor_reply,
)
from czech_tutor.persistence.users import upsert_user
from czech_tutor.tutor.context import trim_history
from czech_tutor.tutor.errors import LLMError, StorageError
from czech_tutor.tutor.prompts import SYSTEM_PROMPT
from czech_tutor.tutor.schemas import ReplyToSend


async def process_text_message(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    api_key: str,
    telegram_user_id: int,
    username: str | None,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str,
) -> ReplyToSend | None:
    """Process one text message, returning None if it was already reserved."""
    try:
        async with session_factory.begin() as session:
            await upsert_user(session, telegram_user_id, username)
            conversation = await get_or_create_conversation(session, telegram_user_id)
            message_id = await reserve_user_message(
                session,
                conversation.id,
                telegram_chat_id,
                telegram_message_id,
                text,
            )
            if message_id is not None:
                turns = await get_recent_turns(session, conversation.id, message_id)
                history_texts = [(user.text, assistant.text) for user, assistant in turns]
    except SQLAlchemyError as error:
        raise StorageError("Failed to prepare the conversation.") from error

    if message_id is None:
        return None

    history = trim_history(history_texts)

    try:
        result = await generate_reply(client, api_key, text, SYSTEM_PROMPT, history)
    except LLMError:
        try:
            async with session_factory.begin() as session:
                await mark_generation_failed(session, message_id)
        except SQLAlchemyError as error:
            raise StorageError("Failed to record generation failure.") from error
        raise

    try:
        async with session_factory.begin() as session:
            reply = await save_tutor_reply(session, message_id, result)
    except (SQLAlchemyError, ValueError) as error:
        raise StorageError("Failed to save the tutor reply.") from error

    return ReplyToSend(
        reply_id=reply.id,
        reply_cs=result.reply_cs,
        corrections_count=len(result.corrections),
    )
