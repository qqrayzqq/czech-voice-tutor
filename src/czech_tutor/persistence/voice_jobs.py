import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.models import Message, VoiceEditInput, VoiceJob

ACTIVE_VOICE_JOB_STATUSES = ("received", "transcribing", "awaiting_confirmation", "awaiting_edit")


async def reserve_voice_job(
    session: AsyncSession,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_file_id: str,
) -> int | None:
    """Reserve one voice input within the caller's transaction.

    The identity is (telegram_chat_id, telegram_message_id). A new input starts
    in status 'received' and returns its database ID. A conflict returns None
    regardless of the existing job's status; it never reclaims that job.

    The database unique constraint arbitrates concurrent reservations. This
    function does not commit: the caller must commit before starting ASR.
    Rolling back the transaction also rolls back a newly inserted reservation.
    """
    statement = (
        insert(VoiceJob)
        .values(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            telegram_file_id=telegram_file_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
        .on_conflict_do_nothing(
            index_elements=[
                VoiceJob.telegram_chat_id,
                VoiceJob.telegram_message_id,
            ],
        )
        .returning(VoiceJob.id)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_active_voice_job(
    session: AsyncSession,
    telegram_user_id: int,
) -> VoiceJob | None:
    """Find the most recent pending voice job for a learner without changing rows.

    Checks active statuses ('received', 'transcribing', 'awaiting_confirmation').
    Returns the job if one is pending, or None if the learner has no active
    voice jobs. Does not commit.
    """
    statement = (
        select(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == telegram_user_id,
            VoiceJob.status.in_(ACTIVE_VOICE_JOB_STATUSES),
        )
        .order_by(VoiceJob.created_at.desc())
        .limit(1)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_voice_job(
    session: AsyncSession,
    job_id: int,
    *,
    lock: bool = False,
) -> VoiceJob | None:
    """Find a voice job by primary key without changing any rows."""
    if lock:
        return await session.get(VoiceJob, job_id, with_for_update=True, populate_existing=True)
    return await session.get(VoiceJob, job_id)


async def save_voice_job_transcript(
    session: AsyncSession,
    job_id: int,
    transcript: str,
) -> bool:
    """Store the transcription and move a pending job to awaiting confirmation.

    Returns True if the job was updated. Returns False if the job is
    missing or has already transitioned to a terminal or confirmed status.
    The caller must commit this transaction.
    """
    statement = (
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.status.in_(("received", "transcribing")),
        )
        .values(
            transcript=transcript,
            status="awaiting_confirmation",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        .returning(VoiceJob.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def mark_voice_job_failed(
    session: AsyncSession,
    job_id: int,
    reason: str = "asr_error",
) -> bool:
    """Mark a pending voice job as failed without affecting other jobs.

    Returns True if the status changed. Returns False if the job is
    missing or has already finished (confirmed, cancelled, or failed).
    The caller must commit this transaction.
    """
    statement = (
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.status.in_(("received", "transcribing")),
        )
        .values(status="failed", failure_reason=reason)
        .returning(VoiceJob.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def confirm_voice_job(
    session: AsyncSession,
    job_id: int,
    confirmed_text: str | None = None,
    *,
    revision: int = 0,
) -> bool:
    """Transition a pending voice job to confirmed status with optional final text.

    Returns True if the job was successfully transitioned from awaiting_confirmation.
    Returns False if the job is missing or not awaiting confirmation.
    The caller must commit this transaction.
    """
    values: dict[str, object] = {"status": "confirmed"}
    if confirmed_text is not None:
        values["confirmed_text"] = confirmed_text

    statement = (
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.status == "awaiting_confirmation",
            VoiceJob.revision == revision,
        )
        .values(**values)
        .returning(VoiceJob.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def cancel_voice_job(
    session: AsyncSession,
    job_id: int,
) -> bool:
    """Cancel a pending voice job without affecting finished jobs.

    Returns True if the job was cancelled. Returns False if the job is
    missing or already finished (confirmed, cancelled, or failed).
    The caller must commit this transaction.
    """
    statement = (
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.status.in_(ACTIVE_VOICE_JOB_STATUSES),
        )
        .values(status="cancelled")
        .returning(VoiceJob.id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def request_voice_edit(
    session: AsyncSession, job_id: int, user_id: int, revision: int
) -> bool:
    result = await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.telegram_user_id == user_id,
            or_(
                VoiceJob.status == "awaiting_confirmation",
                and_(
                    VoiceJob.status == "awaiting_edit", VoiceJob.failure_reason == "clarification"
                ),
            ),
            VoiceJob.revision == revision,
        )
        .values(
            status="awaiting_edit", failure_reason="manual_edit", revision=VoiceJob.revision + 1
        )
        .returning(VoiceJob.id)
    )
    return result.scalar_one_or_none() is not None


async def update_voice_edit(
    session: AsyncSession, job_id: int, user_id: int, new_text: str
) -> bool:
    result = await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.status == "awaiting_edit",
        )
        .values(
            status="awaiting_confirmation",
            confirmed_text=new_text,
            revision=VoiceJob.revision + 1,
            failure_reason=None,
        )
        .returning(VoiceJob.id)
    )
    return result.scalar_one_or_none() is not None


async def expire_voice_jobs(session: AsyncSession, user_id: int) -> None:
    """Free abandoned reservations on user activity; never restart paid operations."""
    now = datetime.now(UTC)
    legacy_expired = and_(
        VoiceJob.expires_at.is_(None),
        or_(
            and_(VoiceJob.status == "received", VoiceJob.created_at < now - timedelta(minutes=2)),
            and_(
                VoiceJob.status == "transcribing", VoiceJob.created_at < now - timedelta(minutes=15)
            ),
            VoiceJob.created_at < now - timedelta(hours=24),
        ),
    )
    await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.status.in_(ACTIVE_VOICE_JOB_STATUSES),
            or_(VoiceJob.expires_at < now, legacy_expired),
        )
        .values(status="failed", failure_reason="expired")
    )


async def get_latest_voice_job(session: AsyncSession, user_id: int) -> VoiceJob | None:
    result = await session.execute(
        select(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == user_id,
        )
        .order_by(VoiceJob.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_voice_job_by_message(
    session: AsyncSession, chat_id: int, message_id: int
) -> VoiceJob | None:
    result = await session.execute(
        select(VoiceJob).where(
            VoiceJob.telegram_chat_id == chat_id,
            VoiceJob.telegram_message_id == message_id,
        )
    )
    return result.scalar_one_or_none()


async def find_voice_edit(session: AsyncSession, chat_id: int, message_id: int) -> int | None:
    result = await session.execute(
        select(VoiceEditInput.job_id).where(
            VoiceEditInput.telegram_chat_id == chat_id,
            VoiceEditInput.telegram_message_id == message_id,
        )
    )
    return result.scalar_one_or_none()


async def reserve_voice_edit(
    session: AsyncSession, job_id: int, chat_id: int, message_id: int
) -> bool:
    result = await session.execute(
        insert(VoiceEditInput)
        .values(
            job_id=job_id,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
        )
        .on_conflict_do_nothing()
        .returning(VoiceEditInput.job_id)
    )
    return result.scalar_one_or_none() is not None


async def get_cleanup_candidates(session: AsyncSession, user_id: int) -> list[VoiceJob]:
    result = await session.execute(
        select(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.status.not_in(("received", "transcribing")),
            or_(
                VoiceJob.soniox_transcription_id.is_not(None),
                and_(
                    VoiceJob.soniox_file_id.is_not(None),
                    VoiceJob.failure_reason == "unused_file",
                ),
            ),
        )
        .order_by(VoiceJob.id)
        .limit(5)
    )
    return list(result.scalars())


async def get_undelivered_voice_jobs(session: AsyncSession, user_id: int) -> list[VoiceJob]:
    result = await session.execute(
        select(VoiceJob)
        .join(
            Message,
            Message.reply_to_message_id == VoiceJob.source_message_id,
        )
        .where(
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.status.in_(("confirmed", "awaiting_edit")),
            Message.telegram_message_id.is_(None),
        )
        .order_by(VoiceJob.id)
        .limit(5)
    )
    return list(result.scalars())


async def abandon_clarification_draft(session: AsyncSession, user_id: int) -> None:
    """A new conversational input supersedes optional fallback controls."""
    await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.status == "awaiting_edit",
            VoiceJob.failure_reason == "clarification",
        )
        .values(status="cancelled", revision=VoiceJob.revision + 1)
    )


async def remember_voice_card(
    session: AsyncSession,
    user_id: int,
    job_id: int,
    message_id: int,
    *,
    token: str | None = None,
) -> bool:
    """Store the delivered Telegram message ID, bound to the matching reservation token."""
    conditions = [
        VoiceJob.id == job_id,
        VoiceJob.telegram_user_id == user_id,
    ]
    if token is not None:
        conditions.append(VoiceJob.card_token == token)
    result = await session.execute(
        update(VoiceJob)
        .where(*conditions)
        .values(
            card_message_id=message_id,
            card_token=None,
            card_lease_until=None,
        )
        .returning(VoiceJob.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def claim_voice_card(
    session: AsyncSession,
    user_id: int,
    job_id: int,
    expected_message_id: int | None,
    *,
    allow_recovery: bool = False,
) -> str | None:
    """Reserve one card POST with a 90-second lease token; zero denotes an in-flight creation."""
    now = datetime.now(UTC)
    token = secrets.token_hex(16)
    if expected_message_id == 0:
        if not allow_recovery:
            return None
        condition = and_(
            VoiceJob.card_message_id == 0,
            or_(VoiceJob.card_lease_until.is_(None), VoiceJob.card_lease_until < now),
        )
    elif expected_message_id is None:
        if allow_recovery:
            condition = or_(
                VoiceJob.card_message_id.is_(None),
                and_(
                    VoiceJob.card_message_id == 0,
                    or_(VoiceJob.card_lease_until.is_(None), VoiceJob.card_lease_until < now),
                ),
            )
        else:
            condition = VoiceJob.card_message_id.is_(None)
    else:
        if not allow_recovery:
            return None
        condition = VoiceJob.card_message_id == expected_message_id

    result = await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.telegram_user_id == user_id,
            condition,
        )
        .values(
            card_message_id=0,
            card_token=token,
            card_lease_until=now + timedelta(seconds=90),
        )
        .returning(VoiceJob.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is not None:
        return token
    return None


async def release_voice_card(
    session: AsyncSession,
    user_id: int,
    job_id: int,
    token: str,
) -> bool:
    """Release reservation when card creation is definitively rejected by Telegram."""
    result = await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.id == job_id,
            VoiceJob.telegram_user_id == user_id,
            VoiceJob.card_token == token,
        )
        .values(
            card_message_id=None,
            card_token=None,
            card_lease_until=None,
        )
        .returning(VoiceJob.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None
