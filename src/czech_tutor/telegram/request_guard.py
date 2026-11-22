"""Bound per-user Telegram work across processes using committed database leases."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import CallbackQuery, Message, TelegramObject

from czech_tutor.telegram.localization import ui
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.request_guard import REQUEST_TIMEOUT_SECONDS, RequestClaim

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestGuardActions:
    acquire: Callable[[int, str | None, str], Awaitable[RequestClaim]]
    release: Callable[[int, str], Awaitable[None]]


async def _notify(event: Message | CallbackQuery, key: str, language: str) -> None:
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(ui(key, language))
        else:
            await event.answer(ui(key, language), parse_mode=None)
    except Exception:
        logger.warning("Failed to send request guard notification")


async def _release(actions: RequestGuardActions, user_id: int, token: str) -> None:
    """Drain the short release even if cancellation is repeated; leave expiry on DB failure."""
    task = asyncio.create_task(actions.release(user_id, token))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except StorageError:
            break
    try:
        task.result()
    except StorageError:
        # An already delivered reply must never be resent to repair bookkeeping.
        logger.warning("Failed to release request processing lease")
    if cancelled:
        raise asyncio.CancelledError


class RequestGuardMiddleware(BaseMiddleware):
    def __init__(
        self, actions: RequestGuardActions, *, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self.actions = actions
        self.timeout_seconds = timeout_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if get_flag(data, "concurrency_exempt", default=False):
            return await handler(event, data)
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        if event.from_user is None:
            return None
        return await self._run(handler, event, data)

    async def _run(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        assert user is not None
        request_key = (
            f"callback:{event.id}"
            if isinstance(event, CallbackQuery)
            else f"message:{event.chat.id}:{event.message_id}"
        )
        try:
            claim = await self.actions.acquire(user.id, user.username, request_key)
        except StorageError:
            await _notify(event, "request_unavailable", "ru")
            return None
        if claim.status != "acquired":
            if claim.status == "busy":
                await _notify(event, "request_busy", claim.interface_language)
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    logger.warning("Failed to acknowledge duplicate request")
            return None
        assert claim.token is not None
        deadline = asyncio.timeout(self.timeout_seconds)
        try:
            try:
                async with deadline:
                    return await handler(event, data)
            except TimeoutError:
                if not deadline.expired():
                    raise
                logger.warning("Request processing deadline exceeded")
                await _notify(event, "request_timeout", claim.interface_language)
                return None
        finally:
            await _release(self.actions, user.id, claim.token)
