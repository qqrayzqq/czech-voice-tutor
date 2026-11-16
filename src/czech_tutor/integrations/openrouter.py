import httpx

from czech_tutor.tutor.errors import LLMError
from czech_tutor.tutor.schemas import TutorReply, parse_tutor_reply


def build_request_payload(
    user_text: str,
    system_prompt: str,
    history: list[tuple[str, str]],
) -> dict[str, object]:
    res: dict[str, object] = {}
    res["model"] = "deepseek/deepseek-v4.1-flash-1017"
    res["provider"] = {
        "only": ["sail-research/fp4"],
        "allow_fallbacks": True,
        # USD per million tokens; headroom for price changes at the selected endpoint.
        "max_price": {"prompt": 0.20, "completion": 0.30},
    }
    res["reasoning"] = {"effort": "high"}
    res["temperature"] = 0.3
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
        "https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30.0
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
) -> TutorReply:
    payload = build_request_payload(user_text, system_prompt, history)

    try:
        response = await send_request(client, api_key, payload)
        raw_text = extract_reply(response)
        tutor_reply = parse_tutor_reply(raw_text)
        return tutor_reply
    except (httpx.HTTPError, ValueError) as error:
        raise LLMError("Failed to generate a tutor reply.") from error
