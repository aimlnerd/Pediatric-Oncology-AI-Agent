"""Typed construction of workflow Events.

ADK's Event accepts `message=` (chat text) and `route=` (which edge to follow) as
convenience arguments through a validator, but its signature does not declare them,
so type checkers reject direct use. This wrapper keeps the call sites typed.
"""

from typing import Any

from google.adk import Event


def make_event(
    *,
    message: str | None = None,
    output: Any = None,  # noqa: ANN401
    route: str | None = None,
) -> Event:
    data: dict[str, Any] = {}
    if message is not None:
        data["message"] = message
    if output is not None:
        data["output"] = output
    if route is not None:
        data["route"] = route
    return Event.model_validate(data)
