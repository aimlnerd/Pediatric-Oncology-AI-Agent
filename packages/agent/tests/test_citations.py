from agent.citations import Chunk, chunks_from_state, chunks_to_state, resolve_citations


def chunk(chunk_id: str, page: int, section: str | None = None) -> Chunk:
    return Chunk(
        id=chunk_id,
        protocol_id="far-rms",
        short_title="FaR-RMS",
        version="2.0c",
        filename="far-rms.pdf",
        page_start=page,
        page_end=page,
        section=section,
        text="...",
    )


CHUNKS = {"c1": chunk("c1", 61, "12.1 Pulmonary"), "c2": chunk("c2", 70)}


def test_marker_becomes_protocol_version_page_and_section() -> None:
    result = resolve_citations("No biopsy is indicated [c1].", CHUNKS)
    assert result.text == "No biopsy is indicated [FaR-RMS v2.0c, p. 61, § 12.1 Pulmonary]."
    assert [c.chunk_id for c in result.citations] == ["c1"]


def test_markers_for_ids_that_were_not_retrieved_are_removed_and_reported() -> None:
    result = resolve_citations("A fact [c9]. Another [c2].", CHUNKS)
    assert "c9" not in result.text
    assert result.unknown_ids == ["c9"]
    assert [c.chunk_id for c in result.citations] == ["c2"]


def test_combined_markers_are_resolved_individually() -> None:
    result = resolve_citations("Both agree [c1, c2].", CHUNKS)
    assert (
        result.text == "Both agree [FaR-RMS v2.0c, p. 61, § 12.1 Pulmonary; FaR-RMS v2.0c, p. 70]."
    )


def test_each_chunk_is_listed_once_in_citations() -> None:
    result = resolve_citations("A [c1]. B [c1]. C [c2].", CHUNKS)
    assert [c.chunk_id for c in result.citations] == ["c1", "c2"]


def test_text_without_markers_has_no_citations() -> None:
    result = resolve_citations("Unsupported claim.", CHUNKS)
    assert result.citations == []


def test_page_range_label() -> None:
    ranged = chunk("c3", 10).model_copy(update={"page_end": 12})
    assert ranged.pages == "pp. 10-12"


def test_chunks_survive_a_round_trip_through_session_state() -> None:
    assert chunks_from_state(chunks_to_state(CHUNKS)) == CHUNKS
    assert chunks_from_state(None) == {}
