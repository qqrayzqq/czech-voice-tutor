"""Atomically reserve one active processing slot per learner."""

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import User


async def try_acquire_request_lease(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    token: str,
    now: datetime,
    lease_until: datetime,
) -> str | None:
    """Return locale only when the insert or conditional update owns the slot."""
    result = await session.execute(
        insert(User)
        .values(
            telegram_user_id=user_id,
            username=username,
            processing_token=token,
            processing_lease_until=lease_until,
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_user_id],
            set_={"processing_token": token, "processing_lease_until": lease_until},
            where=or_(
                User.processing_token.is_(None),
                User.processing_lease_until <= now,
            ),
        )
        .returning(User.interface_language)
    )
    return result.scalar_one_or_none()


async def get_request_lease(session: AsyncSession, user_id: int) -> tuple[str | None, str] | None:
    """Read owner and locale after a conditional claim failed in the same transaction."""
    result = await session.execute(
        select(User.processing_token, User.interface_language).where(
            User.telegram_user_id == user_id
        )
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row is not None else None


async def release_request_lease(session: AsyncSession, user_id: int, token: str) -> None:
    """Clear a lease only for its owner; stale cleanup cannot clear a newer slot."""
    await session.execute(
        update(User)
        .where(User.telegram_user_id == user_id, User.processing_token == token)
        .values(processing_token=None, processing_lease_until=None)
    )
