import httpx
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.config import Settings
from czech_tutor.telegram.handlers import create_router
from czech_tutor.tutor.delivery_service import confirm_reply_delivery
from czech_tutor.tutor.schemas import ReplyToSend
from czech_tutor.tutor.text_service import process_text_message


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
) -> Dispatcher:
    async def reply_to_text(
        telegram_user_id: int,
        username: str | None,
        telegram_chat_id: int,
        telegram_message_id: int,
        text: str,
    ) -> ReplyToSend | None:
        return await process_text_message(
            session_factory=session_factory,
            client=client,
            api_key=settings.open_router_api_key,
            telegram_user_id=telegram_user_id,
            username=username,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            text=text,
        )

    async def confirm_reply_sent(
        reply_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> None:
        await confirm_reply_delivery(
            session_factory=session_factory,
            reply_id=reply_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            settings.allowed_user_ids,
            reply_to_text,
            confirm_reply_sent,
            settings.mini_app_url,
        )
    )
    return dispatcher
