"""Tests for chat panel logic (citation formatting and turn submission)."""

import pytest

from ui.agent_client import AgentTurnResult, MockAgentClient
from ui.chat import (
    format_citation,
    format_citations,
    queue_query,
    run_pending_query,
    submit_query,
)
from ui.data import load_hospital_mapping
from ui.state import init_state, reset_chat, set_hospital


class StubClient:
    """Records calls and returns a scripted sequence of results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.sessions = []

    def get_or_create_session(self, adk_session_id):
        session_id = adk_session_id or "stub-session"
        self.sessions.append(adk_session_id)
        return session_id

    def send(self, query, selected_protocols, include_subprotocols, session_id, hospital=None):
        self.calls.append(
            {
                "query": query,
                "selected_protocols": selected_protocols,
                "include_subprotocols": include_subprotocols,
                "session_id": session_id,
                "hospital": hospital,
            }
        )
        return self.results.pop(0)


@pytest.fixture
def state():
    state: dict = {}
    init_state(state)
    set_hospital(state, "UZA", load_hospital_mapping())
    return state


def test_format_citation_with_all_fields():
    citation = {
        "doc_title": "ALLTogether1 Master Protocol",
        "version": "v6.0",
        "page": 214,
        "section": "Maintenance Therapy",
    }
    assert format_citation(citation) == (
        "[ALLTogether1 Master Protocol, v6.0, p. 214, Maintenance Therapy]"
    )


def test_format_citation_omits_missing_optional_fields():
    assert format_citation({"doc_title": "HLH-2004", "page": 12}) == "[HLH-2004, p. 12]"


def test_format_citation_prefers_the_agents_page_display():
    """The real agent supplies a pre-formatted page label, incl. page ranges."""
    citation = {"doc_title": "HRNBL2", "page_display": "pp. 13–37", "page": 13}
    assert format_citation(citation) == "[HRNBL2, pp. 13–37]"


def test_format_citation_handles_missing_title():
    assert format_citation({}) == "[Unknown document]"


def test_format_citations_empty_returns_empty_string():
    assert format_citations([]) == ""


def test_format_citations_joins_multiple_sources():
    caption = format_citations(
        [
            {"doc_title": "A", "page": 1},
            {"doc_title": "B", "page": 2},
        ]
    )
    assert caption.startswith("📄 Sources:")
    assert "[A, p. 1]" in caption
    assert "[B, p. 2]" in caption


def test_submit_query_appends_user_and_assistant_messages(state):
    client = StubClient([AgentTurnResult("An answer", [{"doc_title": "X", "page": 3}])])

    submit_query(state, client, "What is the dose?")

    assert len(state["chat_history"]) == 2
    assert state["chat_history"][0] == {
        "role": "user",
        "content": "What is the dose?",
        "citations": [],
        "is_error": False,
    }
    assert state["chat_history"][1]["role"] == "assistant"
    assert state["chat_history"][1]["content"] == "An answer"
    assert state["chat_history"][1]["citations"] == [{"doc_title": "X", "page": 3}]


def test_submit_query_forwards_selected_protocols_and_toggle(state):
    client = StubClient([AgentTurnResult("ok")])
    state["include_subprotocols"] = True

    submit_query(state, client, "question")

    call = client.calls[0]
    assert call["selected_protocols"] == sorted(state["selected_protocol_ids"])
    assert call["include_subprotocols"] is True


def test_submit_query_forwards_the_selected_hospital(state):
    """Tier 1 context must reach the agent so it can scope the search."""
    client = StubClient([AgentTurnResult("ok")])

    submit_query(state, client, "question")

    assert client.calls[0]["hospital"] == "UZA"


def test_submit_query_stores_and_reuses_the_adk_session_id(state):
    client = StubClient([AgentTurnResult("a"), AgentTurnResult("b")])

    submit_query(state, client, "first")
    session_id = state["adk_session_id"]
    submit_query(state, client, "second")

    assert session_id == "stub-session"
    assert state["adk_session_id"] == session_id
    assert client.sessions == [None, "stub-session"]


def test_submit_query_sets_pending_clarification(state):
    client = StubClient([AgentTurnResult("Which risk group?", is_clarifying_question=True)])

    submit_query(state, client, "ambiguous question")

    assert state["pending_clarification"] == {"question": "Which risk group?"}


def test_submit_query_clears_pending_clarification_on_definitive_answer(state):
    client = StubClient(
        [
            AgentTurnResult("Which risk group?", is_clarifying_question=True),
            AgentTurnResult("Standard risk answer"),
        ]
    )

    submit_query(state, client, "ambiguous question")
    submit_query(state, client, "standard risk")

    assert state["pending_clarification"] is None


def test_submit_query_end_to_end_with_mock_client(state):
    """The real mock client drives a full clarify-then-answer exchange."""
    client = MockAgentClient()

    first = submit_query(
        state,
        client,
        "Starting chemotherapy for leukemia, how high should platelets and "
        "white blood cells be?",
    )
    assert first.is_clarifying_question is True
    assert state["pending_clarification"] is not None

    second = submit_query(state, client, "ALL, maintenance phase")
    assert second.is_clarifying_question is False
    assert state["pending_clarification"] is None
    assert len(state["chat_history"]) == 4
    assert state["chat_history"][3]["citations"][0]["page"] == 214


# --- Error states --------------------------------------------------------


class FailingClient:
    """Raises on ``send`` to simulate an agent/Vertex AI outage."""

    def __init__(self, exc=None, fail_on_session=False):
        self.exc = exc or RuntimeError("503 Service Unavailable")
        self.fail_on_session = fail_on_session

    def get_or_create_session(self, adk_session_id):
        if self.fail_on_session:
            raise self.exc
        return adk_session_id or "stub-session"

    def send(self, **kwargs):
        raise self.exc


def test_submit_query_converts_an_agent_failure_into_an_error_turn(state):
    """A backend outage must not raise; the clinician gets an explicit notice."""
    result = submit_query(state, FailingClient(), "What is the dose?")

    assert result.is_error is True
    assert len(state["chat_history"]) == 2
    assert state["chat_history"][0]["role"] == "user"

    reply = state["chat_history"][1]
    assert reply["role"] == "assistant"
    assert reply["is_error"] is True
    assert reply["citations"] == []
    # The failure must be unmistakably non-clinical.
    assert "not" in reply["content"] and "clinical finding" in reply["content"]


def test_submit_query_records_the_underlying_error_for_diagnostics(state):
    submit_query(state, FailingClient(RuntimeError("boom")), "q")
    assert state["last_error"] == "boom"


def test_submit_query_handles_a_session_creation_failure(state):
    """Failures before `send` are caught too, not just failures inside it."""
    result = submit_query(state, FailingClient(fail_on_session=True), "q")

    assert result.is_error is True
    assert state["chat_history"][1]["is_error"] is True


def test_submit_query_keeps_a_pending_clarification_after_a_failure(state):
    """The agent's unanswered question still stands once the outage clears."""
    client = StubClient([AgentTurnResult("Which risk group?", is_clarifying_question=True)])
    submit_query(state, client, "ambiguous")
    assert state["pending_clarification"] is not None

    submit_query(state, FailingClient(), "standard risk")

    assert state["pending_clarification"] is not None


