"""Read authoritative voice-card state without invoking external services."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence import voice_jobs as jobs
from czech_tutor.persistence.messages import get_tutor_reply
from czech_tutor.persistence.users import get_user
from czech_tutor.tutor.voice_schemas import VoiceInput, VoiceRecovery


def _expired(status: str, expires_at: datetime | None, created_at: datetime) -> bool:
    if status not in jobs.ACTIVE_VOICE_JOB_STATUSES:
        return False
    if status in {"received", "transcribing"}:
        ttl = timedelta(minutes=2 if status == "received" else 15)
    else:
        ttl = timedelta(hours=24)
    deadline = expires_at or created_at + ttl
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= datetime.now(UTC)


async def load_voice_card_state(
    session: AsyncSession, user_id: int, job_id: int
) -> VoiceInput | None:
    """Return a detached owner-only snapshot, including delivery recovery links.

    Expired pending jobs are presented without obsolete action buttons. This
    read does not mutate their persisted status or restart ASR/LLM processing.
    """
    job = await jobs.get_voice_job(session, job_id)
    if job is None or job.telegram_user_id != user_id:
        return None
    learner = await get_user(session, user_id)
    locale = learner.interface_language if learner else "ru"
    status = job.status
    failure_reason = job.failure_reason
    if _expired(status, job.expires_at, job.created_at):
        status, failure_reason = "failed", "expired"
    reply = await get_tutor_reply(session, job.source_message_id) if job.source_message_id else None
    pending = await jobs.get_undelivered_voice_jobs(session, user_id)
    now = datetime.now(UTC)
    card_lease_until = (
        job.card_lease_until.replace(tzinfo=UTC)
        if (job.card_lease_until and job.card_lease_until.tzinfo is None)
        else job.card_lease_until
    )
    lease_active = bool(card_lease_until and card_lease_until > now)
    return VoiceInput(
        job_id=job.id,
        status=status,
        text=job.confirmed_text if job.confirmed_text is not None else job.transcript,
        revision=job.revision,
        failure_reason=failure_reason,
        can_recover_reply=reply is not None and reply.telegram_message_id is None,
        pending_replies=tuple(
            VoiceRecovery(
                item.id, item.revision, (item.confirmed_text or item.transcript or "")[:32]
            )
            for item in pending
            if item.id != job.id
        ),
        card_message_id=job.card_message_id,
        interface_language=locale,
        clarification_count=job.clarification_count,
        telegram_chat_id=job.telegram_chat_id,
        card_lease_active=lease_active,
    )
