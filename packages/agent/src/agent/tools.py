"""Custom Vertex AI Search RAG tools for the BSPHO Pediatric Oncology ADK Agent."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext
from google.cloud import discoveryengine_v1 as discoveryengine

from .config import (
    DEFAULT_DATA_STORE_ID,
    DEFAULT_DISCOVERY_LOCATION,
    DEFAULT_PROJECT_ID,
    get_documents_for_hospital,
    get_gcp_credentials,
    load_hospital_mapping,
)


def _resolve_allowed_short_titles(
    selected_hospital: str,
    ui_selected_protocols: list[str] | None,
    llm_requested_protocols: list[str] | None,
    include_subprotocols: bool,
) -> tuple[list[str], list[str]]:
    """
    Resolve the exact `short_title` values allowed for this search call,
    respecting Tier-1 (Hospital) and Tier-2 (UI Sidebar Checkboxes),
    and return `(allowed_short_titles, excluded_at_hospital_short_titles)`.
    """
    mapping = load_hospital_mapping()
    all_docs = mapping.get("documents", [])

    hospital_docs = get_documents_for_hospital(
        selected_hospital,
        include_subprotocols=include_subprotocols,
    )
    excluded_at_hospital = [
        d["short_title"]
        for d in all_docs
        if d.get("doc_type") == "protocol" and selected_hospital not in d.get("hospitals", [])
    ]

    # Tier 2: Filter by UI sidebar selection if provided
    if ui_selected_protocols is not None:
        ui_set = {p.lower().strip() for p in ui_selected_protocols}
        active_docs = [
            d
            for d in hospital_docs
            if d["short_title"].lower() in ui_set
            or d["filename"].lower() in ui_set
            or d["id"].lower() in ui_set
            or (include_subprotocols and d.get("doc_type") == "subprotocol")
        ]
    else:
        active_docs = hospital_docs

    # If the LLM requested specific protocols for a targeted search, fuzzy-match within active_docs
    if llm_requested_protocols:
        matched: list[str] = []
        for req in llm_requested_protocols:
            req_clean = req.lower().strip()
            for d in active_docs:
                if (
                    req_clean in d["short_title"].lower()
                    or req_clean in d["filename"].lower()
                    or req_clean in d["title"].lower()
                ):
                    if d["short_title"] not in matched:
                        matched.append(d["short_title"])
        if matched:
            # Also keep subprotocols if include_subprotocols is True
            if include_subprotocols:
                for d in active_docs:
                    if d.get("doc_type") == "subprotocol" and d["short_title"] not in matched:
                        matched.append(d["short_title"])
            return matched, excluded_at_hospital

    return [d["short_title"] for d in active_docs], excluded_at_hospital


def _build_filter_expression(
    allowed_short_titles: list[str],
    doc_type_filter: str | None = None,
) -> str:
    """Build a Discovery Engine filter expression."""
    parts: list[str] = []
    if allowed_short_titles:
        quoted = ", ".join(f'"{t.replace(chr(34), chr(92) + chr(34))}"' for t in allowed_short_titles)
        parts.append(f"short_title: ANY({quoted})")
    if doc_type_filter:
        parts.append(f'doc_type: ANY("{doc_type_filter}")')
    return " AND ".join(parts)


def _execute_chunk_search(
    query: str,
    filter_expr: str,
    page_size: int = 25,
    max_chunks_per_doc: int = 4,
    max_total_chunks: int = 12,
) -> list[dict[str, Any]]:
    """
    Run a chunk-level search against Vertex AI Search, extract page numbers + metadata,
    and diversify results across matching protocols so a single large PDF does not crowd out others.
    """
    creds, project_id = get_gcp_credentials(DEFAULT_PROJECT_ID)
    search_client = discoveryengine.SearchServiceClient(credentials=creds)
    serving_config = (
        f"projects/{project_id}/locations/{DEFAULT_DISCOVERY_LOCATION}"
        f"/collections/default_collection/dataStores/{DEFAULT_DATA_STORE_ID}"
        f"/servingConfigs/default_search"
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        filter=filter_expr,
        page_size=page_size,
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            search_result_mode=discoveryengine.SearchRequest.ContentSearchSpec.SearchResultMode.CHUNKS,
            chunk_spec=discoveryengine.SearchRequest.ContentSearchSpec.ChunkSpec(
                num_previous_chunks=1,
                num_next_chunks=1,
            ),
        ),
    )

    response = search_client.search(request)
    all_parsed_chunks: list[dict[str, Any]] = []

    for result in response.results:
        chunk = result.chunk
        struct_data = (
            dict(chunk.document_metadata.struct_data)
            if chunk.document_metadata and chunk.document_metadata.struct_data
            else {}
        )
        short_title = struct_data.get("short_title") or chunk.document_metadata.title or "Unknown Protocol"
        filename = struct_data.get("filename") or ""
        full_title = struct_data.get("title") or short_title
        doc_type = struct_data.get("doc_type") or "protocol"

        page_start = chunk.page_span.page_start if chunk.page_span else 0
        page_end = chunk.page_span.page_end if chunk.page_span else 0
        if page_start and page_end and page_start != page_end:
            page_display = f"pp. {page_start}–{page_end}"
        elif page_start:
            page_display = f"p. {page_start}"
        else:
            page_display = "page unknown"

        # Assemble previous + current + next chunk text for full table/section context
        context_parts: list[str] = []
        if chunk.chunk_metadata and chunk.chunk_metadata.previous_chunks:
            for prev_c in reversed(list(chunk.chunk_metadata.previous_chunks)):
                if prev_c.content:
                    context_parts.append(prev_c.content.strip())
        if chunk.content:
            context_parts.append(chunk.content.strip())
        if chunk.chunk_metadata and chunk.chunk_metadata.next_chunks:
            for next_c in chunk.chunk_metadata.next_chunks:
                if next_c.content:
                    context_parts.append(next_c.content.strip())

        combined_text = "\n\n".join(context_parts)
        citation_tag = f"[{short_title} ({filename}), {page_display}]"

        all_parsed_chunks.append(
            {
                "citation": citation_tag,
                "short_title": short_title,
                "filename": filename,
                "title": full_title,
                "doc_type": doc_type,
                "page_start": page_start,
                "page_end": page_end,
                "page_display": page_display,
                "relevance_score": round(float(chunk.relevance_score or 0.0), 4),
                "content": combined_text,
            }
        )

    # Diversify chunks across distinct protocols first, then backfill if slots remain
    diversified: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    doc_counts: dict[str, int] = {}

    for item in all_parsed_chunks:
        doc_key = item["short_title"]
        count = doc_counts.get(doc_key, 0)
        if count < max_chunks_per_doc:
            diversified.append(item)
            doc_counts[doc_key] = count + 1
        else:
            overflow.append(item)

    if len(diversified) < max_total_chunks:
        diversified.extend(overflow[: max_total_chunks - len(diversified)])

    return diversified[:max_total_chunks]


def search_protocols(
    query: str,
    protocol_names: list[str] | None = None,
    include_subprotocols: bool = False,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """
    Search the active Belgian pediatric hematology and oncology clinical trial protocols
    in Vertex AI Search.

    Args:
        query: Search query with medical keywords (e.g., "dinutuximab starting criteria platelets ANC",
               "FaR-RMS staging investigations lung nodules biopsy", "hyperleukocytosis WBC > 100").
        protocol_names: Optional list of specific protocol short titles to narrow the search
                        (e.g., ["BEACON2", "HRNBL2"] or ["CHIP-AML22", "ALLTogether1"]).
                        Leave empty/None to search across all protocols checked by the user.
        include_subprotocols: Set to True if you also want to include the 5 ALLTogether1 subprotocols
                              in this search.
    """
    selected_hospital = "UZG"
    ui_selected_protocols: list[str] | None = None
    state_include_sub = False

    if tool_context is not None:
        selected_hospital = tool_context.state.get("selected_hospital", "UZG")
        ui_selected_protocols = tool_context.state.get("selected_protocols")
        state_include_sub = bool(tool_context.state.get("include_subprotocols", False))

    use_subprotocols = include_subprotocols or state_include_sub
    allowed_short_titles, excluded_at_hospital = _resolve_allowed_short_titles(
        selected_hospital=selected_hospital,
        ui_selected_protocols=ui_selected_protocols,
        llm_requested_protocols=protocol_names,
        include_subprotocols=use_subprotocols,
    )

    if not allowed_short_titles:
        return {
            "status": "NO_PROTOCOLS_SELECTED",
            "selected_hospital": selected_hospital,
            "excluded_at_hospital": excluded_at_hospital,
            "message": "No protocols are currently selected in the sidebar filter (or the requested protocol is not active at this hospital).",
            "results": [],
        }

    filter_expr = _build_filter_expression(
        allowed_short_titles=allowed_short_titles,
        doc_type_filter=None if use_subprotocols else "protocol",
    )

    chunks = _execute_chunk_search(query=query, filter_expr=filter_expr, page_size=25)

    if tool_context is not None and chunks:
        existing = list(tool_context.state.get("last_citations", []))
        seen = {(c["filename"], c["page_display"]) for c in existing}
        for c in chunks:
            key = (c["filename"], c["page_display"])
            if key not in seen:
                existing.append(c)
                seen.add(key)
        tool_context.state["last_citations"] = existing

    return {
        "status": "OK" if chunks else "NO_MATCHING_CHUNKS",
        "selected_hospital": selected_hospital,
        "searched_protocols_count": len(allowed_short_titles),
        "excluded_at_this_hospital": excluded_at_hospital,
        "subprotocols_included": use_subprotocols,
        "results": chunks,
    }


def search_subprotocols(
    query: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """
    Perform a deep-dive search across the 5 ALLTogether1 supporting subprotocols/guidelines:
    - Clinical Guideline for Thrombosis (sinus venous thrombosis, LMWH enoxaparin, antithrombin)
    - Clinical Guideline for Pancreatitis (asparaginase-associated pancreatitis)
    - Guidelines for 6-MP / Methotrexate Maintenance Therapy
    - Glucarpidase (Voraxaze) Guidelines for Delayed MTX Clearance
    - Inotuzumab Ozogamicin (InO) Sub-protocol

    Use this tool when the user asks for a deep dive into subprotocols or when a complication
    (such as a stroke-like event, thrombosis, pancreatitis, or delayed MTX clearance) is queried.

    Args:
        query: Specific clinical search query for the subprotocols.
    """
    filter_expr = 'doc_type: ANY("subprotocol")'
    chunks = _execute_chunk_search(query=query, filter_expr=filter_expr, page_size=8)

    if tool_context is not None and chunks:
        existing = list(tool_context.state.get("last_citations", []))
        seen = {(c["filename"], c["page_display"]) for c in existing}
        for c in chunks:
            key = (c["filename"], c["page_display"])
            if key not in seen:
                existing.append(c)
                seen.add(key)
        tool_context.state["last_citations"] = existing

    return {
        "status": "OK" if chunks else "NO_MATCHING_CHUNKS",
        "doc_type": "subprotocol",
        "results": chunks,
    }
