from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Conversation, Message, ReplyAudio, User


async def get_owned_reply(session: AsyncSession, user_id: int, reply_id: int):
    result = await session.execute(
        select(Message.text, User.response_mode)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(User, User.telegram_user_id == Conversation.telegram_user_id)
        .where(
            Message.id == reply_id, Message.role == "assistant", User.telegram_user_id == user_id
        )
    )
    return result.one_or_none()


async def reserve_audio(session: AsyncSession, reply_id: int, model: str, voice: str) -> bool:
    result = await session.execute(
        insert(ReplyAudio)
        .values(reply_id=reply_id, model=model, voice=voice)
        .on_conflict_do_nothing(index_elements=[ReplyAudio.reply_id])
        .returning(ReplyAudio.reply_id)
    )
    return result.scalar_one_or_none() is not None


async def get_audio(session: AsyncSession, reply_id: int) -> ReplyAudio | None:
    result = await session.execute(select(ReplyAudio).where(ReplyAudio.reply_id == reply_id))
    return result.scalar_one_or_none()


async def save_and_claim_audio(
    session: AsyncSession, reply_id: int, mp3: bytes, token: str
) -> bool:
    """Persist synthesis and its first lease together; rollback leaves the reservation untouched."""
    now = datetime.now(UTC)
    result = await session.execute(
        update(ReplyAudio)
        .where(ReplyAudio.reply_id == reply_id, ReplyAudio.status == "generating")
        .values(
            mp3=mp3,
            status="ready",
            delivery_token=token,
            delivery_lease_until=now + timedelta(seconds=90),
        )
        .returning(ReplyAudio.reply_id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def fail_audio(session: AsyncSession, reply_id: int) -> None:
    await session.execute(
        update(ReplyAudio)
        .where(ReplyAudio.reply_id == reply_id, ReplyAudio.status == "generating")
        .values(status="failed")
    )


async def claim_audio(session: AsyncSession, reply_id: int, token: str) -> bool:
    now = datetime.now(UTC)
    result = await session.execute(
        update(ReplyAudio)
        .where(
            ReplyAudio.reply_id == reply_id,
            ReplyAudio.status == "ready",
            or_(ReplyAudio.delivery_lease_until.is_(None), ReplyAudio.delivery_lease_until < now),
        )
        .values(delivery_token=token, delivery_lease_until=now + timedelta(seconds=90))
        .returning(ReplyAudio.reply_id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def finish_audio(
    session: AsyncSession,
    user_id: int,
    reply_id: int,
    token: str,
    file_id: str,
    chat_id: int,
    message_id: int,
) -> None:
    await session.execute(
        update(ReplyAudio)
        .where(
            ReplyAudio.reply_id == reply_id,
            ReplyAudio.delivery_token == token,
            ReplyAudio.reply_id.in_(_owned_reply_ids(user_id)),
        )
        .values(
            status="delivered",
            telegram_file_id=file_id,
            mp3=None,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            delivery_token=None,
            delivery_lease_until=None,
        )
    )


def _owned_reply_ids(user_id: int):
    return (
        select(Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == "assistant", Conversation.telegram_user_id == user_id)
    )


async def release_audio(session: AsyncSession, user_id: int, reply_id: int, token: str) -> None:
    await session.execute(
        update(ReplyAudio)
        .where(
            ReplyAudio.reply_id == reply_id,
            ReplyAudio.delivery_token == token,
            ReplyAudio.reply_id.in_(_owned_reply_ids(user_id)),
        )
        .values(delivery_token=None, delivery_lease_until=None)
    )
