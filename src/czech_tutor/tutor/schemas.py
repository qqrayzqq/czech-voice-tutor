"""Data contracts for LLM output, Telegram delivery, and Mini App feedback."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DIACRITICS_EXPLANATION_UK = "Виправлено пропущені або неправильні діакритичні знаки."

DIACRITICS_EXPLANATION_RU = "Исправлены пропущенные или неверные диакритические знаки."


class ShortEdit(BaseModel):
    """One compact before/after pair without a model-generated explanation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    original: str
    corrected: str


class Correction(BaseModel):
    """One before/after edit with a Russian explanation.

    The original fragment belongs to the current learner message; corrected
    contains its replacement. This nested value is used in TutorReply and
    ReplyFeedback. Persistence stores it as a separate MessageCorrection row;
    this model itself has no database identity or ORM behavior.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    original: str
    corrected: str
    explanation_ru: str = ""
    kind: Literal["error", "advice"] = "error"


class TutorReply(BaseModel):
    """The structured LLM response validated before application use and storage.

    reply_cs is the Czech answer, note_ru is an optional Russian explanation
    represented by an empty string when absent, and corrections contains the
    learner's edits. The OpenRouter adapter parses this contract from JSON.
    It has no message IDs because the reply has not yet been saved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    reply_cs: str
    note_ru: str
    corrections: list[Correction]
    diacritics: list[ShortEdit] = Field(default_factory=list)
    feedback_status: Literal["evaluated", "not_evaluated", "clarification"] = "evaluated"
    feedback_language: Literal["ru", "uk"] = "ru"
    replaces_previous: bool = False

    @field_validator("reply_cs")
    @classmethod
    def validate_reply_cs(cls, value: str) -> str:
        """Reject blank Czech answers without changing valid text formatting."""
        if value.strip() == "":
            raise ValueError("Reply cs is empty")
        return value


class ReplyToSend(BaseModel):
    """The text service's internal result for the Telegram handler.

    reply_id identifies the saved assistant message. reply_cs is sent to the
    chat; corrections_count and has_note determine whether to attach the
    feedback button and how to label it. Detailed Russian feedback stays in
    storage for Mini App retrieval instead of being sent in the chat.
    """

    model_config = ConfigDict(frozen=True)

    reply_id: int
    reply_cs: str
    corrections_count: int
    has_note: bool = False
    advice_count: int = 0
    feedback_status: Literal["evaluated", "not_evaluated", "clarification", "superseded"] = (
        "evaluated"
    )
    feedback_language: Literal["ru", "uk"] = "ru"
    interface_language: Literal["ru", "uk"] = "ru"


class ReplyFeedback(BaseModel):
    """The saved feedback returned by the HTTP API to the Mini App.

    The feedback service assembles this contract from database rows after
    checking ownership. It includes the saved reply ID, Czech answer, Russian
    note, and correction details so the Mini App can render the full review
    without calling the LLM again. This is an API response, not an ORM entity.
    """

    model_config = ConfigDict(frozen=True)

    reply_id: int
    reply_cs: str
    note_ru: str
    corrections: list[Correction]
    advice_count: int = 0
    feedback_status: Literal["evaluated", "not_evaluated", "clarification", "superseded"] = (
        "evaluated"
    )
    feedback_language: Literal["ru", "uk"] = "ru"
    interface_language: Literal["ru", "uk"] = "ru"


class ProfileSettings(BaseModel):
    """Saved learner preferences returned by the Mini App profile API.

    Both fields must be supplied from storage rather than filled with DTO
    defaults. This immutable snapshot has no ORM behavior or persistence logic.
    """

    model_config = ConfigDict(frozen=True)

    language_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None
    answer_length: Literal["short", "medium", "long"]
    response_mode: Literal["text", "voice"]
    interface_language: Literal["ru", "uk"] = "ru"


class UpdateInterfaceLanguage(BaseModel):
    """Persist the authenticated learner's interface/explanation language."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    interface_language: Literal["ru", "uk"]


class UpdateResponseMode(BaseModel):
    """Persist the authenticated learner's output modality."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    response_mode: Literal["text", "voice"]


class UpdateLanguageLevel(BaseModel):
    """Validate an explicit language-level change, rejecting unrelated fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"]


class UpdateAnswerLength(BaseModel):
    """Validate an explicit answer-length change, rejecting unrelated fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer_length: Literal["short", "medium", "long"]


def parse_tutor_reply(raw_text: str) -> TutorReply:
    """Parse strict LLM JSON and reject duplicate object keys."""

    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key.")
            value[key] = item
        return value

    return TutorReply.model_validate(json.loads(raw_text, object_pairs_hook=unique_keys))


def clean_tutor_reply(reply: TutorReply, latest_text: str) -> TutorReply:
    """Silently remove malformed, no-op and duplicate edit cards.

    This validates structure only. It does not decide whether Czech grammar is
    correct or reconstruct edits omitted by the model.
    """
    if reply.feedback_status != "evaluated":
        return reply.model_copy(
            update={
                "corrections": [],
                "diacritics": [],
                "note_ru": "" if reply.feedback_status == "clarification" else reply.note_ru,
            }
        )
    corrections: list[Correction] = []
    seen: dict[tuple[str, str], int] = {}
    for edit in reply.corrections:
        key = (edit.original, edit.corrected)
        if not edit.original.strip() or edit.original not in latest_text:
            continue
        if edit.original == edit.corrected:
            continue
        if key in seen:
            current = corrections[seen[key]]
            if not current.explanation_ru.strip() and edit.explanation_ru.strip():
                corrections[seen[key]] = current.model_copy(
                    update={"explanation_ru": edit.explanation_ru}
                )
            continue
        seen[key] = len(corrections)
        corrections.append(edit)

    diacritics: list[ShortEdit] = []
    seen_diacritics: set[tuple[str, str]] = set(seen)
    for edit in reply.diacritics:
        key = (edit.original, edit.corrected)
        if not edit.original.strip() or edit.original not in latest_text:
            continue
        if edit.original == edit.corrected or key in seen_diacritics:
            continue
        seen_diacritics.add(key)
        diacritics.append(edit)

    return reply.model_copy(update={"corrections": corrections, "diacritics": diacritics})


def build_diacritics_correction(
    edits: list[ShortEdit], feedback_language: str = "ru"
) -> Correction | None:
    """Turn short accent pairs into one compact persisted/UI correction card."""
    if not edits:
        return None
    return Correction(
        original=" · ".join(edit.original for edit in edits),
        corrected=" · ".join(edit.corrected for edit in edits),
        explanation_ru=(
            DIACRITICS_EXPLANATION_UK if feedback_language == "uk" else DIACRITICS_EXPLANATION_RU
        ),
    )
