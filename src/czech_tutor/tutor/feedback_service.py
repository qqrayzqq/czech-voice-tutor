from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.feedback_queries import get_corrections_for_message, get_reply_for_user
from czech_tutor.persistence.users import get_user
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.schemas import Correction, ReplyFeedback


async def load_reply_feedback(
    session: AsyncSession,
    reply_id: int,
    telegram_user_id: int,
) -> ReplyFeedback | None:
    """Read saved feedback for its owner within the caller's session."""
    try:
        reply = await get_reply_for_user(session, reply_id, telegram_user_id)
        if reply is None:
            return None

        source_message_id = reply.reply_to_message_id
        if source_message_id is None:
            raise StorageError("Saved reply is missing its source message.")

        corrections = await get_corrections_for_message(session, source_message_id)
        user = await get_user(session, telegram_user_id)
    except SQLAlchemyError as error:
        raise StorageError("Failed to load reply feedback.") from error

    stored_status = reply.feedback_status or "evaluated"
    known_status = stored_status in {"evaluated", "not_evaluated", "clarification", "superseded"}
    status = stored_status if known_status else "not_evaluated"
    return ReplyFeedback(
        reply_id=reply.id,
        reply_cs=reply.text,
        note_ru=(reply.note_ru or "")
        if known_status and status not in {"clarification", "superseded"}
        else "",
        corrections=[
            Correction(
                original=correction.original,
                corrected=correction.corrected,
                explanation_ru=correction.explanation_ru,
                kind=correction.kind or "error",
            )
            for correction in corrections
            if status == "evaluated"
        ],
        advice_count=sum(c.kind == "advice" for c in corrections) if status == "evaluated" else 0,
        feedback_status=status,
        feedback_language=reply.feedback_language or "ru",
        interface_language=(user.interface_language or "ru") if user else "ru",
    )
