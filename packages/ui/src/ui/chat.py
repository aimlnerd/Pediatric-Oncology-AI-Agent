"""Main panel: conversational chat with inline citations.

Design doc section 7. Renders chat history, citation badges, the input box,
and routes turns through :mod:`ui.agent_client` (mocked until build step 5).
"""

from __future__ import annotations

import streamlit as st

from ui.agent_client import AgentClient, AgentTurnResult
from ui.state import append_message, record_feedback, reset_chat, set_pending_clarification

CHAT_PLACEHOLDER = "Ask about the selected protocols..."
CLARIFICATION_PLACEHOLDER = "Answer the clarifying question..."
NO_PROTOCOLS_HINT = "Select a hospital and at least one protocol to start chatting."
EMPTY_CHAT_HINT = (
    "Ask a question about the protocols selected on the left. Answers are "
    "grounded in those documents only, and every claim carries a citation."
)
AGENT_ERROR_MESSAGE = (
    "The assistant could not complete this request. This is a technical "
    "problem, **not** a clinical finding — please retry, and do not treat "
    "this as an answer."
)


def format_citation(citation: dict) -> str:
    """Render a single citation as ``[Document Title, Version, p. N, Section]``.

    Accepts either the mock shape (``version``/``page``/``section``) or the
    real agent shape, which supplies a pre-formatted ``page_display`` such as
    ``"p. 42"`` or ``"pp. 13–37"``. Only the parts present are included.
    """
    parts = [citation.get("doc_title", "Unknown document")]
    if citation.get("version"):
        parts.append(str(citation["version"]))
    if citation.get("page_display"):
        parts.append(str(citation["page_display"]))
    elif citation.get("page") is not None:
        parts.append(f"p. {citation['page']}")
    if citation.get("section"):
        parts.append(str(citation["section"]))
    return "[" + ", ".join(parts) + "]"


def format_citations(citations: list[dict]) -> str:
    """Render a caption line for all citations on a message (empty if none)."""
    if not citations:
        return ""
    return "📄 Sources: " + "  ".join(format_citation(c) for c in citations)


def queue_query(state, query: str) -> None:
    """Commit the clinician's question to history *before* calling the agent.

    Pure state logic (no Streamlit calls) so it can be unit-tested directly.
    A failing agent call is converted into an error turn rather than being
    allowed to propagate, so the clinician sees an explicit failure notice
    instead of a Streamlit traceback.
    """
    append_message(state, "user", query)
    state["pending_query"] = query


def run_pending_query(state, client: AgentClient) -> AgentTurnResult | None:
    """Answer the queued question, if there is one.

    Pure state logic (no Streamlit calls) so it can be unit-tested directly.
    A failing agent call is converted into an error turn rather than being
    allowed to propagate, so the clinician sees an explicit failure notice
    instead of a Streamlit traceback.
    """
    query = state.get("pending_query")
    if not query:
        return None

    try:
        session_id = client.get_or_create_session(state.get("adk_session_id"))
        state["adk_session_id"] = session_id

        result = client.send(
            query=query,
            selected_protocols=sorted(state.get("selected_protocol_ids", set())),
            include_subprotocols=bool(state.get("include_subprotocols")),
            session_id=session_id,
            hospital=state.get("hospital"),
        )
    except Exception as exc:
        state["last_error"] = str(exc)
        append_message(state, "assistant", AGENT_ERROR_MESSAGE, is_error=True)
        # Leave any pending clarification untouched: the question the agent
        # asked before the failure is still the one awaiting an answer.
        return AgentTurnResult(answer_markdown=AGENT_ERROR_MESSAGE, is_error=True)
    finally:
        # Always clear, so a failed turn is reported once rather than retried
        # on every subsequent rerun.
        state["pending_query"] = None

    state["last_error"] = None
    append_message(state, "assistant", result.answer_markdown, result.citations)
    set_pending_clarification(
        state, result.answer_markdown if result.is_clarifying_question else None
    )
    return result


def submit_query(state, client: AgentClient, query: str) -> AgentTurnResult:
    """Run one complete chat turn: queue the question, then answer it."""
    queue_query(state, query)
    result = run_pending_query(state, client)
    assert result is not None  # queue_query always leaves a pending query
    return result


def _render_message(message: dict, index: int) -> None:
    with st.chat_message(message["role"]):
        if message.get("is_error"):
            # Never render a failed turn as markdown content; it is not an answer.
            st.error(message["content"])
            return
        st.markdown(message["content"])
        caption = format_citations(message.get("citations", []))
        if caption:
            st.caption(caption)
        if message["role"] == "assistant":
            _render_feedback(index)


def _render_feedback(index: int) -> None:
    key = f"feedback_{index}"
    score = st.feedback("thumbs", key=key)
    if score != st.session_state.get("feedback", {}).get(index):
        record_feedback(st.session_state, index, score)


def render_chat(client: AgentClient) -> None:
    """Render the full chat panel."""
    header_col, reset_col = st.columns([4, 1])
    header_col.subheader("Clinician Chat")
    if st.session_state["chat_history"]:
        reset_col.button(
            "New chat",
            use_container_width=True,
            on_click=reset_chat,
            args=(st.session_state,),
            key="reset_chat",
        )

    for index, message in enumerate(st.session_state["chat_history"]):
        _render_message(message, index)

    pending_query = st.session_state.get("pending_query")
    # Reserve the answer's slot above the input so the layout stays stable
    # while the agent works.
    answer_slot = st.container() if pending_query else None

    no_protocols = not st.session_state.get("selected_protocol_ids")
    if no_protocols:
        st.info(NO_PROTOCOLS_HINT)
    elif not st.session_state["chat_history"]:
        st.info(EMPTY_CHAT_HINT)

    placeholder = (
        CLARIFICATION_PLACEHOLDER
        if st.session_state.get("pending_clarification")
        else CHAT_PLACEHOLDER
    )
    query = st.chat_input(
        placeholder,
        # Blocked while a turn is in flight: re-submitting would abort the
        # running script and lose the answer.
        disabled=no_protocols or bool(pending_query),
        key="chat_input",
    )

    if pending_query:
        # Phase 2 of a turn: the question is already on screen, now answer it.
        with answer_slot, st.chat_message("assistant"):
            with st.spinner("Searching the selected protocols..."):
                run_pending_query(st.session_state, client)
        st.rerun()

    if not query:
        return

    # Phase 1 of a turn: record the question and repaint immediately, so it
    # stays visible for the whole agent round-trip.
    queue_query(st.session_state, query)
    st.rerun()
