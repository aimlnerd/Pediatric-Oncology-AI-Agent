"""Tests for the admin GCS/Vertex AI sync flow.

Every GCP touchpoint (bucket, Discovery Engine request) is injected as a fake,
so these tests run fully offline.
"""

from __future__ import annotations

import json

import pytest

from ui.gcs_sync import (
    SyncResult,
    build_document_record,
    build_metadata_jsonl,
    slugify_doc_id,
    sync_document,
    trigger_incremental_import,
    upload_mapping,
    upload_metadata_jsonl,
    upload_pdf,
    upsert_document,
    validate_upload,
)

SETTINGS = {
    "project_id": "test-project",
    "bucket_name": "test-bucket",
    "gcs_prefix": "uc1",
    "location": "global",
    "data_store_id": "test-ds",
}


class FakeBlob:
    def __init__(self, store: dict, path: str) -> None:
        self._store = store
        self._path = path

    def upload_from_string(self, data, content_type=None):
        self._store[self._path] = {"data": data, "content_type": content_type}


class FakeBucket:
    """Minimal stand-in for ``google.cloud.storage.Bucket``."""

    def __init__(self, name: str = "test-bucket", fail_on: str | None = None) -> None:
        self.name = name
        self.blobs: dict = {}
        self._fail_on = fail_on

    def blob(self, path: str) -> FakeBlob:
        if self._fail_on and self._fail_on in path:
            raise RuntimeError("boom")
        return FakeBlob(self.blobs, path)


def fake_request(method, url, payload, project_id):
    fake_request.calls.append(
        {"method": method, "url": url, "payload": payload, "project_id": project_id}
    )
    return {"name": "projects/test-project/operations/import-123"}


@pytest.fixture(autouse=True)
def _reset_request_calls():
    fake_request.calls = []


@pytest.fixture
def base_mapping():
    return {
        "hospitals": ["UZA", "UZG"],
        "bucket_name": "test-bucket",
        "gcs_prefix": "uc1",
        "documents": [
            {
                "id": "Existing-Protocol",
                "filename": "Existing Protocol.pdf",
                "short_title": "Existing",
                "title": "An existing protocol",
                "doc_type": "protocol",
                "subfolder": "protocols",
                "hospitals": ["UZA"],
                "gcs_uri": "gs://test-bucket/uc1/protocols/Existing Protocol.pdf",
            }
        ],
    }


# --- Pure helpers --------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("HRNBL2 Protocol v2.pdf", "HRNBL2-Protocol-v2"),
        ("D1_Protocol 2024-516115-24.pdf", "D1_Protocol-2024-516115-24"),
        ("--weird--.pdf", "weird"),
    ],
)
def test_slugify_doc_id_produces_valid_vertex_ids(filename, expected):
    assert slugify_doc_id(filename) == expected


def test_slugify_doc_id_truncates_long_names():
    assert len(slugify_doc_id("x" * 300 + ".pdf")) == 120


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"filename": ""}, "Select a PDF file"),
        ({"filename": "notes.txt"}, "Only PDF documents"),
        ({"short_title": "   "}, "Enter a protocol title"),
        ({"hospitals": []}, "at least one participating hospital"),
        ({"hospitals": ["UZA", "NOPE"]}, "Unknown hospital"),
        ({"doc_type": "guideline"}, "Document type must be"),
    ],
)
def test_validate_upload_rejects_bad_input(kwargs, expected_error):
    args = {
        "filename": "p.pdf",
        "short_title": "Title",
        "hospitals": ["UZA"],
        "doc_type": "protocol",
    }
    args.update(kwargs)
    errors = validate_upload(**args)
    assert any(expected_error in e for e in errors), errors


def test_validate_upload_accepts_a_valid_submission():
    assert validate_upload("p.pdf", "Title", ["UZA", "UZG"], "protocol") == []


def test_build_document_record_matches_the_setup_script_shape():
    record = build_document_record(
        filename="HRNBL2.pdf",
        short_title=" HRNBL2 ",
        hospitals=["UZG", "UZA"],
        doc_type="protocol",
        title="High Risk Neuroblastoma Study 2",
        bucket_name="test-bucket",
        gcs_prefix="uc1",
    )
    assert record == {
        "id": "HRNBL2",
        "filename": "HRNBL2.pdf",
        "short_title": "HRNBL2",
        "title": "High Risk Neuroblastoma Study 2",
        "doc_type": "protocol",
        "subfolder": "protocols",
        # Normalised to the canonical HOSPITALS order, not the input order.
        "hospitals": ["UZA", "UZG"],
        "gcs_uri": "gs://test-bucket/uc1/protocols/HRNBL2.pdf",
    }


