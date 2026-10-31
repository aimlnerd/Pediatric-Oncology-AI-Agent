"""Tests for the mocked agent client."""

import pytest

from ui.agent_client import AgentTurnResult, MockAgentClient, get_agent_client

ALL_PROTOCOLS = [
    "ALLTogether1",
    "CHIP-AML22",
    "HRNBL2",
    "EuroNet-PHL-C2",
    "ALLTogether1-Thrombosis",
    "ALLTogether1-Pancreatitis",
]


@pytest.fixture
def client():
    return MockAgentClient()


@pytest.fixture
def session(client):
    return client.get_or_create_session(None)


def send(client, session, query, protocols=None, include_subprotocols=False):
    return client.send(
        query=query,
        selected_protocols=ALL_PROTOCOLS if protocols is None else protocols,
        include_subprotocols=include_subprotocols,
        session_id=session,
    )


def test_get_agent_client_returns_mock_for_now():
    assert isinstance(get_agent_client(), MockAgentClient)


def test_get_or_create_session_creates_and_reuses_ids(client):
    session_id = client.get_or_create_session(None)
    assert session_id
    assert client.get_or_create_session(session_id) == session_id


def test_get_or_create_session_creates_distinct_sessions(client):
    assert client.get_or_create_session(None) != client.get_or_create_session(None)


def test_send_returns_agent_turn_result(client, session):
    result = send(client, session, "Tell me about MYCN in high risk neuroblastoma")
    assert isinstance(result, AgentTurnResult)
    assert result.answer_markdown
    assert result.is_clarifying_question is False


def test_grounded_answer_includes_citations(client, session):
    result = send(client, session, "Is MYCN amplification high risk in neuroblastoma?")
    assert result.citations
    citation = result.citations[0]
    assert citation["doc_title"] == "HRNBL2 Protocol"
    assert citation["page"] == 58


def test_ambiguous_query_triggers_clarifying_question(client, session):
    result = send(
        client,
        session,
        "If I start chemotherapy for leukemia, how high should the platelets "
        "and white blood cells be?",
    )
    assert result.is_clarifying_question is True
    assert result.citations == []
    assert "AML" in result.answer_markdown


def test_clarification_followup_resolves_to_grounded_answer(client, session):
    first = send(
        client,
        session,
        "If I start chemotherapy for leukemia, how high should the platelets "
        "and white blood cells be?",
    )
    assert first.is_clarifying_question is True

    second = send(client, session, "ALL, maintenance phase, standard risk")

    assert second.is_clarifying_question is False
    assert second.citations
    assert second.citations[0]["doc_title"] == "ALLTogether1 Master Protocol"


def test_out_of_scope_query_is_refused(client, session):
    result = send(client, session, "What is the first line treatment for adult diabetes?")
    assert "outside that scope" in result.answer_markdown
    assert result.citations == []


def test_query_without_matching_protocol_selected_is_not_covered(client, session):
    result = send(
        client,
        session,
        "Is MYCN amplification high risk in neuroblastoma?",
        protocols=["CHIP-AML22"],
    )
    assert "could not find information" in result.answer_markdown
    assert result.citations == []


def test_unknown_topic_returns_not_covered(client, session):
    result = send(client, session, "What is the recommended font size for the report?")
    assert "could not find information" in result.answer_markdown


def test_send_without_selected_protocols_prompts_for_selection(client, session):
    result = send(client, session, "Anything at all", protocols=[])
    assert "No protocols are currently selected" in result.answer_markdown


def test_subprotocol_topic_offers_deep_search_when_toggle_is_off(client, session):
    result = send(
        client, session, "How do I manage thrombosis and anticoagulation?", include_subprotocols=False
    )
    assert result.is_clarifying_question is True
    assert "Deep Search" in result.answer_markdown


def test_subprotocol_topic_answers_when_deep_search_enabled(client, session):
    result = send(
        client, session, "How do I manage thrombosis and anticoagulation?", include_subprotocols=True
    )
    assert result.is_clarifying_question is False
    assert result.citations[0]["doc_title"] == "ALLTogether Thrombosis Guidelines"


def test_history_is_recorded_per_session(client):
    session_a = client.get_or_create_session(None)
    session_b = client.get_or_create_session(None)
    send(client, session_a, "Is MYCN amplification high risk in neuroblastoma?")

    assert len(client.get_history(session_a)) == 1
    assert client.get_history(session_b) == []
