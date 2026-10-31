"""Function nodes of the workflow: intake, routing, clarification pause and final answer."""

import logging
from collections.abc import AsyncGenerator
from typing import Literal, cast

from google.adk import Context, Event
from google.adk.events import RequestInput
from pydantic import BaseModel

from agent.citations import chunks_from_state, resolve_citations
from agent.commands import handle_command
from agent.config import get_settings
from agent.events import make_event
from agent.manifest import protocols_for_center
from agent.pii import redact_pii
from agent.scope import Scope, ScopeError, ensure_scope
from agent.state import (
    CASE_NOTES,
    CLARIFY_ROUNDS,
    MAX_CASE_NOTES,
    QUESTION,
    REFUSAL_TEXT,
    RETRIEVED_CHUNKS,
    SEARCH_COUNT,
    StateLike,
)

logger = logging.getLogger(__name__)

DISCLAIMER = "_Search utility only: verify against the protocol before any clinical use._"
DEFAULT_REFUSAL = (
    "I cannot answer this question. My knowledge base is limited to pediatric hematology and "
    "oncology clinical trial protocols."
)


class ScopeDecision(BaseModel):
    in_scope: bool
    refusal_text: str = ""


class ClarifierDecision(BaseModel):
    action: Literal["ask", "ready"]
    question: str = ""


class AnswerDraft(BaseModel):
    status: Literal["answered", "not_covered"]
    body_markdown: str


def _add_note(state: StateLike, note: str) -> None:
    notes: list[str] = [*(state.get(CASE_NOTES) or []), note]
    state[CASE_NOTES] = notes[-MAX_CASE_NOTES:]


def intake(ctx: Context, node_input: str) -> Event:
    """Handle slash commands, redact personal data, reset per-turn state, record the question."""
    text = node_input.strip()
    if text.startswith("/"):
        return make_event(message=handle_command(text, ctx.state), route="command")
    try:
        ensure_scope(ctx.state)
    except ScopeError as error:
        return make_event(
            message=f"{error}\n\nUse `/center` and `/select` to fix your scope.", route="command"
        )

    question = redact_pii(text)
    ctx.state[QUESTION] = question
    ctx.state[CLARIFY_ROUNDS] = 0
    ctx.state[SEARCH_COUNT] = 0
    ctx.state[RETRIEVED_CHUNKS] = {}
    _add_note(ctx.state, question)
    return make_event(output=question, route="question")


def route_scope(ctx: Context, node_input: ScopeDecision) -> Event:
    if node_input.in_scope:
        return make_event(output=ctx.state[QUESTION], route="in_scope")
    ctx.state[REFUSAL_TEXT] = node_input.refusal_text.strip() or DEFAULT_REFUSAL
    return make_event(route="out_of_scope")


def refuse(ctx: Context) -> Event:
    return make_event(message=ctx.state[REFUSAL_TEXT], output={"kind": "out_of_scope"})


def route_clarify(ctx: Context, node_input: ClarifierDecision) -> Event:
    rounds = int(ctx.state.get(CLARIFY_ROUNDS) or 0)
    wants_to_ask = node_input.action == "ask" and bool(node_input.question.strip())
    if wants_to_ask and rounds < get_settings().max_clarification_rounds:
        ctx.state[CLARIFY_ROUNDS] = rounds + 1
        return make_event(output=node_input.question.strip(), route="ask")
    return make_event(output=ctx.state[QUESTION], route="ready")


async def ask_user(node_input: str) -> AsyncGenerator[Event | RequestInput]:
    """Show the clarifying question and pause; the clinician's reply becomes this node's output."""
    yield make_event(message=node_input)
    yield RequestInput(message=node_input, response_schema=None)


def reply_text(value: object) -> str:
    """The clinician's reply as text. adk web sends JSON values, so 12 arrives as an int."""
    if isinstance(value, dict):
        wrapped = cast(dict[str, object], value)
        value = wrapped.get("result", wrapped)
    return str(value).strip()


def record_reply(ctx: Context, node_input: object) -> str:
    reply = redact_pii(reply_text(node_input))
    _add_note(ctx.state, f"Clinician's answer to the clarifying question: {reply}")
    return reply


def prepare_answer(ctx: Context, node_input: object) -> Event:
    """Skip the answerer entirely when nothing was retrieved, so it can never improvise."""
    if ctx.state.get(RETRIEVED_CHUNKS):
        return make_event(output=ctx.state[QUESTION], route="found")
    return make_event(route="empty")


def _unselected_hint(scope: Scope) -> str:
    unselected = [
        p.short_title
        for p in protocols_for_center(scope.center)
        if p.protocol_id not in scope.protocol_ids
    ]
    if not unselected:
        return ""
    shown = ", ".join(unselected[:6]) + (", ..." if len(unselected) > 6 else "")
    return (
        f"\n\n{len(unselected)} other protocol(s) at {scope.center} are not selected ({shown}). "
        "Use `/select` to include them."
    )


def not_covered_message(scope: Scope) -> str:
    selected = ", ".join(p.label for p in scope.protocols)
    return (
        f"This is not covered in the selected protocols ({selected}). I will not guess."
        f"{_unselected_hint(scope)}"
    )


def not_covered(ctx: Context) -> Event:
    return make_event(
        message=not_covered_message(ensure_scope(ctx.state)), output={"kind": "not_covered"}
    )


def finalize(ctx: Context, node_input: AnswerDraft) -> Event:
    """Resolve [cN] markers against the retrieved chunks; an answer without citations is dropped."""
    scope = ensure_scope(ctx.state)
    chunks = chunks_from_state(ctx.state.get(RETRIEVED_CHUNKS))
    resolved = resolve_citations(node_input.body_markdown, chunks)
    if resolved.unknown_ids:
        logger.warning("Dropped citation ids not retrieved this turn: %s", resolved.unknown_ids)

    if node_input.status == "not_covered" or not resolved.citations:
        return make_event(message=not_covered_message(scope), output={"kind": "not_covered"})

    sources = "\n".join(f"- {c.label} (`{c.filename}`)" for c in resolved.citations)
    message = f"{resolved.text}\n\n**Sources**\n{sources}\n\n{DISCLAIMER}"
    return make_event(
        message=message,
        output={
            "kind": "answer",
            "body_markdown": resolved.text,
            "citations": [c.model_dump() for c in resolved.citations],
        },
    )
