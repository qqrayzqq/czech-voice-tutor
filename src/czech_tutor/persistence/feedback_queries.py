from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Conversation, Message, MessageCorrection


async def get_reply_for_user(
    session: AsyncSession,
    reply_id: int,
    telegram_user_id: int,
) -> Message | None:
    statement = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.id == reply_id,
            Message.role == "assistant",
            Conversation.telegram_user_id == telegram_user_id,
        )
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_corrections_for_message(
    session: AsyncSession,
    source_message_id: int,
) -> list[MessageCorrection]:
    statement = (
        select(MessageCorrection)
        .where(MessageCorrection.message_id == source_message_id)
        .order_by(MessageCorrection.id.asc())
    )

    result = await session.execute(statement)
    return list(result.scalars().all())
