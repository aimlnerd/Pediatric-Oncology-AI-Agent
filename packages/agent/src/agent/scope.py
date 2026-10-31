"""Center Scope (Tier 1) and Protocol Selection (Tier 2), kept in session state.

The model never supplies these; code reads them from state and validates them
against the manifest (ADR 0002).
"""

from dataclasses import dataclass
from typing import cast

from agent.config import get_settings
from agent.manifest import CENTERS, Center, Protocol, is_center, load_manifest, protocols_for_center
from agent.state import CENTER, SELECTED_PROTOCOLS, ReadableState, StateLike


class ScopeError(ValueError):
    """The stored center/selection is missing or not allowed."""


@dataclass(frozen=True)
class Scope:
    center: Center
    protocol_ids: tuple[str, ...]

    @property
    def protocols(self) -> list[Protocol]:
        manifest = load_manifest()
        return [manifest[pid] for pid in self.protocol_ids]


def center_scope_ids(center: Center) -> list[str]:
    return [p.protocol_id for p in protocols_for_center(center)]


def validate_selection(center: Center, protocol_ids: list[str]) -> tuple[str, ...]:
    """Return the selection if it is a non-empty subset of the Center Scope."""
    allowed = set(center_scope_ids(center))
    unknown = [pid for pid in protocol_ids if pid not in allowed]
    if unknown:
        raise ScopeError(f"Not available at {center}: {', '.join(unknown)}")
    if not protocol_ids:
        raise ScopeError("Select at least one protocol.")
    return tuple(dict.fromkeys(protocol_ids))


def resolve_scope(state: ReadableState) -> Scope:
    """Read the scope from state (defaults apply when unset) and validate it. Never writes."""
    center = state.get(CENTER) or get_settings().default_center
    if not isinstance(center, str) or not is_center(center):
        raise ScopeError(f"Unknown center {center!r}; expected one of {', '.join(CENTERS)}")
    typed_center = cast(Center, center)
    selected = state.get(SELECTED_PROTOCOLS)
    ids = center_scope_ids(typed_center) if selected is None else [str(pid) for pid in selected]
    return Scope(typed_center, validate_selection(typed_center, ids))


def ensure_scope(state: StateLike) -> Scope:
    """Resolve the scope and store the defaults, so later nodes and the UI see them."""
    scope = resolve_scope(state)
    state[CENTER] = scope.center
    state[SELECTED_PROTOCOLS] = list(scope.protocol_ids)
    return scope
