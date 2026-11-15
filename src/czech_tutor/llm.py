import httpx


def build_request_payload(
    user_text: str,
    system_prompt: str,
) -> dict[str, object]:
    res: dict[str, object] = {}
    res["model"] = "deepseek/deepseek-v4.1-flash-1017"
    res["reasoning"] = {"effort": "high"}
    res["temperature"] = 0.3
    res["max_tokens"] = 5000
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_text,
        },
    ]
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
