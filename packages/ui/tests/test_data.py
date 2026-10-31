"""Tests for the hospital/protocol mapping loader."""

from ui.data import HOSPITALS, get_protocols_for_hospital, load_hospital_mapping


def test_hospitals_constant_matches_readme():
    assert HOSPITALS == ("UZA", "UZG", "UZL", "HUDERF", "UCL", "CHU")


def test_load_hospital_mapping_shape():
    mapping = load_hospital_mapping()
    assert set(mapping.keys()) >= {"hospitals", "protocols", "subprotocols"}
    assert mapping["hospitals"] == list(HOSPITALS)
    assert len(mapping["protocols"]) > 0
    assert all(p["doc_type"] == "protocol" for p in mapping["protocols"])
    assert all(sp["doc_type"] == "subprotocol" for sp in mapping["subprotocols"])


def test_get_protocols_for_hospital_filters_by_hospital():
    mapping = load_hospital_mapping()
    protocols = get_protocols_for_hospital(mapping, "UCL")
    assert all("UCL" in p["hospitals"] for p in protocols)
    # HRNBL2 is tagged for UCL in the mock data.
    assert any(p["id"] == "HRNBL2" for p in protocols)
    # CHIP-AML22 is not tagged for UCL.
    assert all(p["id"] != "CHIP-AML22" for p in protocols)


def test_get_protocols_for_hospital_excludes_subprotocols_by_default():
    mapping = load_hospital_mapping()
    protocols = get_protocols_for_hospital(mapping, "UZA", include_subprotocols=False)
    assert all(p["doc_type"] == "protocol" for p in protocols)


def test_get_protocols_for_hospital_includes_subprotocols_when_requested():
    mapping = load_hospital_mapping()
    protocols = get_protocols_for_hospital(mapping, "UZA", include_subprotocols=True)
    assert any(p["doc_type"] == "subprotocol" for p in protocols)


def test_get_protocols_for_hospital_returns_empty_without_hospital():
    mapping = load_hospital_mapping()
    assert get_protocols_for_hospital(mapping, None) == []
    assert get_protocols_for_hospital(mapping, "") == []
