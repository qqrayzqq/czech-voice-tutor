import os
from dataclasses import dataclass, field


class ConfigurationError(ValueError):
    """The application cannot start with the supplied configuration."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    open_router_api_key: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("Set TELEGRAM_BOT_TOKEN in .env or the environment.")

        raw_ids = os.environ.get("ALLOWED_USER_IDS", "")
        try:
            user_ids = frozenset(int(value.strip()) for value in raw_ids.split(","))
        except ValueError:
            raise ConfigurationError(
                "ALLOWED_USER_IDS must contain comma-separated positive integer IDs."
            ) from None
        if any(user_id <= 0 for user_id in user_ids):
            raise ConfigurationError("ALLOWED_USER_IDS must contain only positive IDs.")

        open_router_api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not open_router_api_key:
            raise ConfigurationError("Set OPENROUTER_API_KEY in .env or the environment.")

        return cls(
            bot_token=token,
            allowed_user_ids=user_ids,
            open_router_api_key=open_router_api_key,
        )