def test_submit_query_clears_last_error_after_a_successful_turn(state):
    submit_query(state, FailingClient(), "q")
    assert state["last_error"] is not None

    submit_query(state, StubClient([AgentTurnResult("ok")]), "q2")

    assert state["last_error"] is None
    assert state["chat_history"][-1]["is_error"] is False


# --- Two-phase turns -----------------------------------------------------
#
# The agent round-trip can take a minute, so a turn is split across two
# script runs: the question is committed and painted first, then answered.


def test_queue_query_records_the_question_without_calling_the_agent(state):
    client = StubClient([AgentTurnResult("never used")])

    queue_query(state, "What is the induction dose?")

    assert state["pending_query"] == "What is the induction dose?"
    assert [m["content"] for m in state["chat_history"]] == ["What is the induction dose?"]
    assert client.calls == []


def test_run_pending_query_answers_the_queued_question(state):
    queue_query(state, "What is the induction dose?")
    client = StubClient([AgentTurnResult("Answer")])

    result = run_pending_query(state, client)

    assert result is not None
    assert client.calls[0]["query"] == "What is the induction dose?"
    assert [m["role"] for m in state["chat_history"]] == ["user", "assistant"]
    assert state["pending_query"] is None


def test_run_pending_query_is_a_no_op_without_a_queued_question(state):
    client = StubClient([AgentTurnResult("never used")])

    assert run_pending_query(state, client) is None
    assert client.calls == []
    assert state["chat_history"] == []


def test_run_pending_query_clears_the_queue_after_a_failure(state):
    queue_query(state, "q")

    run_pending_query(state, FailingClient())

    # Otherwise every subsequent rerun would retry the failing call.
    assert state["pending_query"] is None
    assert state["chat_history"][-1]["is_error"] is True


def test_a_queued_question_survives_an_interrupted_turn(state):
    """An aborted script run must not take the clinician's question with it."""
    queue_query(state, "What is the induction dose?")

    # Simulate the run being interrupted before run_pending_query executes:
    # the next run picks the question back up.
    result = run_pending_query(state, StubClient([AgentTurnResult("Answer")]))

    assert result is not None
    assert state["chat_history"][0]["content"] == "What is the induction dose?"
    assert state["chat_history"][1]["content"] == "Answer"


def test_reset_chat_discards_a_queued_question(state):
    queue_query(state, "q")

    reset_chat(state)

    assert state["pending_query"] is None
    assert state["chat_history"] == []
