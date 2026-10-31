# `agent` Package — BSPHO Pediatric Oncology ADK Agent

This package contains the standalone **Google ADK (`google-adk`)** clinical protocol assistant and custom **Vertex AI Search (`google-cloud-discoveryengine`)** RAG tools.

## Package Structure
- `src/agent/agent.py` — Defines `root_agent` (`bspho_oncoprotocol_assistant`) with clinical guardrails, proactive clarification rules, and dynamic Tier-1/Tier-2 instruction injection.
- `src/agent/tools.py` — Custom `FunctionTool`s (`search_protocols` and `search_subprotocols`) that query Vertex AI Search (`bspho-oncoprotocols-ds`) in `CHUNKS` mode with hospital/protocol metadata filtering and page-number extraction.
- `src/agent/runner.py` — `OncoProtocolAgentRunner` class for programmatic execution + CLI test runner.
- `src/agent/config.py` — GCP environment, credential bridging, and `hospital_mapping.json` loader.

---

## Deployment to Vertex AI Agent Engine (Agent Runtime) with Tracing

Google ADK agents can be deployed directly to **Vertex AI Agent Engine** (also known as Agent Runtime / Reasoning Engines). This managed environment handles container scaling and automatically instruments OpenTelemetry traces sent to **Cloud Trace** for every agent step, tool invocation, and LLM call.

### 1. Architecture & Configuration Options

When deploying to Agent Engine, the deployment uses `adk deploy agent_engine` under the hood. Key configuration parameters:

| Configuration Option | Flag / Environment Variable | Purpose & Recommendation |
| :--- | :--- | :--- |
| **GCP Project** | `--project` or `GOOGLE_CLOUD_PROJECT` | Target GCP project containing Vertex AI and Discovery Engine. |
| **Region** | `--region` or `GOOGLE_CLOUD_LOCATION` | Region for Agent Engine (e.g. `europe-west1`). Note: Vertex AI Search can reside in `global` or `eu`. |
| **Tracing / Telemetry** | `--otel` / `--otel_to_cloud` | Exports OpenTelemetry spans for agent reasoning, LLM latency, and tool invocations to **Cloud Trace**. Enabled by default in our deployment script. |
| **Runtime Service Account** | `--service-account` | The IAM identity assumed by the agent runtime. Needs: `roles/aiplatform.user`, `roles/discoveryengine.viewer`, `roles/storage.objectViewer`, and `roles/cloudtrace.agent`. |
| **Data Store Binding** | `VERTEX_SEARCH_DATA_STORE_ID` | Injected into the runtime `.env` so the agent's RAG tools query `bspho-oncoprotocols-ds`. |

---

### 2. Deploying via Lightweight Script

A lightweight deployment script is provided at `scripts/deploy_agent_engine.py`. It bundles the agent code, packages the hospital mapping metadata, configures runtime variables with tracing enabled, and invokes the ADK deployment pipeline:

```bash
# 1. Run deployment to Vertex AI Agent Engine
uv run python scripts/deploy_agent_engine.py \
  --project qwiklabs-gcp-01-a76134d9a6dc \
  --region europe-west1 \
  --display-name bspho-oncoprotocol-agent
```

Once deployment completes, the script outputs the **Agent Engine Resource ID / URL** (e.g., `projects/123.../locations/europe-west1/reasoningEngines/9876543210`).

---

### 3. Testing the Deployed Agent Engine API

You can test the live deployed endpoint using `scripts/call_agent_engine.py`:

```bash
# Query the live Agent Engine endpoint with hospital context
uv run python scripts/call_agent_engine.py \
  --project qwiklabs-gcp-01-a76134d9a6dc \
  --region europe-west1 \
  --agent-id 9876543210 \
  --hospital UZG \
  -q "I have a patient with 150 000 WBC, what are the next steps for acute management?"
```

#### Viewing Cloud Traces
1. Go to the **Google Cloud Console**.
2. Navigate to **Vertex AI** -> **Agent Engine** (or **Cloud Trace** -> **Trace Explorer**).
3. Inspect the execution graph showing each LLM turn, the `search_protocols` tool call, and latency.

---

## How to Test & Run the Agent Locally

Make sure you have synced the workspace from the repository root (`swg_2026_va/`):
```bash
uv sync --all-packages
```

### 1. Run via the CLI Runner (`agent.runner`)

You can test the agent directly from the terminal using `uv run python -m agent.runner` (or `uv run agent-runner`):

#### A. Single Query (with Hospital & Protocol Filtering)
```bash
# Default hospital (UZG - all 27 protocols active)
uv run python -m agent.runner -q "What are the hematological and non-hematological conditions before starting dinutuximab?"

# Filter by a specific Belgian center (e.g., UZA)
uv run python -m agent.runner --hospital UZA -q "I have a patient with 150 000 WBC, what are the next steps for acute management?"

# Restrict to specific checked protocols + enable subprotocol deep search
uv run python -m agent.runner --hospital UZG --protocols "ALLTogether1 Master Protocol" --subprotocols \
  -q "Patient on ALLTogether1 developed a stroke-like event and suspected sinus venous thrombosis. What is the management?"
```

#### B. Interactive Multi-Turn Chat in Terminal
```bash
uv run python -m agent.runner --interactive --hospital UZG
```

#### C. Golden Dataset Smoke Test (`Q3`, `Q4`, `Q5`)
```bash
uv run python -m agent.runner --golden-smoke
```

---

### 2. Test Programmatically in Python

Import `OncoProtocolAgentRunner` from `agent` to invoke the ADK agent in your own scripts or UI:

```python
from agent import OncoProtocolAgentRunner

runner = OncoProtocolAgentRunner()

# Turn 1: Ask an open-ended or under-specified clinical question
result = runner.ask(
    query="I have a patient with 150 000 WBC, what are the next steps for acute management?",
    session_id="demo-session-1",
    selected_hospital="UZG",           # Tier-1 filter: UZG, UZA, UZL, CHU, UCL, HUDERF
    selected_protocols=None,           # Tier-2 filter: list of short_titles, or None for all active at hospital
    include_subprotocols=False,        # Set True to include the 5 ALLTogether1 subprotocols
)

print("Answer:\n", result["answer"])
print("\nTool Calls:", result["tool_calls"])
for c in result["citations"]:
    print(f"- {c['citation']} (score={c['relevance_score']})")
```

---

### 3. Launch the Built-in Google ADK Developer Web UI

Because `src/agent/__init__.py` exposes `root_agent`, you can also launch Google ADK's built-in interactive web debugger to inspect events, tool calls, and session state visually:

```bash
uv run adk web packages/agent/src
```
Then open `http://localhost:8000` in your browser and select the `agent` app.

