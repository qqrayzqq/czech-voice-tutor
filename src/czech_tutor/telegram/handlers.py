import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from czech_tutor.telegram.keyboards import build_feedback_keyboard
from czech_tutor.tutor.errors import LLMError, StorageError
from czech_tutor.tutor.schemas import ReplyToSend

logger = logging.getLogger(__name__)


def create_router(
    allowed_user_ids: frozenset[int],
    reply_to_text: Callable[[int, str | None, int, int, str], Awaitable[ReplyToSend | None]],
    confirm_reply_sent: Callable[[int, int, int], Awaitable[None]],
    mini_app_url: str | None = None,
) -> Router:
    router = Router(name="local_text_bot")
    router.message.filter(
        F.chat.type == ChatType.PRIVATE,
        F.from_user.id.in_(allowed_user_ids),
    )

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Ahoj! Я помогу тебе практиковать чешский.\n\n"
            "Напиши сообщение по-чешски или по-русски: можем поболтать, "
            "разобрать фразу или узнать новое слово.\n\n"
            "Пока общаемся текстом; голосовые появятся позже."
        )

    @router.message(F.voice)
    async def voice_not_connected(message: Message) -> None:
        await message.answer(
            "Голосовые пока не поддерживаются. Пришли текст: сначала подключим текстовый диалог."
        )

    @router.message(F.text)
    async def handle_text(message: Message) -> None:
        if message.text is None or message.from_user is None:
            return
        try:
            answer = await reply_to_text(
                message.from_user.id,
                message.from_user.username,
                message.chat.id,
                message.message_id,
                message.text,
            )
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

        keyboard = build_feedback_keyboard(
            mini_app_url,
            answer.reply_id,
            answer.corrections_count,
            answer.has_note,
        )
        try:
            sent = await message.answer(
                answer.reply_cs,
                parse_mode=None,
                reply_markup=keyboard,
            )
        except Exception:
            logger.warning("Failed to send tutor reply: reply_id=%s", answer.reply_id)
            raise
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
