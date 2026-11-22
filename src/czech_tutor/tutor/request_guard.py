"""Keep one learner's requests ordered without holding a transaction during AI calls."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.persistence.request_leases import (
    get_request_lease,
    release_request_lease,
    try_acquire_request_lease,
)
from czech_tutor.tutor.errors import StorageError

REQUEST_TIMEOUT_SECONDS = 180
REQUEST_LEASE_SECONDS = 300


@dataclass(frozen=True)
class RequestClaim:
    status: Literal["acquired", "busy", "duplicate"]
    token: str | None
    interface_language: str


async def acquire_request(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    username: str | None,
    request_key: str,
) -> RequestClaim:
    """Claim a fresh nonce, or distinguish an active replay from another request."""
    prefix = sha256(request_key.encode()).hexdigest() + ":"
    token = prefix + uuid4().hex
    now = datetime.now(UTC)
    try:
        async with session_factory.begin() as session:
            locale = await try_acquire_request_lease(
                session,
                user_id,
                username,
                token,
                now,
                now + timedelta(seconds=REQUEST_LEASE_SECONDS),
            )
            if locale is not None:
                return RequestClaim("acquired", token, locale)
            active = await get_request_lease(session, user_id)
            if active is None:
                return RequestClaim("busy", None, "ru")
            owner, locale = active
            status = "duplicate" if owner is not None and owner.startswith(prefix) else "busy"
            return RequestClaim(status, None, locale)
    except SQLAlchemyError:
        # An ambiguous commit must not trigger a retry and a second paid request.
        raise StorageError("Failed to acquire request lease.") from None


async def release_request(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, token: str
) -> None:
    """Release using the exact owner nonce in a separate short transaction."""
    try:
        async with session_factory.begin() as session:
            await release_request_lease(session, user_id, token)
    except SQLAlchemyError:
        raise StorageError("Failed to release request lease.") from None
