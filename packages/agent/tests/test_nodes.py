from typing import Any, cast

from agent.nodes import (
    AnswerDraft,
    ClarifierDecision,
    ScopeDecision,
    finalize,
    intake,
    not_covered,
    prepare_answer,
    record_reply,
    route_clarify,
    route_scope,
)
from agent.state import CASE_NOTES, QUESTION, RETRIEVED_CHUNKS

from .fakes import fake_ctx, message_text


def chunk_state() -> dict[str, Any]:
    chunk = {
        "id": "c1",
        "protocol_id": "far-rms",
        "short_title": "FaR-RMS",
        "version": "2.0c",
        "filename": "far-rms.pdf",
        "page_start": 61,
        "page_end": 61,
        "section": "12.1 Pulmonary",
        "text": "Up to 4 nodules <5 mm: no biopsy.",
    }
    return {"center": "UZG", "question": "biopsy?", RETRIEVED_CHUNKS: {"c1": chunk}}


def test_intake_redacts_records_and_resets_the_turn() -> None:
    state: dict[str, Any] = {RETRIEVED_CHUNKS: {"c1": {}}, "search_count": 2}
    event = intake(fake_ctx(state), "NISS 85.07.30-033.61 has 150000 WBC")

    assert event.actions.route == "question"
    assert event.output == "NISS [REDACTED-NISS] has 150000 WBC"
    assert state[QUESTION] == event.output
    assert state[CASE_NOTES] == [event.output]
    assert state[RETRIEVED_CHUNKS] == {}
    assert state["search_count"] == 0
    assert state["center"] == "UZG"


def test_intake_keeps_earlier_questions_as_context_and_caps_them() -> None:
    state: dict[str, Any] = {}
    for number in range(12):
        intake(fake_ctx(state), f"question {number}")
    assert len(state[CASE_NOTES]) == 8
    assert state[CASE_NOTES][-1] == "question 11"


def test_intake_runs_slash_commands_without_starting_a_question() -> None:
    state: dict[str, Any] = {}
    event = intake(fake_ctx(state), "/center UZA")
    assert event.actions.route == "command"
    assert state["center"] == "UZA"
    assert CASE_NOTES not in state


def test_intake_reports_an_invalid_scope_instead_of_searching() -> None:
    state: dict[str, Any] = {"center": "UZA", "selected_protocols": ["far-rms"]}
    event = intake(fake_ctx(state), "anything")
    assert event.actions.route == "command"
    assert "far-rms" in message_text(event)


def test_out_of_scope_decision_stores_the_refusal() -> None:
    state: dict[str, Any] = {QUESTION: "asthma?"}
    event = route_scope(fake_ctx(state), ScopeDecision(in_scope=False, refusal_text="No."))
    assert event.actions.route == "out_of_scope"
    assert state["refusal_text"] == "No."


def test_in_scope_decision_passes_the_question_on() -> None:
    event = route_scope(fake_ctx({QUESTION: "q"}), ScopeDecision(in_scope=True))
    assert (event.actions.route, event.output) == ("in_scope", "q")


def test_clarifier_can_ask_a_limited_number_of_times() -> None:
    state: dict[str, Any] = {QUESTION: "q"}
    ask = ClarifierDecision(action="ask", question="AML or ALL?")
    routes = [route_clarify(fake_ctx(state), ask).actions.route for _ in range(3)]
    assert routes == ["ask", "ask", "ready"]


def test_clarifier_without_a_question_goes_ahead() -> None:
    decision = ClarifierDecision(action="ask", question=" ")
    event = route_clarify(fake_ctx({QUESTION: "q"}), decision)
    assert event.actions.route == "ready"


def test_reply_is_redacted_and_added_to_the_conversation() -> None:
    state: dict[str, Any] = {CASE_NOTES: ["q"]}
    reply = record_reply(fake_ctx(state), "AML, mail me at a@b.be")
    assert reply == "AML, mail me at [REDACTED-EMAIL]"
    assert state[CASE_NOTES][-1].endswith(reply)


def test_nothing_retrieved_skips_the_answerer() -> None:
    assert prepare_answer(fake_ctx({RETRIEVED_CHUNKS: {}}), "done").actions.route == "empty"
    assert prepare_answer(fake_ctx(chunk_state()), "done").actions.route == "found"


def test_answer_gets_resolved_citations_and_a_sources_list() -> None:
    draft = AnswerDraft(status="answered", body_markdown="No biopsy is needed [c1].")
    event = finalize(fake_ctx(chunk_state()), draft)

    output = cast(dict[str, object], event.output)
    assert output["kind"] == "answer"
    message = message_text(event)
    assert "[FaR-RMS v2.0c, p. 61, § 12.1 Pulmonary]" in message
    assert "**Sources**" in message
    assert "`far-rms.pdf`" in message


def test_an_answer_without_valid_citations_is_replaced_by_not_covered() -> None:
    draft = AnswerDraft(status="answered", body_markdown="Biopsy the nodules.")
    event = finalize(fake_ctx(chunk_state()), draft)
    assert event.output == {"kind": "not_covered"}


def test_an_answer_citing_only_unretrieved_chunks_is_not_covered() -> None:
    draft = AnswerDraft(status="answered", body_markdown="Biopsy the nodules [c7].")
    assert finalize(fake_ctx(chunk_state()), draft).output == {"kind": "not_covered"}


def test_not_covered_names_the_selection_and_hints_at_unselected_protocols() -> None:
    state: dict[str, Any] = {"center": "UZA", "selected_protocols": ["alltogether1"]}
    message = message_text(not_covered(fake_ctx(state)))
    assert "ALLTogether1 Master Protocol v6.0" in message
    assert "20 other protocol(s) at UZA" in message


def test_reply_may_arrive_as_a_number_or_a_wrapped_value() -> None:
    state: dict[str, Any] = {}
    assert record_reply(fake_ctx(state), 12) == "12"
    assert record_reply(fake_ctx(state), {"result": "AML"}) == "AML"
