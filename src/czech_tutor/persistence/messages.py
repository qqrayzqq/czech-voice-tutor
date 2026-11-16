from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Message, MessageCorrection
from czech_tutor.tutor.schemas import TutorReply


async def reserve_user_message(
    session: AsyncSession,
    conversation_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str,
) -> int | None:
    statement = (
        insert(Message)
        .values(
            conversation_id=conversation_id,
            role="user",
            text=text,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            generation_status="processing",
        )
        .on_conflict_do_nothing(
            index_elements=[
                Message.telegram_chat_id,
                Message.telegram_message_id,
            ],
        )
        .returning(Message.id)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def save_tutor_reply(
    session: AsyncSession,
    source_message_id: int,
    reply: TutorReply,
) -> Message:
    """Save a generated reply within the caller's transaction."""
    source = await session.get(Message, source_message_id)
    if source is None:
        raise ValueError("Source message not found.")
    if source.role != "user":
        raise ValueError("Source message must have role 'user'.")
    if source.generation_status != "processing":
        raise ValueError("Source message must be processing.")

    answer = Message(
        conversation_id=source.conversation_id,
        role="assistant",
        text=reply.reply_cs,
        reply_to_message_id=source.id,
        note_ru=reply.note_ru or None,
    )
    session.add(answer)

    for correction in reply.corrections:
        session.add(
            MessageCorrection(
                message_id=source.id,
                original=correction.original,
                corrected=correction.corrected,
                explanation_ru=correction.explanation_ru,
            )
        )

    source.generation_status = "completed"
    await session.flush()
    return answer


async def mark_generation_failed(
    session: AsyncSession,
    source_message_id: int,
) -> bool:
    """Mark a processing user input as failed within the caller's transaction."""
    statement = (
        update(Message)
        .where(
            Message.id == source_message_id,
            Message.role == "user",
            Message.generation_status == "processing",
        )
        .values(generation_status="failed")
        .returning(Message.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def mark_reply_sent(
    session: AsyncSession,
    reply_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> bool:
    """Record confirmed Telegram delivery within the caller's transaction."""
    statement = (
        update(Message)
        .where(
            Message.id == reply_id,
            Message.role == "assistant",
            Message.telegram_chat_id.is_(None),
            Message.telegram_message_id.is_(None),
        )
        .values(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        )
        .returning(Message.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def is_reply_delivery_recorded(
    session: AsyncSession,
    reply_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> bool:
    """Check a confirmation after a possibly successful earlier commit."""
    statement = select(Message.id).where(
        Message.id == reply_id,
        Message.role == "assistant",
        Message.telegram_chat_id == telegram_chat_id,
        Message.telegram_message_id == telegram_message_id,
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None
