import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

import httpx
from aiogram import Bot
from aiogram.types import Update
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from czech_tutor.api.dependencies import get_current_user_id, verify_telegram_webhook
from czech_tutor.api.voice_routes import router as voice_router
from czech_tutor.config import Settings
from czech_tutor.persistence.connection import (
    create_database_engine,
    create_session_factory,
)
from czech_tutor.telegram.bootstrap import create_dispatcher
from czech_tutor.telegram.menu import set_profile_menu
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.feedback_service import load_reply_feedback
from czech_tutor.tutor.profile_service import (
    load_profile,
    save_answer_length,
    save_interface_language,
    save_language_level,
    save_response_mode,
)
from czech_tutor.tutor.schemas import (
    ProfileSettings,
    ReplyFeedback,
    UpdateAnswerLength,
    UpdateInterfaceLanguage,
    UpdateLanguageLevel,
    UpdateResponseMode,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    settings = Settings.from_env()
    engine = create_database_engine(settings.database_url)
    bot = Bot(settings.bot_token)
    app.state.bot = bot
    try:
        app.state.settings = settings
        app.state.session_factory = create_session_factory(engine)
        async with httpx.AsyncClient() as client:
            dispatcher = create_dispatcher(settings, app.state.session_factory, client)
            app.state.dispatcher = dispatcher
            yield
    finally:
        try:
            await engine.dispose()
        finally:
            await bot.session.close()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(voice_router)
    app.mount(
        "/mini-app",
        StaticFiles(directory=FilePath(__file__).resolve().parent / "static", html=True),
        name="mini-app",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/replies/{reply_id}/feedback")
    async def get_reply_feedback(
        request: Request,
        reply_id: Annotated[int, Path(gt=0)],
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> ReplyFeedback:
        session_factory = request.app.state.session_factory
        try:
            async with session_factory() as session:
                feedback = await load_reply_feedback(session, reply_id, telegram_user_id)
        except (SQLAlchemyError, StorageError):
            logger.warning("Failed to load reply feedback: reply_id=%s", reply_id)
            raise HTTPException(
                status_code=503,
                detail="Feedback is temporarily unavailable.",
            )
        if feedback is None:
            raise HTTPException(404, detail="Reply not found.")
        return feedback

    @app.post(
        "/telegram/webhook",
        dependencies=[Depends(verify_telegram_webhook)],
    )
    async def telegram_webhook(
        update: Update,
        request: Request,
    ) -> dict[str, bool]:
        bot = request.app.state.bot
        dispatcher = request.app.state.dispatcher
        await dispatcher.feed_update(bot, update)
        return {"ok": True}

    @app.get("/api/profile")
    async def get_profile(
        request: Request,
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> ProfileSettings:
        """Read saved preferences for the authenticated Mini App user."""
        session_factory = request.app.state.session_factory
        try:
            async with session_factory() as session:
                profile = await load_profile(session, telegram_user_id)
        except SQLAlchemyError:
            logger.warning("Failed to load profile.")
            raise HTTPException(
                status_code=503,
                detail="Profile is temporarily unavailable.",
            )
        if profile is None:
            raise HTTPException(404, detail="Profile not found.")
        return profile

    @app.patch("/api/profile/language-level")
    async def patch_language_level(
        preference: UpdateLanguageLevel,
        request: Request,
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> UpdateLanguageLevel:
        """Change only the authenticated learner's level and confirm after commit."""
        session_factory = request.app.state.session_factory
        try:
            async with session_factory.begin() as session:
                saved = await save_language_level(session, telegram_user_id, preference)
        except SQLAlchemyError:
            logger.warning("Failed to save profile preference: language_level")
            raise HTTPException(
                status_code=503,
                detail="Profile is temporarily unavailable.",
            )
        if saved is None:
            raise HTTPException(404, detail="Profile not found.")
        return saved

    @app.patch("/api/profile/answer-length")
    async def patch_answer_length(
        preference: UpdateAnswerLength,
        request: Request,
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> UpdateAnswerLength:
        """Change only answer length and return its value after successful commit."""
        session_factory = request.app.state.session_factory
        try:
            async with session_factory.begin() as session:
                saved = await save_answer_length(session, telegram_user_id, preference)
        except SQLAlchemyError:
            logger.warning("Failed to save profile preference: answer_length")
            raise HTTPException(
                status_code=503,
                detail="Profile is temporarily unavailable.",
            )
        if saved is None:
            raise HTTPException(404, detail="Profile not found.")
        return saved

    @app.patch("/api/profile/response-mode")
    async def patch_response_mode(
        preference: UpdateResponseMode,
        request: Request,
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> UpdateResponseMode:
        try:
            async with request.app.state.session_factory.begin() as session:
                saved = await save_response_mode(session, telegram_user_id, preference)
        except SQLAlchemyError:
            logger.warning("Failed to save profile preference: response_mode")
            raise HTTPException(503, detail="Profile is temporarily unavailable.") from None
        if saved is None:
            raise HTTPException(404, detail="Profile not found.")
        return saved

    @app.patch("/api/profile/interface-language")
    async def patch_interface_language(
        preference: UpdateInterfaceLanguage,
        request: Request,
        telegram_user_id: Annotated[int, Depends(get_current_user_id)],
    ) -> UpdateInterfaceLanguage:
        try:
            async with request.app.state.session_factory.begin() as session:
                saved = await save_interface_language(session, telegram_user_id, preference)
        except SQLAlchemyError:
            logger.warning("Failed to save profile preference: interface_language")
            raise HTTPException(503, detail="Profile is temporarily unavailable.") from None
        if saved is None:
            raise HTTPException(404, detail="Profile not found.")
        await set_profile_menu(
            getattr(request.app.state, "bot", None), telegram_user_id,
            getattr(request.app.state.settings, "mini_app_url", None), saved.interface_language,
        )
        return saved

    return app
