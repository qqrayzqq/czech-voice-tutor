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
