from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from czech_tutor.persistence.models import Message


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
        .where(
            user_message.conversation_id == conversation_id,
            assistant_message.conversation_id == conversation_id,
            user_message.role == "user",
            user_message.generation_status == "completed",
            assistant_message.role == "assistant",
            user_message.id < before_message_id,
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
