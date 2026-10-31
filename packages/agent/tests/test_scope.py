from typing import Any

import pytest

from agent.scope import ScopeError, ensure_scope, resolve_scope, validate_selection


def test_defaults_to_the_configured_center_with_every_protocol_selected() -> None:
    scope = resolve_scope({})
    assert scope.center == "UZG"
    assert len(scope.protocol_ids) == 27


def test_resolve_never_writes_but_ensure_stores_the_defaults() -> None:
    state: dict[str, Any] = {}
    resolve_scope(state)
    assert state == {}
    ensure_scope(state)
    assert state["center"] == "UZG"
    assert len(state["selected_protocols"]) == 27


def test_selection_must_be_inside_the_center_scope() -> None:
    with pytest.raises(ScopeError, match="chip-aml22"):
        validate_selection("UZA", ["alltogether1", "chip-aml22"])


def test_state_selection_outside_the_center_scope_is_rejected() -> None:
    state: dict[str, Any] = {"center": "UZA", "selected_protocols": ["far-rms"]}
    with pytest.raises(ScopeError, match="far-rms"):
        resolve_scope(state)


def test_empty_selection_is_rejected() -> None:
    with pytest.raises(ScopeError, match="at least one"):
        validate_selection("UZG", [])


def test_unknown_center_is_rejected() -> None:
    with pytest.raises(ScopeError, match="Unknown center"):
        resolve_scope({"center": "AZ Nowhere"})


def test_duplicates_are_collapsed_keeping_order() -> None:
    assert validate_selection("UZG", ["lch-iv", "far-rms", "lch-iv"]) == ("lch-iv", "far-rms")
