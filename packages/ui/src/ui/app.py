"""Streamlit entry point for the BSPHO Virtual Assistant.

Thin composition layer (design doc section 5): page config, session state
init, and the Clinician/Admin view switch. The Tier 1/Tier 2 sidebar lives
in :mod:`ui.sidebar` and the conversational panel in :mod:`ui.chat`.
"""

from __future__ import annotations

import streamlit as st

from ui.admin import render_admin
from ui.agent_client import get_agent_client
from ui.chat import render_chat
from ui.data import load_hospital_mapping
from ui.sidebar import render_sidebar
from ui.state import init_state


@st.cache_resource
def _agent_client():
    """One agent client per server process (holds ADK session state)."""
    return get_agent_client()


def _render_startup_error(exc: Exception) -> None:
    """Explain a hard startup failure instead of showing a raw traceback."""
    st.error(
        "**The assistant could not start.** The agent backend is unavailable, "
        "so no questions can be answered right now."
    )
    st.caption(f"{type(exc).__name__}: {exc}")
    st.info(
        "Check that `gcloud auth application-default login` has been run and "
        "that `data/processed/hospital_mapping.json` exists. To run without "
        "GCP access, start the app with `UI_AGENT_BACKEND=mock "
        "UI_MAPPING_SOURCE=mock`."
    )


def _render_backend_badge(client, mapping: dict) -> None:
    """Show which agent backend and mapping the app is actually running on."""
    is_live = type(client).__name__ == "AdkAgentClient"
    backend = "Vertex AI Search + ADK agent" if is_live else "Mock agent (offline)"
    protocols = len(mapping.get("protocols", [])) + len(mapping.get("subprotocols", []))
    source = "live mapping" if mapping.get("source") == "agent" else "sample mapping"
    st.sidebar.caption(f"{'🟢' if is_live else '🟡'} {backend}\n\n{protocols} documents ({source})")


def render_clinician_view(mapping: dict, client) -> None:
    sidebar_col, chat_col = st.columns([1, 3], gap="large")
    with sidebar_col:
        render_sidebar(mapping)
    with chat_col:
        render_chat(client)


def render_admin_view(mapping: dict) -> None:
    render_admin(mapping)


def main() -> None:
    st.set_page_config(
        page_title="BSPHO Virtual Assistant", layout="wide", page_icon="🩺"
    )
    init_state(st.session_state)
    mapping = load_hospital_mapping()

    st.title("Virtual Assistant — Pediatric Hematology & Oncology")

    view = st.sidebar.radio("View", ["Clinician", "Admin"], key="view_select")
    st.session_state["is_admin"] = view == "Admin"

    try:
        client = _agent_client()
    except Exception as exc:
        _render_startup_error(exc)
        return

    _render_backend_badge(client, mapping)

    if view == "Clinician":
        render_clinician_view(mapping, client)
    else:
        render_admin_view(mapping)


main()
