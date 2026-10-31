"""End-to-end tests for the Streamlit app using streamlit's AppTest harness."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.agent_client import MockAgentClient

APP_PATH = str(Path(__file__).parent.parent / "src" / "ui" / "app.py")


@pytest.fixture
def app():
    at = AppTest.from_file(APP_PATH, default_timeout=10)
    at.run()
    return at


@pytest.fixture
def app_with_hospital(app):
    app.selectbox(key="hospital_select").set_value("UZA").run()
    return app


def selected_ids(at):
    return at.session_state["selected_protocol_ids"]


def available_ids(at):
    return {p["id"] for p in at.session_state["available_protocols"]}


# --- Shell ---------------------------------------------------------------


def test_app_runs_without_exceptions(app):
    assert not app.exception


def test_default_view_is_clinician_with_hospital_prompt(app):
    assert app.sidebar.radio[0].value == "Clinician"
    assert app.session_state["hospital"] is None
    assert any("Select a hospital" in info.value for info in app.info)


def test_switching_to_admin_view_renders_the_upload_form(app):
    app.sidebar.radio[0].set_value("Admin").run()
    assert app.session_state["is_admin"] is True
    assert any("Admin" in header.value for header in app.subheader)
    # The upload form is rendered, not a placeholder.
    assert any("Protocol title" in ti.label for ti in app.text_input)
    assert any("Participating hospitals" in ms.label for ms in app.multiselect)
    assert any("Document type" in r.label for r in app.radio)


def test_admin_view_lists_the_current_document_inventory(app):
    app.sidebar.radio[0].set_value("Admin").run()
    captions = " ".join(c.value for c in app.caption)
    assert "Currently indexed" in captions


def test_admin_submit_without_a_file_reports_validation_errors(app):
    """Validation short-circuits before any GCS call, so this stays offline."""
    app.sidebar.radio[0].set_value("Admin").run()
    app.button[0].click().run()

    assert not app.exception
    assert any("Form validation" in e.value for e in app.error)
    warnings = " ".join(w.value for w in app.warning)
    assert "Select a PDF file" in warnings
    assert "Enter a protocol title" in warnings
    assert "at least one participating hospital" in warnings


# --- Sidebar: Tier 1 / Tier 2 -------------------------------------------


def test_selecting_hospital_populates_protocol_checklist(app_with_hospital):
    at = app_with_hospital
    assert at.session_state["hospital"] == "UZA"
    assert len(at.session_state["available_protocols"]) > 0
    assert len(at.checkbox) == len(at.session_state["available_protocols"])
    assert selected_ids(at) == available_ids(at)


def test_sidebar_shows_selected_protocol_count(app_with_hospital):
    at = app_with_hospital
    total = len(at.session_state["available_protocols"])
    assert any(f"({total}/{total} selected)" in c.value for c in at.caption)


def test_unchecking_a_protocol_updates_selection(app_with_hospital):
    at = app_with_hospital
    protocol_id = at.checkbox[0].key.removeprefix("proto_")

    at.checkbox[0].uncheck().run()

    assert protocol_id not in selected_ids(at)


def test_clear_all_button_deselects_every_protocol(app_with_hospital):
    at = app_with_hospital
    at.button(key="clear_all_protocols").click().run()

    assert selected_ids(at) == set()
    assert any("at least one protocol" in w.value for w in at.warning)


def test_select_all_button_reselects_every_protocol(app_with_hospital):
    at = app_with_hospital
    at.button(key="clear_all_protocols").click().run()
    at.button(key="select_all_protocols").click().run()

    assert selected_ids(at) == available_ids(at)


def test_include_subprotocols_toggle_adds_more_checkboxes(app_with_hospital):
    at = app_with_hospital
    checkbox_count_before = len(at.checkbox)

    at.toggle(key="include_subprotocols_toggle").set_value(True).run()

    assert len(at.checkbox) > checkbox_count_before
    assert at.session_state["include_subprotocols"] is True
    assert selected_ids(at) == available_ids(at)


def test_switching_hospital_resets_stale_checkbox_state(app_with_hospital):
    """A protocol unchecked under one hospital must not stay unchecked under another."""
    at = app_with_hospital
    # ALLTogether1 is offered by every center in the mock mapping.
    at.checkbox(key="proto_ALLTogether1").uncheck().run()
    assert "ALLTogether1" not in selected_ids(at)

    at.selectbox(key="hospital_select").set_value("UCL").run()

    assert at.session_state["hospital"] == "UCL"
    assert "ALLTogether1" in selected_ids(at)
    assert at.checkbox(key="proto_ALLTogether1").value is True


# --- Chat panel ----------------------------------------------------------


def test_chat_input_is_disabled_until_protocols_are_selected(app):
    assert app.chat_input[0].disabled is True
    assert any("at least one protocol" in info.value for info in app.info)


def test_chat_input_is_enabled_once_a_hospital_is_selected(app_with_hospital):
    assert app_with_hospital.chat_input[0].disabled is False


def test_sending_a_message_renders_question_and_grounded_answer(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value(
        "How is early response assessment done with PET in Hodgkin lymphoma?"
    ).run()

    assert not at.exception
    assert len(at.session_state["chat_history"]) == 2
    assert at.session_state["chat_history"][0]["role"] == "user"

    answer = at.session_state["chat_history"][1]
    assert answer["role"] == "assistant"
    assert answer["citations"][0]["doc_title"] == "EuroNet-PHL-C2 Trial Protocol"
    assert any("FDG-PET" in md.value for md in at.markdown)
    assert any("p. 96" in c.value for c in at.caption)


def test_answer_is_scoped_to_the_selected_hospitals_protocols(app_with_hospital):
    """HRNBL2 is not run at UZA, so a neuroblastoma question cannot be answered."""
    at = app_with_hospital
    assert "HRNBL2" not in available_ids(at)

    at.chat_input[0].set_value("Is MYCN amplification high risk in neuroblastoma?").run()

    answer = at.session_state["chat_history"][1]
    assert "could not find information" in answer["content"]
    assert answer["citations"] == []


def test_same_question_is_answered_for_a_participating_hospital(app):
    """UZG does run HRNBL2, so the same question is grounded there."""
    at = app
    at.selectbox(key="hospital_select").set_value("UZG").run()
    assert "HRNBL2" in available_ids(at)

    at.chat_input[0].set_value("Is MYCN amplification high risk in neuroblastoma?").run()

    answer = at.session_state["chat_history"][1]
    assert answer["citations"][0]["doc_title"] == "HRNBL2 Protocol"
    assert answer["citations"][0]["page"] == 58


def test_ambiguous_question_renders_a_clarifying_question(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value(
        "Starting chemotherapy for leukemia, how high should platelets and "
        "white blood cells be?"
    ).run()

    assert at.session_state["pending_clarification"] is not None
    assert at.chat_input[0].placeholder == "Answer the clarifying question..."


def test_clarification_followup_produces_a_cited_answer(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value(
        "Starting chemotherapy for leukemia, how high should platelets and "
        "white blood cells be?"
    ).run()
    at.chat_input[0].set_value("ALL, maintenance phase").run()

    assert at.session_state["pending_clarification"] is None
    assert len(at.session_state["chat_history"]) == 4
    assert at.session_state["chat_history"][3]["citations"][0]["page"] == 214


def test_out_of_scope_question_is_refused_without_citations(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value("What is the treatment for adult diabetes?").run()

    answer = at.session_state["chat_history"][1]
    assert "outside that scope" in answer["content"]
    assert answer["citations"] == []


def test_new_chat_button_clears_history(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value("How is response assessment done in Hodgkin with PET?").run()
    assert at.session_state["chat_history"]

    at.button(key="reset_chat").click().run()

    assert at.session_state["chat_history"] == []
    assert at.session_state["adk_session_id"] is None


def test_changing_hospital_clears_the_conversation(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value("How is response assessment done in Hodgkin with PET?").run()
    assert at.session_state["chat_history"]

    at.selectbox(key="hospital_select").set_value("UCL").run()

    assert at.session_state["chat_history"] == []


def test_subprotocol_question_offers_deep_search_then_answers(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value("How do I manage thrombosis and anticoagulation?").run()

    offer = at.session_state["chat_history"][1]
    assert "Deep Search" in offer["content"]
    assert offer["citations"] == []

    at.toggle(key="include_subprotocols_toggle").set_value(True).run()
    at.chat_input[0].set_value("How do I manage thrombosis and anticoagulation?").run()

    answer = at.session_state["chat_history"][-1]
    assert answer["citations"][0]["doc_title"] == "ALLTogether Thrombosis Guidelines"


# --- Empty & error states ------------------------------------------------


def test_empty_chat_shows_a_getting_started_hint(app_with_hospital):
    """With protocols selected but no history, explain what to do next."""
    at = app_with_hospital
    infos = " ".join(i.value for i in at.info)

    assert "Ask a question about the protocols selected" in infos
    assert at.chat_input[0].disabled is False


def test_getting_started_hint_disappears_once_the_chat_starts(app_with_hospital):
    at = app_with_hospital
    at.chat_input[0].set_value("Is MYCN amplification high risk in neuroblastoma?").run()

    infos = " ".join(i.value for i in at.info)
    assert "Ask a question about the protocols selected" not in infos


def test_agent_failure_renders_an_error_instead_of_crashing(app_with_hospital, monkeypatch):
    """A backend outage must surface as an error turn, not a Streamlit traceback."""
    at = app_with_hospital

    def boom(*args, **kwargs):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(MockAgentClient, "send", boom)
    at.chat_input[0].set_value("What is the induction dose?").run()

    assert not at.exception
    assert at.session_state["chat_history"][-1]["is_error"] is True
    assert at.session_state["last_error"] == "503 Service Unavailable"
    assert any("not" in e.value and "clinical finding" in e.value for e in at.error)


def test_error_turn_is_cleared_by_new_chat(app_with_hospital, monkeypatch):
    at = app_with_hospital
    monkeypatch.setattr(
        MockAgentClient, "send", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    at.chat_input[0].set_value("question").run()
    assert at.session_state["chat_history"]

    monkeypatch.undo()
    at.button(key="reset_chat").click().run()

    assert at.session_state["chat_history"] == []
    assert at.session_state["last_error"] is None


def test_a_question_is_painted_before_the_agent_is_called(app_with_hospital):
    """The question must be on screen for the whole agent round-trip."""
    at = app_with_hospital
    at.session_state["pending_query"] = "What is the induction dose?"
    at.session_state["chat_history"] = [
        {
            "role": "user",
            "content": "What is the induction dose?",
            "citations": [],
            "is_error": False,
        }
    ]
    at.run()

    assert any("What is the induction dose?" in m.value for m in at.markdown)


def test_submitting_a_question_still_produces_an_answer(app_with_hospital):
    """The two-run turn must complete end to end from a single submission."""
    at = app_with_hospital

    at.chat_input[0].set_value("What is the induction dose for ALLTogether1?").run()

    assert at.session_state["pending_query"] is None
    roles = [m["role"] for m in at.session_state["chat_history"]]
    assert roles == ["user", "assistant"]
    assert at.chat_input[0].disabled is False
