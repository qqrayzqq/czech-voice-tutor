from pydantic import BaseModel


class Correction(BaseModel):
    """One language correction returned by the LLM, not a database record."""

    original: str
    corrected: str
    explanation_ru: str


class TutorReply(BaseModel):
    reply_cs: str
    note_ru: str
    corrections: list[Correction]


class ReplyToSend(BaseModel):
    """A saved application reply prepared for delivery, not LLM output."""

    reply_id: int
    reply_cs: str


class ReplyFeedback(BaseModel):
    reply_id: int
    reply_cs: str
    note_ru: str
    corrections: list[Correction]


def parse_tutor_reply(raw_text: str) -> TutorReply:
    return TutorReply.model_validate_json(raw_text)
