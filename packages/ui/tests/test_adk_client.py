"""Tests for the real ADK-backed agent client adapter.

The ``agent`` package's runner is replaced with a stub so these tests verify
the UI-side adapter (argument mapping, citation normalisation, clarification
detection) without contacting Vertex AI.
"""

import os

import pytest

from ui.agent_client import (
    AdkAgentClient,
    MockAgentClient,
    get_agent_client,
    looks_like_clarifying_question,
    normalise_agent_citations,
)


class StubRunner:
    """Stands in for OncoProtocolAgentRunner.ask()."""

    def __init__(self, result=None):
        self.result = result if result is not None else {"answer": "ok", "citations": []}
        self.calls = []

    def ask(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def make_chunk(short_title, page_display, page_start, filename="f.pdf", score=0.9):
    return {
        "citation": f"[{short_title} ({filename}), {page_display}]",
        "short_title": short_title,
        "filename": filename,
        "title": f"Full title of {short_title}",
        "doc_type": "protocol",
        "page_start": page_start,
        "page_end": page_start,
        "page_display": page_display,
        "relevance_score": score,
        "content": "chunk text",
    }


# --- Citation normalisation ---------------------------------------------


def test_normalise_agent_citations_maps_agent_fields_to_ui_shape():
    citations = normalise_agent_citations([make_chunk("HRNBL2", "p. 58", 58)])

    assert citations == [
        {
            "doc_title": "HRNBL2",
            "page_display": "p. 58",
            "page": 58,
            "filename": "f.pdf",
            "relevance_score": 0.9,
        }
    ]


def test_normalise_agent_citations_deduplicates_same_document_and_page():
    chunks = [
        make_chunk("HRNBL2", "p. 58", 58),
        make_chunk("HRNBL2", "p. 58", 58),
        make_chunk("HRNBL2", "pp. 60–61", 60),
    ]
    citations = normalise_agent_citations(chunks)

    assert len(citations) == 2
    assert citations[1]["page_display"] == "pp. 60–61"


def test_normalise_agent_citations_caps_the_number_of_sources():
    chunks = [make_chunk("Doc", f"p. {i}", i) for i in range(1, 30)]
    assert len(normalise_agent_citations(chunks)) == 6


def test_normalise_agent_citations_handles_missing_metadata():
    citations = normalise_agent_citations([{}])
    assert citations[0]["doc_title"] == "Unknown document"
    assert citations[0]["page"] is None


def test_normalise_agent_citations_empty_input():
    assert normalise_agent_citations([]) == []


# --- Clarifying-question heuristic --------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "Is this ALL or AML?",
        "Here is some guidance.\n\nWhich risk group has the patient been stratified into?",
        "Guidance...\n\n**Could you confirm the treatment phase?**",
    ],
)
def test_looks_like_clarifying_question_detects_trailing_questions(answer):
    assert looks_like_clarifying_question(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        "Platelets must be >= 50 x 10^9/L [ALLTogether1, p. 214].",
        "",
        "I cannot answer this as it falls outside the selected protocols.",
    ],
)
def test_looks_like_clarifying_question_ignores_plain_answers(answer):
    assert looks_like_clarifying_question(answer) is False


def test_looks_like_clarifying_question_ignores_mid_answer_questions():
    answer = "Is this relevant? Yes. The threshold is 50 [Doc, p. 1]."
    assert looks_like_clarifying_question(answer) is False


def test_looks_like_clarifying_question_ignores_cited_answers():
    """A cited reply is an answer, even if it ends with a follow-up offer."""
    answer = "The ANC threshold is 0.75.\n\nWould you like the maintenance schedule too?"
    assert looks_like_clarifying_question(answer, has_citations=True) is False
    assert looks_like_clarifying_question(answer, has_citations=False) is True


def test_looks_like_clarifying_question_ignores_long_answers():
    """Long replies are substantive answers ending with a follow-up offer."""
    answer = "Detailed guidance. " * 100 + "\n\nWould you like the maintenance schedule?"
    assert len(answer) > 1200
    assert looks_like_clarifying_question(answer) is False


