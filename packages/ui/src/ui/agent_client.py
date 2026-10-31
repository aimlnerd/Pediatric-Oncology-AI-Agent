"""Client wrapper around the AI Solution Layer (ADK agent).

Per the design doc (section 8), the chat UI talks only to this module so the
UI layer stays decoupled from ``agent/`` internals.

Two implementations share one interface:

* :class:`AdkAgentClient` — build step 5, wraps the ``agent`` package's
  :class:`~agent.runner.OncoProtocolAgentRunner` (ADK ``Runner`` +
  ``InMemorySessionService`` + Vertex AI Search).
* :class:`MockAgentClient` — rule-based canned responses, used for offline
  development and the test suite.

:func:`get_agent_client` picks between them; see :data:`AGENT_BACKEND_ENV`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MOCK_RESPONSES_PATH = Path(__file__).parent / "mock_data" / "sample_responses.json"

#: ``adk`` forces the real agent, ``mock`` forces the stub, ``auto`` (default)
#: uses the real agent when the ``agent`` package can be loaded.
AGENT_BACKEND_ENV = "UI_AGENT_BACKEND"

#: A rule must match at least this many keywords to be considered a hit.
_MIN_KEYWORD_MATCHES = 2

#: Retrieved sources shown under an answer (the agent may retrieve many chunks).
MAX_DISPLAYED_CITATIONS = 6


@dataclass
class AgentTurnResult:
    """The outcome of a single agent turn."""

    answer_markdown: str
    citations: list[dict] = field(default_factory=list)
    is_clarifying_question: bool = False
    #: Set when the turn failed (infrastructure error), not when the agent
    #: declined to answer — a refusal is still a valid, grounded turn.
    is_error: bool = False


class AgentClient(Protocol):
    """Interface the chat UI depends on."""

    def get_or_create_session(self, adk_session_id: str | None) -> str: ...

    def send(
        self,
        query: str,
        selected_protocols: list[str],
        include_subprotocols: bool,
        session_id: str,
        hospital: str | None = None,
    ) -> AgentTurnResult: ...


# --- Shared helpers ------------------------------------------------------


def _matches_keyword(text: str, keyword: str) -> bool:
    """Word-boundary match that tolerates simple plurals ("platelet" -> "platelets")."""
    return re.search(rf"\b{re.escape(keyword)}(s|es)?\b", text) is not None


def _count_keyword_matches(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if _matches_keyword(text, kw))


CLARIFICATION_MAX_CHARS = 1200


def looks_like_clarifying_question(answer: str, *, has_citations: bool = False) -> bool:
    """Heuristic: is the agent's reply a clarifying question rather than an answer?

    The ADK agent returns free-form markdown with no machine-readable flag, so
    the UI infers this to decide whether to switch the input placeholder to
    "Answer the clarifying question...". Only affects that affordance.

    A genuine clarifying question is short and uncited. Long, cited answers
    frequently *end* with a follow-up offer ("Would you like the maintenance
    schedule too?"), which must not be mistaken for a clarification request.
    """
    answer = answer.strip()
    if not answer:
        return False
    if has_citations or len(answer) > CLARIFICATION_MAX_CHARS:
        return False

    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    # Ignore trailing citation/source lines when looking for the closing question.
    for line in reversed(lines[-3:]):
        stripped = re.sub(r"[*_`>#\-\s]+$", "", line)
        if stripped.endswith("?"):
            return True
    return False


def normalise_agent_citations(raw_citations: list[dict]) -> list[dict]:
    """Convert the agent's retrieved chunks into the UI's citation shape.

    Agent chunks carry ``short_title``/``filename``/``page_display``; the UI
    renders ``doc_title`` plus a page label. Chunks are de-duplicated by
    document+page and capped so a long retrieval does not flood the caption.
    """
    seen: set[tuple[str, str]] = set()
    citations: list[dict] = []
    for chunk in raw_citations:
        doc_title = chunk.get("short_title") or chunk.get("title") or "Unknown document"
        page_display = chunk.get("page_display") or ""
        key = (doc_title, page_display)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "doc_title": doc_title,
                "page_display": page_display,
                "page": chunk.get("page_start") or None,
                "filename": chunk.get("filename", ""),
                "relevance_score": chunk.get("relevance_score"),
            }
        )
        if len(citations) >= MAX_DISPLAYED_CITATIONS:
            break
    return citations


# --- Real ADK-backed client ---------------------------------------------


class AdkAgentClient:
    """Adapter over :class:`agent.runner.OncoProtocolAgentRunner`.

    The runner owns a single persistent asyncio event loop, so calls are
    serialised with a lock: Streamlit may service several browser sessions
    from the same cached client instance.
    """

    def __init__(self, runner=None, default_hospital: str = "UZG") -> None:
        if runner is None:
            from agent import OncoProtocolAgentRunner

            runner = OncoProtocolAgentRunner()
        self._runner = runner
        self._default_hospital = default_hospital
        self._lock = threading.Lock()
        self._known_sessions: set[str] = set()

    def get_or_create_session(self, adk_session_id: str | None) -> str:
        """Allocate a session id; the ADK runner creates the session lazily."""
        session_id = adk_session_id or f"ui-{uuid.uuid4().hex[:12]}"
        self._known_sessions.add(session_id)
        return session_id

    def send(
        self,
        query: str,
        selected_protocols: list[str],
        include_subprotocols: bool,
        session_id: str,
        hospital: str | None = None,
    ) -> AgentTurnResult:
        session_id = self.get_or_create_session(session_id)
        with self._lock:
            result = self._runner.ask(
                query=query,
                session_id=session_id,
                selected_hospital=hospital or self._default_hospital,
                # None means "all protocols active at this hospital".
                selected_protocols=list(selected_protocols or []) or None,
                include_subprotocols=include_subprotocols,
            )

        answer = (result.get("answer") or "").strip()
        if not answer:
            answer = "The assistant did not return an answer. Please try rephrasing your question."

        citations = normalise_agent_citations(result.get("citations") or [])
        return AgentTurnResult(
            answer_markdown=answer,
            citations=citations,
            is_clarifying_question=looks_like_clarifying_question(
                answer, has_citations=bool(citations)
            ),
        )


# --- Offline mock client -------------------------------------------------


class MockAgentClient:
    """Rule-based stand-in for the ADK agent.

    Deliberately honours the UI's Tier 2 protocol scoping and the subprotocol
    "Deep Search" toggle so the full clinician flow (including clarifying
    questions and out-of-scope refusals) can be exercised offline.
    """

    def __init__(self, responses_path: Path = MOCK_RESPONSES_PATH) -> None:
        with responses_path.open("r", encoding="utf-8") as fh:
            self._responses = json.load(fh)
        # session_id -> list of {"query": str, "result": AgentTurnResult}
        self._sessions: dict[str, list[dict]] = {}

    def get_or_create_session(self, adk_session_id: str | None) -> str:
        if adk_session_id and adk_session_id in self._sessions:
            return adk_session_id
        session_id = adk_session_id or f"session-{uuid.uuid4().hex[:8]}"
        self._sessions.setdefault(session_id, [])
        return session_id

    def get_history(self, session_id: str) -> list[dict]:
        return self._sessions.get(session_id, [])

    def send(
        self,
        query: str,
        selected_protocols: list[str],
        include_subprotocols: bool,
        session_id: str,
        hospital: str | None = None,
    ) -> AgentTurnResult:
        session_id = self.get_or_create_session(session_id)
        result = self._resolve(query, selected_protocols, include_subprotocols, session_id)
        self._sessions[session_id].append({"query": query, "result": result})
        return result

    def _resolve(
        self,
        query: str,
        selected_protocols: list[str],
        include_subprotocols: bool,
        session_id: str,
    ) -> AgentTurnResult:
        if not selected_protocols:
            return AgentTurnResult(self._responses["no_protocols_selected"]["answer_markdown"])

        text = query.lower()
        out_of_scope = self._responses["out_of_scope"]
        if _count_keyword_matches(text, out_of_scope["keywords"]) > 0:
            return AgentTurnResult(out_of_scope["answer_markdown"])

        # A reply to a clarifying question is matched against the combined
        # conversation context, mirroring how the ADK session retains history.
        answering_clarification = self._awaiting_clarification(session_id)
        if answering_clarification:
            text = f"{self._sessions[session_id][-1]['query'].lower()} {text}"

        rule = self._best_rule(text, skip_clarifying=answering_clarification)
        if rule is None:
            return AgentTurnResult(self._responses["not_covered"]["answer_markdown"])

        if rule["kind"] == "clarifying_question":
            return AgentTurnResult(rule["answer_markdown"], is_clarifying_question=True)

        if rule.get("requires_subprotocols") and not include_subprotocols:
            return AgentTurnResult(
                self._responses["deep_search_offer"]["answer_markdown"],
                is_clarifying_question=True,
            )

        required = rule.get("required_protocol_ids")
        if required and not set(required) & set(selected_protocols):
            return AgentTurnResult(self._responses["not_covered"]["answer_markdown"])

        return AgentTurnResult(rule["answer_markdown"], citations=rule.get("citations", []))

    def _awaiting_clarification(self, session_id: str) -> bool:
        history = self._sessions.get(session_id, [])
        return bool(history) and history[-1]["result"].is_clarifying_question

    def _best_rule(self, text: str, skip_clarifying: bool) -> dict | None:
        best: dict | None = None
        best_score = 0
        for rule in self._responses["rules"]:
            if skip_clarifying and rule["kind"] == "clarifying_question":
                continue
            score = _count_keyword_matches(text, rule["keywords"])
            if score >= _MIN_KEYWORD_MATCHES and score > best_score:
                best, best_score = rule, score
        return best


# --- Selection -----------------------------------------------------------


def get_agent_client() -> AgentClient:
    """Return the agent client the app should use.

    Controlled by ``UI_AGENT_BACKEND``: ``adk`` (require the real agent),
    ``mock`` (always stub), or ``auto`` (default — use the real agent when
    the ``agent`` package loads, otherwise fall back to the stub).
    """
    backend = os.environ.get(AGENT_BACKEND_ENV, "auto").lower()

    if backend == "mock":
        return MockAgentClient()
    if backend == "adk":
        return AdkAgentClient()

    try:
        return AdkAgentClient()
    except Exception:
        return MockAgentClient()
