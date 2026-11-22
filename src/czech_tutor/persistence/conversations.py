from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Conversation


async def get_or_create_conversation(
    session: AsyncSession,
    telegram_user_id: int,
) -> Conversation:
    lookup = select(Conversation).where(Conversation.telegram_user_id == telegram_user_id)
    result = await session.execute(lookup)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    statement = (
        insert(Conversation)
        .values(telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(
            index_elements=[Conversation.telegram_user_id],
        )
        .returning(Conversation)
    )
    result = await session.execute(statement)
    created = result.scalar_one_or_none()
    if created is not None:
        return created
    # A concurrent creator won the unique key after our initial lookup.
    result = await session.execute(lookup)
    return result.scalar_one()
