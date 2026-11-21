from urllib.parse import urljoin

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from czech_tutor.telegram.localization import ui


def build_feedback_keyboard(
    mini_app_url: str | None,
    reply_id: int,
    corrections_count: int,
    has_note: bool = False,
    *,
    include_reply: bool = False,
    advice_count: int = 0,
    language: str = "ru",
) -> InlineKeyboardMarkup | None:
    """Build a WebApp button using a page URL without query parameters or a fragment."""
    if not mini_app_url or (
        corrections_count == 0 and advice_count == 0 and not has_note and not include_reply
    ):
        return None

    tab = "reply" if include_reply else "corrections"
    url = f"{mini_app_url}?reply_id={reply_id}&tab={tab}" + (
        f"&lang={language}" if language != "ru" else ""
    )
    web_app = WebAppInfo(url=url)
    if include_reply:
        button = InlineKeyboardButton(text=ui("transcript", language), web_app=web_app)
    elif advice_count and corrections_count == 0:
        button = InlineKeyboardButton(text=ui("advice", language), web_app=web_app)
    elif corrections_count == 0 and has_note:
        button = InlineKeyboardButton(
            text=ui("explanation", language),
            web_app=web_app,
        )
    else:
        button = InlineKeyboardButton(
            text=ui("review", language),
            web_app=web_app,
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def build_voice_confirmation_keyboard(
    job_id: int,
    revision: int = 0,
    *,
    can_confirm: bool = True,
    mini_app_url: str | None = None,
    language: str = "ru",
) -> InlineKeyboardMarkup:
    """Bind actions to the exact persisted text revision."""
    buttons = []
    if can_confirm:
        buttons.append(
            InlineKeyboardButton(
                text=ui("send", language), callback_data=f"voice:confirm:{job_id}:{revision}"
            )
        )
    buttons.extend(
        [
            build_voice_edit_button(job_id, revision, mini_app_url, language=language),
            InlineKeyboardButton(
                text=ui("cancel", language), callback_data=f"voice:cancel:{job_id}:{revision}"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_voice_status_keyboard(
    job_id: int,
    revision: int,
    *,
    recover: bool = False,
    awaiting_edit: bool = False,
    mini_app_url: str | None = None,
    language: str = "ru",
) -> InlineKeyboardMarkup:
    buttons = []
    if recover:
        buttons.append(
            InlineKeyboardButton(
                text=ui("recover", language), callback_data=f"voice:recover:{job_id}:{revision}"
            )
        )
    else:
        if awaiting_edit:
            buttons.append(
                build_voice_edit_button(job_id, revision, mini_app_url, language=language)
            )
        if not awaiting_edit:
            buttons.append(
                InlineKeyboardButton(
                    text=ui("check", language), callback_data=f"voice:check:{job_id}:{revision}"
                )
            )
        buttons.append(
            InlineKeyboardButton(
                text=ui("cancel", language), callback_data=f"voice:cancel:{job_id}:{revision}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_voice_edit_button(
    job_id: int, revision: int, mini_app_url: str | None, *, language: str = "ru"
) -> InlineKeyboardButton:
    if mini_app_url:
        base = mini_app_url if mini_app_url.endswith(("/", ".html")) else mini_app_url + "/"
        url = f"{urljoin(base, 'voice.html')}?job_id={job_id}" + (
            f"&lang={language}" if language != "ru" else ""
        )
        return InlineKeyboardButton(text=ui("edit", language), web_app=WebAppInfo(url=url))
    return InlineKeyboardButton(
        text=ui("edit", language), callback_data=f"voice:edit:{job_id}:{revision}"
    )
