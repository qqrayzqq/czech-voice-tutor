from datetime import UTC, datetime

from aiogram.utils.web_app import safe_parse_webapp_init_data

from czech_tutor.tutor.errors import MiniAppAuthError

MAX_INIT_DATA_AGE_SECONDS = 3600
MAX_FUTURE_CLOCK_SKEW_SECONDS = 30


def get_mini_app_user_id(
    init_data: str,
    bot_token: str,
    allowed_user_ids: frozenset[int],
) -> int:
    """Validate Telegram identity, access and the application's freshness policy."""
    try:
        result = safe_parse_webapp_init_data(token=bot_token, init_data=init_data)
    except ValueError as error:
        raise MiniAppAuthError("Invalid Mini App authentication data.") from error

    if result.user is None:
        raise MiniAppAuthError("Mini App user is missing.")
    if result.user.id not in allowed_user_ids:
        raise MiniAppAuthError("Mini App user is not allowed.")

    age_seconds = (datetime.now(UTC) - result.auth_date).total_seconds()
    if age_seconds > MAX_INIT_DATA_AGE_SECONDS or age_seconds < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        raise MiniAppAuthError("Invalid Mini App authentication time.")

    return result.user.id
