"""Owner-specific Telegram entry point to the independent profile page."""

import logging
from urllib.parse import urljoin

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from czech_tutor.telegram.localization import ui

logger = logging.getLogger(__name__)


async def set_profile_menu(bot: Bot, user_id: int, mini_app_url: str | None, language: str) -> None:
    if not mini_app_url:
        return
    base = mini_app_url if mini_app_url.endswith(("/", ".html")) else mini_app_url + "/"
    try:
        await bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(
                text=ui("profile", language),
                web_app=WebAppInfo(url=urljoin(base, "profile.html")),
            ),
        )
    except Exception:
        # A menu failure does not roll back an already saved preference or reply.
        logger.warning("Failed to update profile menu: telegram_user_id=%s", user_id)
