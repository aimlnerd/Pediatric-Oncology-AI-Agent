# ui

Streamlit app: center selector (stand-in for BSPHO auth), Protocol Selection sidebar, chat with citation badges, and an admin view for uploading protocols and amendments. The build plan is `design/ui-implementation-plan.md`.

- Talk to the agent only through its `send` / `resume` entry point, and read the center mapping from the agent package's protocol manifest.
- Keep GCS and Vertex AI Search calls injectable so they can be faked in unit tests.
