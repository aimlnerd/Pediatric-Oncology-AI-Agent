"""The clinical Q&A chain as an ADK 2 Workflow graph (ADR 0001).

START -> intake -> scope_guard -> route_scope -+- out_of_scope -> refuse
                                               '- in_scope -> clarifier -> route_clarify
         route_clarify -+- ask -> ask_user (pause) -> record_reply -> clarifier
                        '- ready -> retriever -> prepare_answer -+- empty -> not_covered
                                                                '- found -> answerer -> finalize
"""

from google.adk import Workflow
from google.adk.workflow import START

from agent import nodes
from agent.agents import (
    ModelSpec,
    build_answerer,
    build_clarifier,
    build_retriever,
    build_scope_guard,
)


def build_workflow(
    *,
    guard: ModelSpec | None = None,
    clarifier: ModelSpec | None = None,
    retriever: ModelSpec | None = None,
    answerer: ModelSpec | None = None,
) -> Workflow:
    """Build the graph. The model arguments exist so tests can inject scripted models."""
    scope_guard = build_scope_guard(guard)
    clarifier_agent = build_clarifier(clarifier)
    retriever_agent = build_retriever(retriever)
    answerer_agent = build_answerer(answerer)
    return Workflow(
        name="bspho_assistant",
        edges=[
            (START, nodes.intake),
            (nodes.intake, {"question": scope_guard}),
            (scope_guard, nodes.route_scope),
            (nodes.route_scope, {"in_scope": clarifier_agent, "out_of_scope": nodes.refuse}),
            (clarifier_agent, nodes.route_clarify),
            (nodes.route_clarify, {"ask": nodes.ask_user, "ready": retriever_agent}),
            (nodes.ask_user, nodes.record_reply, clarifier_agent),
            (retriever_agent, nodes.prepare_answer),
            (nodes.prepare_answer, {"found": answerer_agent, "empty": nodes.not_covered}),
            (answerer_agent, nodes.finalize),
        ],
    )
