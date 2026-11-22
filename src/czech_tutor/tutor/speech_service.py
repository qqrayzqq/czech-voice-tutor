"""One synthesis per saved assistant reply, independent of input modality."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.integrations.speech import SPEECH_MODEL, synthesize_speech, validate_spoken_text
from czech_tutor.persistence import reply_audio as audio
from czech_tutor.tutor.errors import StorageError, TTSError

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class TextReply:
    """The learner explicitly selected text output."""


@dataclass(frozen=True)
class SpeechAudio:
    token: str
    mp3: bytes | None = None
    file_id: str | None = None
    sent_chat_id: int | None = None
    sent_message_id: int | None = None


async def prepare_speech(
    session_factory: SessionFactory,
    client: httpx.AsyncClient,
    api_key: str,
    user_id: int,
    reply_id: int,
    voice: str,
    *,
    on_reserved: Callable[[], Awaitable[None]] | None = None,
) -> SpeechAudio | TextReply | None:
    """None means already leased; only a committed reservation permits the paid POST."""
    try:
        async with session_factory.begin() as session:
            owned = await audio.get_owned_reply(session, user_id, reply_id)
            if owned is None:
                raise TTSError("Reply not found.")
            text, mode = owned
            if mode == "text":
                return TextReply()
            validate_spoken_text(text)
            reserved = await audio.reserve_audio(session, reply_id, SPEECH_MODEL, voice)
        if reserved:
            if on_reserved is not None:
                await on_reserved()
            try:
                mp3 = await synthesize_speech(client, api_key, text, voice)
            except TTSError:
                async with session_factory.begin() as session:
                    await audio.fail_audio(session, reply_id)
                raise
            async with session_factory.begin() as session:
                token = uuid4().hex
                if await audio.save_and_claim_audio(session, reply_id, mp3, token):
                    return SpeechAudio(token, mp3=mp3)
                return await _claim_saved_audio(session, reply_id)
        async with session_factory.begin() as session:
            return await _claim_saved_audio(session, reply_id)
    except SQLAlchemyError:
        raise StorageError("Failed to prepare speech.") from None


async def _claim_saved_audio(session: AsyncSession, reply_id: int) -> SpeechAudio | None:
    saved = await audio.get_audio(session, reply_id)
    if saved is None or saved.status in {"failed", "generating"}:
        # An interrupted/ambiguous POST is never issued a second time, including a save rollback.
        raise TTSError("Speech generation is unavailable.")
    if saved.status == "delivered":
        return SpeechAudio(
            "",
            file_id=saved.telegram_file_id,
            sent_chat_id=saved.telegram_chat_id,
            sent_message_id=saved.telegram_message_id,
        )
    token = uuid4().hex
    if not await audio.claim_audio(session, reply_id, token):
        return None
    return SpeechAudio(token, mp3=saved.mp3, file_id=saved.telegram_file_id)


async def complete_speech(
    session_factory: SessionFactory,
    user_id: int,
    reply_id: int,
    token: str,
    file_id: str,
    chat_id: int,
    message_id: int,
) -> None:
    try:
        async with session_factory.begin() as session:
            await audio.finish_audio(
                session, user_id, reply_id, token, file_id, chat_id, message_id
            )
    except SQLAlchemyError:
        raise StorageError("Failed to record speech delivery.") from None


async def release_speech(
    session_factory: SessionFactory,
    user_id: int,
    reply_id: int,
    token: str,
) -> None:
    try:
        async with session_factory.begin() as session:
            await audio.release_audio(session, user_id, reply_id, token)
    except SQLAlchemyError:
        raise StorageError("Failed to release speech delivery.") from None
