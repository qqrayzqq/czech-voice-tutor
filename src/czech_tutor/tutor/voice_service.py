"""Persistent voice input: reserve once, recover ASR, edit, then explicitly confirm."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.soniox import (
    EmptyTranscript,
    TranscriptionFailed,
    TranscriptionPending,
    cleanup_transcription,
    create_transcription_job,
    upload_audio_file,
    wait_for_transcript,
)
from czech_tutor.persistence import voice_jobs as jobs
from czech_tutor.persistence.messages import get_tutor_reply
from czech_tutor.persistence.models import Message, User, VoiceJob
from czech_tutor.persistence.users import upsert_user
from czech_tutor.tutor.errors import ActiveVoiceJobError, ASRError, InputTooLongError, StorageError
from czech_tutor.tutor.voice_confirmation import (
    confirm_voice_input,  # noqa: F401 -- public service API
    release_voice_reply,  # noqa: F401 -- public service API
)
from czech_tutor.tutor.voice_schemas import (
    MAX_VOICE_BYTES,
    MAX_VOICE_TEXT_CHARS,
    EmptyVoiceTextError,
    VoiceInput,
    VoiceRecovery,
)

logger = logging.getLogger(__name__)
SessionFactory = async_sessionmaker[AsyncSession]
ASR_POLL_SECONDS = 12
CLEANUP_SECONDS = 3


def _snapshot(job: VoiceJob, *, can_recover: bool = False) -> VoiceInput:
    return VoiceInput(
        job.id,
        job.status,
        job.confirmed_text if job.confirmed_text is not None else job.transcript,
        job.revision,
        job.failure_reason,
        can_recover,
    )


async def _read(session_factory: SessionFactory, user_id: int, job_id: int) -> VoiceInput | None:
    async with session_factory.begin() as session:
        job = await jobs.get_voice_job(session, job_id)
        if job is None or job.telegram_user_id != user_id:
            return None
        can_recover = False
        if job.status == "confirmed" and job.source_message_id:
            reply = await get_tutor_reply(session, job.source_message_id)
            can_recover = reply is not None and reply.telegram_message_id is None
            if reply is None:
                source = await session.get(Message, job.source_message_id)
                if source is not None:
                    created = (
                        source.created_at.replace(tzinfo=UTC)
                        if (source.created_at.tzinfo is None)
                        else source.created_at
                    )
                    if source.generation_status == "failed":
                        return replace(_snapshot(job), failure_reason="generation_failed")
                    if created < datetime.now(UTC) - timedelta(minutes=2):
                        return replace(_snapshot(job), failure_reason="generation_interrupted")
        return _snapshot(job, can_recover=can_recover)


async def _cleanup_job(
    session_factory: SessionFactory, client: httpx.AsyncClient, api_key: str, job: VoiceJob
) -> None:
    file_id, transcription_id = job.soniox_file_id, job.soniox_transcription_id
    if not transcription_id and job.failure_reason != "unused_file":
        # A missing create response is ambiguous. Never remove a possibly queued file.
        return
    if not await cleanup_transcription(client, api_key, file_id, transcription_id):
        return
    async with session_factory.begin() as session:
        await session.execute(
            update(VoiceJob)
            .where(
                VoiceJob.id == job.id,
                VoiceJob.soniox_transcription_id == transcription_id,
            )
            .values(soniox_file_id=None, soniox_transcription_id=None)
        )


async def _cleanup_for_user(
    session_factory: SessionFactory, client: httpx.AsyncClient, api_key: str, user_id: int
) -> None:
    """Bounded opportunistic cleanup, including cancelled jobs once Soniox is terminal."""
    try:
        async with session_factory.begin() as session:
            candidates = await jobs.get_cleanup_candidates(session, user_id)
        async with asyncio.timeout(CLEANUP_SECONDS):
            for job in candidates:
                await _cleanup_job(session_factory, client, api_key, job)
    except (SQLAlchemyError, TimeoutError):
        # Cleanup cannot undo a successfully saved transcript or local cancellation.
        logger.warning("Failed to record Soniox cleanup: telegram_user_id=%s", user_id)


async def _poll_job(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    api_key: str,
    user_id: int,
    job: VoiceJob,
) -> VoiceInput | None:
    if not job.soniox_transcription_id:
        return await _read(session_factory, user_id, job.id)
    try:
        async with asyncio.timeout(ASR_POLL_SECONDS):
            transcript = await wait_for_transcript(client, api_key, job.soniox_transcription_id)
    except (TranscriptionPending, TimeoutError):
        return await _read(session_factory, user_id, job.id)
    except EmptyTranscript:
        transcript = ""
    except TranscriptionFailed:
        async with session_factory.begin() as session:
            await jobs.mark_voice_job_failed(session, job.id)
        await _cleanup_for_user(session_factory, client, api_key, user_id)
        return await _read(session_factory, user_id, job.id)
    # A transport/schema error leaves the external ID intact for a safe future read.
    async with session_factory.begin() as session:
        changed = await jobs.save_voice_job_transcript(session, job.id, transcript)
        if changed and (not transcript.strip() or len(transcript) > MAX_VOICE_TEXT_CHARS):
            await session.execute(
                update(VoiceJob)
                .where(
                    VoiceJob.id == job.id,
                    VoiceJob.status == "awaiting_confirmation",
                )
                .values(
                    status="awaiting_edit",
                    revision=VoiceJob.revision + 1,
                    failure_reason="empty" if not transcript.strip() else "too_long",
                )
            )
    await _cleanup_for_user(session_factory, client, api_key, user_id)
    return await _read(session_factory, user_id, job.id)


async def process_voice_message(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    soniox_api_key: str,
    telegram_user_id: int,
    username: str | None,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_file_id: str,
    audio_bytes: bytes,
) -> VoiceInput | None:
    """Only a new committed reservation may upload and create paid ASR work.

    Every later action only reads an existing provider job. A create/upload with
    an uncertain result is never repeated. Interrupted received reservations
    expire after two minutes, existing ASR jobs after fifteen minutes, and
    confirmation/editing after a day. Expiry and cleanup run on user activity.
    """
    if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
        raise ASRError("Voice audio is empty or exceeds the byte limit.")
    try:
        async with session_factory.begin() as session:
            await upsert_user(session, telegram_user_id, username)
            await jobs.expire_voice_jobs(session, telegram_user_id)
            existing = await jobs.find_voice_job_by_message(
                session, telegram_chat_id, telegram_message_id
            )
            if existing is not None:
                return _snapshot(existing)
            active = await jobs.get_active_voice_job(session, telegram_user_id)
            if active is not None:
                raise ActiveVoiceJobError("User already has an active voice job.")
            job_id = await jobs.reserve_voice_job(
                session, telegram_user_id, telegram_chat_id, telegram_message_id, telegram_file_id
            )
        if job_id is None:
            return None
        try:
            file_id = await upload_audio_file(client, soniox_api_key, audio_bytes)
        except ASRError:
            async with session_factory.begin() as session:
                await jobs.mark_voice_job_failed(session, job_id, "upload_failed")
            raise
        async with session_factory.begin() as session:
            job = await jobs.get_voice_job(session, job_id, lock=True)
            job.soniox_file_id = file_id
            proceed = job.status == "received"
            if not proceed:
                job.failure_reason = "unused_file"
        if not proceed:
            # No create was attempted, so file-only cleanup is safe.
            await _cleanup_for_user(session_factory, client, soniox_api_key, telegram_user_id)
            return await _read(session_factory, telegram_user_id, job_id)
        try:
            transcription_id = await create_transcription_job(client, soniox_api_key, file_id)
        except ASRError:
            async with session_factory.begin() as session:
                await jobs.mark_voice_job_failed(session, job_id, "submission_uncertain")
            raise
        async with session_factory.begin() as session:
            job = await jobs.get_voice_job(session, job_id, lock=True)
            job.soniox_transcription_id = transcription_id
            if job.status == "received":
                job.status = "transcribing"
                job.expires_at = datetime.now(UTC) + timedelta(minutes=15)
            proceed = job.status == "transcribing"
        if proceed:
            return await _poll_job(session_factory, client, soniox_api_key, telegram_user_id, job)
        await _cleanup_for_user(session_factory, client, soniox_api_key, telegram_user_id)
        return await _read(session_factory, telegram_user_id, job_id)
    except SQLAlchemyError:
        logger.warning(
            "Voice storage failed: telegram_chat_id=%s telegram_message_id=%s",
            telegram_chat_id,
            telegram_message_id,
        )
        raise StorageError("Failed to store voice input.") from None


async def load_voice_input(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    soniox_api_key: str,
    telegram_user_id: int,
    job_id: int | None = None,
) -> VoiceInput | None:
    """Recover UI or poll a saved ASR ID; never upload, create or generate."""
    try:
        async with session_factory.begin() as session:
            await jobs.expire_voice_jobs(session, telegram_user_id)
            if job_id is not None:
                job = await jobs.get_voice_job(session, job_id)
            else:
                job = await jobs.get_active_voice_job(session, telegram_user_id)
                if job is None:
                    job = await jobs.get_latest_voice_job(session, telegram_user_id)
            if job is None or job.telegram_user_id != telegram_user_id:
                return None
        if job.status == "transcribing":
            try:
                state = await _poll_job(
                    session_factory, client, soniox_api_key, telegram_user_id, job
                )
            except ASRError:
                if job_id is not None:
                    raise
                # An unavailable current ASR must not hide older saved replies.
                state = await _read(session_factory, telegram_user_id, job.id)
        else:
            await _cleanup_for_user(session_factory, client, soniox_api_key, telegram_user_id)
            state = await _read(session_factory, telegram_user_id, job.id)
        if job_id is None and state is not None:
            async with session_factory.begin() as session:
                pending = await jobs.get_undelivered_voice_jobs(session, telegram_user_id)
            state = replace(
                state,
                pending_replies=tuple(
                    VoiceRecovery(
                        item.id,
                        item.revision,
                        (item.confirmed_text or item.transcript or "Голосовое")[:32],
                    )
                    for item in pending
                    if item.id != state.job_id
                ),
            )
        return state
    except SQLAlchemyError:
        raise StorageError("Failed to recover voice input.") from None


async def request_voice_edit(
    session_factory: SessionFactory, telegram_user_id: int, job_id: int, revision: int
) -> VoiceInput | None:
    try:
        async with session_factory.begin() as session:
            await jobs.expire_voice_jobs(session, telegram_user_id)
            changed = await jobs.request_voice_edit(session, job_id, telegram_user_id, revision)
            if not changed:
                return None
        return await _read(session_factory, telegram_user_id, job_id)
    except SQLAlchemyError:
        raise StorageError("Failed to request voice editing.") from None


async def submit_voice_edit(
    session_factory: SessionFactory,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str,
) -> VoiceInput | None:
    """Consume text only in persisted edit mode; deduplicate every consumed message."""
    try:
        validation_error = None
        async with session_factory.begin() as session:
            # Serialize edits and new reservations for the same learner.
            learner = await session.get(User, telegram_user_id, with_for_update=True)
            if learner is None:
                return None
            previous_job = await jobs.find_voice_edit(
                session, telegram_chat_id, telegram_message_id
            )
            if previous_job is not None:
                job = await jobs.get_voice_job(session, previous_job)
                return _snapshot(job) if job and job.telegram_user_id == telegram_user_id else None
            await jobs.expire_voice_jobs(session, telegram_user_id)
            job = await jobs.get_active_voice_job(session, telegram_user_id)
            if job is None or job.status != "awaiting_edit":
                return None
            ordinary = await session.execute(
                select(Message.id).where(
                    Message.telegram_chat_id == telegram_chat_id,
                    Message.telegram_message_id == telegram_message_id,
                )
            )
            if ordinary.scalar_one_or_none() is not None:
                return _snapshot(job)
            job = await jobs.get_voice_job(session, job.id, lock=True)
            # Journal every consumed edit, even when validation/cancellation wins.
            # Validation errors are raised only after this short transaction commits.
            await jobs.reserve_voice_edit(session, job.id, telegram_chat_id, telegram_message_id)
            clean = text.strip()
            if not clean:
                validation_error = EmptyVoiceTextError("Edited voice text cannot be empty.")
            elif len(clean) > MAX_VOICE_TEXT_CHARS:
                validation_error = InputTooLongError(MAX_VOICE_TEXT_CHARS)
            else:
                await jobs.update_voice_edit(session, job.id, telegram_user_id, clean)
            job_id = job.id
        if validation_error is not None:
            raise validation_error
        return await _read(session_factory, telegram_user_id, job_id)
    except SQLAlchemyError:
        raise StorageError("Failed to store voice editing.") from None


async def cancel_voice_input(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    soniox_api_key: str,
    telegram_user_id: int,
    job_id: int,
    revision: int = 0,
) -> bool:
    try:
        async with session_factory.begin() as session:
            await jobs.expire_voice_jobs(session, telegram_user_id)
            job = await jobs.get_voice_job(session, job_id, lock=True)
            if job is None or job.telegram_user_id != telegram_user_id or job.revision != revision:
                return False
            changed = await jobs.cancel_voice_job(session, job_id)
        if changed:
            await _cleanup_for_user(session_factory, client, soniox_api_key, telegram_user_id)
        return changed
    except SQLAlchemyError:
        raise StorageError("Failed to cancel voice input.") from None
