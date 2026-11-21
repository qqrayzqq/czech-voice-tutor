import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from czech_tutor.telegram.reply_sender import SpeechActions, send_tutor_reply
from czech_tutor.telegram.voice_handlers import (
    VoiceActions,
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

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Ahoj! Я помогу тебе практиковать чешский.\n\n"
            "Напиши сообщение по-чешски или по-русски: можем поболтать, "
            "разобрать фразу или узнать новое слово.\n\n"
            + (
                "Распознанный текст можно проверить и исправить перед отправкой. "
                "/voice — вернуться к голосовому."
                if voice_actions
                else "Пока общаемся текстом; голосовые появятся позже."
            )
        )

    register_voice_handlers(router, voice_actions, confirm_reply_sent, mini_app_url, speech_actions)

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
                    await present_voice_input(message, edited, mini_app_url)
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
                    "Слишком длинное сообщение. Сейчас можно отправить не больше "
                    f"{error.max_chars} символов. Сократи текст и отправь снова.",
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
                "Текст не должен быть пустым. Пришли исправленный вариант или открой /voice.",
                parse_mode=None,
            )
            return
        except (LLMError, StorageError):
            try:
                await message.answer(
                    "Сейчас не удалось получить ответ. Попробуй чуть позже.",
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

        try:
            sent = await send_tutor_reply(
                message,
                message.from_user.id,
                answer,
                mini_app_url,
                speech_actions,
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
