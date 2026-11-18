import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.persistence.messages import is_reply_delivery_recorded, mark_reply_sent
from czech_tutor.tutor.errors import StorageError

logger = logging.getLogger(__name__)


async def confirm_reply_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    reply_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> None:
    for attempt in range(3):
        try:
            async with session_factory.begin() as session:
                changed = await mark_reply_sent(
                    session, reply_id, telegram_chat_id, telegram_message_id
                )
                if not changed and not await is_reply_delivery_recorded(
                    session, reply_id, telegram_chat_id, telegram_message_id
                ):
                    logger.warning("Telegram delivery confirmation mismatch: reply_id=%s", reply_id)
                    raise StorageError("Reply delivery confirmation does not match.")
            return
        except SQLAlchemyError as error:
            if attempt == 2:
                raise StorageError("Failed to record Telegram delivery.") from error
            logger.warning(
                "Retrying Telegram delivery recording: reply_id=%s "
                "failed_attempt=%s next_attempt=%s",
                reply_id,
                attempt + 1,
                attempt + 2,
            )
            await asyncio.sleep(0.25 * 2**attempt)