# --- AdkAgentClient adapter ---------------------------------------------


def test_adk_client_forwards_ui_filters_to_the_runner():
    runner = StubRunner()
    client = AdkAgentClient(runner=runner)
    session_id = client.get_or_create_session(None)

    client.send(
        query="What is the dose?",
        selected_protocols=["ALLTogether1", "HRNBL2"],
        include_subprotocols=True,
        session_id=session_id,
        hospital="UZA",
    )

    call = runner.calls[0]
    assert call["query"] == "What is the dose?"
    assert call["selected_hospital"] == "UZA"
    assert call["selected_protocols"] == ["ALLTogether1", "HRNBL2"]
    assert call["include_subprotocols"] is True
    assert call["session_id"] == session_id


def test_adk_client_passes_none_when_no_protocols_selected():
    """None tells the agent to use every protocol active at the hospital."""
    runner = StubRunner()
    client = AdkAgentClient(runner=runner)

    client.send("q", [], False, client.get_or_create_session(None), hospital="UZG")

    assert runner.calls[0]["selected_protocols"] is None


def test_adk_client_falls_back_to_default_hospital():
    runner = StubRunner()
    client = AdkAgentClient(runner=runner, default_hospital="UZG")

    client.send("q", ["P"], False, client.get_or_create_session(None), hospital=None)

    assert runner.calls[0]["selected_hospital"] == "UZG"


def test_adk_client_returns_answer_with_normalised_citations():
    runner = StubRunner(
        {
            "answer": "Platelets must be >= 50 [HRNBL2, p. 58].",
            "citations": [make_chunk("HRNBL2", "p. 58", 58)],
        }
    )
    client = AdkAgentClient(runner=runner)

    result = client.send("q", ["HRNBL2"], False, client.get_or_create_session(None), "UZG")

    assert result.answer_markdown == "Platelets must be >= 50 [HRNBL2, p. 58]."
    assert result.citations[0]["doc_title"] == "HRNBL2"
    assert result.citations[0]["page_display"] == "p. 58"
    assert result.is_clarifying_question is False


def test_adk_client_flags_clarifying_questions():
    runner = StubRunner({"answer": "Is this ALL or AML?", "citations": []})
    client = AdkAgentClient(runner=runner)

    result = client.send("q", ["P"], False, client.get_or_create_session(None), "UZG")

    assert result.is_clarifying_question is True


def test_adk_client_handles_an_empty_answer():
    runner = StubRunner({"answer": "   ", "citations": []})
    client = AdkAgentClient(runner=runner)

    result = client.send("q", ["P"], False, client.get_or_create_session(None), "UZG")

    assert "did not return an answer" in result.answer_markdown


def test_adk_client_sessions_are_stable_and_unique():
    client = AdkAgentClient(runner=StubRunner())

    first = client.get_or_create_session(None)
    second = client.get_or_create_session(None)

    assert first != second
    assert client.get_or_create_session(first) == first


# --- Backend selection ---------------------------------------------------


def test_get_agent_client_respects_mock_override(monkeypatch):
    monkeypatch.setenv("UI_AGENT_BACKEND", "mock")
    assert isinstance(get_agent_client(), MockAgentClient)


def test_get_agent_client_auto_falls_back_to_mock_when_agent_unavailable(monkeypatch):
    monkeypatch.setenv("UI_AGENT_BACKEND", "auto")
    monkeypatch.setattr(
        "ui.agent_client.AdkAgentClient",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no credentials")),
    )
    assert isinstance(get_agent_client(), MockAgentClient)


def test_get_agent_client_adk_mode_propagates_failures(monkeypatch):
    monkeypatch.setenv("UI_AGENT_BACKEND", "adk")
    monkeypatch.setattr(
        "ui.agent_client.AdkAgentClient",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no credentials")),
    )
    with pytest.raises(RuntimeError):
        get_agent_client()


def test_tests_run_against_the_mock_backend_by_default():
    assert os.environ["UI_AGENT_BACKEND"] == "mock"
