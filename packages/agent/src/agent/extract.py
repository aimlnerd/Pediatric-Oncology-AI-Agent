"""Turn raw search hits into per-turn chunks with stable ids."""

from collections.abc import Iterable

from agent.citations import Chunk
from agent.search import RawHit, extract_section


def to_chunks(hits: Iterable[RawHit], existing: dict[str, Chunk]) -> list[Chunk]:
    """Chunks for hits not already collected this turn, numbered after the existing ones."""
    seen = {(c.protocol_id, c.page_start, c.text) for c in existing.values()}
    next_number = len(existing) + 1
    created: list[Chunk] = []
    for hit in hits:
        key = (hit.protocol_id, hit.page_start, hit.text)
        if key in seen:
            continue
        seen.add(key)
        created.append(
            Chunk(
                id=f"c{next_number}",
                protocol_id=hit.protocol_id,
                short_title=hit.short_title,
                version=hit.version,
                filename=hit.filename,
                page_start=hit.page_start,
                page_end=hit.page_end,
                section=extract_section(hit.text),
                text=hit.text,
            )
        )
        next_number += 1
    return created
