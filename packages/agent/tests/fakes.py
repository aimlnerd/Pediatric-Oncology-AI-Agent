"""Test doubles: a scripted Gemini stand-in and an in-memory chunk searcher."""

from collections.abc import AsyncGenerator, Sequence
from types import SimpleNamespace
from typing import Any, cast

from google.adk import Context, Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import Field

from agent.search import RawHit


def text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


def call(name: str, **args: Any) -> LlmResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


class ScriptedLlm(BaseLlm):
    """Returns the scripted responses in order and records every request it receives."""

    model: str = "scripted"
    script: list[LlmResponse] = Field(default_factory=lambda: list[LlmResponse]())
    requests: list[LlmRequest] = Field(default_factory=lambda: list[LlmRequest]())

    @classmethod
    def of(cls, *responses: LlmResponse) -> "ScriptedLlm":
        return cls(script=list(responses), requests=[])

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse]:
        self.requests.append(llm_request)
        if not self.script:
            raise AssertionError("ScriptedLlm was called more often than scripted")
        yield self.script.pop(0)

    def system_prompt(self, index: int = 0) -> str:
        return str(self.requests[index].config.system_instruction)  # type: ignore[union-attr]


class FakeSearcher:
    """Returns canned hits and records the protocol ids it was asked to search."""

    def __init__(self, hits: Sequence[RawHit] = ()) -> None:
        self.hits = list(hits)
        self.calls: list[tuple[str, tuple[str, ...], int]] = []

    def search(self, query: str, protocol_ids: Sequence[str], page_size: int) -> list[RawHit]:
        self.calls.append((query, tuple(protocol_ids), page_size))
        return [hit for hit in self.hits if hit.protocol_id in protocol_ids]


DEFAULT_TEXT = "## 12.1 Pulmonary metastases\nUp to 4 nodules under 5 mm are not metastases."


def hit(
    protocol_id: str = "far-rms",
    text: str = DEFAULT_TEXT,
    page: int = 61,
) -> RawHit:
    return RawHit(
        protocol_id=protocol_id,
        short_title="FaR-RMS",
        version="2.0c",
        filename="FaR-RMS Protocol Version 2.0c 21-Mar-2024.pdf",
        page_start=page,
        page_end=page + 1,
        text=text,
    )


def fake_ctx(state: dict[str, Any]) -> Context:
    """A stand-in for ADK's Context carrying only session state (enough for function nodes)."""
    return cast(Context, SimpleNamespace(state=state))


def fake_tool_context(state: dict[str, Any]) -> ToolContext:
    return cast(ToolContext, SimpleNamespace(state=state))


def message_text(event: Event) -> str:
    """The chat text carried by an event."""
    parts = event.content.parts if event.content and event.content.parts else []
    return "".join(part.text or "" for part in parts)
