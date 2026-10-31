# UI Implementation Plan — Virtual Assistant (BSPHO Pediatric Hem/Onc)

> Source of truth: [README.md](../README.md) — "Solution Design (Draft v2)" section; terms per [GLOSSARY.md](../GLOSSARY.md). Deep Search (sub-protocols) is deferred.
> Scope: `ui/` package only (Streamlit app + GCS/admin sync helper). The AI Solution Layer (`agent/`) is a separate, decoupled module consumed through its ADK `Runner` interface.

## 1. Goal & Constraints

Build a **NotebookLM-style, 2-column Streamlit web app** that lets a pediatric oncologist:
1. Select their center (Tier 1 — Center Scope; stand-in for BSPHO auth).
2. Check/uncheck protocols within the Center Scope (Tier 2 — Protocol Selection).
3. Chat with the grounded ADK agent and see inline `[Document Title, Page #]` citations.
4. (Admin) Upload/replace a protocol PDF, tag participating hospitals, and trigger re-indexing.

Hackathon constraints: few hours of build time → favor Streamlit's built-in widgets/state over custom components, keep the UI stateless between reruns by leaning on `st.session_state`, and stub/mock anything not yet wired to GCP (hospital mapping, ADK agent) behind clean interfaces so the AI layer can be swapped in without UI changes.

## 2. Tech Stack (from README §3)

| Concern | Choice |
|---|---|
| UI framework | Streamlit (`ui/app.py`) |
| Agent integration | Google ADK `Runner` + `InMemorySessionService` (imported from `agent/`) |
| Document/admin sync | `ui/gcs_sync.py` using `google-cloud-storage` + `google-cloud-discoveryengine` |
| Config/mapping source | `hospital_mapping.json` + `metadata.jsonl` in GCS (mirrored locally for dev) |
| Deployment | Dockerfile → Cloud Run |

## 3. File/Module Breakdown

```text
ui/
├── app.py                 # Entry point: page config, layout, routing between Clinician/Admin views
├── state.py                # st.session_state schema + init helpers (new)
├── sidebar.py               # Tier 1 hospital selector + Tier 2 protocol checklist (new)
├── chat.py                  # Chat history render, input box, citation badge rendering (new)
├── admin.py                  # Admin tab: upload form, hospital tag multiselect, sync button (new)
├── agent_client.py           # Thin wrapper around agent.Runner (invoke, stream, session mgmt) (new)
├── gcs_sync.py               # GCS upload, hospital_mapping.json update, VAIS import trigger
└── mock_data/
    ├── hospital_mapping.json # Local dev fallback mirroring GCS mapping table
    └── sample_responses.json # Canned agent responses for UI dev before agent/ is ready
```

Rationale for splitting beyond the README's single `app.py`: keeps each Streamlit "fragment" independently testable and lets the sidebar/chat/admin pieces be built in parallel by different contributors during the hackathon, while `app.py` stays a thin composition layer.

## 4. Session State Schema (`state.py`)

```python
st.session_state = {
    "hospital": str | None,  # Tier 1 selection, e.g. "UZA"
    "available_protocols": list[dict],  # [{id, title, doc_type}] filtered by hospital
    "selected_protocol_ids": set[str],  # Tier 2 checklist state
    "chat_history": list[{role, content, citations}],
    "adk_session_id": str,  # Bound to ADK InMemorySessionService session
    "pending_interrupt_id": str
    | None,  # Set when the workflow paused on a Clarifying Question (RequestInput)
    "is_admin": bool,  # Toggles Clinician vs Admin view
}
```

- Initialize once via `init_state()` called at the top of `app.py` (idempotent, uses `st.session_state.setdefault`).
- Changing `hospital` must reset `selected_protocol_ids` and re-derive `available_protocols` from the mapping table.

## 5. Page Layout (`app.py`)

1. `st.set_page_config(layout="wide", page_title="BSPHO Virtual Assistant")`.
2. Top-level view switch: `st.sidebar.radio("View", ["Clinician", "Admin"])` → routes to clinician 2-column layout or the admin form.
3. Clinician view:
   - `col_sidebar, col_chat = st.columns([1, 3])`
   - `col_sidebar` → `render_sidebar()` (Tier 1 + Tier 2).
   - `col_chat` → `render_chat()` (history + input + citations).
4. Admin view → `render_admin()` full-width form.

## 6. Sidebar — Tier 1 & Tier 2 (`sidebar.py`)

- **Tier 1:** `st.selectbox("Hospital / Center", HOSPITALS)` where `HOSPITALS = ["UZA", "UZG", "UZL", "HUDERF", "UCL", "CHU"]`.
- On change, load the protocol list for that hospital from `hospital_mapping.json` (via a cached loader, `@st.cache_data(ttl=...)`, pointed at GCS in prod / local JSON in dev).
- **Tier 2:** render one `st.checkbox(protocol.title, value=True, key=f"proto_{protocol.id}")` per applicable protocol inside an `st.expander` or scrollable container; sync checked ids into `selected_protocol_ids`.
- Guard rail: disable the chat input (and show a warning) if `selected_protocol_ids` is empty.

