from google.cloud import discoveryengine_v1 as de

from agent.search import hits_from_results


def result(**fields: object) -> de.SearchResponse.SearchResult:
    chunk = de.Chunk(
        content="## 12.1 Pulmonary metastases\nNo biopsy.",
        document_metadata=de.Chunk.DocumentMetadata(
            uri="gs://bucket/uc1/protocols/far-rms.pdf",
            title="FaR-RMS",
            struct_data=fields
            or {
                "protocol_id": "far-rms",
                "short_title": "FaR-RMS",
                "version": "2.0c",
                "filename": "far-rms.pdf",
            },
        ),
        page_span=de.Chunk.PageSpan(page_start=61, page_end=62),
    )
    return de.SearchResponse.SearchResult(chunk=chunk)


def test_chunk_metadata_and_page_span_become_a_hit() -> None:
    [hit] = hits_from_results([result()])

    assert hit.protocol_id == "far-rms"
    assert hit.short_title == "FaR-RMS"
    assert hit.version == "2.0c"
    assert hit.filename == "far-rms.pdf"
    assert (hit.page_start, hit.page_end) == (61, 62)
    assert hit.text.startswith("## 12.1")


def test_missing_version_and_pages_are_left_unset() -> None:
    chunk = de.Chunk(
        content="text",
        document_metadata=de.Chunk.DocumentMetadata(
            title="HLH 2004", struct_data={"protocol_id": "hlh-2004", "filename": "h.pdf"}
        ),
    )
    [hit] = hits_from_results([de.SearchResponse.SearchResult(chunk=chunk)])

    assert hit.version is None
    assert hit.page_start is None
    assert hit.short_title == "HLH 2004"
