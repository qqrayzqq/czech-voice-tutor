"""Telegram presentation for persisted voice input and reply recovery."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from czech_tutor.telegram.keyboards import (
    build_feedback_keyboard,
    build_voice_confirmation_keyboard,
    build_voice_status_keyboard,
)
from czech_tutor.tutor.errors import (
    ActiveVoiceJobError,
    ASRError,
    InputTooLongError,
    LLMError,
    StorageError,
)
from czech_tutor.tutor.voice_schemas import (
    MAX_VOICE_BYTES,
    MAX_VOICE_SECONDS,
    MAX_VOICE_TEXT_CHARS,
    EmptyVoiceTextError,
    VoiceInput,
    VoiceReply,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceActions:
    process: Callable[[int, str | None, int, int, str, bytes], Awaitable[VoiceInput | None]]
    load: Callable[[int, int | None], Awaitable[VoiceInput | None]]
    request_edit: Callable[[int, int, int], Awaitable[VoiceInput | None]]
    submit_edit: Callable[[int, int, int, str], Awaitable[VoiceInput | None]]
    confirm: Callable[[int, int, int, bool], Awaitable[VoiceReply | None]]
    cancel: Callable[[int, int, int], Awaitable[bool]]
    release_reply: Callable[[int, int, str], Awaitable[None]]


class _VoiceFileTooLarge(ValueError):
    pass


class _LimitedBuffer(BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_VOICE_BYTES:
            raise _VoiceFileTooLarge
        return super().write(data)


def voice_confirmation_text(text: str) -> str:
    return f"🎤 Распознанный текст:\n«{text}»\n\nОтправить репетитору или исправить?"


async def present_voice_input(
    message: Message,
    voice: VoiceInput | None,
    mini_app_url: str | None = None,
) -> None:
    keyboard = None
    if voice is None:
        text = "Нет доступного голосового сообщения. Можно записать новое."
    elif voice.status in {"received", "transcribing"}:
        text = "🎤 Голосовое ещё распознаётся. Проверь немного позже или отмени его."
        keyboard = build_voice_status_keyboard(voice.job_id, voice.revision)
    elif voice.status == "awaiting_confirmation":
        transcript = voice.text or ""
        valid = bool(transcript.strip()) and len(transcript) <= MAX_VOICE_TEXT_CHARS
        preview = transcript[:MAX_VOICE_TEXT_CHARS]
        if valid:
            text = voice_confirmation_text(preview)
        elif len(transcript) > MAX_VOICE_TEXT_CHARS:
            text = (
                f"🎤 Начало распознанного текста:\n«{preview}…»\n\n"
                f"Текст слишком длинный. Сократи его до {MAX_VOICE_TEXT_CHARS} символов "
                "через «Изменить» или отмени голосовое."
            )
        else:
            text = "Не удалось получить текст. Нажми «Изменить» и введи его или отмени голосовое."
        keyboard = build_voice_confirmation_keyboard(
            voice.job_id,
            voice.revision,
            can_confirm=valid,
            mini_app_url=mini_app_url,
        )
    elif voice.status == "awaiting_edit":
        transcript = voice.text or ""
        preview = transcript[:MAX_VOICE_TEXT_CHARS]
        if voice.failure_reason == "empty":
            explanation = (
                "Не удалось распознать речь. Можно ввести текст или отменить голосовое.\n\n"
            )
        elif voice.failure_reason == "too_long":
            explanation = (
                f"🎤 Начало распознанного текста:\n«{preview}…»\n\n"
                "Текст слишком длинный. Сократи его или напиши заново.\n\n"
            )
        elif transcript:
            suffix = "…" if len(transcript) > MAX_VOICE_TEXT_CHARS else ""
            explanation = f"🎤 Текущий текст:\n«{preview}{suffix}»\n\n"
        else:
            explanation = ""
        instruction = (
            "✏️ Нажми «Изменить»: текст в редакторе уже заполнен. "
            "Поправь нужный фрагмент и сохрани. После этого подтверди отправку в чате."
            if mini_app_url
            else "✏️ Редактор пока не подключён. Скопируй текущий текст, исправь нужный фрагмент "
            "и отправь обычным сообщением. "
            f"Не больше {MAX_VOICE_TEXT_CHARS} символов. После этого подтверди отправку. "
            "Вернуться к голосовому можно через /voice."
        )
        text = explanation + instruction
        keyboard = build_voice_status_keyboard(
            voice.job_id,
            voice.revision,
            awaiting_edit=True,
            mini_app_url=mini_app_url,
        )
    elif voice.status == "confirmed":
        if voice.can_recover_reply:
            text = "Ответ репетитора сохранён. Нажми «Получить ответ», чтобы получить его."
            keyboard = build_voice_status_keyboard(voice.job_id, voice.revision, recover=True)
        elif voice.failure_reason in {
            "generation_failed",
            "generation_interrupted",
            "reply_unavailable",
        }:
            text = (
                "Не удалось получить ответ репетитора на это голосовое. "
                "Чтобы попробовать ещё раз, отправь новое сообщение."
            )
        else:
            text = (
                "Голосовое уже подтверждено. Ответ отправлен или ещё готовится. "
                "Если он не появился, проверь /voice немного позже."
            )
    elif voice.status == "cancelled":
        text = "Голосовое сообщение отменено. Можно записать новое."
    else:
        text = "Не удалось обработать это голосовое. Можно записать новое."
    if voice is not None and voice.pending_replies:
        rows = [list(row) for row in keyboard.inline_keyboard] if keyboard is not None else []
        for pending in voice.pending_replies:
            preview = " ".join(pending.preview.split())[:32]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📩 Ответ: {preview}" if preview else "📩 Сохранённый ответ",
                        callback_data=f"voice:recover:{pending.job_id}:{pending.revision}",
                    )
                ]
            )
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        text += "\n\nНиже также доступны сохранённые ответы на прежние голосовые."
    await message.answer(text, parse_mode=None, reply_markup=keyboard)


async def _voice_error(message: Message, error: Exception) -> None:
    if isinstance(error, InputTooLongError):
        text = (
            f"Слишком длинный текст. Пришли не больше {error.max_chars} символов. "
            "Вернуться к голосовому можно через /voice."
        )
    elif isinstance(error, EmptyVoiceTextError):
        text = "Текст не должен быть пустым. Исправь его через /voice."
    elif isinstance(error, ASRError):
        text = (
            "Не удалось распознать голосовое сообщение. Проверь /voice чуть позже "
            "или запиши новое, если обработка завершилась."
        )
    else:
        text = "Сейчас не удалось обработать голосовое. Попробуй чуть позже через /voice."
    await message.answer(text, parse_mode=None)


async def _remove_markup(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning("Failed to remove voice buttons: telegram_message_id=%s", message.message_id)


async def _send_voice_reply(
    message: Message,
    user_id: int,
    result: VoiceReply,
    actions: VoiceActions,
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None,
) -> None:
    answer = result.reply
    try:
        sent = await message.answer(
            answer.reply_cs,
            parse_mode=None,
            reply_markup=build_feedback_keyboard(
                mini_app_url, answer.reply_id, answer.corrections_count, answer.has_note
            ),
        )
    except Exception:
        logger.warning("Failed to send saved voice reply: reply_id=%s", answer.reply_id)
        try:
            await actions.release_reply(user_id, result.job_id, result.delivery_token)
        except Exception:
            logger.warning("Failed to release voice reply delivery: job_id=%s", result.job_id)
        try:
            await message.answer(
                "Не удалось отправить ответ. Он сохранён: нажми «Получить ответ». "
                "Вернуться к текущему голосовому можно через /voice.",
                parse_mode=None,
                reply_markup=build_voice_status_keyboard(
                    result.job_id, result.revision, recover=True
                ),
            )
        except Exception:
            logger.warning("Failed to send voice recovery notice: job_id=%s", result.job_id)
        return
    try:
        await confirm_reply_sent(answer.reply_id, sent.chat.id, sent.message_id)
    except StorageError:
        logger.warning("Telegram voice reply delivery is unconfirmed: reply_id=%s", answer.reply_id)


def register_voice_handlers(
    router: Router,
    actions: VoiceActions | None,
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None,
) -> None:
    async def not_connected(message: Message) -> None:
        await message.answer(
            "Голосовые пока не подключены. Пришли текст для практики чешского.", parse_mode=None
        )

    @router.message(Command("voice"))
    async def restore_voice(message: Message) -> None:
        if message.from_user is None:
            return
        if actions is None:
            await not_connected(message)
            return
        try:
            await present_voice_input(
                message, await actions.load(message.from_user.id, None), mini_app_url
            )
        except (ASRError, StorageError) as error:
            await _voice_error(message, error)

    @router.message(F.voice)
    async def handle_voice(message: Message) -> None:
        if message.voice is None or message.from_user is None:
            return
        if actions is None:
            await not_connected(message)
            return
        if message.voice.duration > MAX_VOICE_SECONDS:
            await message.answer(
                "Голосовое слишком длинное. Запиши не больше 2 минут.", parse_mode=None
            )
            return
        if message.voice.file_size is not None and message.voice.file_size > MAX_VOICE_BYTES:
            await message.answer(
                "Размер голосового слишком большой. Максимум — 5 МБ.", parse_mode=None
            )
            return
        try:
            with _LimitedBuffer() as buffer:
                await message.bot.download(message.voice.file_id, destination=buffer)
                audio_bytes = buffer.getvalue()
        except _VoiceFileTooLarge:
            await message.answer(
                "Размер голосового слишком большой. Максимум — 5 МБ.", parse_mode=None
            )
            return
        except Exception:
            logger.warning("Failed to download voice: telegram_message_id=%s", message.message_id)
            await message.answer(
                "Не удалось загрузить аудио из Telegram. Попробуй ещё раз.", parse_mode=None
            )
            return
        if not audio_bytes:
            await message.answer(
                "В голосовом нет аудио. Попробуй записать ещё раз.", parse_mode=None
            )
            return
        try:
            voice = await actions.process(
                message.from_user.id,
                message.from_user.username,
                message.chat.id,
                message.message_id,
                message.voice.file_id,
                audio_bytes,
            )
            if voice is not None:
                await present_voice_input(message, voice, mini_app_url)
        except ActiveVoiceJobError:
            await message.answer(
                "У тебя уже есть активное голосовое сообщение. Вернёмся к нему.", parse_mode=None
            )
            try:
                await present_voice_input(
                    message, await actions.load(message.from_user.id, None), mini_app_url
                )
            except (ASRError, StorageError) as error:
                await _voice_error(message, error)
        except (ASRError, StorageError, InputTooLongError, EmptyVoiceTextError) as error:
            await _voice_error(message, error)

    @router.callback_query(F.data.startswith("voice:"))
    async def handle_voice_callback(callback: CallbackQuery) -> None:
        # Acknowledgement must not sit behind ASR polling or LLM generation.
        try:
            await callback.answer()
        except TelegramAPIError:
            logger.warning("Failed to acknowledge voice callback")
        if not isinstance(callback.message, Message) or callback.data is None:
            return
        message = callback.message
        if actions is None:
            await not_connected(message)
            return
        parts = callback.data.split(":")
        if len(parts) not in {3, 4}:
            return
        action = parts[1]
        try:
            job_id = int(parts[2])
            revision = int(parts[3]) if len(parts) == 4 else 0
        except ValueError:
            return
        if job_id <= 0 or revision < 0:
            return
        try:
            if action == "check":
                await present_voice_input(
                    message,
                    await actions.load(callback.from_user.id, job_id),
                    mini_app_url,
                )
                return
            if action == "edit":
                voice = await actions.request_edit(callback.from_user.id, job_id, revision)
                if voice is not None:
                    await _remove_markup(message)
                    await present_voice_input(message, voice, mini_app_url)
                    return
            elif action == "cancel":
                if await actions.cancel(callback.from_user.id, job_id, revision):
                    await _remove_markup(message)
                    await message.answer("❌ Голосовое сообщение отменено.", parse_mode=None)
                    return
            elif action in {"confirm", "recover"}:
                result = await actions.confirm(
                    callback.from_user.id, job_id, revision, action == "recover"
                )
                if result is not None:
                    await _remove_markup(message)
                    await _send_voice_reply(
                        message,
                        callback.from_user.id,
                        result,
                        actions,
                        confirm_reply_sent,
                        mini_app_url,
                    )
                    return
            else:
                return
        except (InputTooLongError, EmptyVoiceTextError, LLMError, ASRError, StorageError) as error:
            await _voice_error(message, error)
            return
        await message.answer(
            "Эта кнопка устарела или сообщение уже обработано. "
            "Открой /voice для текущего состояния.",
            parse_mode=None,
        )
