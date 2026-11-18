import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.openrouter import generate_reply
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.history import get_recent_turns
from czech_tutor.persistence.messages import (
    mark_generation_failed,
    reserve_user_message,
)
from czech_tutor.persistence.users import upsert_user
from czech_tutor.tutor.context import trim_history
from czech_tutor.tutor.errors import LLMError, StorageError
from czech_tutor.tutor.prompts import SYSTEM_PROMPT
from czech_tutor.tutor.reply_storage_service import reply_storage
from czech_tutor.tutor.schemas import ReplyToSend

logger = logging.getLogger(__name__)


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
    """Generate and save a reply for one previously unreserved Telegram input.

    The Telegram handler is responsible for access checks. The first short
    transaction prepares the user, conversation, reservation, and history.
    Only a committed new reservation may proceed to generation; a conflict on
    (telegram_chat_id, telegram_message_id) returns None without loading
    history, calling the LLM, or saving or returning a reply. Existing inputs
    are skipped in processing, completed, and failed states alike.

    Generation runs outside database transactions. An LLMError triggers a
    separate transaction to mark the input failed before the error propagates.
    The storage service saves the reply and completed status, retrying only
    recognized connection failures without regenerating the reply. ReplyToSend
    therefore describes a saved reply, not confirmed Telegram delivery;
    sending and recording delivery belong to the caller.

    This workflow does not retry failed inputs, recover stuck processing
    inputs, or resend saved replies. A save failure or interrupted task may
    leave a committed reservation processing. Database failures are exposed
    as StorageError, and reply validation failures during saving are wrapped
    in StorageError as well.
    """
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
        logger.warning(
            "Failed to prepare conversation: telegram_chat_id=%s telegram_message_id=%s",
            telegram_chat_id,
            telegram_message_id,
        )
        raise StorageError("Failed to prepare the conversation.") from error

    if message_id is None:
        return None

    history = trim_history(history_texts)

    try:
        result = await generate_reply(client, api_key, text, SYSTEM_PROMPT, history)
    except LLMError:
        logger.warning("Failed to generate tutor reply: source_message_id=%s", message_id)
        try:
            async with session_factory.begin() as session:
                await mark_generation_failed(session, message_id)
        except SQLAlchemyError as error:
            logger.warning(
                "Failed to record generation failure: source_message_id=%s",
                message_id,
            )
            raise StorageError("Failed to record generation failure.") from error
        raise

    reply = await reply_storage(session_factory, message_id, result)

    return ReplyToSend(
        reply_id=reply.id,
        reply_cs=reply.text,
        corrections_count=len(result.corrections),
        has_note=bool(reply.note_ru and reply.note_ru.strip()),
    )
