from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Message, MessageCorrection
from czech_tutor.tutor.schemas import TutorReply, build_diacritics_correction


async def reserve_user_message(
    session: AsyncSession,
    conversation_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str,
    input_revision: int = 0,
) -> int | None:
    """Reserve one Telegram input within the caller's transaction.

    The identity is (telegram_chat_id, telegram_message_id), not the text or
    Telegram update_id. A new input starts as processing and returns its
    database ID. A conflict returns None regardless of the existing input's
    processing, completed, or failed status; it never reclaims that input.
    Sending the same text with a new Telegram message ID is a separate input.

    The database unique constraint arbitrates concurrent reservations. This
    function does not commit: the caller must commit before calling the LLM.
    Rolling back the transaction also rolls back a newly inserted reservation.
    """
    statement = (
        insert(Message)
        .values(
            conversation_id=conversation_id,
            role="user",
            text=text,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            generation_status="processing",
            input_revision=input_revision,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Message.telegram_chat_id,
                Message.telegram_message_id,
                Message.input_revision,
            ],
        )
        .returning(Message.id)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_tutor_reply(
    session: AsyncSession,
    source_message_id: int,
) -> Message | None:
    """Find a saved reply to the source input without changing any rows."""
    statement = select(Message).where(Message.reply_to_message_id == source_message_id)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def save_tutor_reply(
    session: AsyncSession,
    source_message_id: int,
    reply: TutorReply,
    *,
    replacement_target_id: int | None = None,
) -> Message:
    """Lock the source input, then return or save its reply in this transaction."""
    source = await session.get(Message, source_message_id, with_for_update=True)
    if source is None:
        raise ValueError("Source message not found.")
    if source.role != "user":
        raise ValueError("Source message must have role 'user'.")
    existing_reply = await get_tutor_reply(session, source_message_id)
    if existing_reply is not None:
        return existing_reply
    if source.generation_status != "processing":
        raise ValueError("Source message must be processing.")

    if reply.replaces_previous:
        if replacement_target_id is None:
            await supersede_previous_feedback(session, source.conversation_id, source.id)
        elif replacement_target_id > 0:
            await session.execute(
                update(Message)
                .where(
                    Message.id == replacement_target_id,
                    Message.conversation_id == source.conversation_id,
                    Message.role == "assistant",
                    Message.reply_to_message_id < source.id,
                    Message.telegram_chat_id.is_not(None),
                    Message.telegram_message_id.is_not(None),
                )
                .values(feedback_status="superseded")
            )

    answer = Message(
        conversation_id=source.conversation_id,
        role="assistant",
        text=reply.reply_cs,
        reply_to_message_id=source.id,
        note_ru=(reply.note_ru or None) if reply.feedback_status != "clarification" else None,
        feedback_language=reply.feedback_language,
        feedback_status=reply.feedback_status,
    )
    session.add(answer)

    for correction in reply.corrections if reply.feedback_status == "evaluated" else []:
        session.add(
            MessageCorrection(
                message_id=source.id,
                original=correction.original,
                corrected=correction.corrected,
                explanation_ru=correction.explanation_ru,
                kind=correction.kind,
            )
        )

    diacritics = build_diacritics_correction(
        reply.diacritics if reply.feedback_status == "evaluated" else [], reply.feedback_language
    )
    if diacritics is not None:
        session.add(
            MessageCorrection(
                message_id=source.id,
                original=diacritics.original,
                corrected=diacritics.corrected,
                explanation_ru=diacritics.explanation_ru,
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


async def supersede_previous_feedback(
    session: AsyncSession, conversation_id: int, before_source_id: int
) -> None:
    """Withdraw only the immediately preceding own turn after an explicit repair.

    This keeps the raw transcript and historical cards for diagnostics while
    preventing the disputed input from entering future model history.
    """
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.reply_to_message_id < before_source_id,
            Message.telegram_chat_id.is_not(None),
            Message.telegram_message_id.is_not(None),
        )
        .order_by(Message.reply_to_message_id.desc())
        .limit(1)
        .with_for_update()
    )
    previous = result.scalar_one_or_none()
    if previous is not None:
        previous.feedback_status = "superseded"


async def supersede_clarification(
    session: AsyncSession, conversation_id: int, reply_id: int
) -> None:
    """Withdraw the exact prior own question resolved by this new reply."""
    await session.execute(
        update(Message)
        .where(
            Message.id == reply_id,
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.feedback_status == "clarification",
        )
        .values(feedback_status="superseded")
    )
