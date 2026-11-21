"""Reconcile one Telegram card against authoritative, short-lived DB snapshots."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.telegram.keyboards import (
    build_voice_confirmation_keyboard,
    build_voice_status_keyboard,
)
from czech_tutor.telegram.localization import ui
from czech_tutor.tutor.voice_card_service import load_voice_card_state
from czech_tutor.tutor.voice_schemas import MAX_VOICE_TEXT_CHARS, VoiceInput

logger = logging.getLogger(__name__)


class MissingVoiceCard(Exception):
    """The card message in Telegram does not exist or was deleted."""

    def __init__(self, state: VoiceInput) -> None:
        super().__init__(f"Voice card message not found: job_id={state.job_id}")
        self.state = state


def render_voice_card(
    voice: VoiceInput | None, mini_app_url: str | None, language: str = "ru"
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Render one immutable snapshot without database or Telegram operations."""
    if voice is None:
        return ui("unavailable_voice", language), None
    language = voice.interface_language
    keyboard = None
    raw = voice.text or ""
    preview = raw[:MAX_VOICE_TEXT_CHARS]
    valid = bool(raw.strip()) and len(raw) <= MAX_VOICE_TEXT_CHARS
    if voice.status in {"received", "transcribing"}:
        text = ui("transcribing", language)
        keyboard = build_voice_status_keyboard(voice.job_id, voice.revision, language=language)
    elif voice.status == "awaiting_confirmation":
        text = (
            ui("confirmation", language, text=preview)
            if valid
            else ui("too_long" if raw.strip() else "empty", language)
        )
        keyboard = build_voice_confirmation_keyboard(
            voice.job_id,
            voice.revision,
            can_confirm=valid,
            mini_app_url=mini_app_url,
            language=language,
        )
    elif voice.status == "awaiting_edit":
        if voice.failure_reason == "clarification":
            text = ui("fallback", language, text=preview)
            keyboard = build_voice_confirmation_keyboard(
                voice.job_id,
                voice.revision,
                can_confirm=valid,
                mini_app_url=mini_app_url,
                language=language,
            )
        else:
            if voice.failure_reason in {"empty", "too_long"}:
                text = ui(voice.failure_reason, language)
                if preview:
                    text += f"\n\n«{preview}…»"
            else:
                text = (
                    ui("editing", language, text=preview)
                    if mini_app_url
                    else (f"«{preview}»\n\n" + ui("chat_edit", language))
                )
            keyboard = build_voice_status_keyboard(
                voice.job_id,
                voice.revision,
                awaiting_edit=True,
                mini_app_url=mini_app_url,
                language=language,
            )
        if voice.can_recover_reply:
            rows = [list(row) for row in keyboard.inline_keyboard]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=ui("recover", language),
                        callback_data=f"voice:recover:{voice.job_id}:{voice.revision}",
                    )
                ]
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    elif voice.status == "confirmed":
        text = ui("submitted", language)
        if voice.can_recover_reply:
            text = ui("saved_reply", language)
            keyboard = build_voice_status_keyboard(
                voice.job_id,
                voice.revision,
                recover=True,
                language=language,
            )
        elif voice.failure_reason in {
            "generation_failed",
            "generation_interrupted",
            "reply_unavailable",
        }:
            text = ui("generation_failed", language)
    else:
        text = ui("cancelled" if voice.status == "cancelled" else "voice_failed", language)
    if voice.pending_replies:
        rows = [list(row) for row in keyboard.inline_keyboard] if keyboard else []
        for pending in voice.pending_replies:
            preview = " ".join(pending.preview.split())[:32]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=ui("answer", language, text=preview)
                        if preview
                        else ui("saved_answer", language),
                        callback_data=f"voice:recover:{pending.job_id}:{pending.revision}",
                    )
                ]
            )
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        text += "\n\n" + ui("pending", language)
    return text, keyboard


async def _clear_controls(bot: Bot, state: VoiceInput) -> None:
    if not state.card_message_id or state.card_message_id <= 0 or state.telegram_chat_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=state.telegram_chat_id,
            message_id=state.card_message_id,
            reply_markup=None,
        )
    except TelegramAPIError as error:
        if "message is not modified" not in str(error).lower():
            logger.warning("Failed to clear changing voice card: job_id=%s", state.job_id)


async def reconcile_voice_card(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    job_id: int,
    mini_app_url: str | None,
) -> bool:
    """Draw the newest state, rechecking after each network edit; never send.

    Database transactions close before every Telegram await. A concurrent
    revision/terminal change triggers a redraw; after three unstable edits,
    controls are removed rather than publishing an actionable stale snapshot.
    """

    async def read() -> VoiceInput | None:
        async with session_factory.begin() as session:
            return await load_voice_card_state(session, user_id, job_id)

    state = None
    try:
        state = await read()
        for _ in range(3):
            if state is None or not state.card_message_id or state.card_message_id <= 0:
                return False
            if state.telegram_chat_id is None:
                return False
            text, keyboard = render_voice_card(state, mini_app_url)
            try:
                await bot.edit_message_text(
                    chat_id=state.telegram_chat_id,
                    message_id=state.card_message_id,
                    text=text,
                    parse_mode=None,
                    reply_markup=keyboard,
                )
            except TelegramAPIError as error:
                err_str = str(error).lower()
                if "message to edit not found" in err_str:
                    logger.warning("Voice card message not found in Telegram: job_id=%s", job_id)
                    raise MissingVoiceCard(state) from error
                if "message is not modified" not in err_str:
                    logger.warning("Failed to reconcile voice card: job_id=%s", job_id)
                    await _clear_controls(bot, state)
                    return False
            latest = await read()
            if latest == state:
                return True
            if latest is None or latest.card_message_id != state.card_message_id:
                await _clear_controls(bot, state)
            state = latest
        if state is not None:
            await _clear_controls(bot, state)
        return False
    except SQLAlchemyError:
        logger.warning("Failed to reload voice card: job_id=%s", job_id)
        if state is not None:
            await _clear_controls(bot, state)
        return False