## 7. Chat Panel (`chat.py`)

- Render history: loop `chat_history`, use `st.chat_message(role)` for user/assistant bubbles.
- For assistant messages, render markdown answer followed by citation badges, e.g. `st.caption("📄 " + ", ".join(f"[{c.doc_title}, p.{c.page}]" for c in msg.citations))`.
- Input: `st.chat_input("Ask about protocols...")`, disabled when no protocols selected.
- On submit:
  1. Append user message to `chat_history`.
  2. If `pending_interrupt_id` is set, call `agent_client.resume(reply, pending_interrupt_id, adk_session_id)`; otherwise call `agent_client.send(query, center, selected_protocol_ids, adk_session_id)`.
  3. If the result is a **Clarifying Question**, render it as an assistant message and store its `interrupt_id` in `pending_interrupt_id`; otherwise clear `pending_interrupt_id`.
  4. Append assistant answer + citations to `chat_history`; `st.rerun()` to refresh the view.
- Feedback (stretch): `st.feedback("thumbs")` under each assistant message, persisted to a lightweight log (local file or GCS) keyed by session id and turn index.

## 8. Agent Integration (`agent_client.py`)

- Wraps ADK `Runner` + `InMemorySessionService` so `chat.py`/`app.py` never import `agent/` internals directly — keeps the UI/AI layers decoupled per the README's architecture split.
- API surface:
  - `get_or_create_session(adk_session_id: str | None) -> str`
  - `send(query: str, center: str, selected_protocols: list[str], session_id: str) -> AgentTurnResult` — writes center + Protocol Selection into ADK session state, then runs the workflow.
  - `resume(reply: str, interrupt_id: str, session_id: str) -> AgentTurnResult` — resumes a workflow paused on a Clarifying Question.
  - `AgentTurnResult` has `kind` (`answer` / `not_covered` / `clarifying_question` / `out_of_scope`), `body_markdown`, `citations` (resolved: protocol, version, page, section), `interrupt_id`.
- Until `agent/` is implemented/stable, `agent_client.py` can read from `mock_data/sample_responses.json` behind the same function signature, so swapping in the real agent is a one-line change.

## 9. Admin View (`admin.py` + `gcs_sync.py`)

- `st.file_uploader("Upload protocol PDF", type="pdf")`.
- `st.text_input("Protocol ID / title")`.
- `st.multiselect("Participating hospitals", HOSPITALS)`.
- `st.button("Sync & Re-index")` →
  1. `gcs_sync.upload_pdf(file)` → writes to `gs://<bucket>/protocols/` (an amendment replaces the previous version under the same protocol ID).
  2. `gcs_sync.update_mapping(protocol_id, hospitals)` → updates `hospital_mapping.json` + appends/updates `metadata.jsonl`.
  3. `gcs_sync.trigger_incremental_import()` → calls Vertex AI Search `import_documents` (`INCREMENTAL` mode).
  4. Show `st.success` with upload + mapping + re-index confirmation (or `st.error` with the failure stage).
- Keep `gcs_sync.py` functions pure/testable (accept bucket/client as params) so they can be unit-tested without live GCP credentials (mock the clients).

## 10. Build Order (Hackathon Sequencing)

1. **Scaffold & state** — `state.py`, empty `app.py` shell with view switch, mock hospital mapping JSON.
2. **Sidebar (Tier 1 + Tier 2)** — wire selection + checklist against mock mapping data; verify `selected_protocol_ids` updates correctly.
3. **Chat UI with mocked agent** — `chat.py` + `agent_client.py` against `sample_responses.json`; validate citation rendering and clarifying-question flow end-to-end with fake data.
4. **Admin upload flow** — `admin.py` + `gcs_sync.py` stubs; validate against a real GCS bucket once provisioned by `scripts/setup_gcp.py`.
5. **Wire real ADK agent** — swap `agent_client.py` internals to the real `Runner`; no changes needed in `chat.py`.
6. **Polish** — feedback buttons, loading spinners (`st.spinner` while awaiting agent), empty/error states, Dockerfile smoke test for Cloud Run.

## 11. Open Questions / Risks

- **Clickable citations → PDF viewer** (Could-Have): would need an embedded PDF viewer component (e.g. `streamlit-pdf-viewer` or an `st.components.v1.iframe`); deferred unless time remains.
- **Streaming responses:** Streamlit's `st.chat_message` supports token streaming via a generator; confirm whether ADK `Runner` exposes a streaming API before committing to it, otherwise fall back to single-shot responses with a spinner.
- **Hospital mapping source of truth during dev:** until `scripts/setup_gcp.py` populates real GCS metadata, `mock_data/hospital_mapping.json` must be kept in sync manually with the Excel mapping table.
- **Session persistence across Streamlit reruns:** `InMemorySessionService` is process-local; confirm this is acceptable for a single-instance Cloud Run hackathon demo (no multi-replica session loss concerns).
