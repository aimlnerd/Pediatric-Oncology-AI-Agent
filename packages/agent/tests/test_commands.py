from typing import Any

from agent.commands import handle_command


def test_center_command_switches_scope_and_selects_everything_available() -> None:
    state: dict[str, Any] = {}
    reply = handle_command("/center uza", state)
    assert state["center"] == "UZA"
    assert len(state["selected_protocols"]) == 21
    assert "21 protocols available" in reply


def test_unknown_center_is_refused_and_state_is_untouched() -> None:
    state: dict[str, Any] = {"center": "UZG"}
    reply = handle_command("/center Atlantis", state)
    assert "Unknown center" in reply
    assert state["center"] == "UZG"


def test_select_narrows_the_protocol_selection() -> None:
    state: dict[str, Any] = {"center": "UZG"}
    reply = handle_command("/select far-rms, lch-iv", state)
    assert state["selected_protocols"] == ["far-rms", "lch-iv"]
    assert "[x] `far-rms`" in reply
    assert "[ ] `phitt`" in reply


def test_select_refuses_protocols_the_center_does_not_run() -> None:
    state: dict[str, Any] = {"center": "UZA"}
    reply = handle_command("/select far-rms", state)
    assert "Not available at UZA: far-rms" in reply
    assert "far-rms" not in state["selected_protocols"]


def test_select_all_restores_the_full_center_scope() -> None:
    state: dict[str, Any] = {"center": "UZA", "selected_protocols": ["lch-iv"]}
    handle_command("/select all", state)
    assert len(state["selected_protocols"]) == 21


def test_new_clears_the_conversation_notes() -> None:
    state: dict[str, Any] = {"case_notes": ["a", "b"]}
    handle_command("/new", state)
    assert state["case_notes"] == []


def test_unknown_commands_show_help() -> None:
    assert "/center" in handle_command("/bogus", {})
