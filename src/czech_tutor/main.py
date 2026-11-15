import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError
from dotenv import load_dotenv

from czech_tutor.config import ConfigurationError, Settings
from czech_tutor.telegram import create_router


async def main(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(settings.allowed_user_ids))

    try:
        # Sequential handling is sufficient for this first local development slice.
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
