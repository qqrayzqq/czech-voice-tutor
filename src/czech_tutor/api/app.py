from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from czech_tutor.api.dependencies import get_current_user_id
from czech_tutor.config import Settings
from czech_tutor.persistence.connection import (
    create_database_engine,
    create_session_factory,
)
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.feedback_service import load_reply_feedback
from czech_tutor.tutor.schemas import ReplyFeedback


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()
    settings = Settings.from_env()
    engine = create_database_engine(settings.database_url)
    try:
        app.state.settings = settings
        app.state.session_factory = create_session_factory(engine)
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
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
            raise HTTPException(
                status_code=503,
                detail="Feedback is temporarily unavailable.",
            )
        if feedback is None:
            raise HTTPException(404, detail="Reply not found.")
        return feedback

    return app
