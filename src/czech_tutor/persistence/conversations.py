from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Conversation


async def get_or_create_conversation(
    session: AsyncSession,
    telegram_user_id: int,
) -> Conversation:
    statement = (
        insert(Conversation)
        .values(telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(
            index_elements=[Conversation.telegram_user_id],
        )
    )
    await session.execute(statement)

    result = await session.execute(
        select(Conversation).where(
            Conversation.telegram_user_id == telegram_user_id,
        )
    )
    return result.scalar_one()
