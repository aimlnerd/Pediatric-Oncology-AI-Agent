# UI Package — BSPHO Virtual Assistant

Streamlit web app for the clinician-facing Virtual Assistant and the admin
document-sync workflow described in the root [README.md](../../README.md)
("Solution Design (Draft v1)") and the [UI implementation plan](../../design/ui-implementation-plan.md).

This package currently implements **build steps 1–5** from the
implementation plan — "Scaffold & state", "Sidebar (Tier 1 + Tier 2)",
"Chat UI", "Admin upload flow" and "Wire the real agent": the page shell and
Clinician/Admin view switch, session state, the hospital/protocol sidebar, a
fully working conversational panel with inline citations, a client that talks
to the real Google ADK agent in [`packages/agent`](../agent/README.md) (with
an offline mock fallback), and an admin flow that uploads protocol PDFs to
GCS and re-indexes Vertex AI Search. Only **polish** (build step 6) remains.

## Package layout

```text
packages/ui/
├── pyproject.toml
├── src/ui/
│   ├── app.py                       # Streamlit entry point (page config, view switch, composition)
│   ├── sidebar.py                   # Tier 1 hospital filter + Tier 2 protocol checklist
│   ├── chat.py                      # Chat history, citation badges, input, turn submission
│   ├── admin.py                     # Admin view: upload form, hospital tagging, sync reporting
│   ├── gcs_sync.py                  # GCS upload + mapping/manifest update + VAIS re-index
│   ├── agent_client.py              # Agent interface + AdkAgentClient (real) + MockAgentClient
│   ├── data.py                      # Hospital/protocol mapping loader + Tier 1→Tier 2 filtering
│   ├── state.py                     # Session state schema + mutation helpers
│   └── mock_data/
│       ├── hospital_mapping.json    # Offline fallback for the agent's mapping table
│       └── sample_responses.json    # Canned agent answers, clarifications and refusals
└── tests/
    ├── conftest.py                  # Forces the offline mock backend so tests never hit Vertex AI
    ├── test_app.py                  # End-to-end app behavior via Streamlit's AppTest harness
    ├── test_adk_client.py           # Real-agent adapter: citation normalisation, arg forwarding
    ├── test_agent_client.py         # Mock agent rule matching, scoping, clarification flow
    ├── test_chat.py                 # Citation formatting + turn submission logic
    ├── test_data.py                 # Mapping loader / filtering unit tests
    ├── test_gcs_sync.py             # Admin sync: record building, uploads, re-index, failures
    └── test_state.py                # Session-state mutation unit tests
```

## Implementation notes

- **`data.py`** — `HOSPITALS` is the fixed list of Belgian centers
  (`UZA`, `UZG`, `UZL`, `HUDERF`, `UCL`, `CHU`). `load_hospital_mapping()`
  (cached with `lru_cache`) prefers the **real mapping** owned by the agent
  package (`agent.config.load_hospital_mapping()`, which reads
  `data/processed/hospital_mapping.json` or falls back to GCS) and normalises
  its `documents` entries into the UI's protocol shape; if that is
  unavailable it falls back to the bundled `mock_data/hospital_mapping.json`.
  Set `UI_MAPPING_SOURCE=mock` to force the offline sample.
  `get_protocols_for_hospital()` implements the Tier 1 → Tier 2 filter: given
  a hospital and an "include subprotocols" flag, it returns the protocols
  (and optionally subprotocols) tagged for that hospital.
- **`state.py`** — defines the full `st.session_state` schema
  (`DEFAULT_STATE`) and a set of pure mutation functions (`init_state`,
  `set_hospital`, `set_include_subprotocols`, `toggle_protocol`,
  `append_message`, `set_pending_clarification`, `record_feedback`,
  `reset_chat`). Each function takes a mutable mapping, so the same logic
  runs against `st.session_state` in the app and against a plain `dict` in
  unit tests — no Streamlit runtime required to test the state logic.
