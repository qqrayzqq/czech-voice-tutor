"""Move the model-history boundary without deleting learner records."""

from sqlalchemy import exists, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.messages import reserve_user_message
from czech_tutor.persistence.models import Conversation, Message, VoiceJob
from czech_tutor.persistence.voice_jobs import ACTIVE_VOICE_JOB_STATUSES


async def reset_history(
    session: AsyncSession, conversation: Conversation, user_id: int, chat_id: int, message_id: int
) -> bool:
    """Reserve the command exactly once while the caller holds the learner lock."""
    marker = await reserve_user_message(session, conversation.id, chat_id, message_id, "/reset")
    if marker is None:
        return False
    await session.execute(
        update(Message)
        .where(Message.id == marker)
        .values(generation_status="completed", notice_state="skipped")
    )
    conversation.history_reset_after_id = marker
    delivered = exists().where(
        Message.reply_to_message_id == VoiceJob.source_message_id,
        Message.telegram_message_id.is_not(None),
    )
    await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == user_id,
            or_(
                VoiceJob.status.in_(ACTIVE_VOICE_JOB_STATUSES),
                (VoiceJob.status == "confirmed") & ~delivered,
            ),
        )
        .values(
            status="cancelled",
            failure_reason="context_reset",
            revision=VoiceJob.revision + 1,
            delivery_token=None,
            delivery_lease_until=None,
            card_token=None,
            card_lease_until=None,
        )
        .execution_options(synchronize_session=False)
    )
    return True
