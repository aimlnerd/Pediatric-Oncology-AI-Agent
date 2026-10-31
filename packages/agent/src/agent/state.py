"""Session-state keys shared by the UI, nodes and tools."""

from typing import Any, Protocol

# Written by the UI (or the /center and /select commands in adk web).
CENTER = "center"
SELECTED_PROTOCOLS = "selected_protocols"

# Conversation context, kept across turns.
CASE_NOTES = "case_notes"

# Reset at the start of every turn.
QUESTION = "question"
REFUSAL_TEXT = "refusal_text"
CLARIFY_ROUNDS = "clarify_rounds"
SEARCH_COUNT = "search_count"
RETRIEVED_CHUNKS = "retrieved_chunks"

MAX_CASE_NOTES = 8


class ReadableState(Protocol):
    """Read access shared by ADK's State, its read-only instruction view, and plain dicts."""

    def __getitem__(self, key: str, /) -> Any: ...  # noqa: ANN401
    def __contains__(self, key: object, /) -> bool: ...
    def get(self, key: str, /) -> Any | None: ...  # noqa: ANN401


class StateLike(ReadableState, Protocol):
    """Read/write state, as seen by nodes and tools."""

    def __setitem__(self, key: str, value: Any, /) -> None: ...  # noqa: ANN401
