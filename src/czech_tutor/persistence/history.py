from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from czech_tutor.persistence.models import Conversation, Message


async def get_recent_turns(
    session: AsyncSession,
    conversation_id: int,
    before_message_id: int,
    limit: int = 5,
) -> list[tuple[Message, Message]]:
    user_message = aliased(Message, name="user_message")
    assistant_message = aliased(Message, name="assistant_message")

    statement = (
        select(user_message, assistant_message)
        .select_from(user_message)
        .join(
            assistant_message,
            assistant_message.reply_to_message_id == user_message.id,
        )
        .join(Conversation, Conversation.id == user_message.conversation_id)
        .where(
            user_message.conversation_id == conversation_id,
            assistant_message.conversation_id == conversation_id,
            user_message.role == "user",
            user_message.generation_status == "completed",
            assistant_message.role == "assistant",
            assistant_message.feedback_status.not_in(("superseded", "clarification")),
            user_message.id < before_message_id,
            user_message.id > Conversation.history_reset_after_id,
            assistant_message.id < before_message_id,
            assistant_message.telegram_chat_id.is_not(None),
            assistant_message.telegram_message_id.is_not(None),
        )
        .order_by(
            user_message.created_at.desc(),
            user_message.id.desc(),
        )
        .limit(limit)
    )

    result = await session.execute(statement)
    rows = result.all()

    return [(user, assistant) for user, assistant in reversed(rows)]


async def get_latest_clarification(
    session: AsyncSession, conversation_id: int, before_source_id: int
) -> tuple[int, str] | None:
    """Return only the latest delivered assistant question, without its ASR source.

    Inspect the latest delivered reply first, rather than searching backwards
    for any clarification: an old question must not survive a later normal turn.
    """
    result = await session.execute(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.reply_to_message_id < before_source_id,
            Message.reply_to_message_id > Conversation.history_reset_after_id,
            Message.telegram_chat_id.is_not(None),
            Message.telegram_message_id.is_not(None),
        )
        .order_by(Message.reply_to_message_id.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest is None or latest.feedback_status != "clarification":
        return None
    return latest.id, latest.text
