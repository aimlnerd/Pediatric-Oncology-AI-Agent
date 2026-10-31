import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from agent.pii import redact_llm_request, redact_pii


@pytest.mark.parametrize(
    "niss",
    ["85.07.30-033.61", "85073003361", "85 07 30 033 61", "85-07-30-033-61"],
)
def test_belgian_national_numbers_are_redacted(niss: str) -> None:
    assert redact_pii(f"Patient {niss} has ALL") == "Patient [REDACTED-NISS] has ALL"


def test_emails_are_redacted() -> None:
    assert redact_pii("mail dr.x@uzgent.be please") == "mail [REDACTED-EMAIL] please"


@pytest.mark.parametrize("text", ["150000 WBC", "WBC 150.000/uL", "dose 12.5 mg/m2 on day 15"])
def test_clinical_numbers_are_left_alone(text: str) -> None:
    assert redact_pii(text) == text


def test_before_model_callback_redacts_every_content_part() -> None:
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="NISS 85.07.30-033.61")])]
    )
    assert redact_llm_request(None, request) is None  # type: ignore[arg-type]
    assert request.contents[0].parts is not None
    assert request.contents[0].parts[0].text == "NISS [REDACTED-NISS]"
