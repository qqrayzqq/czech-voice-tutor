import asyncio
import logging

from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.persistence.messages import get_tutor_reply, save_tutor_reply
from czech_tutor.persistence.models import Message
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.schemas import TutorReply

logger = logging.getLogger(__name__)


async def reply_storage(
    session_factory: async_sessionmaker[AsyncSession],
    source_message_id: int,
    reply: TutorReply,
    *,
    replacement_target_id: int | None = None,
) -> Message:
    """Persist one generated reply with at most three storage attempts.

    Only a connection invalidation or pool timeout is retried. Each attempt
    uses a fresh session and transaction. The same generated reply is reused;
    this service never calls the LLM or Telegram. A lost commit confirmation
    is reconciled by the idempotent save or the final
    read-only lookup. Recovery does not survive process termination.

    An explicit replacement_target_id is the visible context snapshot taken
    before generation. Zero means no visible target; None retains the legacy
    standalone lookup. Preserve that exact value across all storage attempts.
    """
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with session_factory.begin() as session:
                if replacement_target_id is None:
                    saved = await save_tutor_reply(session, source_message_id, reply)
                else:
                    saved = await save_tutor_reply(
                        session,
                        source_message_id,
                        reply,
                        replacement_target_id=replacement_target_id,
                    )
            return saved
        except (SQLAlchemyError, ValueError) as error:
            # SQLAlchemy explicitly reports a broken connection or pool timeout.
            retryable = isinstance(error, SQLAlchemyTimeoutError) or (
                isinstance(error, DBAPIError) and error.connection_invalidated
            )
            if not retryable:
                logger.warning(
                    "Failed to save tutor reply: source_message_id=%s attempt=%s",
                    source_message_id,
                    attempt + 1,
                )
                raise StorageError("Failed to save tutor reply.") from error

            last_error = error
            if attempt < 2:
                logger.warning(
                    "Retrying tutor reply storage: source_message_id=%s "
                    "failed_attempt=%s next_attempt=%s",
                    source_message_id,
                    attempt + 1,
                    attempt + 2,
                )
                await asyncio.sleep(0.25 * 2**attempt)

    logger.warning(
        "Tutor reply storage attempts exhausted: source_message_id=%s attempts=3",
        source_message_id,
    )
    try:
        async with session_factory.begin() as session:
            saved = await get_tutor_reply(session, source_message_id)
    except SQLAlchemyError as error:
        logger.warning("Failed to check saved tutor reply: source_message_id=%s", source_message_id)
        raise StorageError("Failed to check saved tutor reply.") from error

    if saved is not None:
        logger.warning(
            "Recovered saved tutor reply: source_message_id=%s reply_id=%s",
            source_message_id,
            saved.id,
        )
        return saved
    raise StorageError("Failed to save tutor reply.") from last_error
