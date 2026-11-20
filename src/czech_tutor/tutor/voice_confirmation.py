"""Explicit confirmation and recoverable delivery of a saved voice reply."""

import logging
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.openrouter import generate_reply
from czech_tutor.persistence import voice_jobs as jobs
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.feedback_queries import get_corrections_for_message
from czech_tutor.persistence.history import get_recent_turns
from czech_tutor.persistence.messages import (
    get_tutor_reply,
    mark_generation_failed,
    reserve_user_message,
)
from czech_tutor.persistence.models import VoiceJob
from czech_tutor.persistence.users import get_user
from czech_tutor.tutor.context import trim_history
from czech_tutor.tutor.errors import InputTooLongError, LLMError, StorageError
from czech_tutor.tutor.prompts import build_system_prompt
from czech_tutor.tutor.reply_storage_service import reply_storage
from czech_tutor.tutor.schemas import ReplyToSend, TutorReply
from czech_tutor.tutor.voice_schemas import MAX_VOICE_TEXT_CHARS, EmptyVoiceTextError, VoiceReply

logger = logging.getLogger(__name__)
SessionFactory = async_sessionmaker[AsyncSession]
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
        if job is None or job.telegram_user_id != user_id or not job.source_message_id:
            return None
        saved = await get_tutor_reply(session, job.source_message_id)
        if saved is None or saved.telegram_message_id is not None:
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
        corrections = await get_corrections_for_message(session, job.source_message_id)
        return VoiceReply(
            job.id,
            token,
            ReplyToSend(
                reply_id=saved.id,
                reply_cs=saved.text,
                corrections_count=len(corrections),
                has_note=bool(saved.note_ru and saved.note_ru.strip()),
            ),
            job.revision,
        )


async def confirm_voice_input(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    openrouter_api_key: str,
    telegram_user_id: int,
    job_id: int,
    revision: int = 0,
    recover: bool = False,
) -> VoiceReply | None:
    """Generate only after one CAS confirmation; recovery reads an existing reply."""
    try:
        if recover:
            return await _claim_saved_reply(session_factory, telegram_user_id, job_id)
        async with session_factory.begin() as session:
            await jobs.expire_voice_jobs(session, telegram_user_id)
            job = await jobs.get_voice_job(session, job_id, lock=True)
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
            if not await jobs.confirm_voice_job(session, job_id, final_text, revision=revision):
                return None
            learner = await get_user(session, telegram_user_id)
            level = learner.language_level if learner else None
            length = learner.answer_length if learner else "short"
            conversation = await get_or_create_conversation(session, telegram_user_id)
            source_id = await reserve_user_message(
                session, conversation.id, job.telegram_chat_id, job.telegram_message_id, final_text
            )
            if source_id is None:
                raise StorageError("Voice source message was already reserved.")
            job.source_message_id = source_id
            turns = await get_recent_turns(session, conversation.id, source_id)
            history = trim_history([(user.text, assistant.text) for user, assistant in turns])
        try:
            result = await generate_reply(
                client=client,
                api_key=openrouter_api_key,
                user_text=final_text,
                system_prompt=build_system_prompt(level, length) + VOICE_INSTRUCTIONS,
                history=history,
            )
        except LLMError:
            async with session_factory.begin() as session:
                await mark_generation_failed(session, source_id)
                await session.execute(
                    update(VoiceJob)
                    .where(VoiceJob.id == job_id)
                    .values(failure_reason="generation_failed")
                )
            raise
        await reply_storage(session_factory, source_id, _clean_voice_reply(result))
        return await _claim_saved_reply(session_factory, telegram_user_id, job_id)
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
