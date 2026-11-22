import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from czech_tutor.integrations.speech import SPEECH_VOICES


class ConfigurationError(ValueError):
    """The application cannot start with the supplied configuration."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    open_router_api_key: str = field(repr=False)
    database_url: str = field(repr=False)
    mini_app_url: str | None = None
    webhook_secret: str | None = field(default=None, repr=False)
    soniox_api_key: str | None = field(default=None, repr=False)
    tts_enabled: bool = True
    tts_voice: str = "nadine"
    webhook_max_connections: int = 4

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

        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("Set DATABASE_URL in .env or the environment.")

        mini_app_url = os.environ.get("MINI_APP_URL", "").strip() or None
        if mini_app_url is not None:
            parsed = urlsplit(mini_app_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
                raise ConfigurationError(
                    "MINI_APP_URL must be an HTTPS URL without query parameters or a fragment."
                )

        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip() or None
        if (
            webhook_secret is not None
            and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", webhook_secret) is None
        ):
            raise ConfigurationError(
                "TELEGRAM_WEBHOOK_SECRET must contain 1-256 ASCII letters, digits, '_' or '-'."
            )

        raw_max_connections = os.environ.get("TELEGRAM_WEBHOOK_MAX_CONNECTIONS", "4").strip()
        try:
            webhook_max_connections = int(raw_max_connections)
        except ValueError:
            raise ConfigurationError(
                "TELEGRAM_WEBHOOK_MAX_CONNECTIONS must be an integer from 1 to 100."
            ) from None
        if not 1 <= webhook_max_connections <= 100:
            raise ConfigurationError(
                "TELEGRAM_WEBHOOK_MAX_CONNECTIONS must be an integer from 1 to 100."
            )

        soniox_api_key = os.environ.get("SONIOX_API_KEY", "").strip() or None
        tts_enabled = os.environ.get("TTS_ENABLED", "true").strip().lower()
        if tts_enabled not in {"true", "false"}:
            raise ConfigurationError("TTS_ENABLED must be true or false.")
        tts_voice = os.environ.get("TTS_VOICE", "nadine").strip()
        if tts_voice not in SPEECH_VOICES:
            raise ConfigurationError("TTS_VOICE must be a supported HeyGen voice.")

        return cls(
            bot_token=token,
            allowed_user_ids=user_ids,
            open_router_api_key=open_router_api_key,
            database_url=database_url,
            mini_app_url=mini_app_url,
            webhook_secret=webhook_secret,
            soniox_api_key=soniox_api_key,
            tts_enabled=tts_enabled == "true",
            tts_voice=tts_voice,
            webhook_max_connections=webhook_max_connections,
        )