def test_build_document_record_falls_back_to_short_title():
    record = build_document_record("p.pdf", "Short", ["UZA"])
    assert record["title"] == "Short"


def test_build_document_record_routes_subprotocols_to_their_own_folder():
    record = build_document_record(
        "thrombosis.pdf", "Thrombosis", ["UZA"], doc_type="subprotocol",
        bucket_name="test-bucket",
    )
    assert record["subfolder"] == "subprotocols"
    assert record["gcs_uri"] == "gs://test-bucket/uc1/subprotocols/thrombosis.pdf"


def test_upsert_document_appends_a_new_document(base_mapping):
    record = build_document_record("New.pdf", "New", ["UZG"])
    result = upsert_document(base_mapping["documents"], record)
    assert [d["id"] for d in result] == ["Existing-Protocol", "New"]


def test_upsert_document_replaces_an_existing_document(base_mapping):
    """Re-uploading a revised protocol must update it, not duplicate it."""
    record = build_document_record("Existing Protocol.pdf", "Existing v2", ["UZA", "UZG"])
    result = upsert_document(base_mapping["documents"], record)
    assert len(result) == 1
    assert result[0]["short_title"] == "Existing v2"
    assert result[0]["hospitals"] == ["UZA", "UZG"]


def test_upsert_document_does_not_mutate_the_input(base_mapping):
    original = [dict(d) for d in base_mapping["documents"]]
    upsert_document(base_mapping["documents"], build_document_record("N.pdf", "N", ["UZA"]))
    assert base_mapping["documents"] == original


def test_build_metadata_jsonl_emits_one_import_record_per_document(base_mapping):
    jsonl = build_metadata_jsonl(base_mapping["documents"])
    lines = [json.loads(line) for line in jsonl.strip().splitlines()]
    assert len(lines) == 1
    assert lines[0] == {
        "id": "Existing-Protocol",
        "structData": {
            "filename": "Existing Protocol.pdf",
            "short_title": "Existing",
            "title": "An existing protocol",
            "doc_type": "protocol",
            "hospitals": ["UZA"],
        },
        "content": {
            "mimeType": "application/pdf",
            "uri": "gs://test-bucket/uc1/protocols/Existing Protocol.pdf",
        },
    }


def test_build_metadata_jsonl_handles_an_empty_document_list():
    assert build_metadata_jsonl([]) == ""


# --- GCS primitives ------------------------------------------------------


def test_upload_pdf_writes_to_the_doc_type_subfolder():
    bucket = FakeBucket()
    uri = upload_pdf(bucket, b"%PDF-1.7", "HRNBL2.pdf", "protocol", "uc1")

    assert uri == "gs://test-bucket/uc1/protocols/HRNBL2.pdf"
    blob = bucket.blobs["uc1/protocols/HRNBL2.pdf"]
    assert blob["data"] == b"%PDF-1.7"
    assert blob["content_type"] == "application/pdf"


def test_upload_pdf_routes_subprotocols_separately():
    bucket = FakeBucket()
    uri = upload_pdf(bucket, b"x", "thrombosis.pdf", "subprotocol", "uc1")
    assert uri == "gs://test-bucket/uc1/subprotocols/thrombosis.pdf"


def test_upload_mapping_writes_json_to_the_metadata_prefix(base_mapping):
    bucket = FakeBucket()
    path = upload_mapping(bucket, base_mapping, "uc1")

    assert path == "uc1/metadata/hospital_mapping.json"
    assert json.loads(bucket.blobs[path]["data"]) == base_mapping


def test_upload_metadata_jsonl_returns_the_import_uri():
    bucket = FakeBucket()
    uri = upload_metadata_jsonl(bucket, '{"id": "a"}\n', "uc1")
    assert uri == "gs://test-bucket/uc1/metadata/metadata.jsonl"
    assert bucket.blobs["uc1/metadata/metadata.jsonl"]["data"] == '{"id": "a"}\n'


# --- Vertex AI Search re-index -------------------------------------------


def test_trigger_incremental_import_posts_an_incremental_import():
    operation = trigger_incremental_import(
        jsonl_gcs_uri="gs://test-bucket/uc1/metadata/metadata.jsonl",
        project_id="test-project",
        data_store_id="test-ds",
        location="global",
        request_fn=fake_request,
    )

    assert operation == "projects/test-project/operations/import-123"
    call = fake_request.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == (
        "https://discoveryengine.googleapis.com/v1/projects/test-project/locations/global"
        "/collections/default_collection/dataStores/test-ds/branches/0/documents:import"
    )
    assert call["payload"]["reconciliationMode"] == "INCREMENTAL"
    assert call["payload"]["gcsSource"]["inputUris"] == [
        "gs://test-bucket/uc1/metadata/metadata.jsonl"
    ]


