"""The four LLM agents of the chain. Each is a single-turn node of the workflow."""

from google.adk import Agent
from google.adk.models.base_llm import BaseLlm

from agent.config import get_settings
from agent.nodes import AnswerDraft, ClarifierDecision, ScopeDecision
from agent.pii import redact_llm_request
from agent.prompts import (
    answerer_instruction,
    clarifier_instruction,
    guard_instruction,
    retriever_instruction,
)
from agent.tools.search_protocols import search_protocols

ModelSpec = str | BaseLlm


def build_scope_guard(model: ModelSpec | None = None) -> Agent:
    return Agent(
        name="scope_guard",
        description="Refuses questions outside pediatric hematology and oncology.",
        model=model or get_settings().guard_model,
        instruction=guard_instruction,
        output_schema=ScopeDecision,
        before_model_callback=redact_llm_request,
    )


def build_clarifier(model: ModelSpec | None = None) -> Agent:
    return Agent(
        name="clarifier",
        description="Asks for a missing clinical discriminator before searching.",
        model=model or get_settings().main_model,
        instruction=clarifier_instruction,
        output_schema=ClarifierDecision,
        before_model_callback=redact_llm_request,
    )


def build_retriever(model: ModelSpec | None = None) -> Agent:
    return Agent(
        name="retriever",
        description="Searches the selected protocols and collects passages.",
        model=model or get_settings().main_model,
        instruction=retriever_instruction,
        tools=[search_protocols],
        before_model_callback=redact_llm_request,
    )


def build_answerer(model: ModelSpec | None = None) -> Agent:
    return Agent(
        name="answerer",
        description="Writes a cited answer from the retrieved passages only.",
        model=model or get_settings().main_model,
        instruction=answerer_instruction,
        output_schema=AnswerDraft,
        before_model_callback=redact_llm_request,
    )
