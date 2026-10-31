"""Hospital/protocol mapping loader.

Prefers the **real** mapping owned by the ``agent`` package
(``data/processed/hospital_mapping.json``, synced from GCS), normalising it
to the shape the UI renders. Falls back to the bundled mock copy when the
agent package or its mapping is unavailable, so the UI (and its test suite)
still runs fully offline.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

MOCK_MAPPING_PATH = Path(__file__).parent / "mock_data" / "hospital_mapping.json"

#: Belgian centers participating in the hackathon pilot (README "Expected Inputs").
HOSPITALS: tuple[str, ...] = ("UZA", "UZG", "UZL", "HUDERF", "UCL", "CHU")

#: ``mock`` forces the bundled sample mapping; ``auto`` (default) prefers the real one.
MAPPING_SOURCE_ENV = "UI_MAPPING_SOURCE"


def _normalise_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert an ``agent`` mapping document into the UI's protocol shape.

    The agent accepts ``short_title``, ``filename`` or ``id`` for its Tier 2
    filter, so the UI keeps using ``id`` as the checkbox identity and simply
    carries ``short_title`` along for display and for the agent call.
    """
    short_title = doc.get("short_title") or doc.get("title") or doc.get("id", "")
    return {
        "id": doc.get("id") or short_title,
        "short_title": short_title,
        "title": short_title,
        "full_title": doc.get("title") or short_title,
        "doc_type": doc.get("doc_type", "protocol"),
        "hospitals": list(doc.get("hospitals", [])),
        "filename": doc.get("filename", ""),
    }


def _load_real_mapping() -> dict | None:
    """Load and normalise the agent package's mapping, or ``None`` if unavailable."""
    try:
        from agent.config import load_hospital_mapping as load_agent_mapping
    except Exception:
        return None

    try:
        raw = load_agent_mapping()
    except Exception:
        return None

    documents = raw.get("documents") or []
    if not documents:
        return None

    normalised = [_normalise_document(d) for d in documents]
    return {
        "source": "agent",
        "hospitals": raw.get("hospitals") or list(HOSPITALS),
        "protocols": [d for d in normalised if d["doc_type"] == "protocol"],
        "subprotocols": [d for d in normalised if d["doc_type"] == "subprotocol"],
    }


def _load_mock_mapping(path: Path = MOCK_MAPPING_PATH) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        mapping = json.load(fh)
    mapping.setdefault("source", "mock")
    for doc in mapping.get("protocols", []) + mapping.get("subprotocols", []):
        doc.setdefault("short_title", doc.get("id", ""))
    return mapping


@lru_cache(maxsize=1)
def load_hospital_mapping() -> dict:
    """Load the hospital-to-protocol mapping table.

    Cached because the mapping only changes when the Admin "Sync & Re-index"
    flow runs (a later build step), at which point the cache should be
    invalidated with ``load_hospital_mapping.cache_clear()``.
    """
    if os.environ.get(MAPPING_SOURCE_ENV, "auto").lower() != "mock":
        real = _load_real_mapping()
        if real is not None:
            return real
    return _load_mock_mapping()


def get_protocols_for_hospital(
    mapping: dict, hospital: str | None, include_subprotocols: bool = False
) -> list[dict]:
    """Return the protocols (and optionally subprotocols) scoped to ``hospital``.

    Mirrors the Tier 1 -> Tier 2 filtering described in the design doc: the
    hospital selection (Tier 1) determines which protocols are offered for
    the Tier 2 checklist.
    """
    if not hospital:
        return []

    protocols = [p for p in mapping.get("protocols", []) if hospital in p.get("hospitals", [])]
    if include_subprotocols:
        protocols += [
            sp for sp in mapping.get("subprotocols", []) if hospital in sp.get("hospitals", [])
        ]
    return protocols
