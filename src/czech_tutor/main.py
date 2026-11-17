import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from czech_tutor.config import ConfigurationError, Settings
from czech_tutor.integrations.openrouter import generate_reply
from czech_tutor.persistence.connection import (
    create_database_engine,
    create_session_factory,
)
from czech_tutor.persistence.conversations import get_or_create_conversation
from czech_tutor.persistence.history import get_recent_turns
from czech_tutor.persistence.messages import (
    is_reply_delivery_recorded,
    mark_generation_failed,
    mark_reply_sent,
    reserve_user_message,
    save_tutor_reply,
)
from czech_tutor.persistence.users import upsert_user
from czech_tutor.telegram.handlers import create_router
from czech_tutor.tutor.context import trim_history
from czech_tutor.tutor.errors import LLMError, StorageError
from czech_tutor.tutor.prompts import SYSTEM_PROMPT
from czech_tutor.tutor.schemas import ReplyToSend


async def main(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    try:
        async with httpx.AsyncClient() as client:

            async def reply_to_text(
                telegram_user_id: int,
                username: str | None,
                telegram_chat_id: int,
                telegram_message_id: int,
                text: str,
            ) -> ReplyToSend | None:
                try:
                    async with session_factory.begin() as session:
                        await upsert_user(session, telegram_user_id, username)
                        conversation = await get_or_create_conversation(session, telegram_user_id)
                        message_id = await reserve_user_message(
                            session,
                            conversation.id,
                            telegram_chat_id,
                            telegram_message_id,
                            text,
                        )
                        if message_id is not None:
                            turns = await get_recent_turns(session, conversation.id, message_id)
                            history_texts = [
                                (user.text, assistant.text) for user, assistant in turns
                            ]
                except SQLAlchemyError as error:
                    raise StorageError("Failed to prepare the conversation.") from error

                if message_id is None:
                    return None

                history = trim_history(history_texts)

                try:
                    result = await generate_reply(
                        client,
                        settings.open_router_api_key,
                        text,
                        SYSTEM_PROMPT,
                        history,
                    )
                except LLMError:
                    try:
                        async with session_factory.begin() as session:
                            await mark_generation_failed(session, message_id)
                    except SQLAlchemyError as error:
                        raise StorageError("Failed to record generation failure.") from error
                    raise

                try:
                    async with session_factory.begin() as session:
                        reply = await save_tutor_reply(session, message_id, result)
                except (SQLAlchemyError, ValueError) as error:
                    raise StorageError("Failed to save the tutor reply.") from error

                return ReplyToSend(
                    reply_id=reply.id,
                    reply_cs=result.reply_cs,
                    corrections_count=len(result.corrections),
                )

            async def confirm_reply_sent(
                reply_id: int,
                telegram_chat_id: int,
                telegram_message_id: int,
            ) -> None:
                for attempt in range(3):
                    try:
                        async with session_factory.begin() as session:
                            changed = await mark_reply_sent(
                                session, reply_id, telegram_chat_id, telegram_message_id
                            )
                            if not changed and not await is_reply_delivery_recorded(
                                session, reply_id, telegram_chat_id, telegram_message_id
                            ):
                                raise StorageError("Reply delivery confirmation does not match.")
                        return
                    except SQLAlchemyError as error:
                        if attempt == 2:
                            raise StorageError("Failed to record Telegram delivery.") from error
                        await asyncio.sleep(0.25 * 2**attempt)

            dispatcher = Dispatcher()
            dispatcher.include_router(
                create_router(
                    settings.allowed_user_ids,
                    reply_to_text,
                    confirm_reply_sent,
                    settings.mini_app_url,
                )
            )
            await dispatcher.start_polling(
                bot,
                handle_as_tasks=False,
                close_bot_session=False,
            )
    finally:
        try:
            await engine.dispose()
        finally:
            await bot.session.close()


def run() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    try:
        settings = Settings.from_env()
        asyncio.run(main(settings))
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    except TokenValidationError:
        raise SystemExit("Invalid TELEGRAM_BOT_TOKEN format. Check your local .env.") from None


if __name__ == "__main__":
    run()
