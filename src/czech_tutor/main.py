import asyncio
import logging

import httpx
from aiogram import Bot
from aiogram.utils.token import TokenValidationError
from dotenv import load_dotenv

from czech_tutor.config import ConfigurationError, Settings
from czech_tutor.persistence.connection import (
    create_database_engine,
    create_session_factory,
)
from czech_tutor.telegram.bootstrap import create_dispatcher


async def main(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    try:
        async with httpx.AsyncClient() as client:
            dispatcher = create_dispatcher(settings, session_factory, client)
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
