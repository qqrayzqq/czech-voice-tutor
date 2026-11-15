import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError
from dotenv import load_dotenv

from czech_tutor.config import ConfigurationError, Settings
from czech_tutor.llm import generate_reply
from czech_tutor.prompts import SYSTEM_PROMPT
from czech_tutor.reply_models import TutorReply
from czech_tutor.telegram import create_router


async def main(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)

    try:
        async with httpx.AsyncClient() as client:

            async def reply_to_text(text: str) -> TutorReply:
                answer = await generate_reply(
                    client, settings.open_router_api_key, text, SYSTEM_PROMPT
                )
                return answer

            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(settings.allowed_user_ids, reply_to_text))
            await dispatcher.start_polling(bot, handle_as_tasks=False, close_bot_session=False)
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
