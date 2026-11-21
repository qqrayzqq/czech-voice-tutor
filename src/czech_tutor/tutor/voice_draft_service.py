"""Owner-only, versioned editing of existing ASR text; no ASR/LLM calls."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import User, VoiceJob
from czech_tutor.persistence.voice_jobs import get_voice_job
from czech_tutor.tutor.voice_schemas import (
    SavedVoiceDraft,
    UpdateVoiceDraft,
    VoiceDraft,
    VoiceDraftConflict,
)


def _check_editable(job: VoiceJob) -> None:
    if job.status not in {"awaiting_confirmation", "awaiting_edit"}:
        raise VoiceDraftConflict("Voice input is no longer editable.")
    expires = job.expires_at or job.created_at + timedelta(hours=24)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        raise VoiceDraftConflict("Voice input has expired.")


async def load_voice_draft(session: AsyncSession, user_id: int, job_id: int) -> VoiceDraft | None:
    """Read the latest text without changing mode, version, expiry or history."""
    job = await get_voice_job(session, job_id)
    if job is None or job.telegram_user_id != user_id:
        return None
    _check_editable(job)
    return VoiceDraft(
        job_id=job.id,
        revision=job.revision,
        text=job.confirmed_text if job.confirmed_text is not None else job.transcript or "",
    )


async def save_voice_draft(
    session: AsyncSession,
    user_id: int,
    job_id: int,
    request: UpdateVoiceDraft,
) -> SavedVoiceDraft | None:
    """Lock and compare the version; caller commits before notifying Telegram.

    An exact retry of the most recently saved value returns that value without
    increasing its version or sending a second confirmation message.
    """
    job = await get_voice_job(session, job_id, lock=True)
    if job is None or job.telegram_user_id != user_id:
        return None
    _check_editable(job)
    changed = job.revision == request.revision
    if not changed:
        if not (
            job.revision == request.revision + 1
            and job.confirmed_text == request.text
            and job.status == "awaiting_confirmation"
        ):
            raise VoiceDraftConflict("Voice text changed since the editor loaded it.")
    else:
        job.confirmed_text = request.text
        job.status = "awaiting_confirmation"
        job.revision += 1
        job.failure_reason = None
        await session.flush()
    return SavedVoiceDraft(
        draft=VoiceDraft(job_id=job.id, revision=job.revision, text=job.confirmed_text),
        telegram_chat_id=job.telegram_chat_id,
        changed=changed,
        card_message_id=job.card_message_id,
        interface_language=(
            learner.interface_language if (learner := await session.get(User, user_id)) else "ru"
        ),
    )
