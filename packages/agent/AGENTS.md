# agent

The AI layer: an ADK `Workflow` (scope guard → clarifier → retriever → answerer) over a Vertex AI Search data store of protocol PDFs. `agent.py` exposes `root_agent` for `adk web`; `workflow.py` builds the graph and accepts scripted models for tests.

- `google-adk` is pinned at 2.13.0. ADK 2 changed a lot from 1.x, so check the installed package source (`.venv/Lib/site-packages/google/adk`) rather than memory. Build flows as `Workflow` graphs with routed edges; the `SequentialAgent` family is deprecated.
- The search tool receives only query text. Center and Protocol Selection come from session state and are validated against the protocol manifest (ADR 0002). Center participation lives in the manifest, never in the index.
- Citations reference retrieved chunk ids; page numbers and versions are filled in by code (`citations.py`, `finalize` node), never by the model.
- Unit tests cover code that makes no model call and use a fake searcher (`tests/fakes.py`). `tests/test_workflow.py` runs the whole chain with scripted models. LLM behaviour against the Golden Dataset is meant to be checked with `adk eval` (not written yet).

## ADK 2 behaviours that are easy to get wrong

- A node does not re-run after a `RequestInput` pause: the clinician's reply becomes that node's output and flows to the next edge. That is why the clarification loop is `ask_user → record_reply → clarifier`.
- To resume a pause, send a `FunctionResponse` named `adk_request_input` with the interrupt id, and pass the paused run's `invocation_id`. A plain text message starts a new run instead.
- A single-turn `Agent` node sees only its own input, not the chat history. Context reaches it through the instruction provider (`case_notes` in `prompts.py`). Instructions are provider functions so braces in protocol text are never treated as template placeholders.
- `Event(message=..., route=...)` works at runtime but pyright rejects it; use `events.make_event`.
- An agent node's JSON output also appears as a chat bubble in `adk web`; the clean reply is the `bspho_assistant` message from the following function node.
- Chunks open with their ancestor headings (`# 10. FRONTLINE… ## 10.1 …`); headings after the body starts are page headers and footers (`# CRCTU-PRT-QCD-001, version 1.0`). `search.extract_section` therefore reads only the leading heading block.
- Vertex AI Search chunk requests need `searchResultMode: CHUNKS`; the `protocol_id` filter works because the schema marks it indexable. Import JSONL must be ASCII-escaped (Windows stdout is cp1252 and corrupted non-ASCII titles).
