# Center Scope enforced in code, not by the model or the index

The search tool takes only a query string from the model. The clinician's Center and Protocol Selection live in ADK session state, written by the UI; the tool re-validates the selection against the Mapping Table and builds the Vertex AI Search filter (`protocol_id: ANY(...)`) itself. Center participation is deliberately **not** stored as metadata in the search index.

We chose this because a clinician must never receive protocol content from a trial their Center does not run, and a model-supplied filter can be widened by a rephrased prompt. Keeping participation out of the index means a Mapping Table change takes effect immediately, without re-importing 27 large PDFs.

## Considered options

- **Model passes `selected_protocols` as a tool argument** (README draft v1) — rejected: the model controls the scope.
- **Filter on a `centers` metadata field in the index** — rejected: every participation change would require re-indexing, and the code check is needed anyway.

## Consequences

- The Mapping Table (protocol manifest) must be available to the agent at runtime.
- When the answer exists only outside the Protocol Selection, the assistant says Not Covered; it can hint "check it in the sidebar" only for protocols inside the Center Scope.
