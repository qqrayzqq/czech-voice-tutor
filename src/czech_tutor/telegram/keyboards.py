from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)


def build_feedback_keyboard(
    mini_app_url: str | None,
    reply_id: int,
    corrections_count: int,
) -> InlineKeyboardMarkup | None:
    """Build a WebApp button using a page URL without query parameters or a fragment."""
    if not mini_app_url or corrections_count == 0:
        return None

    url = f"{mini_app_url}?reply_id={reply_id}"
    web_app = WebAppInfo(url=url)
    button = InlineKeyboardButton(
        text=f"✏️ Исправления · {corrections_count}",
        web_app=web_app,
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])
