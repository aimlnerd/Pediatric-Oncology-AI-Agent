"""Left panel: Tier 1 hospital filter and Tier 2 protocol checklist.

Design doc section 6. Tier 1 (hospital) determines which protocols are
offered; Tier 2 (checkboxes) scopes the query sent to the agent.
"""

from __future__ import annotations

import streamlit as st

from ui.data import HOSPITALS, load_hospital_mapping
from ui.state import (
    reset_chat,
    set_hospital,
    set_include_subprotocols,
    toggle_protocol,
)

PROTOCOL_KEY_PREFIX = "proto_"


def _protocol_widget_key(protocol_id: str) -> str:
    return f"{PROTOCOL_KEY_PREFIX}{protocol_id}"


def _clear_protocol_widget_state() -> None:
    """Drop stale ``proto_*`` checkbox widget values.

    Streamlit gives a keyed widget's stored value precedence over the ``value``
    argument, so without this a protocol unchecked under one hospital would
    stay unchecked after switching to another hospital that also offers it.
    """
    for key in [k for k in st.session_state if k.startswith(PROTOCOL_KEY_PREFIX)]:
        del st.session_state[key]


def _set_all_protocols(checked: bool) -> None:
    """on_click handler for the Select all / Clear all buttons."""
    for protocol in st.session_state["available_protocols"]:
        st.session_state[_protocol_widget_key(protocol["id"])] = checked


def render_sidebar(mapping: dict | None = None) -> None:
    """Render the Tier 1 selector, subprotocol toggle and Tier 2 checklist."""
    mapping = mapping if mapping is not None else load_hospital_mapping()

    st.subheader("Context")

    hospital = st.selectbox(
        "Hospital / Center",
        options=HOSPITALS,
        index=None,
        placeholder="Select your hospital...",
        key="hospital_select",
        help="Tier 1 filter: only protocols this center participates in are listed below.",
    )
    if hospital != st.session_state["hospital"]:
        _clear_protocol_widget_state()
        set_hospital(st.session_state, hospital, mapping)
        # Protocol scope changed, so previous grounded answers no longer apply.
        reset_chat(st.session_state)

    if not st.session_state["hospital"]:
        st.info("Select a hospital to see its applicable protocols.")
        return

    include_subprotocols = st.toggle(
        "Include Subprotocols (Deep Search)",
        value=st.session_state["include_subprotocols"],
        key="include_subprotocols_toggle",
        help="Also search the supporting guideline documents (e.g. thrombosis, pancreatitis).",
    )
    if include_subprotocols != st.session_state["include_subprotocols"]:
        set_include_subprotocols(st.session_state, include_subprotocols, mapping)

    available = st.session_state["available_protocols"]
    selected = st.session_state["selected_protocol_ids"]
    st.caption(f"Protocols ({len(selected)}/{len(available)} selected)")

    select_col, clear_col = st.columns(2)
    select_col.button(
        "Select all",
        use_container_width=True,
        on_click=_set_all_protocols,
        args=(True,),
        key="select_all_protocols",
    )
    clear_col.button(
        "Clear all",
        use_container_width=True,
        on_click=_set_all_protocols,
        args=(False,),
        key="clear_all_protocols",
    )

    for protocol in available:
        checked = protocol["id"] in selected
        label = protocol["title"]
        if protocol.get("doc_type") == "subprotocol":
            label = f"↳ {label}"
        new_value = st.checkbox(label, value=checked, key=_protocol_widget_key(protocol["id"]))
        if new_value != checked:
            toggle_protocol(st.session_state, protocol["id"], new_value)

    if not st.session_state["selected_protocol_ids"]:
        st.warning("Select at least one protocol to start a conversation.")
