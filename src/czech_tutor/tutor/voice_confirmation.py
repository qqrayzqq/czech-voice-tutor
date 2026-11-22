"""Explicit confirmation and recoverable delivery of a saved voice reply."""

import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, select, true, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.openrouter import generate_reply
from czech_tutor.persistence import voice_jobs as jobs
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.history import get_latest_clarification, get_recent_turns
from czech_tutor.persistence.messages import (
    mark_generation_failed,
    reserve_user_message,
)
from czech_tutor.persistence.models import Message, MessageCorrection, User, VoiceJob
from czech_tutor.persistence.users import get_user
from czech_tutor.tutor.context import trim_history
from czech_tutor.tutor.errors import InputTooLongError, LLMError, StorageError
from czech_tutor.tutor.prompts import build_system_prompt
from czech_tutor.tutor.reply_storage_service import reply_storage
from czech_tutor.tutor.schemas import ReplyToSend, TutorReply
from czech_tutor.tutor.voice_schemas import (
    MAX_VOICE_TEXT_CHARS,
    EmptyVoiceTextError,
    VoiceInput,
    VoiceReply,
)

logger = logging.getLogger(__name__)
SessionFactory = async_sessionmaker[AsyncSession]
AUTO_VOICE_INSTRUCTIONS = (
    "\n\nVOICE INPUT: This is an unconfirmed ASR transcript, not a verified learner quote. "
    "Use the previous clarification question and ASR metadata as context, not proof. "
    "If the meaning is unclear or seems mistranscribed, return feedback_status=clarification, "
    "empty corrections and diacritics, empty note_ru, and one natural Czech clarification "
    "question in reply_cs. Do not reconstruct Czech by translating a potentially wrong "
    "Russian/Ukrainian transcript. A clear Russian/Ukrainian question about Czech is valid. "
    "A recoverable Czech grammar error is not by itself a transcription failure. "
    "Do not praise or grade uncertain speech. Never grade ASR punctuation, accents, spelling "
    "or infer pronunciation. If the learner says you misheard them, set replaces_previous=true. "
    "Do not quote uncertain previous speech as an established fact."
)

VOICE_INSTRUCTIONS = (
    "\n\nVOICE INPUT: The latest text is a learner-confirmed speech transcript, possibly "
    "manually edited. Assess only clear grammar or word-choice errors. Spelling, casing, "
    "punctuation and accent marks were produced by ASR: never grade them as learner mistakes "
    "in any field; return diacritics=[]. Do not infer pronunciation quality or blame the "
    "learner for transcription artifacts. Respond conversationally to the confirmed meaning."
)


def _letters(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if c.isalnum())


def _clean_voice_reply(result: TutorReply) -> TutorReply:
    return result.model_copy(
        update={
            "diacritics": [],
            "corrections": [
                correction
                for correction in result.corrections
                if _letters(correction.original) != _letters(correction.corrected)
            ],
        }
    )


async def _claim_saved_reply(
    session_factory: SessionFactory, user_id: int, job_id: int
) -> VoiceReply | None:
    async with session_factory.begin() as session:
        job = await jobs.get_voice_job(session, job_id, lock=True)
        return await _claim_saved_reply_in_session(session, user_id, job)


