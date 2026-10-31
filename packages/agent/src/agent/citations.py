"""Retrieved chunks and citation resolution.

The model cites chunk ids like [c3]; code replaces them with Protocol, Version,
page and section taken from the retrieved chunk, so the model never writes a
page number itself.
"""

import re
from typing import Any, cast

from pydantic import BaseModel

_MARKER = re.compile(r"\[\s*(c\d+(?:\s*[,;]\s*c\d+)*)\s*\]")
_ID = re.compile(r"c\d+")


class Chunk(BaseModel):
    id: str
    protocol_id: str
    short_title: str
    version: str | None = None
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    text: str

    @property
    def pages(self) -> str:
        if self.page_start is None:
            return "p. ?"
        if self.page_end is None or self.page_end == self.page_start:
            return f"p. {self.page_start}"
        return f"pp. {self.page_start}-{self.page_end}"

    @property
    def citation_label(self) -> str:
        parts = [self.short_title + (f" v{self.version}" if self.version else ""), self.pages]
        if self.section:
            parts.append(f"§ {self.section}")
        return ", ".join(parts)


class Citation(BaseModel):
    chunk_id: str
    protocol_id: str
    short_title: str
    version: str | None
    filename: str
    page_start: int | None
    page_end: int | None
    section: str | None
    label: str


class ResolvedAnswer(BaseModel):
    text: str
    citations: list[Citation]
    unknown_ids: list[str]


def _citation(chunk: Chunk) -> Citation:
    return Citation(
        chunk_id=chunk.id,
        protocol_id=chunk.protocol_id,
        short_title=chunk.short_title,
        version=chunk.version,
        filename=chunk.filename,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section=chunk.section,
        label=chunk.citation_label,
    )


def resolve_citations(text: str, chunks: dict[str, Chunk]) -> ResolvedAnswer:
    """Replace [cN] markers with readable labels; drop markers that match no retrieved chunk."""
    used: dict[str, Citation] = {}
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        labels: list[str] = []
        for chunk_id in _ID.findall(match.group(1)):
            chunk = chunks.get(chunk_id)
            if chunk is None:
                if chunk_id not in unknown:
                    unknown.append(chunk_id)
                continue
            used.setdefault(chunk_id, _citation(chunk))
            labels.append(chunk.citation_label)
        return f"[{'; '.join(labels)}]" if labels else ""

    resolved = _MARKER.sub(replace, text)
    resolved = re.sub(r"[ \t]+([.,;:])", r"\1", resolved)
    return ResolvedAnswer(text=resolved.strip(), citations=list(used.values()), unknown_ids=unknown)


def chunks_from_state(raw: object) -> dict[str, Chunk]:
    """Chunks stored in session state (as plain dicts), keyed by chunk id."""
    if not isinstance(raw, dict):
        return {}
    items = cast(dict[object, object], raw)
    return {str(chunk_id): Chunk.model_validate(value) for chunk_id, value in items.items()}


def chunks_to_state(chunks: dict[str, Chunk]) -> dict[str, dict[str, Any]]:
    return {chunk_id: chunk.model_dump() for chunk_id, chunk in chunks.items()}
