"""Telegram presentation for persisted voice input and reply recovery."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from czech_tutor.telegram.keyboards import (
    build_voice_status_keyboard,
)
from czech_tutor.telegram.localization import ui
from czech_tutor.telegram.reply_sender import NoticeActions, SpeechActions, send_tutor_reply
from czech_tutor.telegram.voice_cards import MissingVoiceCard, render_voice_card
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
    confirm_with_cleanup: (
        Callable[[int, int, int, bool, Callable[[], Awaitable[None]]], Awaitable[VoiceReply | None]]
        | None
    ) = None
    advance: Callable[[int, VoiceInput], Awaitable[VoiceReply | None]] | None = None
    remember_card: Callable[[int, int, int, str | None], Awaitable[bool]] | None = None
    claim_card: Callable[..., Awaitable[str | None]] | None = None
    release_card: Callable[[int, int, str], Awaitable[bool]] | None = None
    reconcile_card: Callable[[Bot, int, int], Awaitable[bool]] | None = None
    obsolete_cards: Callable[[int], Awaitable[list[int]]] | None = None
    cleanup: Callable[[int], Awaitable[None]] | None = None


class _VoiceFileTooLarge(ValueError):
    pass


class _LimitedBuffer(BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_VOICE_BYTES:
            raise _VoiceFileTooLarge
        return super().write(data)


@asynccontextmanager
async def record_voice_action(
    bot: Bot, chat_id: int, *, interval: float = 4.5
) -> AsyncIterator[None]:
    """Keep the chat action alive without delaying processing on Telegram's response."""

    async def _pulse() -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="record_voice")
            except Exception:
                # Cosmetic status failures must not interrupt the actual reply.
                pass
            await asyncio.sleep(interval)

    task = asyncio.create_task(_pulse())
    try:
        # Let the first action start, without waiting for its network response.
        await asyncio.sleep(0)
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def voice_cleanup(actions: VoiceActions, user_id: int) -> AsyncIterator[None]:
    """Overlap free cleanup with reply preparation, and join it before handler exit."""
    cleanup = getattr(actions, "cleanup", None)
    if cleanup is None:
        yield
        return

    async def run() -> None:
        try:
            await cleanup(user_id)
        except Exception:
            logger.warning("Voice resource cleanup failed")

    task = asyncio.create_task(run())
    cancelled = False
    try:
        await asyncio.sleep(0)
        yield
    except asyncio.CancelledError:
        cancelled = True
        task.cancel()
        raise
    finally:
        try:
            await task
        except asyncio.CancelledError:
            if not cancelled:
                raise


def voice_confirmation_text(text: str, language: str = "ru") -> str:
    return ui("confirmation", language, text=text)


async def present_voice_input(
    message: Message,
    voice: VoiceInput | None,
    mini_app_url: str | None = None,
    *,
    remember_card: Callable[[int, int, int, str | None], Awaitable[bool]] | None = None,
    owner_id: int | None = None,
    claim_card: Callable[..., Awaitable[str | None]] | None = None,
    release_card: Callable[[int, int, str], Awaitable[bool]] | None = None,
    reconcile_card: Callable[[Bot, int, int], Awaitable[bool]] | None = None,
    language: str = "ru",
    allow_replacement: bool = False,
) -> None:
    text, keyboard = render_voice_card(voice, mini_app_url, language)
    missing_card_state: VoiceInput | None = None
    if (
        voice is not None
        and voice.card_message_id
        and voice.card_message_id > 0
        and reconcile_card is not None
        and owner_id is not None
    ):
        try:
            if await reconcile_card(message.bot, owner_id, voice.job_id):
                return
        except MissingVoiceCard as exc:
            if not allow_replacement:
                return
            missing_card_state = exc.state
        else:
            return
    if voice is not None and voice.card_message_id == 0:
        if not allow_replacement:
            return
        if voice.card_lease_active:
            await message.answer(ui("card_in_flight", language), parse_mode=None)
            return
    if (
        voice is not None
        and voice.card_message_id
        and voice.card_message_id > 0
        and missing_card_state is None
    ):
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=voice.card_message_id,
                text=text,
                parse_mode=None,
                reply_markup=keyboard,
            )
        except TelegramAPIError as error:
            err_str = str(error).lower()
            if allow_replacement and "message to edit not found" in err_str:
                missing_card_state = voice
            else:
                if "message is not modified" not in err_str:
                    logger.warning("Failed to update voice card: job_id=%s", voice.job_id)
                return
        else:
            return

    target_voice = missing_card_state if missing_card_state is not None else voice
    if target_voice is not None:
        text, keyboard = render_voice_card(target_voice, mini_app_url, language)

    token: str | None = None
    if target_voice is not None and claim_card is not None and owner_id is not None:
        expected_id = target_voice.card_message_id
        token = await claim_card(
            owner_id,
            target_voice.job_id,
            expected_id,
            allow_recovery=allow_replacement,
        )
        if token is None:
            return

    try:
        sent = await message.answer(text, parse_mode=None, reply_markup=keyboard)
    except TelegramAPIError as error:
        logger.warning(
            "Failed to send voice card: job_id=%s error=%s",
            getattr(target_voice, "job_id", None),
            error,
        )
        if (
            target_voice is not None
            and release_card is not None
            and owner_id is not None
            and token is not None
            and isinstance(error, (TelegramForbiddenError, TelegramBadRequest))
        ):
            try:
                await release_card(owner_id, target_voice.job_id, token)
            except Exception:
                pass
        return

    if (
        target_voice is not None
        and remember_card is not None
        and owner_id is not None
        and token is not None
    ):
        try:
            recorded = await remember_card(owner_id, target_voice.job_id, sent.message_id, token)
            if recorded and reconcile_card is not None:
                try:
                    await reconcile_card(message.bot, owner_id, target_voice.job_id)
                except MissingVoiceCard:
                    pass
        except StorageError:
            logger.warning("Failed to record voice card: job_id=%s", target_voice.job_id)


