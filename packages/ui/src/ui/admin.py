"""Admin view: upload a protocol PDF, tag hospitals, and trigger re-indexing.

Design doc section 9. All GCP work is delegated to :mod:`ui.gcs_sync`; this
module only renders the form, reports per-stage outcomes, and invalidates the
cached hospital mapping so a newly uploaded protocol shows up in the
clinician sidebar without restarting the server.
"""

from __future__ import annotations

import streamlit as st

from ui.data import HOSPITALS, load_hospital_mapping
from ui.gcs_sync import DOC_TYPES, SyncResult, slugify_doc_id, sync_document

#: Stage -> operator-facing label, used to explain where a failed sync stopped.
STAGE_LABELS: dict[str, str] = {
    "validate": "Form validation",
    "upload": "PDF upload to Cloud Storage",
    "mapping": "Hospital mapping & search manifest update",
    "reindex": "Vertex AI Search re-indexing",
    "done": "Complete",
}


def _render_result(result: SyncResult) -> None:
    """Render the outcome, naming the stage that failed (design doc section 9.4)."""
    if not result.ok:
        st.error(f"**{STAGE_LABELS.get(result.stage, result.stage)} failed** — {result.message}")
        for error in result.errors:
            st.warning(error)
        if result.gcs_uri:
            st.caption(f"The PDF was uploaded to `{result.gcs_uri}` before the failure.")
        return

    st.success(result.message)
    st.caption(
        f"Uploaded to `{result.gcs_uri}` · mapping now tracks "
        f"{result.document_count} documents"
    )
    if result.operation_name:
        st.caption(f"Re-index operation: `{result.operation_name}`")
    st.info(
        "Indexing runs asynchronously — the document becomes searchable once "
        "Vertex AI Search finishes processing it."
    )


def render_admin(mapping: dict | None = None) -> None:
    """Render the admin upload form and handle submission."""
    mapping = mapping if mapping is not None else load_hospital_mapping()

    st.subheader("Admin / Document Sync")
    st.caption(
        "Upload a protocol PDF, tag the hospitals that run it, and re-index "
        "the Vertex AI Search data store."
    )

    with st.form("admin_upload_form"):
        uploaded = st.file_uploader("Protocol PDF", type="pdf")
        short_title = st.text_input(
            "Protocol title",
            help="Short title shown in the sidebar and in citations (e.g. 'HRNBL2').",
        )
        full_title = st.text_input(
            "Full title (optional)",
            help="Long-form study title, used for search relevance.",
        )
        doc_type = st.radio("Document type", DOC_TYPES, horizontal=True)
        hospitals = st.multiselect("Participating hospitals", HOSPITALS)
        submitted = st.form_submit_button("Sync & Re-index", type="primary")

    if uploaded is not None:
        st.caption(f"Document ID will be `{slugify_doc_id(uploaded.name)}`")

    if not submitted:
        _render_current_inventory(mapping)
        return

    with st.spinner("Uploading, updating the mapping and re-indexing..."):
        result = sync_document(
            file_bytes=uploaded.getvalue() if uploaded is not None else b"",
            filename=uploaded.name if uploaded is not None else "",
            short_title=short_title,
            hospitals=hospitals,
            doc_type=doc_type,
            title=full_title,
        )

    if result.ok:
        # The sidebar reads this cached mapping; refresh it so the new
        # document appears without a server restart.
        load_hospital_mapping.cache_clear()

    _render_result(result)


def _render_current_inventory(mapping: dict) -> None:
    """Show what is currently indexed, so admins can verify before uploading."""
    protocols = mapping.get("protocols", [])
    subprotocols = mapping.get("subprotocols", [])
    st.divider()
    st.caption(
        f"Currently indexed: **{len(protocols)}** protocols and "
        f"**{len(subprotocols)}** subprotocols."
    )
    with st.expander("Current document inventory"):
        for doc in protocols + subprotocols:
            hospitals = ", ".join(doc.get("hospitals", [])) or "—"
            st.markdown(f"- **{doc.get('short_title', doc.get('id'))}** · {hospitals}")