- **`sidebar.py`** — renders the Tier 1 hospital selector, the "Include
  Subprotocols (Deep Search)" toggle, and the Tier 2 protocol checklist with
  a selected-count caption and Select all / Clear all buttons. Changing the
  hospital clears stale `proto_*` checkbox widget values (Streamlit gives a
  keyed widget's stored value precedence over its `value` argument) and
  resets the conversation, since previously grounded answers no longer apply
  to the new protocol scope.
- **`agent_client.py`** — defines the `AgentClient` interface the chat UI
  depends on (`get_or_create_session`, `send`) plus the `AgentTurnResult`
  dataclass (`answer_markdown`, `citations`, `is_clarifying_question`). This
  is the **only** module that knows about the `agent` package, keeping the
  Streamlit layer decoupled from ADK internals.
  - `AdkAgentClient` wraps `agent.OncoProtocolAgentRunner`. It forwards the
    Tier 1 hospital as `selected_hospital` and the Tier 2 selection as
    `selected_protocols` (an empty selection becomes `None`, meaning "all
    protocols active at this hospital"). Because Streamlit shares one cached
    client across browser sessions while the runner owns a single persistent
    event loop, calls are serialised with a `threading.Lock`.
  - `normalise_agent_citations()` converts the agent's retrieved chunks
    (`short_title` / `filename` / `page_display`) into the UI's citation
    shape, de-duplicates by document + page and caps the list, since the
    agent returns everything it retrieved rather than only what it cited.
  - `looks_like_clarifying_question()` infers — the agent returns free-form
    markdown with no machine-readable flag — whether a reply is a
    clarification request: short, uncited replies ending in a question
    qualify, while long or cited answers do not (those often *end* with a
    follow-up offer). It only drives the input placeholder.
  - `MockAgentClient` serves rule-based canned answers from
    `sample_responses.json` and deliberately honours the UI's Tier 2 protocol
    scoping and Deep Search toggle, so it reproduces grounded answers with
    citations, proactive clarifying questions, subprotocol deep-search
    offers, out-of-scope refusals and "not covered in the selected documents"
    responses — no cloud access required.
  - `get_agent_client()` picks the backend from `UI_AGENT_BACKEND`
    (see [Backend selection](#backend-selection)).
- **`chat.py`** — renders chat history with `st.chat_message`, formats
  citations as `[Document Title, Version, p. N, Section]` caption badges
  (preferring the agent's pre-formatted `page_display`, which also covers
  page ranges such as `pp. 13–37`), shows thumbs up/down feedback per
  assistant message, swaps the input placeholder while a clarifying question
  is pending, and disables input until at least one protocol is selected.
  `submit_query()` holds the turn logic (append user message → call agent →
  append answer → update pending clarification) with no Streamlit calls, so
  it is directly unit-testable.

  A turn is deliberately split across **two script runs**, because the live
  agent can take a minute to answer:
  1. `queue_query()` appends the clinician's question and stores it in
     `pending_query`, then the app reruns — so the question is painted
     immediately and the chat input is disabled.
  2. `run_pending_query()` calls the agent behind a spinner in the assistant
     bubble, appends the answer and clears `pending_query`.

  Doing it in one run meant nothing was painted until the agent returned, so
  an interrupted run (browser reconnect, page reload, impatient re-submit)
  silently discarded the question *and* the answer — the chat appeared to
  wipe itself. Committing the question first makes it survive, and disabling
  the input while a turn is in flight stops the re-submit that caused it.
- **`gcs_sync.py`** — the three-stage admin pipeline, with every GCP
  touchpoint (storage bucket, Discovery Engine request) accepted as a
  parameter so the module is fully unit-testable without credentials:
  1. `upload_pdf()` writes the PDF to
     `gs://<bucket>/<prefix>/protocols/` or `/subprotocols/` depending on the
     document type.
  2. `upsert_document()` + `build_metadata_jsonl()` refresh
     `hospital_mapping.json` and `metadata.jsonl`, which are then uploaded
     under `<prefix>/metadata/` and mirrored to
     `data/processed/hospital_mapping.json` so the agent sees the new
     document immediately.
  3. `trigger_incremental_import()` POSTs to the Discovery Engine
     `documents:import` endpoint with `reconciliationMode=INCREMENTAL`, which
     upserts just the manifest's documents rather than rebuilding the index.

  Record shapes and `slugify_doc_id()` deliberately match
  [`scripts/setup_gcp.py`](../../scripts/setup_gcp.py), so re-uploading a
  revised protocol **replaces** its existing entry (same derived id) instead
  of creating a duplicate. `sync_document()` orchestrates the stages,
  short-circuiting on the first failure and returning a `SyncResult` that
  names the failing stage (`validate` / `upload` / `mapping` / `reindex`).
- **`admin.py`** — renders the upload form (PDF uploader, title, document
  type, participating-hospital multiselect), previews the document ID that
  will be derived from the filename, and lists the current document
  inventory. On submit it calls `sync_document()` and reports the outcome
  per stage; on success it calls `load_hospital_mapping.cache_clear()` so the
  new protocol appears in the clinician sidebar without a server restart.
- **`app.py`** — thin composition layer: page config, state init, mapping
  load, Clinician/Admin radio switch, the sidebar backend badge, and the 1:3
  two-column clinician layout. The agent client is created once per server
  process via `st.cache_resource`.

## Backend selection

Two environment variables control what the app talks to. Both default to
"use the real thing if it works", so a plain `streamlit run` gives the live
demo while tests and offline development stay deterministic.

| Variable | Values | Default | Effect |
| --- | --- | --- | --- |
| `UI_AGENT_BACKEND` | `auto` \| `adk` \| `mock` | `auto` | `auto` tries the real ADK agent and silently falls back to the mock if it cannot be constructed; `adk` forces the real agent and surfaces errors; `mock` forces the offline stub. |
| `UI_MAPPING_SOURCE` | `auto` \| `mock` | `auto` | `auto` prefers the agent's real hospital mapping; `mock` forces the bundled sample mapping. |

The sidebar shows which pair is live — 🟢 *Vertex AI Search + ADK agent* or
🟡 *Offline mock agent*, plus the document count and mapping source — so you
can tell at a glance whether a demo is hitting real data.

### Prerequisites for the real backend

1. Authenticate against the GCP project backing Vertex AI Search:
   ```bash
   gcloud auth application-default login
   ```
2. Make sure the hospital mapping is present at
   `data/processed/hospital_mapping.json` (repository root). If it is
   missing, the agent falls back to downloading it from GCS, or you can pull
   it directly:
   ```bash
   gsutil cp gs://<bucket>/uc1/metadata/hospital_mapping.json data/processed/
   ```

See [`packages/agent/README.md`](../agent/README.md) for the agent's own
configuration.

## Setup

From the repository root (this is a `uv` workspace; the `ui` member depends
on `streamlit` and on the workspace `agent` package, with `pytest` as a dev
dependency):

```bash
uv sync --package ui
```

## Running the app (manual smoke test)

```bash
uv run --package ui streamlit run packages/ui/src/ui/app.py
```

To run fully offline (no GCP credentials needed):

```bash
UI_AGENT_BACKEND=mock UI_MAPPING_SOURCE=mock \
  uv run --package ui streamlit run packages/ui/src/ui/app.py
```

This starts the app at `http://localhost:8501` (use `--server.port <port>` if
that port is taken, and `--server.headless true` to skip the local browser
prompt/telemetry notice in CI-style runs). Open the URL in a browser and
check:

0. **Backend badge:** the sidebar reports 🟢 *Vertex AI Search + ADK agent*
   with the full document count when credentials and the mapping are
   available, or 🟡 *Offline mock agent* otherwise.

1. **Sidebar (Tier 1 / Tier 2):**
   - No hospital selected → an info banner prompts you to pick one and no
     checkboxes are shown.
   - Selecting a hospital (e.g. `UZA`) populates the protocol checklist, with
     every protocol checked by default and a `(n/n selected)` caption.
   - Unchecking a protocol removes it from the selection; **Clear all** empties
     the selection and the app warns that at least one protocol is required;
     **Select all** restores it.
   - Toggling "Include Subprotocols (Deep Search)" adds the hospital's
     subprotocols (shown with a `↳` prefix, selected by default) without
     disturbing manual unchecks you've already made on protocols.
   - Switching the hospital resets the checklist back to "all selected" for
     the new hospital and clears the conversation.
2. **Chat panel:**
   - The input is disabled until a hospital and at least one protocol are
     selected.
   - A grounded question (e.g. *"How is early response assessment done with
     PET in Hodgkin lymphoma?"* at a center running `EuroNet-PHL-C2`) returns
     a markdown answer with a `📄 Sources: [..., p. 96, ...]` caption.
   - An ambiguous question (e.g. *"Starting chemotherapy for leukemia, how
     high should the platelets and white blood cells be?"*) returns a
     clarifying question and the input placeholder switches to *"Answer the
     clarifying question..."*; replying *"ALL, maintenance phase"* then gives
     a cited answer.
   - Asking about a protocol the selected hospital does not run (e.g.
     neuroblastoma/`HRNBL2` at `UZA`) returns a "could not find information"
     refusal with no citations.
   - An out-of-scope question (e.g. *"treatment for adult diabetes"*) is
     refused.
   - A subprotocol topic (e.g. *"thrombosis and anticoagulation"*) offers a
     Deep Search; enabling the toggle and re-asking returns a cited answer.
   - Thumbs up/down appears under each assistant message, and **New chat**
     clears the conversation.
3. **Admin view:** switch the sidebar radio to **Admin**.
   - The current inventory is reported (e.g. *"Currently indexed: 27
     protocols and 5 subprotocols"*) and can be expanded per document.
   - Clicking **Sync & Re-index** with an empty form reports
     *"Form validation failed"* plus one warning per problem — no GCS call is
     made.
   - Selecting a PDF previews the derived document ID.
   - A full submission (PDF + title + at least one hospital) uploads the
     file, rewrites the mapping and manifest, triggers an incremental
     re-index, and reports the resulting `gs://` URI, document count and
     operation name. The new protocol appears in the clinician sidebar for
     the tagged hospitals right away; it becomes *searchable* once Vertex AI
     Search finishes indexing.
   - If any stage fails, the error names the stage that failed and, when the
     PDF already landed, the URI it landed at.

> **Note:** a real submission writes to the shared GCS bucket and the live
> Vertex AI Search data store. Use `UI_MAPPING_SOURCE=mock` or a scratch
> `GCS_PREFIX` if you want to exercise the form without touching the demo
> corpus.

Stop the server with `Ctrl+C` (or find and kill the process:
`ps aux | grep streamlit`, then `kill <PID>`).

## Running the automated tests

```bash
uv run --package ui pytest packages/ui
```

or, from inside `packages/ui/`:

```bash
uv run pytest
```

The tests **never touch Vertex AI or GCS**: `tests/conftest.py` pins
`UI_AGENT_BACKEND=mock` and `UI_MAPPING_SOURCE=mock` and clears the mapping
cache between tests, so the suite is offline, fast and deterministic
regardless of your local gcloud state.

Test coverage:
- **`test_data.py`** — mapping loader shape, and Tier 1 → Tier 2 filtering
  (per-hospital scoping, subprotocol inclusion/exclusion, empty-hospital
  handling).
- **`test_state.py`** — state initialization/defaults/idempotency, hospital
  switch resetting the selection, protocol checkbox toggling, subprotocol
  toggle preserving manual deselections, chat message appending, and
  `reset_chat` behavior — all against plain dicts, no Streamlit runtime
  needed.
- **`test_agent_client.py`** — session creation/reuse, grounded answers with
  citations, clarifying questions and their follow-up resolution, out-of-scope
  refusals, Tier 2 protocol scoping ("not covered" when the relevant protocol
  is unselected), the subprotocol Deep Search offer/answer, and per-session
  history isolation.
- **`test_chat.py`** — citation formatting (full, partial and missing fields,
  plus the agent's `page_display` page ranges), and `submit_query` turn logic
  verified with a stub client: message appending, forwarding the selected
  hospital, protocols and Deep Search flag, ADK session id reuse, and
  pending-clarification set/clear — plus one end-to-end exchange against the
  real `MockAgentClient`.
- **`test_adk_client.py`** — the real-agent adapter against a stub runner:
  citation normalisation (de-duplication, capping, title/page fallbacks),
  the clarifying-question heuristic (including that long or cited answers are
  *not* treated as clarifications), argument forwarding to the runner
  (hospital, protocol scoping, empty selection → `None`), and
  `get_agent_client()` backend selection for each `UI_AGENT_BACKEND` value.
- **`test_gcs_sync.py`** — the admin pipeline against a fake bucket and a
  fake Discovery Engine request: ID slugification (matching the setup
  script), form validation, record/manifest shapes, upserting vs. appending
  on re-upload, doc-type subfolder routing, the `INCREMENTAL` import payload
  and regional endpoint host, and `sync_document` orchestration — including
  that each stage failure (`validate` / `upload` / `mapping` / `reindex`)
  short-circuits the remaining stages and still reports what succeeded.
- **`test_app.py`** — full app behavior using Streamlit's
  [`AppTest`](https://docs.streamlit.io/develop/api-reference/app-testing)
  harness: the shell and view switch, the sidebar (checklist population,
  selected count, unchecking, Select all / Clear all, Deep Search toggle,
  stale-checkbox reset on hospital change), and the chat panel (input
  enable/disable, grounded answer with a rendered citation caption, the
  hospital scoping of answers, clarify-then-answer flow and placeholder
  switch, out-of-scope refusal, subprotocol deep-search flow, New chat, and
  conversation reset on hospital change) — plus the admin view (form
  rendering, inventory caption, and the validation-error path).

All 126 tests should pass with no live GCP credentials or network access
required (everything runs against the bundled mock mapping data and fake
GCS/Discovery Engine clients).

## Deployment (Docker / Cloud Run)

The repo-root [`Dockerfile`](../../Dockerfile) builds the UI for Cloud Run.
It is a multi-stage `uv` build: dependencies are installed into their own
layer before the source is copied, and the runtime stage runs as a non-root
`appuser`.

```bash
# Build (from the repo root)
docker build -t bspho-ui .

# Run offline, with no GCP credentials required
docker run --rm -p 8080:8080 \
  -e UI_AGENT_BACKEND=mock -e UI_MAPPING_SOURCE=mock \
  --name bspho-ui-smoke bspho-ui

curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/_stcore/health  # 200
```

The container honours Cloud Run's injected `PORT` (defaulting to `8080`), and
Streamlit runs headless with CORS/XSRF protection disabled because Cloud Run
terminates TLS in front of the container.

```bash
gcloud run deploy bspho-ui \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --service-account bspho-ui@<PROJECT_ID>.iam.gserviceaccount.com
```

Two things to note when deploying:

- **The service account needs Vertex AI Search and GCS access** —
  `roles/discoveryengine.viewer` (plus `roles/discoveryengine.editor` if the
  Admin sync view is enabled) and read access to the protocol bucket.
- **The hospital mapping is not baked into the image.** `data/` is
  gitignored and excluded by `.dockerignore`, so at runtime the agent falls
  back to fetching `hospital_mapping.json` from GCS. Leave
  `UI_MAPPING_SOURCE` unset (`auto`) in production.
