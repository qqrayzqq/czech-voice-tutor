class LLMError(RuntimeError):
    """A tutor reply could not be generated or validated."""


class StorageError(RuntimeError):
    """Conversation data could not be stored."""
