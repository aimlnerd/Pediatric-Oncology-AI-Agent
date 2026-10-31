"""Tests for session state helpers.

These use a plain ``dict`` to stand in for ``st.session_state`` so the state
logic is verified without needing a live Streamlit runtime.
"""

import pytest

from ui.data import load_hospital_mapping
from ui.state import (
    DEFAULT_STATE,
    append_message,
    init_state,
    reset_chat,
    set_hospital,
    set_include_subprotocols,
    toggle_protocol,
)


@pytest.fixture
def mapping():
    return load_hospital_mapping()


def test_init_state_sets_all_defaults():
    state: dict = {}
    init_state(state)
    assert set(state.keys()) == set(DEFAULT_STATE.keys())
    assert state["hospital"] is None
    assert state["selected_protocol_ids"] == set()
    assert state["chat_history"] == []
    assert state["is_admin"] is False


def test_init_state_is_idempotent_and_preserves_existing_values():
    state: dict = {}
    init_state(state)
    state["hospital"] = "UZA"
    init_state(state)  # simulate a Streamlit rerun
    assert state["hospital"] == "UZA"


def test_init_state_does_not_share_mutable_defaults_between_instances():
    state_a: dict = {}
    state_b: dict = {}
    init_state(state_a)
    init_state(state_b)
    state_a["chat_history"].append({"role": "user", "content": "hi", "citations": []})
    assert state_b["chat_history"] == []


def test_set_hospital_populates_available_and_selected_protocols(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)

    assert state["hospital"] == "UZA"
    assert len(state["available_protocols"]) > 0
    assert all(p["hospitals"].__contains__("UZA") for p in state["available_protocols"])
    # All available protocols are selected by default.
    assert state["selected_protocol_ids"] == {p["id"] for p in state["available_protocols"]}


def test_set_hospital_resets_selection_when_switching_hospitals(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    toggle_protocol(state, next(iter(state["selected_protocol_ids"])), False)
    assert len(state["selected_protocol_ids"]) < len(state["available_protocols"])

    set_hospital(state, "UCL", mapping)
    assert state["hospital"] == "UCL"
    assert state["selected_protocol_ids"] == {p["id"] for p in state["available_protocols"]}


def test_set_hospital_clears_pending_clarification(mapping):
    state: dict = {}
    init_state(state)
    state["pending_clarification"] = {"question": "What is the risk group?"}
    set_hospital(state, "UZA", mapping)
    assert state["pending_clarification"] is None


def test_toggle_protocol_adds_and_removes(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    protocol_id = next(iter(state["selected_protocol_ids"]))

    toggle_protocol(state, protocol_id, False)
    assert protocol_id not in state["selected_protocol_ids"]

    toggle_protocol(state, protocol_id, True)
    assert protocol_id in state["selected_protocol_ids"]


def test_set_include_subprotocols_adds_subprotocols_as_selected(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    protocols_before = len(state["available_protocols"])

    set_include_subprotocols(state, True, mapping)

    assert len(state["available_protocols"]) > protocols_before
    assert state["include_subprotocols"] is True
    # Newly revealed subprotocols default to selected.
    assert state["selected_protocol_ids"] == {p["id"] for p in state["available_protocols"]}


def test_set_include_subprotocols_preserves_manual_deselection(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    protocol_id = next(iter(state["selected_protocol_ids"]))
    toggle_protocol(state, protocol_id, False)

    set_include_subprotocols(state, True, mapping)

    # The manually unchecked protocol should stay unchecked after the toggle.
    assert protocol_id not in state["selected_protocol_ids"]


def test_set_include_subprotocols_false_removes_subprotocol_selection(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    set_include_subprotocols(state, True, mapping)

    set_include_subprotocols(state, False, mapping)

    assert state["include_subprotocols"] is False
    assert all(p["doc_type"] == "protocol" for p in state["available_protocols"])
    assert state["selected_protocol_ids"] == {p["id"] for p in state["available_protocols"]}


def test_append_message_appends_role_content_and_citations():
    state: dict = {}
    init_state(state)
    append_message(state, "user", "What is the induction dose?")
    append_message(
        state,
        "assistant",
        "See induction phase.",
        citations=[{"doc_title": "ALLTogether1", "page": 42}],
    )

    assert len(state["chat_history"]) == 2
    assert state["chat_history"][0] == {
        "role": "user",
        "content": "What is the induction dose?",
        "citations": [],
        "is_error": False,
    }
    assert state["chat_history"][1]["citations"] == [
        {"doc_title": "ALLTogether1", "page": 42}
    ]


def test_append_message_can_mark_a_turn_as_an_error():
    """Failed turns are flagged so the UI never renders them as clinical content."""
    state: dict = {}
    init_state(state)
    append_message(state, "assistant", "Something broke.", is_error=True)

    assert state["chat_history"][0]["is_error"] is True


def test_reset_chat_clears_history_and_clarification_but_keeps_filters(mapping):
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", mapping)
    append_message(state, "user", "hi")
    state["pending_clarification"] = {"question": "age?"}

    reset_chat(state)

    assert state["chat_history"] == []
    assert state["pending_clarification"] is None
    assert state["hospital"] == "UZA"
    assert len(state["selected_protocol_ids"]) > 0
