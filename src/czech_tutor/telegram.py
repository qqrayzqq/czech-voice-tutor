from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from czech_tutor.llm import LLMError


def create_router(
    allowed_user_ids: frozenset[int],
    reply_to_text: Callable[[str], Awaitable[str]],
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
        if message.text is not None:
            try:
                answer = await reply_to_text(message.text)
            except LLMError:
                await message.answer(
                    "Сейчас не удалось получить ответ. Попробуй чуть позже.", parse_mode=None
                )
                return
            await message.answer(answer, parse_mode=None)

    return router
