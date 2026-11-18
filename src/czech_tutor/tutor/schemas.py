"""Data contracts for LLM output, Telegram delivery, and Mini App feedback."""

from pydantic import BaseModel, ConfigDict, field_validator


class Correction(BaseModel):
    """One before/after edit with a Russian explanation.

    The original fragment belongs to the current learner message; corrected
    contains its replacement. This nested value is used in TutorReply and
    ReplyFeedback. Persistence stores it as a separate MessageCorrection row;
    this model itself has no database identity or ORM behavior.
    """

    model_config = ConfigDict(frozen=True)

    original: str
    corrected: str
    explanation_ru: str


class TutorReply(BaseModel):
    """The structured LLM response validated before application use and storage.

    reply_cs is the Czech answer, note_ru is an optional Russian explanation
    represented by an empty string when absent, and corrections contains the
    learner's edits. The OpenRouter adapter parses this contract from JSON.
    It has no message IDs because the reply has not yet been saved.
    """

    model_config = ConfigDict(frozen=True)

    reply_cs: str
    note_ru: str
    corrections: list[Correction]

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


def parse_tutor_reply(raw_text: str) -> TutorReply:
    """Parse LLM JSON into TutorReply, raising ValidationError for invalid data."""
    return TutorReply.model_validate_json(raw_text)
