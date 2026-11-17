from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request

from czech_tutor.config import Settings
from czech_tutor.telegram.mini_app_auth import get_mini_app_user_id
from czech_tutor.tutor.errors import MiniAppAuthError


async def get_current_user_id(
    request: Request,
    init_data: Annotated[
        str | None,
        Header(alias="X-Telegram-Init-Data"),
    ] = None,
) -> int:
    if init_data is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid Mini App authentication.",
        )
    settings: Settings = request.app.state.settings
    try:
        return get_mini_app_user_id(init_data, settings.bot_token, settings.allowed_user_ids)
    except MiniAppAuthError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Mini App authentication.",
        )


async def verify_telegram_webhook(
    request: Request,
    secret_token: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> None:
    settings: Settings = request.app.state.settings
    if settings.webhook_secret is None:
        raise HTTPException(
            status_code=503,
            detail="Webhook is not configured.",
        )
    if secret_token is None:
        raise HTTPException(
            status_code=403,
            detail="Invalid Webhook authentication.",
        )
    if not compare_digest(
        secret_token.encode("utf-8"),
        settings.webhook_secret.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid Webhook authentication.",
        )
