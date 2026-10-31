"""The whole chain, run through ADK's runner with scripted models instead of Gemini."""

from typing import Literal

from google.adk import Event, Workflow
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent.nodes import AnswerDraft, ClarifierDecision, ScopeDecision
from agent.search import set_searcher
from agent.workflow import build_workflow

from .fakes import FakeSearcher, ScriptedLlm, call, hit, message_text, text

WORKFLOW_NAME = "bspho_assistant"
RETRIEVE = [call("search_protocols", query="pulmonary nodules biopsy"), text("done")]


def in_scope() -> ScriptedLlm:
    return ScriptedLlm.of(text(ScopeDecision(in_scope=True).model_dump_json()))


def ready() -> LlmResponse:
    return text(ClarifierDecision(action="ready").model_dump_json())


def answer(body: str, status: Literal["answered", "not_covered"] = "answered") -> LlmResponse:
    draft = AnswerDraft(status=status, body_markdown=body)
    return text(draft.model_dump_json())


class Chat:
    def __init__(self, workflow: Workflow) -> None:
        self.runner = InMemoryRunner(node=workflow)
        self.session_id = ""

    async def start(self) -> "Chat":
        session = await self.runner.session_service.create_session(
            app_name=self.runner.app_name, user_id="u"
        )
        self.session_id = session.id
        return self

    async def say(self, message: str) -> list[Event]:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        return [
            event
            async for event in self.runner.run_async(
                user_id="u", session_id=self.session_id, new_message=content
            )
        ]

    async def answer_question(self, paused: Event, reply: object) -> list[Event]:
        response = types.FunctionResponse(
            id=next(iter(paused.long_running_tool_ids or [])),
            name="adk_request_input",
            response={"result": reply},
        )
        content = types.Content(role="user", parts=[types.Part(function_response=response)])
        return [
            event
            async for event in self.runner.run_async(
                user_id="u",
                session_id=self.session_id,
                invocation_id=paused.invocation_id,
                new_message=content,
            )
        ]


def replies(events: list[Event]) -> list[str]:
    """Chat messages shown to the clinician, in order (agent JSON drafts excluded)."""
    return [
        message_text(e) for e in events if e.author == WORKFLOW_NAME and message_text(e).strip()
    ]


def build(
    *,
    guard: ScriptedLlm | None = None,
    clarifier: ScriptedLlm | None = None,
    retriever: ScriptedLlm | None = None,
    answerer: ScriptedLlm | None = None,
) -> tuple[Workflow, dict[str, ScriptedLlm]]:
    models = {
        "guard": guard or ScriptedLlm.of(),
        "clarifier": clarifier or ScriptedLlm.of(),
        "retriever": retriever or ScriptedLlm.of(),
        "answerer": answerer or ScriptedLlm.of(),
    }
    return build_workflow(**models), models


async def test_adult_medicine_question_is_refused_and_nothing_is_searched() -> None:
    refusal = ScopeDecision(in_scope=False, refusal_text="I cannot answer: adult cardiology.")
    workflow, models = build(guard=ScriptedLlm.of(text(refusal.model_dump_json())))
    searcher = FakeSearcher([hit()])
    set_searcher(searcher)
    chat = await Chat(workflow).start()

    events = await chat.say("First-line antihypertensive for a 65-year-old after MI?")

    assert replies(events) == ["I cannot answer: adult cardiology."]
    assert searcher.calls == []
    assert models["clarifier"].requests == models["retriever"].requests == []


async def test_nothing_retrieved_is_reported_as_not_covered_without_calling_the_answerer() -> None:
    workflow, models = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(),
    )
    set_searcher(FakeSearcher([]))
    chat = await Chat(workflow).start()

    events = await chat.say("What is the dose of drug X in protocol Y?")

    [message] = replies(events)
    assert "not covered in the selected protocols" in message
    assert models["answerer"].requests == []


async def test_answer_cites_protocol_version_page_and_section() -> None:
    workflow, models = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(
            answer("No lung biopsy is indicated for up to 4 nodules under 5 mm [c1].")
        ),
    )
    set_searcher(FakeSearcher([hit()]))
    chat = await Chat(workflow).start()

    events = await chat.say("3 subpleural nodules under 5 mm in rhabdomyosarcoma: biopsy?")

    [message] = replies(events)
    assert "[FaR-RMS v2.0c, pp. 61-62, § 12.1 Pulmonary metastases]" in message
    assert "**Sources**" in message
    assert "FaR-RMS Protocol Version 2.0c 21-Mar-2024.pdf" in message
    assert "[c1]" not in message
    assert "Up to 4 nodules under 5 mm" in models["answerer"].system_prompt()


