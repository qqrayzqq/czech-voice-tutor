from urllib.parse import urljoin

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)


def build_feedback_keyboard(
    mini_app_url: str | None,
    reply_id: int,
    corrections_count: int,
    has_note: bool = False,
) -> InlineKeyboardMarkup | None:
    """Build a WebApp button using a page URL without query parameters or a fragment."""
    if not mini_app_url or (corrections_count == 0 and not has_note):
        return None

    url = f"{mini_app_url}?reply_id={reply_id}"
    web_app = WebAppInfo(url=url)
    if corrections_count == 0 and has_note:
        button = InlineKeyboardButton(
            text="📖 Пояснение",
            web_app=web_app,
        )
    else:
        button = InlineKeyboardButton(
            text=f"✏️ Исправления · {corrections_count}",
            web_app=web_app,
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def build_voice_confirmation_keyboard(
    job_id: int, revision: int = 0, *, can_confirm: bool = True, mini_app_url: str | None = None
) -> InlineKeyboardMarkup:
    """Bind actions to the exact persisted text revision."""
    buttons = []
    if can_confirm:
        buttons.append(
            InlineKeyboardButton(
                text="✅ Отправить", callback_data=f"voice:confirm:{job_id}:{revision}"
            )
        )
    buttons.extend(
        [
            build_voice_edit_button(job_id, revision, mini_app_url),
            InlineKeyboardButton(
                text="❌ Отмена", callback_data=f"voice:cancel:{job_id}:{revision}"
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
) -> InlineKeyboardMarkup:
    buttons = []
    if recover:
        buttons.append(
            InlineKeyboardButton(
                text="📩 Получить ответ", callback_data=f"voice:recover:{job_id}:{revision}"
            )
        )
    else:
        if awaiting_edit and mini_app_url:
            buttons.append(build_voice_edit_button(job_id, revision, mini_app_url))
        if not awaiting_edit:
            buttons.append(
                InlineKeyboardButton(
                    text="🔄 Проверить", callback_data=f"voice:check:{job_id}:{revision}"
                )
            )
        buttons.append(
            InlineKeyboardButton(
                text="❌ Отмена", callback_data=f"voice:cancel:{job_id}:{revision}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_voice_edit_button(
    job_id: int, revision: int, mini_app_url: str | None
) -> InlineKeyboardButton:
    if mini_app_url:
        base = mini_app_url if mini_app_url.endswith(("/", ".html")) else mini_app_url + "/"
        url = f"{urljoin(base, 'voice.html')}?job_id={job_id}"
        return InlineKeyboardButton(text="✏️ Изменить", web_app=WebAppInfo(url=url))
    return InlineKeyboardButton(text="✏️ Изменить", callback_data=f"voice:edit:{job_id}:{revision}")
