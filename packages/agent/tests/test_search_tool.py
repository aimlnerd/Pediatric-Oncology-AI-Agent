from typing import Any

from agent.search import build_filter, extract_section, set_searcher
from agent.state import RETRIEVED_CHUNKS, SEARCH_COUNT
from agent.tools.search_protocols import search_protocols

from .fakes import FakeSearcher, fake_tool_context, hit


def test_filter_lists_the_protocols_to_search() -> None:
    assert build_filter(["far-rms", "lch-iv"]) == 'protocol_id: ANY("far-rms", "lch-iv")'


def test_section_comes_from_the_leading_ancestor_headings() -> None:
    text = (
        "# 10. FRONTLINE (NEWLY DIAGNOSED) PATIENTS\n\n## 10.1 Frontline Assessments\n\n"
        "h. Staging as per local practice.\n\n# CRCTU-PRT-QCD-001, version 1.0\n\nPage 68 of 193"
    )
    expected = "10. FRONTLINE (NEWLY DIAGNOSED) PATIENTS > 10.1 Frontline Assessments"
    assert extract_section(text) == expected


def test_a_single_leading_heading_is_the_section() -> None:
    assert extract_section("## 12.1 Pulmonary metastases\nbody") == "12.1 Pulmonary metastases"


def test_headings_after_the_body_starts_are_page_noise_not_the_section() -> None:
    assert extract_section("body text first\n\n# CRCTU-PRT-QCD-001, version 1.0") is None
    assert extract_section("no headings here") is None


def test_search_is_limited_to_the_selection_held_in_state() -> None:
    searcher = FakeSearcher([hit("far-rms")])
    set_searcher(searcher)
    state: dict[str, Any] = {"center": "UZG", "selected_protocols": ["far-rms", "lch-iv"]}

    search_protocols("lung nodules", fake_tool_context(state))

    assert searcher.calls[0][1] == ("far-rms", "lch-iv")


def test_the_model_cannot_widen_scope_because_the_tool_only_takes_a_query() -> None:
    searcher = FakeSearcher([hit("far-rms"), hit("beacon2")])
    set_searcher(searcher)
    state: dict[str, Any] = {"center": "UZG", "selected_protocols": ["far-rms"]}

    result = search_protocols("beacon2 dinutuximab", fake_tool_context(state))

    assert searcher.calls[0][1] == ("far-rms",)
    assert [p["protocol"] for p in result["new_passages"]] == ["FaR-RMS"]


def test_selection_outside_the_center_scope_searches_nothing() -> None:
    searcher = FakeSearcher([hit("chip-aml22")])
    set_searcher(searcher)
    state: dict[str, Any] = {"center": "UZA", "selected_protocols": ["chip-aml22"]}

    result = search_protocols("hyperleukocytosis", fake_tool_context(state))

    assert "chip-aml22" in result["error"]
    assert searcher.calls == []


def test_passages_get_stable_ids_and_are_stored_in_state() -> None:
    set_searcher(FakeSearcher([hit(page=61), hit(page=70, text="Second passage")]))
    state: dict[str, Any] = {}

    result = search_protocols("q", fake_tool_context(state))

    assert [p["id"] for p in result["new_passages"]] == ["c1", "c2"]
    assert set(state[RETRIEVED_CHUNKS]) == {"c1", "c2"}
    assert state[RETRIEVED_CHUNKS]["c1"]["section"] == "12.1 Pulmonary metastases"


def test_repeated_passages_are_not_collected_twice_and_numbering_continues() -> None:
    searcher = FakeSearcher([hit(page=61)])
    set_searcher(searcher)
    state: dict[str, Any] = {}
    search_protocols("first", fake_tool_context(state))

    searcher.hits.append(hit(page=70, text="New passage"))
    result = search_protocols("second", fake_tool_context(state))

    assert [p["id"] for p in result["new_passages"]] == ["c2"]
    assert result["total_passages_collected"] == 2


def test_searches_are_capped_per_question() -> None:
    searcher = FakeSearcher([hit()])
    set_searcher(searcher)
    state: dict[str, Any] = {}

    results = [search_protocols(f"q{i}", fake_tool_context(state)) for i in range(5)]

    assert len(searcher.calls) == 3
    assert "limit" in results[3]["error"]
    assert state[SEARCH_COUNT] == 3
