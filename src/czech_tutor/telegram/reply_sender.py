"""Shared presentation for replies to typed and confirmed spoken input."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram.types import BufferedInputFile, Chat, Message

from czech_tutor.telegram.keyboards import build_feedback_keyboard
from czech_tutor.tutor.errors import StorageError, TTSError
from czech_tutor.tutor.feedback_notice import notice_text
from czech_tutor.tutor.schemas import ReplyToSend
from czech_tutor.tutor.speech_service import SpeechAudio, TextReply

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechActions:
    prepare: Callable[[int, int], Awaitable[SpeechAudio | TextReply | None]]
    complete: Callable[[int, int, str, str, int, int], Awaitable[None]]
    release: Callable[[int, int, str], Awaitable[None]]
    prepare_with_notice: (
        Callable[
            [int, int, Callable[[], Awaitable[None]]],
            Awaitable[SpeechAudio | TextReply | None],
        ]
        | None
    ) = None


@dataclass(frozen=True)
class NoticeActions:
    prepare: Callable[[int, int], Awaitable[bool]]
    complete: Callable[[int, int, int], Awaitable[None]]


async def _send_notice(
    message: Message,
    user_id: int,
    answer: ReplyToSend,
    mini_app_url: str | None,
    actions: NoticeActions | None,
) -> None:
    text = notice_text(answer)
    if actions is None or text is None:
        return
    try:
        if not await actions.prepare(user_id, answer.reply_id):
            return
        sent = await message.answer(
            text,
            parse_mode=None,
            reply_markup=build_feedback_keyboard(
                mini_app_url,
                answer.reply_id,
                answer.corrections_count,
                advice_count=answer.advice_count,
                language=answer.interface_language,
            ),
        )
        await actions.complete(user_id, answer.reply_id, sent.message_id)
    except Exception:
        # The reserved attempt remains sending if Telegram's result is uncertain.
        # Continue with the main reply; recovery must not duplicate this notice.
        logger.warning("Feedback notice delivery incomplete: reply_id=%s", answer.reply_id)


@dataclass(frozen=True)
class DeliveryReceipt:
    chat: Chat
    message_id: int


async def _prepare_audio(
    message: Message,
    user_id: int,
    answer: ReplyToSend,
    mini_app_url: str,
    speech: SpeechActions,
    notices: NoticeActions | None,
) -> tuple[SpeechAudio | TextReply | None, bool]:
    """Overlap notices only after a new committed synthesis reservation; always join them."""
    notice_task: asyncio.Task[None] | None = None

    async def on_reserved() -> None:
        nonlocal notice_task
        if notice_task is None:
            notice_task = asyncio.create_task(
                _send_notice(message, user_id, answer, mini_app_url, notices)
            )

    try:
        try:
            if speech.prepare_with_notice is not None:
                audio = await speech.prepare_with_notice(user_id, answer.reply_id, on_reserved)
            else:
                audio = await speech.prepare(user_id, answer.reply_id)
        except (TTSError, StorageError):
            logger.warning(
                "Speech unavailable; delivering saved text: reply_id=%s", answer.reply_id
            )
            audio = TextReply()
        if notice_task is not None:
            await notice_task
        return audio, notice_task is not None
    finally:
        if notice_task is not None:
            if not notice_task.done():
                notice_task.cancel()
            try:
                await notice_task
            except asyncio.CancelledError:
                # Caller cancellation/exception is already propagating; drain the child.
                pass


async def send_tutor_reply(
    message: Message,
    user_id: int,
    answer: ReplyToSend,
    mini_app_url: str | None,
    speech: SpeechActions | None = None,
    notices: NoticeActions | None = None,
) -> Message | DeliveryReceipt | None:
    audio: SpeechAudio | TextReply | None = TextReply()
    notice_started = False
    # Voice output requires a working route to its exact readable transcript.
    if speech is not None and mini_app_url:
        audio, notice_started = await _prepare_audio(
            message, user_id, answer, mini_app_url, speech, notices
        )
    if audio is None:
        return None
    if isinstance(audio, TextReply):
        if not notice_started:
            await _send_notice(message, user_id, answer, mini_app_url, notices)
        sent = await message.answer(
            answer.reply_cs,
            parse_mode=None,
            reply_markup=build_feedback_keyboard(
                mini_app_url,
                answer.reply_id,
                answer.corrections_count,
                answer.has_note,
                advice_count=answer.advice_count,
                language=answer.interface_language,
            )
            if (
                notices is None
                or (answer.has_note and not answer.corrections_count and not answer.advice_count)
            )
            else None,
        )
        return sent
    if audio.sent_chat_id is not None and audio.sent_message_id is not None:
        return DeliveryReceipt(Chat(id=audio.sent_chat_id, type="private"), audio.sent_message_id)
    assert speech is not None
    if not notice_started:
        await _send_notice(message, user_id, answer, mini_app_url, notices)
    try:
        if not audio.file_id and not audio.mp3:
            raise TTSError("Saved audio is empty.")
        sent = await message.answer_voice(
            voice=audio.file_id or BufferedInputFile(audio.mp3, filename="reply.mp3"),
            reply_markup=build_feedback_keyboard(
                mini_app_url,
                answer.reply_id,
                answer.corrections_count,
                answer.has_note,
                include_reply=True,
                language=answer.interface_language,
            ),
        )
    except Exception:
        try:
            await speech.release(user_id, answer.reply_id, audio.token)
        except StorageError:
            logger.warning("Failed to release speech delivery: reply_id=%s", answer.reply_id)
        raise
    if sent.voice is not None:
        try:
            await speech.complete(
                user_id,
                answer.reply_id,
                audio.token,
                sent.voice.file_id,
                sent.chat.id,
                sent.message_id,
            )
        except StorageError:
            # Do not resend an already sent Telegram message to repair a DB write.
            logger.warning("Speech delivery recording failed: reply_id=%s", answer.reply_id)
    return sent