async def _voice_error(message: Message, error: Exception, language: str = "ru") -> None:
    if isinstance(error, InputTooLongError):
        text = ui("input_limit", language, max_chars=error.max_chars)
    elif isinstance(error, EmptyVoiceTextError):
        text = ui("empty_text", language)
    elif isinstance(error, ASRError):
        text = ui("asr_error", language)
    else:
        text = ui("voice_error", language)
    await message.answer(text, parse_mode=None)


async def _remove_markup(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning("Failed to remove voice buttons: telegram_message_id=%s", message.message_id)


async def clear_obsolete_controls(
    message: Message, user_id: int, actions: VoiceActions | None
) -> None:
    lookup = getattr(actions, "obsolete_cards", None)
    if lookup is None:
        return
    try:
        ids = await lookup(user_id)
        for message_id in ids:
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id, message_id=message_id, reply_markup=None
                )
            except TelegramAPIError:
                pass
    except StorageError:
        logger.warning("Failed to find obsolete voice controls: telegram_user_id=%s", user_id)


async def _send_voice_reply(
    message: Message,
    user_id: int,
    result: VoiceReply,
    actions: VoiceActions,
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None,
    speech_actions: SpeechActions | None = None,
    notice_actions: NoticeActions | None = None,
) -> None:
    answer = result.reply
    await clear_obsolete_controls(message, user_id, actions)
    try:
        sent = await send_tutor_reply(
            message, user_id, answer, mini_app_url, speech_actions, notice_actions
        )
    except Exception:
        logger.warning("Failed to send saved voice reply: reply_id=%s", answer.reply_id)
        try:
            await actions.release_reply(user_id, result.job_id, result.delivery_token)
        except Exception:
            logger.warning("Failed to release voice reply delivery: job_id=%s", result.job_id)
        try:
            await message.answer(
                ui("delivery_error", answer.interface_language),
                parse_mode=None,
                reply_markup=build_voice_status_keyboard(
                    result.job_id, result.revision, recover=True, language=answer.interface_language
                ),
            )
        except Exception:
            logger.warning("Failed to send voice recovery notice: job_id=%s", result.job_id)
        return
    if sent is None:
        return
    try:
        await confirm_reply_sent(answer.reply_id, sent.chat.id, sent.message_id)
    except StorageError:
        logger.warning("Telegram voice reply delivery is unconfirmed: reply_id=%s", answer.reply_id)

    if result.fallback_input is not None:
        await present_voice_input(
            message,
            result.fallback_input,
            mini_app_url,
            remember_card=getattr(actions, "remember_card", None),
            claim_card=getattr(actions, "claim_card", None),
            release_card=getattr(actions, "release_card", None),
            reconcile_card=getattr(actions, "reconcile_card", None),
            owner_id=user_id,
        )


