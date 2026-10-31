"""Chunk search over the Vertex AI Search data store."""

import re
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de
from pydantic import BaseModel

from agent.config import Settings, get_settings


class RawHit(BaseModel):
    """One chunk as returned by the index, before it gets a per-turn id."""

    protocol_id: str
    short_title: str
    version: str | None = None
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    text: str


class ChunkSearcher(Protocol):
    def search(self, query: str, protocol_ids: Sequence[str], page_size: int) -> list[RawHit]: ...


def build_filter(protocol_ids: Sequence[str]) -> str:
    """Metadata filter restricting a search to the given protocols."""
    quoted = ", ".join('"' + pid.replace('"', "") + '"' for pid in protocol_ids)
    return f"protocol_id: ANY({quoted})"


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def extract_section(text: str) -> str | None:
    """Section path from the heading block that opens the chunk (its ancestor headings).

    Headings further down are page headers and footers the parser mistook for headings,
    for example "# CRCTU-PRT-QCD-001, version 1.0", so they are ignored.
    """
    headings: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _HEADING.match(line)
        if match is None:
            break
        headings.append(match.group(1))
    return " > ".join(headings[-2:])[:160] if headings else None


def hits_from_results(results: Iterable[de.SearchResponse.SearchResult]) -> list[RawHit]:
    """Map Vertex AI Search chunk results to hits carrying protocol metadata and page span."""
    hits: list[RawHit] = []
    for result in results:
        chunk = result.chunk
        meta: dict[str, Any] = dict(chunk.document_metadata.struct_data)
        span = chunk.page_span
        hits.append(
            RawHit(
                protocol_id=str(meta.get("protocol_id", "")),
                short_title=str(meta.get("short_title", chunk.document_metadata.title)),
                version=str(meta["version"]) if meta.get("version") else None,
                filename=str(meta.get("filename", "")),
                page_start=span.page_start or None,
                page_end=span.page_end or None,
                text=chunk.content,
            )
        )
    return hits


class DiscoveryEngineSearcher:
    """Calls Vertex AI Search in CHUNKS mode. Requires Application Default Credentials."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
        location = self._settings.search_location
        endpoint = (
            "discoveryengine.googleapis.com"
            if location == "global"
            else f"{location}-discoveryengine.googleapis.com"
        )
        self._client = de.SearchServiceClient(client_options=ClientOptions(api_endpoint=endpoint))
        self._serving_config = (
            f"projects/{self._settings.project}/locations/{location}/collections/default_collection"
            f"/dataStores/{self._settings.data_store_id}/servingConfigs/default_search"
        )

    def search(self, query: str, protocol_ids: Sequence[str], page_size: int) -> list[RawHit]:
        request = de.SearchRequest(
            serving_config=self._serving_config,
            query=query,
            page_size=page_size,
            filter=build_filter(protocol_ids),
            content_search_spec=de.SearchRequest.ContentSearchSpec(
                search_result_mode=de.SearchRequest.ContentSearchSpec.SearchResultMode.CHUNKS,
            ),
        )
        return hits_from_results(self._client.search(request).results)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


_searcher: ChunkSearcher | None = None


def get_searcher() -> ChunkSearcher:
    global _searcher
    if _searcher is None:
        _searcher = DiscoveryEngineSearcher()
    return _searcher


def set_searcher(searcher: ChunkSearcher | None) -> None:
    """Swap the searcher (tests use a fake); None restores the real one on next use."""
    global _searcher
    _searcher = searcher