def test_trigger_incremental_import_uses_the_regional_host_outside_global():
    trigger_incremental_import(
        "gs://b/m.jsonl", "p", "ds", location="eu", request_fn=fake_request
    )
    assert fake_request.calls[0]["url"].startswith("https://eu-discoveryengine.googleapis.com/")


# --- Orchestration -------------------------------------------------------


def _sync(bucket, mapping, **overrides):
    kwargs = {
        "file_bytes": b"%PDF-1.7",
        "filename": "HRNBL2.pdf",
        "short_title": "HRNBL2",
        "hospitals": ["UZA"],
        "doc_type": "protocol",
        "bucket": bucket,
        "mapping": mapping,
        "request_fn": fake_request,
        "settings": SETTINGS,
        "write_local": False,
    }
    kwargs.update(overrides)
    return sync_document(**kwargs)


def test_sync_document_runs_all_three_stages(base_mapping):
    bucket = FakeBucket()
    result = _sync(bucket, base_mapping)

    assert isinstance(result, SyncResult)
    assert result.ok is True
    assert result.stage == "done"
    assert result.gcs_uri == "gs://test-bucket/uc1/protocols/HRNBL2.pdf"
    assert result.operation_name == "projects/test-project/operations/import-123"
    assert result.document_count == 2

    assert "uc1/protocols/HRNBL2.pdf" in bucket.blobs
    assert "uc1/metadata/hospital_mapping.json" in bucket.blobs
    assert "uc1/metadata/metadata.jsonl" in bucket.blobs
    assert len(fake_request.calls) == 1


def test_sync_document_adds_the_document_to_the_uploaded_mapping(base_mapping):
    bucket = FakeBucket()
    _sync(bucket, base_mapping)

    uploaded = json.loads(bucket.blobs["uc1/metadata/hospital_mapping.json"]["data"])
    ids = [d["id"] for d in uploaded["documents"]]
    assert ids == ["Existing-Protocol", "HRNBL2"]

    jsonl = bucket.blobs["uc1/metadata/metadata.jsonl"]["data"]
    assert len(jsonl.strip().splitlines()) == 2


def test_sync_document_does_not_mutate_the_caller_mapping(base_mapping):
    _sync(FakeBucket(), base_mapping)
    assert len(base_mapping["documents"]) == 1


def test_sync_document_stops_at_validation_without_touching_gcs(base_mapping):
    bucket = FakeBucket()
    result = _sync(bucket, base_mapping, filename="", short_title="")

    assert result.ok is False
    assert result.stage == "validate"
    assert len(result.errors) == 2
    assert bucket.blobs == {}
    assert fake_request.calls == []


def test_sync_document_reports_the_upload_stage_on_pdf_failure(base_mapping):
    result = _sync(FakeBucket(fail_on="protocols/"), base_mapping)

    assert result.ok is False
    assert result.stage == "upload"
    assert "PDF upload failed" in result.message
    assert fake_request.calls == []


def test_sync_document_reports_the_mapping_stage_and_keeps_the_upload_uri(base_mapping):
    result = _sync(FakeBucket(fail_on="metadata/"), base_mapping)

    assert result.ok is False
    assert result.stage == "mapping"
    # The operator needs to know the PDF landed even though the sync failed.
    assert result.gcs_uri == "gs://test-bucket/uc1/protocols/HRNBL2.pdf"
    assert fake_request.calls == []


def test_sync_document_reports_the_reindex_stage_on_import_failure(base_mapping):
    def failing_request(*args, **kwargs):
        raise RuntimeError("HTTP 403")

    result = _sync(FakeBucket(), base_mapping, request_fn=failing_request)

    assert result.ok is False
    assert result.stage == "reindex"
    assert "re-indexing failed" in result.message
    # Upload and mapping did succeed, so report what was achieved.
    assert result.gcs_uri == "gs://test-bucket/uc1/protocols/HRNBL2.pdf"
    assert result.document_count == 2


def test_sync_document_replaces_an_existing_document_on_reupload(base_mapping):
    bucket = FakeBucket()
    result = _sync(
        bucket,
        base_mapping,
        filename="Existing Protocol.pdf",
        short_title="Existing v2",
        hospitals=["UZA", "UZG"],
    )

    assert result.ok is True
    assert result.document_count == 1
    uploaded = json.loads(bucket.blobs["uc1/metadata/hospital_mapping.json"]["data"])
    assert uploaded["documents"][0]["short_title"] == "Existing v2"