async def _claim_saved_reply_in_session(
    session: AsyncSession, user_id: int, job: VoiceJob | None
) -> VoiceReply | None:
    """Claim delivery using the caller's locked job and transaction."""
    if (
        job is None
        or job.telegram_user_id != user_id
        or not job.source_message_id
        or job.failure_reason == "context_reset"
    ):
        return None
    counts = (
        select(
            func.count().filter(MessageCorrection.kind == "error").label("errors"),
            func.count().filter(MessageCorrection.kind == "advice").label("advice"),
        )
        .where(MessageCorrection.message_id == job.source_message_id)
        .subquery()
    )
    result = await session.execute(
        select(Message, User.interface_language, counts.c.errors, counts.c.advice)
        .select_from(Message)
        .outerjoin(User, User.telegram_user_id == user_id)
        .join(counts, true())
        .where(Message.reply_to_message_id == job.source_message_id)
    )
    saved_reply = result.one_or_none()
    if saved_reply is None:
        return None
    saved, locale, corrections_count, advice_count = saved_reply
    if saved.telegram_message_id is not None:
        return None
    now = datetime.now(UTC)
    token = uuid4().hex
    claimed = await session.execute(
        update(VoiceJob)
        .where(
            VoiceJob.id == job.id,
            or_(VoiceJob.delivery_lease_until.is_(None), VoiceJob.delivery_lease_until < now),
        )
        .values(delivery_token=token, delivery_lease_until=now + timedelta(seconds=90))
        .returning(VoiceJob.id)
        .execution_options(synchronize_session=False)
    )
    if claimed.scalar_one_or_none() is None:
        return None
    locale = locale or "ru"
    fallback = None
    if job.status == "awaiting_edit" and job.failure_reason == "clarification":
        fallback = VoiceInput(
            job.id,
            job.status,
            job.transcript,
            job.revision,
            failure_reason=job.failure_reason,
            card_message_id=job.card_message_id,
            interface_language=locale,
            clarification_count=job.clarification_count,
        )
    return VoiceReply(
        job.id,
        token,
        ReplyToSend(
            reply_id=saved.id,
            reply_cs=saved.text,
            corrections_count=corrections_count,
            advice_count=advice_count,
            feedback_status=saved.feedback_status,
            feedback_language=saved.feedback_language,
            interface_language=locale,
            has_note=bool(saved.note_ru and saved.note_ru.strip()),
        ),
        job.revision,
        fallback_input=fallback,
    )


