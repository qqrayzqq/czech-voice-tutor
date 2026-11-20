"""Authenticated Mini App editor; saving a draft never invokes ASR or LLM."""

import logging
from typing import Annotated

from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from czech_tutor.api.dependencies import get_current_user_id
from czech_tutor.telegram.keyboards import build_voice_confirmation_keyboard
from czech_tutor.telegram.voice_handlers import voice_confirmation_text
from czech_tutor.tutor.voice_draft_service import load_voice_draft, save_voice_draft
from czech_tutor.tutor.voice_schemas import (
    UpdateVoiceDraft,
    VoiceDraft,
    VoiceDraftConflict,
    VoiceDraftSaved,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice-jobs")


@router.get("/{job_id}/draft")
async def get_draft(
    request: Request,
    response: Response,
    job_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Depends(get_current_user_id)],
) -> VoiceDraft:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with request.app.state.session_factory.begin() as session:
            draft = await load_voice_draft(session, user_id, job_id)
    except VoiceDraftConflict:
        raise HTTPException(409, detail="Voice input is no longer editable.") from None
    except SQLAlchemyError:
        logger.warning("Failed to load voice draft: job_id=%s", job_id)
        raise HTTPException(503, detail="Voice editor is temporarily unavailable.") from None
    if draft is None:
        raise HTTPException(404, detail="Voice input not found.")
    return draft


@router.patch("/{job_id}/draft")
async def patch_draft(
    request: Request,
    response: Response,
    command: UpdateVoiceDraft,
    job_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Depends(get_current_user_id)],
) -> VoiceDraftSaved:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with request.app.state.session_factory.begin() as session:
            saved = await save_voice_draft(session, user_id, job_id, command)
    except VoiceDraftConflict:
        raise HTTPException(409, detail="Voice input changed or is no longer editable.") from None
    except SQLAlchemyError:
        logger.warning("Failed to save voice draft: job_id=%s", job_id)
        raise HTTPException(503, detail="Voice editor is temporarily unavailable.") from None
    if saved is None:
        raise HTTPException(404, detail="Voice input not found.")
    notified = False
    if saved.changed:
        try:
            await request.app.state.bot.send_message(
                chat_id=saved.telegram_chat_id,
                text=voice_confirmation_text(saved.draft.text),
                parse_mode=None,
                reply_markup=build_voice_confirmation_keyboard(
                    saved.draft.job_id,
                    saved.draft.revision,
                    mini_app_url=request.app.state.settings.mini_app_url,
                ),
            )
            notified = True
        except TelegramAPIError:
            logger.warning("Saved voice draft notification failed: job_id=%s", job_id)
    return VoiceDraftSaved(**saved.draft.model_dump(), notification_sent=notified)
