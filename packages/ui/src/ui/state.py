"""Session state schema and mutation helpers for the Streamlit UI.

Per the design doc (section 4 "Session State Schema"), all mutation
functions here take a mutable mapping (``st.session_state`` in the running
app, or a plain ``dict`` in tests) so the logic is testable without a live
Streamlit runtime.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from ui.data import get_protocols_for_hospital

StateMapping = MutableMapping[str, Any]

#: Keys and defaults for every field in the session state schema.
DEFAULT_STATE: dict[str, Any] = {
    "hospital": None,
    "available_protocols": [],
    "selected_protocol_ids": set(),
    "include_subprotocols": False,
    "chat_history": [],
    "adk_session_id": None,
    "pending_clarification": None,
    "pending_query": None,
    "feedback": {},
    "last_error": None,
    "is_admin": False,
}


def init_state(state: StateMapping) -> None:
    """Populate ``state`` with default values for any missing keys.

    Idempotent: safe to call on every Streamlit rerun.
    """
    for key, default in DEFAULT_STATE.items():
        if key not in state:
            # Copy mutable defaults (list/set) so reruns don't share references.
            state[key] = default.copy() if isinstance(default, (list, set, dict)) else default


def set_hospital(state: StateMapping, hospital: str | None, mapping: dict) -> None:
    """Handle a Tier 1 hospital change.

    Resets the Tier 2 selection and re-derives the available protocol list,
    as required by the design doc.
    """
    state["hospital"] = hospital
    state["available_protocols"] = get_protocols_for_hospital(
        mapping, hospital, include_subprotocols=state.get("include_subprotocols", False)
    )
    # All protocols are selected by default when a hospital is (re)chosen.
    state["selected_protocol_ids"] = {p["id"] for p in state["available_protocols"]}
    state["pending_clarification"] = None


def set_include_subprotocols(state: StateMapping, include: bool, mapping: dict) -> None:
    """Toggle subprotocol "Deep Search" and refresh the available protocol list."""
    previous_available_ids = {p["id"] for p in state.get("available_protocols", [])}
    previously_selected = state.get("selected_protocol_ids", set())

    state["include_subprotocols"] = include
    state["available_protocols"] = get_protocols_for_hospital(
        mapping, state.get("hospital"), include_subprotocols=include
    )
    available_ids = {p["id"] for p in state["available_protocols"]}
    newly_available_ids = available_ids - previous_available_ids

    # Keep existing selections that are still valid, default newly revealed
    # protocols/subprotocols to selected, and drop ids no longer available.
    state["selected_protocol_ids"] = (previously_selected & available_ids) | newly_available_ids


def toggle_protocol(state: StateMapping, protocol_id: str, checked: bool) -> None:
    """Check/uncheck a single protocol in the Tier 2 checklist."""
    selected = state.setdefault("selected_protocol_ids", set())
    if checked:
        selected.add(protocol_id)
    else:
        selected.discard(protocol_id)


def append_message(
    state: StateMapping,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    is_error: bool = False,
) -> None:
    """Append a chat turn to history.

    ``is_error`` marks a failed agent call so the UI can render it as an
    error rather than as clinical content — an infrastructure failure must
    never be mistaken for a grounded answer.
    """
    state.setdefault("chat_history", []).append(
        {
            "role": role,
            "content": content,
            "citations": citations or [],
            "is_error": is_error,
        }
    )


def reset_chat(state: StateMapping) -> None:
    """Clear chat history and any in-flight clarification, keeping filters intact."""
    state["chat_history"] = []
    state["pending_clarification"] = None
    state["pending_query"] = None
    state["feedback"] = {}
    state["adk_session_id"] = None
    state["last_error"] = None


def set_pending_clarification(state: StateMapping, question: str | None) -> None:
    """Record (or clear) the clarifying question the agent is waiting on."""
    state["pending_clarification"] = {"question": question} if question else None


def record_feedback(state: StateMapping, turn_index: int, score: int | None) -> None:
    """Store thumbs up/down feedback for the assistant message at ``turn_index``."""
    feedback = state.setdefault("feedback", {})
    if score is None:
        feedback.pop(turn_index, None)
    else:
        feedback[turn_index] = score
