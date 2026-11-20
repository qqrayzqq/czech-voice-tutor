class LLMError(RuntimeError):
    """A tutor reply could not be generated or validated."""


class StorageError(RuntimeError):
    """Conversation data could not be stored."""


class MiniAppAuthError(ValueError):
    """Mini App identity, access or authentication time is invalid."""


class InputTooLongError(RuntimeError):
    """Learner input exceeds the character limit before any paid processing."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        super().__init__(f"Input text exceeds the limit of {max_chars} characters.")
