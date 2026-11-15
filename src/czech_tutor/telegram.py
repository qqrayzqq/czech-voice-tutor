from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message


def create_router(allowed_user_ids: frozenset[int]) -> Router:
    router = Router(name="local_text_bot")
    router.message.filter(
        F.chat.type == ChatType.PRIVATE,
        F.from_user.id.in_(allowed_user_ids),
    )

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Ahoj! Это первый шаг Czech Voice Tutor. "
            "Пока я повторяю твой текст; LLM ещё не подключена. "
            "Пришли текстовое сообщение."
        )

    @router.message(F.voice)
    async def voice_not_connected(message: Message) -> None:
        await message.answer(
            "Голосовые пока не поддерживаются. Пришли текст: сначала подключим текстовый диалог."
        )

    @router.message(F.text)
    async def echo(message: Message) -> None:
        # This filter ensures text is present; no HTML/Markdown parsing is enabled.
        if message.text is not None:
            await message.answer(message.text, parse_mode=None)

    return router