async def confirm_voice_input(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    openrouter_api_key: str,
    telegram_user_id: int,
    job_id: int,
    revision: int = 0,
    recover: bool = False,
    *,
    automatic: bool = False,
    on_accepted: Callable[[], Awaitable[None]] | None = None,
) -> VoiceReply | None:
    """Generate only after one CAS confirmation; recovery reads an existing reply."""
    try:
        if recover:
            return await _claim_saved_reply(session_factory, telegram_user_id, job_id)
        async with session_factory.begin() as session:
            learner = await get_user(session, telegram_user_id, lock=True)
            await jobs.expire_voice_jobs(session, telegram_user_id)
            job = await jobs.get_voice_job(session, job_id, lock=True)
            if (
                job is not None
                and job.telegram_user_id == telegram_user_id
                and job.status == "awaiting_edit"
                and job.failure_reason == "clarification"
                and job.revision == revision
                and not automatic
            ):
                job.status = "awaiting_confirmation"
                await session.flush()
            if (
                job is None
                or job.telegram_user_id != telegram_user_id
                or job.status != "awaiting_confirmation"
                or job.revision != revision
            ):
                return None
            final_text = (
                job.confirmed_text if job.confirmed_text is not None else job.transcript or ""
            ).strip()
            if not final_text:
                raise EmptyVoiceTextError("Confirmed voice text cannot be empty.")
            if len(final_text) > MAX_VOICE_TEXT_CHARS:
                raise InputTooLongError(MAX_VOICE_TEXT_CHARS)
            previously_assessed = job.source_message_id is not None
            if not await jobs.confirm_voice_job(
                session, job_id, None if automatic else final_text, revision=revision
            ):
                return None
            level = learner.language_level if learner else None
            length = learner.answer_length if learner else "short"
            locale = learner.interface_language if learner else "ru"
            metadata = job.asr_metadata
            old_source_id = job.source_message_id

            conversation = await get_or_create_conversation(session, telegram_user_id)
            source_id = await reserve_user_message(
                session,
                conversation.id,
                job.telegram_chat_id,
                job.telegram_message_id,
                final_text,
                input_revision=revision if previously_assessed else 0,
            )
            if source_id is None:
                raise StorageError("Voice source message was already reserved.")
            clarification = await get_latest_clarification(session, conversation.id, source_id)
            previous_reply = await session.get(Message, clarification[0]) if clarification else None
            previous_job = None
            if previous_reply is not None:
                previous = await session.execute(
                    select(VoiceJob).where(
                        VoiceJob.source_message_id == previous_reply.reply_to_message_id,
                        VoiceJob.telegram_user_id == telegram_user_id,
                    )
                )
                previous_job = previous.scalar_one_or_none()
            continuing_clarification = previous_reply is not None
            clarification_count = (
                previous_job.clarification_count if previous_job else int(continuing_clarification)
            )
            clarification_question = clarification[1] if clarification else ""
            job.source_message_id = source_id
            job.failure_reason = None
            job.delivery_token = None
            job.delivery_lease_until = None
            turns = await get_recent_turns(session, conversation.id, source_id)
            history = trim_history([(user.text, assistant.text) for user, assistant in turns])
            replacement_target_id = (
                clarification[0] if clarification else (turns[-1][1].id if turns else 0)
            )
        if on_accepted is not None:
            try:
                await on_accepted()
            except Exception:
                logger.warning("Voice card cleanup failed: job_id=%s", job_id)
        instructions = AUTO_VOICE_INSTRUCTIONS if automatic else VOICE_INSTRUCTIONS
        if automatic and metadata:
            instructions += "\nASR metadata (uncertain evidence): " + json.dumps(metadata)
        if clarification_question:
            history.append(
                (
                    "[Previous voice input was unclear; no verified transcript.]",
                    clarification_question,
                )
            )
            instructions += (
                "\nThe final history pair contains the previous clarification question and a safe "
                "placeholder, not verified learner speech. The latest input may answer it."
            )
        try:
            result = await generate_reply(
                client=client,
                api_key=openrouter_api_key,
                user_text=final_text,
                system_prompt=build_system_prompt(level, length, locale) + instructions,
                history=history,
                feedback_language=locale,
            )
        except LLMError:
            async with session_factory.begin() as session:
                await mark_generation_failed(session, source_id)
                await session.execute(
                    update(VoiceJob)
                    .where(
                        VoiceJob.id == job_id,
                        VoiceJob.failure_reason.is_distinct_from("context_reset"),
                    )
                    .values(failure_reason="generation_failed")
                )
            raise
        result = _clean_voice_reply(result)
        if not automatic and previously_assessed:
            result = result.model_copy(update={"replaces_previous": False})
        await reply_storage(
            session_factory, source_id, result, replacement_target_id=replacement_target_id
        )
        async with session_factory.begin() as session:
            current = await jobs.get_voice_job(session, job_id, lock=True)
            if current is None or current.failure_reason == "context_reset":
                return None
            if result.feedback_status == "clarification":
                current.clarification_count = clarification_count + 1
                if current.clarification_count >= 2:
                    current.status = "awaiting_edit"
                    current.failure_reason = "clarification"
                    current.revision += 1
                    current.expires_at = datetime.now(UTC) + timedelta(hours=24)
            else:
                current.clarification_count = 0
                # Earlier doubtful ASR is retained only as a technical record.
                if continuing_clarification and previous_reply is not None:
                    await session.execute(
                        update(Message)
                        .where(Message.id == previous_reply.id)
                        .values(feedback_status="superseded")
                    )
                if previously_assessed and old_source_id:
                    await session.execute(
                        update(Message)
                        .where(Message.reply_to_message_id == old_source_id)
                        .values(feedback_status="superseded")
                    )
            return await _claim_saved_reply_in_session(session, telegram_user_id, current)
    except SQLAlchemyError:
        logger.warning("Failed to confirm voice input: job_id=%s", job_id)
        raise StorageError("Failed to confirm voice input.") from None


async def release_voice_reply(
    session_factory: SessionFactory, telegram_user_id: int, job_id: int, delivery_token: str
) -> None:
    """Release only this sender's lease, so an old failure cannot release a new sender."""
    try:
        async with session_factory.begin() as session:
            await session.execute(
                update(VoiceJob)
                .where(
                    VoiceJob.id == job_id,
                    VoiceJob.telegram_user_id == telegram_user_id,
                    VoiceJob.delivery_token == delivery_token,
                )
                .values(delivery_token=None, delivery_lease_until=None)
            )
    except SQLAlchemyError:
        raise StorageError("Failed to release voice reply delivery.") from None