def register_voice_handlers(
    router: Router,
    actions: VoiceActions | None,
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None,
    speech_actions: SpeechActions | None = None,
    load_language: Callable[[int], Awaitable[str]] | None = None,
    notice_actions: NoticeActions | None = None,
) -> None:
    async def locale(user_id: int) -> str:
        return await load_language(user_id) if load_language else "ru"

    async def present(
        message: Message, voice: VoiceInput | None, user_id: int, *, restore: bool = False
    ) -> None:
        if (
            voice is not None
            and voice.status == "awaiting_confirmation"
            and voice.revision == 0
            and getattr(actions, "advance", None) is not None
        ):
            result = await actions.advance(user_id, voice)
            if result is not None:
                await _send_voice_reply(
                    message,
                    user_id,
                    result,
                    actions,
                    confirm_reply_sent,
                    mini_app_url,
                    speech_actions,
                    notice_actions,
                )
            return
        await present_voice_input(
            message,
            voice,
            mini_app_url,
            remember_card=getattr(actions, "remember_card", None),
            claim_card=getattr(actions, "claim_card", None),
            release_card=getattr(actions, "release_card", None),
            reconcile_card=getattr(actions, "reconcile_card", None),
            owner_id=user_id,
            language=await locale(user_id),
            allow_replacement=restore,
        )

    async def not_connected(message: Message) -> None:
        await message.answer(ui("not_connected", await locale(message.chat.id)), parse_mode=None)

    @router.message(Command("voice"))
    async def restore_voice(message: Message) -> None:
        if message.from_user is None:
            return
        if actions is None:
            await not_connected(message)
            return
        try:
            await present(
                message,
                await actions.load(message.from_user.id, None),
                message.from_user.id,
                restore=True,
            )
        except (ASRError, StorageError, LLMError) as error:
            await _voice_error(message, error, await locale(message.chat.id))

    @router.message(F.voice)
    async def handle_voice(message: Message) -> None:
        if message.voice is None or message.from_user is None:
            return
        if actions is None:
            await not_connected(message)
            return
        if message.voice.duration > MAX_VOICE_SECONDS:
            await message.answer(
                ui("duration", await locale(message.from_user.id)), parse_mode=None
            )
            return
        if message.voice.file_size is not None and message.voice.file_size > MAX_VOICE_BYTES:
            await message.answer(ui("size", await locale(message.from_user.id)), parse_mode=None)
            return
        try:
            with _LimitedBuffer() as buffer:
                await message.bot.download(message.voice.file_id, destination=buffer)
                audio_bytes = buffer.getvalue()
        except _VoiceFileTooLarge:
            await message.answer(ui("size", await locale(message.from_user.id)), parse_mode=None)
            return
        except Exception:
            logger.warning("Failed to download voice: telegram_message_id=%s", message.message_id)
            await message.answer(
                ui("download", await locale(message.from_user.id)), parse_mode=None
            )
            return
        if not audio_bytes:
            await message.answer(
                ui("no_audio", await locale(message.from_user.id)), parse_mode=None
            )
            return
        try:
            async with record_voice_action(message.bot, message.chat.id):
                voice = await actions.process(
                    message.from_user.id,
                    message.from_user.username,
                    message.chat.id,
                    message.message_id,
                    message.voice.file_id,
                    audio_bytes,
                )
                if voice is not None and voice.status != "confirmed":
                    async with voice_cleanup(actions, message.from_user.id):
                        await present(message, voice, message.from_user.id)
        except ActiveVoiceJobError:
            await message.answer(ui("active", await locale(message.from_user.id)), parse_mode=None)
            try:
                await present(
                    message, await actions.load(message.from_user.id, None), message.from_user.id
                )
            except (ASRError, StorageError, LLMError) as error:
                await _voice_error(message, error, await locale(message.chat.id))
        except (ASRError, StorageError, LLMError, InputTooLongError, EmptyVoiceTextError) as error:
            await _voice_error(message, error, await locale(message.chat.id))

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
                await present(
                    message,
                    await actions.load(callback.from_user.id, job_id),
                    callback.from_user.id,
                )
                return
            if action == "edit":
                voice = await actions.request_edit(callback.from_user.id, job_id, revision)
                if voice is not None:
                    await _remove_markup(message)
                    await present(message, voice, callback.from_user.id)
                    return
            elif action == "cancel":
                if await actions.cancel(callback.from_user.id, job_id, revision):
                    await _remove_markup(message)
                    await message.edit_text(
                        ui("cancelled", await locale(callback.from_user.id)),
                        parse_mode=None,
                        reply_markup=None,
                    )
                    return
            elif action in {"confirm", "recover"}:

                async def accepted() -> None:
                    await _remove_markup(message)
                    try:
                        await message.edit_text(
                            ui("submitted", await locale(callback.from_user.id)),
                            parse_mode=None,
                            reply_markup=None,
                        )
                    except TelegramAPIError:
                        logger.warning("Failed to close voice card: job_id=%s", job_id)

                async with record_voice_action(message.bot, message.chat.id):
                    guarded = getattr(actions, "confirm_with_cleanup", None)
                    if guarded is not None:
                        result = await guarded(
                            callback.from_user.id, job_id, revision, action == "recover", accepted
                        )
                    else:
                        result = await actions.confirm(
                            callback.from_user.id, job_id, revision, action == "recover"
                        )
                    if result is not None:
                        if action == "confirm" and guarded is None:
                            await _remove_markup(message)
                            try:
                                await message.edit_text(
                                    ui("submitted", await locale(callback.from_user.id)),
                                    parse_mode=None,
                                    reply_markup=None,
                                )
                            except TelegramAPIError:
                                logger.warning("Failed to close voice card: job_id=%s", job_id)
                        await _send_voice_reply(
                            message,
                            callback.from_user.id,
                            result,
                            actions,
                            confirm_reply_sent,
                            mini_app_url,
                            speech_actions,
                            notice_actions,
                        )
                        return
            else:
                return
        except (InputTooLongError, EmptyVoiceTextError, LLMError, ASRError, StorageError) as error:
            await _voice_error(message, error, await locale(message.chat.id))
            return
        return
