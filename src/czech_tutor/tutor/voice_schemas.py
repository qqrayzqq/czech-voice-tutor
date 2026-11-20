"""Immutable snapshots crossing the voice service / Telegram boundary."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from czech_tutor.tutor.schemas import ReplyToSend

MAX_VOICE_SECONDS = 120
MAX_VOICE_BYTES = 5 * 1024 * 1024
MAX_VOICE_TEXT_CHARS = 2000


@dataclass(frozen=True)
class VoiceRecovery:
    job_id: int
    revision: int
    preview: str


@dataclass(frozen=True)
class VoiceInput:
    job_id: int
    status: str
    text: str | None
    revision: int
    failure_reason: str | None = None
    can_recover_reply: bool = False
    pending_replies: tuple[VoiceRecovery, ...] = ()


@dataclass(frozen=True)
class VoiceReply:
    job_id: int
    delivery_token: str
    reply: ReplyToSend
    revision: int = 0


class EmptyVoiceTextError(ValueError):
    """An edited or confirmed voice input is empty."""


class VoiceDraft(BaseModel):
    """Owner-only editor snapshot; initial ASR may still be empty or too long."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    job_id: int
    revision: int
    text: str
    max_chars: Literal[2000] = MAX_VOICE_TEXT_CHARS


class UpdateVoiceDraft(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    revision: int = Field(ge=0)
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_VOICE_TEXT_CHARS)
    ]


class VoiceDraftSaved(VoiceDraft):
    notification_sent: bool


@dataclass(frozen=True)
class SavedVoiceDraft:
    draft: VoiceDraft
    telegram_chat_id: int
    changed: bool


class VoiceDraftConflict(RuntimeError):
    """The draft expired, was closed, or changed since the editor loaded it."""
