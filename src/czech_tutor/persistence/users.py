from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import User


async def upsert_user(
    session: AsyncSession,
    telegram_user_id: int,
    username: str | None,
) -> User:
    statement = (
        insert(User)
        .values(
            telegram_user_id=telegram_user_id,
            username=username,
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_user_id],
            set_={"username": username},
        )
        .returning(User)
    )

    result = await session.execute(
        statement,
        execution_options={"populate_existing": True},
    )
    return result.scalar_one()


async def get_user(
    session: AsyncSession,
    telegram_user_id: int,
) -> User | None:
    """Read a learner by Telegram ID without creating rows or committing."""

    statement = select(User).where(User.telegram_user_id == telegram_user_id)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def update_language_level(
    session: AsyncSession,
    telegram_user_id: int,
    language_level: str,
) -> bool:
    """Update only the level and report whether the learner exists; do not commit."""
    statement = (
        update(User)
        .where(User.telegram_user_id == telegram_user_id)
        .values(language_level=language_level)
        .returning(User.telegram_user_id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def update_answer_length(
    session: AsyncSession,
    telegram_user_id: int,
    answer_length: str,
) -> bool:
    """Update only answer length and report whether the learner exists."""
    statement = (
        update(User)
        .where(User.telegram_user_id == telegram_user_id)
        .values(answer_length=answer_length)
        .returning(User.telegram_user_id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def update_response_mode(session: AsyncSession, telegram_user_id: int, mode: str) -> bool:
    result = await session.execute(
        update(User)
        .where(User.telegram_user_id == telegram_user_id)
        .values(response_mode=mode)
        .returning(User.telegram_user_id)
    )
    return result.scalar_one_or_none() is not None
