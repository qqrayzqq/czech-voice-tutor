import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from czech_tutor.telegram.localization import ui
from czech_tutor.telegram.menu import set_profile_menu
from czech_tutor.telegram.reply_sender import NoticeActions, SpeechActions, send_tutor_reply
from czech_tutor.telegram.voice_handlers import (
    VoiceActions,
    clear_obsolete_controls,
    present_voice_input,
    register_voice_handlers,
)
from czech_tutor.tutor.errors import (
    InputTooLongError,
    LLMError,
    StorageError,
)
from czech_tutor.tutor.schemas import ReplyToSend
from czech_tutor.tutor.voice_schemas import EmptyVoiceTextError

logger = logging.getLogger(__name__)


def create_router(
    allowed_user_ids: frozenset[int],
    reply_to_text: Callable[[int, str | None, int, int, str], Awaitable[ReplyToSend | None]],
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None = None,
    voice_actions: VoiceActions | None = None,
    speech_actions: SpeechActions | None = None,
    notice_actions: NoticeActions | None = None,
    load_language: Callable[[int], Awaitable[str]] | None = None,
) -> Router:
    router = Router(name="local_text_bot")
    router.message.filter(
        F.chat.type == ChatType.PRIVATE,
        F.from_user.id.in_(allowed_user_ids),
    )
    router.callback_query.filter(
        F.message.chat.type == ChatType.PRIVATE,
        F.from_user.id.in_(allowed_user_ids),
    )

    async def language(user_id: int) -> str:
        return await load_language(user_id) if load_language else "ru"

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user is None:
            return
        locale = await language(message.from_user.id)
        await set_profile_menu(message.bot, message.from_user.id, mini_app_url, locale)
        await message.answer(
            ui("start", locale)
            + "\n\n"
            + ui("start_voice" if voice_actions else "start_text", locale),
            parse_mode=None,
        )

    register_voice_handlers(
        router,
        voice_actions,
        confirm_reply_sent,
        mini_app_url,
        speech_actions,
        load_language,
        notice_actions,
    )

    @router.message(F.text)
    async def handle_text(message: Message) -> None:
        if message.text is None or message.from_user is None:
            return
        try:
            if voice_actions is not None:
                edited = await voice_actions.submit_edit(
                    message.from_user.id, message.chat.id, message.message_id, message.text
                )
                if edited is not None:
                    await present_voice_input(
                        message,
                        edited,
                        mini_app_url,
                        remember_card=getattr(voice_actions, "remember_card", None),
                        claim_card=getattr(voice_actions, "claim_card", None),
                        release_card=getattr(voice_actions, "release_card", None),
                        reconcile_card=getattr(voice_actions, "reconcile_card", None),
                        owner_id=message.from_user.id,
                    )
                    return
            answer = await reply_to_text(
                message.from_user.id,
                message.from_user.username,
                message.chat.id,
                message.message_id,
                message.text,
            )
        except InputTooLongError as error:
            try:
                await message.answer(
                    ui(
                        "input_limit",
                        await language(message.from_user.id),
                        max_chars=error.max_chars,
                    ),
                    parse_mode=None,
                )
            except Exception:
                logger.warning(
                    "Failed to send Telegram input limit notification: "
                    "telegram_chat_id=%s telegram_message_id=%s",
                    message.chat.id,
                    message.message_id,
                )
                raise
            return
        except EmptyVoiceTextError:
            await message.answer(
                ui("empty_text", await language(message.from_user.id)),
                parse_mode=None,
            )
            return
        except (LLMError, StorageError):
            try:
                await message.answer(
                    ui("error", await language(message.from_user.id)),
                    parse_mode=None,
                )
            except Exception:
                logger.warning(
                    "Failed to send Telegram error notification: "
                    "telegram_chat_id=%s telegram_message_id=%s",
                    message.chat.id,
                    message.message_id,
                )
                raise
            return

        if answer is None:
            return

        await clear_obsolete_controls(message, message.from_user.id, voice_actions)
        try:
            sent = await send_tutor_reply(
                message,
                message.from_user.id,
                answer,
                mini_app_url,
                speech_actions,
                notice_actions,
            )
        except Exception:
            logger.warning("Failed to send tutor reply: reply_id=%s", answer.reply_id)
            raise
        if sent is None:
            return
        try:
            await confirm_reply_sent(answer.reply_id, sent.chat.id, sent.message_id)
        except StorageError:
            logger.warning(
                "Telegram reply delivery is unconfirmed: reply_id=%s "
                "telegram_chat_id=%s telegram_message_id=%s",
                answer.reply_id,
                sent.chat.id,
                sent.message_id,
            )

    return router
