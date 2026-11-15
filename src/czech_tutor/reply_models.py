from pydantic import BaseModel


class Correction(BaseModel):
    original: str
    corrected: str
    explanation_ru: str


class TutorReply(BaseModel):
    reply_cs: str
    note_ru: str
    corrections: list[Correction]


def parse_tutor_reply(raw_text: str) -> TutorReply:
    return TutorReply.model_validate_json(raw_text)
