from sqlalchemy.ext.asyncio import AsyncSession

from czech_tutor.persistence.users import get_user, update_answer_length, update_language_level
from czech_tutor.tutor.schemas import ProfileSettings, UpdateAnswerLength, UpdateLanguageLevel


async def load_profile(
    session: AsyncSession,
    telegram_user_id: int,
) -> ProfileSettings | None:
    """Build a profile DTO for an existing learner in the caller's session."""

    user = await get_user(session, telegram_user_id)
    if user is None:
        return None
    return ProfileSettings(
        language_level=user.language_level,
        answer_length=user.answer_length,
    )


async def save_language_level(
    session: AsyncSession,
    telegram_user_id: int,
    preference: UpdateLanguageLevel,
) -> UpdateLanguageLevel | None:
    """Save the level and reuse its validated DTO; the caller must still commit."""
    found = await update_language_level(session, telegram_user_id, preference.language_level)
    if not found:
        return None
    return preference


async def save_answer_length(
    session: AsyncSession,
    telegram_user_id: int,
    preference: UpdateAnswerLength,
) -> UpdateAnswerLength | None:
    """Save answer length and reuse its validated DTO before caller commit."""
    found = await update_answer_length(session, telegram_user_id, preference.answer_length)
    if not found:
        return None
    return preference