async def test_an_answer_that_cites_nothing_retrieved_is_not_shown() -> None:
    workflow, _ = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Do a biopsy [c9].")),
    )
    set_searcher(FakeSearcher([hit()]))
    chat = await Chat(workflow).start()

    [message] = replies(await chat.say("biopsy?"))

    assert "not covered" in message
    assert "biopsy [" not in message


async def test_clarifying_question_pauses_then_the_reply_resumes_the_chain() -> None:
    ask = ClarifierDecision(action="ask", question="Is this AML or ALL?")
    clarifier = ScriptedLlm.of(text(ask.model_dump_json()), ready())
    workflow, models = build(
        guard=in_scope(),
        clarifier=clarifier,
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Hydrate and start cytoreduction [c1].")),
    )
    set_searcher(FakeSearcher([hit("chip-aml22")]))
    chat = await Chat(workflow).start()

    first = await chat.say("Patient presents with 150,000 WBC. What should I do?")

    assert "Is this AML or ALL?" in replies(first)
    paused = next(e for e in first if e.long_running_tool_ids)
    assert models["retriever"].requests == []

    second = await chat.answer_question(paused, "AML")

    assert any("Hydrate and start cytoreduction" in m for m in replies(second))
    assert "AML" in clarifier.system_prompt(1)
    assert "150,000 WBC" in clarifier.system_prompt(1)


async def test_a_typed_reply_instead_of_resuming_still_carries_the_earlier_context() -> None:
    guard = ScriptedLlm.of(
        text(ScopeDecision(in_scope=True).model_dump_json()),
        text(ScopeDecision(in_scope=True).model_dump_json()),
    )
    ask = ClarifierDecision(action="ask", question="Is this AML or ALL?")
    workflow, _ = build(
        guard=guard,
        clarifier=ScriptedLlm.of(text(ask.model_dump_json()), ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Cytoreduction [c1].")),
    )
    set_searcher(FakeSearcher([hit("chip-aml22")]))
    chat = await Chat(workflow).start()

    await chat.say("150,000 WBC, what next?")
    events = await chat.say("AML")

    assert any("Cytoreduction" in m for m in replies(events))
    assert "150,000 WBC, what next?" in guard.system_prompt(1)


async def test_uza_clinician_never_searches_protocols_their_center_does_not_run() -> None:
    searcher = FakeSearcher([hit("chip-aml22"), hit("alltogether1")])
    set_searcher(searcher)
    workflow, _ = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Fact [c1].")),
    )
    chat = await Chat(workflow).start()

    await chat.say("/center UZA")
    await chat.say("hyperleukocytosis management")

    [(_, searched, _)] = searcher.calls
    assert len(searched) == 21
    assert "chip-aml22" not in searched
    assert "alltogether1" in searched


async def test_clarifier_sees_only_the_selected_protocols() -> None:
    workflow, models = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Fact [c1].")),
    )
    set_searcher(FakeSearcher([hit()]))
    chat = await Chat(workflow).start()

    await chat.say("/center UZA")
    await chat.say("/select alltogether1")
    await chat.say("150,000 WBC, what next?")

    catalog = models["clarifier"].system_prompt()
    assert "ALLTogether1" in catalog
    assert "CHIP-AML22" not in catalog


async def test_national_register_number_never_reaches_any_model() -> None:
    workflow, models = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Fact [c1].")),
    )
    set_searcher(FakeSearcher([hit()]))
    chat = await Chat(workflow).start()

    await chat.say("Patient 85.07.30-033.61 has nodules, biopsy?")

    for model in models.values():
        for request in model.requests:
            sent = model.system_prompt(model.requests.index(request)) + "".join(
                part.text or "" for content in request.contents for part in content.parts or []
            )
            assert "85.07.30" not in sent
    assert "REDACTED-NISS" in models["guard"].system_prompt()


async def test_slash_commands_answer_without_calling_any_model() -> None:
    workflow, models = build()
    chat = await Chat(workflow).start()

    [message] = replies(await chat.say("/scope"))

    assert "Center:" in message
    assert all(model.requests == [] for model in models.values())


async def test_a_numeric_reply_from_the_adk_web_input_box_is_accepted() -> None:
    ask = ClarifierDecision(action="ask", question="How old is the patient (years)?")
    workflow, models = build(
        guard=in_scope(),
        clarifier=ScriptedLlm.of(text(ask.model_dump_json()), ready()),
        retriever=ScriptedLlm.of(*RETRIEVE),
        answerer=ScriptedLlm.of(answer("Fact [c1].")),
    )
    set_searcher(FakeSearcher([hit()]))
    chat = await Chat(workflow).start()
    paused = next(e for e in await chat.say("Neuroblastoma staging?") if e.long_running_tool_ids)

    events = await chat.answer_question(paused, 12)

    assert any("Fact" in m for m in replies(events))
    assert "12" in models["clarifier"].system_prompt(1)
