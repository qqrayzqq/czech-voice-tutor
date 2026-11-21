"""Stable Czech feedback copy and persisted at-most-once notice attempts."""

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.persistence.feedback_queries import get_reply_for_user
from czech_tutor.persistence.models import Message
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.schemas import ReplyToSend

_ERROR = (
    "Děkuji! V téhle větě je něco k opravě. Podívejme se na to společně. ✍️",
    "Rozumím ti. V rozboru najdeš opravu, která ti pomůže příště. ✍️",
    "Díky za zprávu! Mám pro tebe krátké vysvětlení k opravě. ✍️",
    "Pojďme se podívat na jednu jazykovou věc v tvé zprávě. ✍️",
)
_ADVICE = (
    "Všechno je správně! Mám jen tip, jak to říct přirozeněji. 💡",
    "Bez chyby! Přidal jsem malý tip k vyjádření. 💡",
    "Tohle je správně. V rozboru najdeš ještě jeden užitečný tip. 💡",
)
_PRAISE = (
    "Skvěle, tentokrát bez chyby! 👏",
    "Výborně, všechno je správně! 🌟",
    "Hezky řečeno, nemám co opravovat! 👏",
    "Povedlo se, tahle zpráva je bez chyby! 🌿",
)


def notice_text(answer: ReplyToSend) -> str | None:
    if answer.feedback_status != "evaluated":
        return None
    turn = answer.reply_id // 2
    if answer.corrections_count:
        return _ERROR[turn % len(_ERROR)]
    if answer.advice_count:
        return _ADVICE[turn % len(_ADVICE)]
    # Leave space for conversation: praise about half of evaluated clean replies.
    return _PRAISE[(turn // 2) % len(_PRAISE)] if turn % 2 == 0 else None


async def claim_notice(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, reply_id: int
) -> bool:
    try:
        async with session_factory.begin() as session:
            if await get_reply_for_user(session, reply_id, user_id) is None:
                return False
            result = await session.execute(
                update(Message)
                .where(
                    Message.id == reply_id,
                    Message.notice_state == "pending",
                    Message.feedback_status == "evaluated",
                    Message.telegram_message_id.is_(None),
                )
                .values(notice_state="sending")
                .returning(Message.id)
            )
            return result.scalar_one_or_none() is not None
    except SQLAlchemyError:
        raise StorageError("Failed to reserve feedback notice.") from None


async def complete_notice(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, reply_id: int, message_id: int
) -> None:
    try:
        async with session_factory.begin() as session:
            if await get_reply_for_user(session, reply_id, user_id) is None:
                return
            await session.execute(
                update(Message)
                .where(
                    Message.id == reply_id,
                    Message.notice_state == "sending",
                )
                .values(notice_state="sent", notice_message_id=message_id)
            )
    except SQLAlchemyError:
        raise StorageError("Failed to record feedback notice.") from None
