"""Personal-data redaction applied before any text reaches Gemini."""

import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

# Belgian national register number (NISS/INSZ): YY.MM.DD-XXX.CC, separators optional.
_NISS = re.compile(r"(?<!\d)\d{2}[.\-\s]?[01]\d[.\-\s]?[0-3]\d[.\-\s]?\d{3}[.\-\s]?\d{2}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact_pii(text: str) -> str:
    text = _NISS.sub("[REDACTED-NISS]", text)
    return _EMAIL.sub("[REDACTED-EMAIL]", text)


def redact_llm_request(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    """before_model_callback: redact identifiers in everything sent to the model."""
    for content in llm_request.contents:
        for part in content.parts or []:
            if part.text:
                part.text = redact_pii(part.text)
    return None
