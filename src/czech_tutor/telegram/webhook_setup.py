import argparse
import asyncio
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import WebhookInfo
from aiogram.utils.token import TokenValidationError
from dotenv import load_dotenv

from czech_tutor.config import ConfigurationError, Settings


async def register_webhook(
    settings: Settings,
    webhook_url: str,
) -> None:
    if settings.webhook_secret is None:
        raise ConfigurationError("Webhook secret not set")
    bot = Bot(settings.bot_token)
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
            max_connections=1,
            allowed_updates=["message"],
        )
    finally:
        await bot.session.close()


def run() -> None:
    parser = argparse.ArgumentParser(description="Register or inspect the Telegram webhook.")
    parser.add_argument(
        "webhook_url",
        nargs="?",
        help="Full HTTPS URL of the deployed webhook endpoint.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show the current webhook status without changing it.",
    )
    args = parser.parse_args()

    if args.status and args.webhook_url is not None:
        parser.error("Use either webhook_url or --status, not both.")
    if not args.status and args.webhook_url is None:
        parser.error("Provide webhook_url or --status.")

    if not args.status:
        try:
            parsed_url = urlsplit(args.webhook_url)
        except ValueError:
            parser.error("webhook_url must be an absolute HTTPS URL.")
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            parser.error("webhook_url must be an absolute HTTPS URL.")

    load_dotenv()
    try:
        settings = Settings.from_env()
        if args.status:
            info = asyncio.run(get_webhook_status(settings))
            print(f"URL: {info.url or 'Not configured'}")
            print(f"Pending updates: {info.pending_update_count}")
            print(f"Last delivery error: {info.last_error_message or 'None'}")
        else:
            asyncio.run(register_webhook(settings, args.webhook_url))
            print("Webhook registered.")
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    except TokenValidationError:
        raise SystemExit("Invalid TELEGRAM_BOT_TOKEN format.") from None
    except TelegramAPIError:
        raise SystemExit("Webhook operation failed.") from None


async def get_webhook_status(settings: Settings) -> WebhookInfo:
    bot = Bot(settings.bot_token)
    try:
        return await bot.get_webhook_info()
    finally:
        await bot.session.close()
