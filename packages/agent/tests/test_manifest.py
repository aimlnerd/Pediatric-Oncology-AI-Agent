from agent.manifest import CENTERS, load_manifest, protocols_for_center


def test_manifest_has_the_27_master_protocols() -> None:
    manifest = load_manifest()
    assert len(manifest) == 27
    assert all(p.doc_type == "protocol" for p in manifest.values())


def test_uzg_runs_everything_and_uza_runs_21() -> None:
    assert len(protocols_for_center("UZG")) == 27
    assert len(protocols_for_center("UZA")) == 21


def test_uza_does_not_run_the_six_protocols_the_mapping_table_excludes() -> None:
    uza = {p.protocol_id for p in protocols_for_center("UZA")}
    excluded = set(load_manifest()) - uza
    assert excluded == {
        "beacon2",
        "chip-aml22",
        "ewog-saa-2010",
        "far-rms",
        "hrnbl2",
        "ml-ds-2018",
    }


def test_every_protocol_is_run_by_at_least_one_known_center() -> None:
    for protocol in load_manifest().values():
        assert protocol.centers
        assert set(protocol.centers) <= set(CENTERS)


def test_label_includes_version_when_known() -> None:
    manifest = load_manifest()
    assert manifest["far-rms"].label == "FaR-RMS v2.0c"
    assert manifest["beacon2"].label == manifest["beacon2"].short_title
