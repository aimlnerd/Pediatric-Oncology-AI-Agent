"""Slash commands that set the Center Scope and Protocol Selection from chat.

The Streamlit UI will write the same state keys from its sidebar; in adk web these
commands stand in for it.
"""

from typing import cast

from agent.manifest import CENTERS, Center, is_center, protocols_for_center
from agent.scope import ScopeError, center_scope_ids, ensure_scope, validate_selection
from agent.state import CASE_NOTES, CENTER, SELECTED_PROTOCOLS, StateLike

HELP = (
    "Commands:\n"
    f"- `/center <{'|'.join(CENTERS)}>`: choose your center (Tier 1)\n"
    "- `/select <protocol_id>[,<protocol_id>...]` or `/select all`: choose protocols (Tier 2)\n"
    "- `/scope`: show your center and selected protocols\n"
    "- `/new`: forget the earlier conversation context"
)


def describe_scope(state: StateLike) -> str:
    try:
        scope = ensure_scope(state)
    except ScopeError as error:
        return str(error)
    available = protocols_for_center(scope.center)
    selected = set(scope.protocol_ids)
    lines = [f"**Center:** {scope.center}, {len(available)} protocols available."]
    for protocol in available:
        mark = "x" if protocol.protocol_id in selected else " "
        lines.append(f"- [{mark}] `{protocol.protocol_id}`: {protocol.label}")
    return "\n".join(lines)


def handle_command(text: str, state: StateLike) -> str:
    """Run a slash command and return the reply to show the clinician."""
    command, _, argument = text.strip().partition(" ")
    argument = argument.strip()

    if command == "/center":
        if not is_center(argument.upper()):
            return f"Unknown center `{argument}`. Choose one of: {', '.join(CENTERS)}."
        center = cast(Center, argument.upper())
        state[CENTER] = center
        state[SELECTED_PROTOCOLS] = center_scope_ids(center)
        return f"Center set to {center}; all its protocols are selected.\n\n{describe_scope(state)}"

    if command == "/select":
        try:
            current = ensure_scope(state)
            wanted = (
                center_scope_ids(current.center)
                if argument.lower() == "all"
                else [part.strip() for part in argument.split(",") if part.strip()]
            )
            state[SELECTED_PROTOCOLS] = list(validate_selection(current.center, wanted))
        except ScopeError as error:
            return f"{error}\n\nUse `/scope` to see the protocol ids available at your center."
        return describe_scope(state)

    if command == "/scope":
        return describe_scope(state)

    if command == "/new":
        state[CASE_NOTES] = []
        return "Earlier conversation context cleared."

    return HELP
