# Chain of specialised agents instead of a single agent

The clinical Q&A flow is a chain of four ADK agents — **scope guard → clarifier → retriever → answerer** — rather than one `LlmAgent` holding all guardrails in a single prompt. We chose the chain so each responsibility (refusal, clarification, retrieval, grounded answering) has its own small instruction set that can be tested in isolation against the Golden Dataset, and so the answerer never has search tools and can only cite what the retriever put in state.

The chain is built as an ADK 2 `Workflow` graph with routed edges (`out_of_scope` → refusal, `ask` → `RequestInput` pause), not the deprecated `SequentialAgent`, so early exits are explicit edges validated at build time.

## Considered options

- **Single `LlmAgent` + search tool + callbacks** — fewer model calls and lower latency, but all behaviours compete in one prompt and are hard to test separately.
- **Coordinator with specialist sub-agents** — flexible, but routing is model-driven and less predictable.
- **Custom `BaseAgent` orchestrator / `SequentialAgent` with skip callbacks** — the ADK 1.x way; superseded by `Workflow` in ADK 2.

## Consequences

- More model calls per turn (latency, cost); the scope guard and clarifier can short-circuit the chain so refusals and clarifications stay cheap.
- The clarifier runs before retrieval, so it judges missing Clinical Discriminators from a protocol catalog, not protocol text. Branching discovered only in the text (risk group, phase) is handled by the answerer with a per-branch answer rather than a second question.
- A clarification reply resumes the paused workflow, so the UI must track the pending interrupt instead of always starting a new turn.
