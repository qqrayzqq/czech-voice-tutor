import httpx

from czech_tutor.tutor.errors import LLMError
from czech_tutor.tutor.schemas import TutorReply, clean_tutor_reply, parse_tutor_reply


def build_request_payload(
    user_text: str,
    system_prompt: str,
    history: list[tuple[str, str]],
    feedback_language: str = "ru",
) -> dict[str, object]:
    if feedback_language not in {"ru", "uk"}:
        raise ValueError("Unsupported feedback language.")
    explanation_lang = "Russian" if feedback_language == "ru" else "Ukrainian"
    res: dict[str, object] = {}
    res["model"] = "openai/gpt-6-luna"
    res["provider"] = {
        "only": ["amazon-bedrock/global"],
        "allow_fallbacks": False,
        "require_parameters": True,
        # USD per million base input/output tokens; cache pricing is separate.
        "max_price": {"prompt": 0.10, "completion": 0.50},
    }
    # Ask the provider for the same contract that Pydantic validates locally.
    res["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "tutor_reply",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "feedback_status": {
                        "type": "string",
                        "enum": ["evaluated", "not_evaluated", "clarification"],
                        "description": (
                            "Whether learner Czech was assessed or voice needs clarification."
                        ),
                    },
                    "feedback_language": {"type": "string", "enum": [feedback_language]},
                    "replaces_previous": {
                        "type": "boolean",
                        "description": "Explicit repair of previously misheard input only.",
                    },
                    "reply_cs": {
                        "type": "string",
                        "pattern": r"\S",
                        "description": (
                            "Required non-blank Czech answer, including when the requested "
                            "Russian explanation is in note_ru; no grammar lecture."
                        ),
                    },
                    "note_ru": {
                        "type": "string",
                        "description": (
                            f"Explanation strictly in {explanation_lang}. NEVER in Czech. "
                            "Czech quotations allowed. "
                            "Empty if not needed or already explained in corrections."
                        ),
                    },
                    "corrections": {
                        "type": "array",
                        "description": (
                            "Minimal local non-diacritic edits; never a full rewritten input."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["error", "advice"]},
                                "original": {
                                    "type": "string",
                                    "description": (
                                        "Exact erroneous fragment of the current user message."
                                    ),
                                },
                                "corrected": {
                                    "type": "string",
                                    "description": (
                                        "Corrected version preserving the intended meaning."
                                    ),
                                },
                                "explanation_ru": {
                                    "type": "string",
                                    "description": (
                                        f"Brief reason strictly in {explanation_lang}. "
                                        "NEVER in Czech. Czech quotations allowed."
                                    ),
                                },
                            },
                            "required": ["original", "corrected", "explanation_ru", "kind"],
                            "additionalProperties": False,
                        },
                    },
                    "diacritics": {
                        "type": "array",
                        "description": (
                            "Short accent-restoration pairs; distant changes stay separate."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string"},
                                "corrected": {"type": "string"},
                            },
                            "required": ["original", "corrected"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "reply_cs",
                    "note_ru",
                    "corrections",
                    "diacritics",
                    "feedback_status",
                    "feedback_language",
                    "replaces_previous",
                ],
                "additionalProperties": False,
            },
        },
    }
    # Low reasoning effort was selected in the completed model evaluation.
    res["reasoning"] = {"effort": "low"}
    res["max_tokens"] = 5000
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        },
    ]
    for past_user_text, past_assistant_text in history:
        messages.append({"role": "user", "content": past_user_text})
        messages.append({"role": "assistant", "content": past_assistant_text})

    messages.append({"role": "user", "content": user_text})
    res["messages"] = messages
    return res


async def send_request(
    client: httpx.AsyncClient,
    api_key: str,
    payload: dict[str, object],
) -> httpx.Response:
    headers: dict[str, str] = {
        "Authorization": "Bearer " + api_key,
    }
    response = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    return response


def extract_reply(response: httpx.Response) -> str:
    data = response.json()
    answer = data["choices"][0]
    if answer["finish_reason"] != "stop":
        raise ValueError("Model generation did not finish successfully.")
    message = answer["message"]["content"]
    if not isinstance(message, str):
        raise ValueError("Model response content must be a string.")
    if not message.strip():
        raise ValueError("Model response content is empty.")
    return message


async def generate_reply(
    client: httpx.AsyncClient,
    api_key: str,
    user_text: str,
    system_prompt: str,
    history: list[tuple[str, str]],
    *,
    feedback_language: str = "ru",
) -> TutorReply:
    payload = build_request_payload(user_text, system_prompt, history, feedback_language)

    try:
        response = await send_request(client, api_key, payload)
        raw_text = extract_reply(response)
        tutor_reply = parse_tutor_reply(raw_text)
        metadata = {"feedback_language", "feedback_status", "replaces_previous"}
        if not metadata.issubset(tutor_reply.model_fields_set):
            raise ValueError("Model response is missing assessment metadata.")
        if tutor_reply.feedback_language != feedback_language:
            raise ValueError("Model response has an unexpected feedback language.")
        if any("kind" not in correction.model_fields_set for correction in tutor_reply.corrections):
            raise ValueError("Model response is missing correction kinds.")
        return clean_tutor_reply(tutor_reply, user_text)
    except (httpx.HTTPError, ValueError) as error:
        raise LLMError("Failed to generate a tutor reply.") from error
