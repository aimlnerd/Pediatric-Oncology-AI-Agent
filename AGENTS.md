# BSPHO Protocol Assistant

A search assistant that answers Belgian pediatric hematology-oncology clinicians strictly from the clinical trial protocols their center runs, with a page-level citation for every claim.

This is a `uv` workspace of two packages, `agent` (ADK agents + Vertex AI Search, built) and `ui` (Streamlit, not built yet). Run tools with `uv run`, add dependencies with `uv add --package <name>`, and install with `uv sync --all-packages`.

**Start here:** the README section "Project status and handoff" lists what is done, what is unverified, and the next steps in order.

## Feedback loop

A change is done when all of these are green:

```bash
uv run ruff check --fix . && uv run ruff format .
uv run pyright
uv run pytest
```

Ruff requires type hints on every signature and pyright runs in strict mode, so let their errors guide fixes. For deterministic code, write the failing unit test first.

## Where to look

- Domain language: `GLOSSARY.md`. Use its terms (Center Scope, Protocol Selection, Clarifying Question, Not Covered…) in code, tests and prose.
- Architecture decisions: `docs/adr/`. Read them before changing agent topology or how scope is enforced; record a new ADR for hard-to-reverse trade-offs.
- Design and requirements: `README.md`. When it disagrees with the BSPHO deck in `swg-use-cases/`, the deck wins.
- Architecture diagrams: sources in `docs/diagrams/*.mmd`, shown in the README. When the workflow graph, tool or index design changes, update the `.mmd` and re-render its PNG (command in the README).
- Run the agents with `uv run adk web packages/agent/src` (setup and chat commands in the README). The search index is managed by `scripts/search_index.sh`.

## Environment

- **Python on managed Windows:** Application Control blocks the unsigned `_ssl` in uv-downloaded Pythons, so every HTTPS call from them fails. The venv uses a signed Miniconda Python pinned in `.python-version` (gitignored; recreate with `uv python pin <path-to-python.exe>`). Never set `UV_PYTHON`: `uv pip` then installs into the conda base environment.
- **gcloud** needs `CLOUDSDK_PYTHON` pointing at its bundled Python (`<Cloud SDK>/platform/bundledpython/python.exe`) in each shell, or permanently as a user environment variable.
- **GCP:** project and region are in `packages/agent/src/agent/.env` (gitignored; `.env.example` is the template). The Python clients use Application Default Credentials (`gcloud auth application-default login`).
- **Shell:** long heredocs in the Bash tool fail on quoting; create files with the Write tool instead.

Test fixtures and prompts use synthetic patient vignettes only.
