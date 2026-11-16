def trim_history(
    turns: list[tuple[str, str]],
    max_chars: int = 6000,
    max_turns: int = 5,
) -> list[tuple[str, str]]:
    if max_chars < 0 or max_turns < 0:
        raise ValueError("History limits cannot be negative.")
    if max_chars == 0 or max_turns == 0:
        return []
    used_chars = 0
    result: list[tuple[str, str]] = []
    for user_answer, assistant_answer in reversed(turns):
        if max_turns <= 0:
            break
        curr_chars = len(user_answer) + len(assistant_answer)
        if used_chars + curr_chars > max_chars:
            break
        result.append((user_answer, assistant_answer))
        used_chars += curr_chars
        max_turns -= 1
    result.reverse()
    return result
