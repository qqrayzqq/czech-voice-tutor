"""Shared presentation for replies to typed and confirmed spoken input."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram.types import BufferedInputFile, Chat, Message

from czech_tutor.telegram.keyboards import build_feedback_keyboard
from czech_tutor.tutor.errors import StorageError, TTSError
from czech_tutor.tutor.schemas import ReplyToSend
from czech_tutor.tutor.speech_service import SpeechAudio, TextReply

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechActions:
    prepare: Callable[[int, int], Awaitable[SpeechAudio | TextReply | None]]
    complete: Callable[[int, int, str, str, int, int], Awaitable[None]]
    release: Callable[[int, int, str], Awaitable[None]]


@dataclass(frozen=True)
class DeliveryReceipt:
    chat: Chat
    message_id: int


async def send_tutor_reply(
    message: Message,
    user_id: int,
    answer: ReplyToSend,
    mini_app_url: str | None,
    speech: SpeechActions | None = None,
) -> Message | DeliveryReceipt | None:
    audio: SpeechAudio | TextReply | None = TextReply()
    # Voice output requires a working route to its exact readable transcript.
    if speech is not None and mini_app_url:
        try:
            audio = await speech.prepare(user_id, answer.reply_id)
        except (TTSError, StorageError):
            logger.warning(
                "Speech unavailable; delivering saved text: reply_id=%s", answer.reply_id
            )
    if audio is None:
        return None
    if isinstance(audio, TextReply):
        return await message.answer(
            answer.reply_cs,
            parse_mode=None,
            reply_markup=build_feedback_keyboard(
                mini_app_url,
                answer.reply_id,
                answer.corrections_count,
                answer.has_note,
            ),
        )
    if audio.sent_chat_id is not None and audio.sent_message_id is not None:
        return DeliveryReceipt(Chat(id=audio.sent_chat_id, type="private"), audio.sent_message_id)
    assert speech is not None
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
