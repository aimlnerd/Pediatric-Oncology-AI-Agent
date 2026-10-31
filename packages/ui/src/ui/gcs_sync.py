"""GCS upload + mapping update + Vertex AI Search re-index for the Admin flow.

Implements the three stages of design doc section 9:

1. ``upload_pdf`` writes the protocol PDF to ``gs://<bucket>/<prefix>/protocols/``
   (or ``/subprotocols/``).
2. ``upload_mapping`` / ``upload_metadata_jsonl`` refresh ``hospital_mapping.json``
   and ``metadata.jsonl`` under ``gs://<bucket>/<prefix>/metadata/``, keeping the
   exact record shape produced by ``scripts/setup_gcp.py`` so the agent and the
   one-shot setup script stay interchangeable.
3. ``trigger_incremental_import`` calls the Discovery Engine
   ``documents:import`` endpoint with ``reconciliationMode=INCREMENTAL``.

Every function that touches GCP accepts its client (or request callable) as a
parameter, so the whole module is unit-testable with fakes and the test suite
never needs live credentials.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ui.data import HOSPITALS

#: GCS layout: ``doc_type`` decides the destination subfolder.
SUBFOLDERS: dict[str, str] = {"protocol": "protocols", "subprotocol": "subprotocols"}

DOC_TYPES: tuple[str, ...] = ("protocol", "subprotocol")

#: Written back so the agent (which reads this file) sees new documents immediately.
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
LOCAL_MAPPING_PATH = WORKSPACE_ROOT / "data" / "processed" / "hospital_mapping.json"


def _settings() -> dict[str, str]:
    """Resolve GCP settings, preferring the agent package's single source of truth."""
    try:
        from agent import config as agent_config

        return {
            "project_id": agent_config.DEFAULT_PROJECT_ID,
            "bucket_name": agent_config.DEFAULT_BUCKET_NAME,
            "gcs_prefix": agent_config.DEFAULT_GCS_PREFIX,
            "location": agent_config.DEFAULT_DISCOVERY_LOCATION,
            "data_store_id": agent_config.DEFAULT_DATA_STORE_ID,
        }
    except Exception:
        import os

        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        return {
            "project_id": project_id,
            "bucket_name": os.environ.get("GCS_BUCKET_NAME", f"use-cases-{project_id}"),
            "gcs_prefix": os.environ.get("GCS_PREFIX", "uc1"),
            "location": os.environ.get("DISCOVERY_ENGINE_LOCATION", "global"),
            "data_store_id": os.environ.get("VERTEX_SEARCH_DATA_STORE_ID", ""),
        }


# --- Pure helpers (no GCP) -----------------------------------------------


def slugify_doc_id(filename: str) -> str:
    """Create a valid Vertex AI Search document ID (``[a-zA-Z0-9-_]``, <=128 chars).

    Mirrors ``scripts/setup_gcp.py`` so a document re-uploaded through the UI
    gets the same id as the batch import and therefore *replaces* it rather
    than creating a duplicate.
    """
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")
    return slug[:120]


def validate_upload(
    filename: str, short_title: str, hospitals: list[str], doc_type: str
) -> list[str]:
    """Return human-readable validation errors for the admin form, if any."""
    errors: list[str] = []
    if not filename:
        errors.append("Select a PDF file to upload.")
    elif not filename.lower().endswith(".pdf"):
        errors.append("Only PDF documents can be indexed.")
    if not short_title.strip():
        errors.append("Enter a protocol title.")
    if not hospitals:
        errors.append("Select at least one participating hospital.")
    unknown = [h for h in hospitals if h not in HOSPITALS]
    if unknown:
        errors.append(f"Unknown hospital(s): {', '.join(unknown)}.")
    if doc_type not in DOC_TYPES:
        errors.append(f"Document type must be one of {', '.join(DOC_TYPES)}.")
    return errors


def build_document_record(
    filename: str,
    short_title: str,
    hospitals: list[str],
    doc_type: str = "protocol",
    title: str = "",
    bucket_name: str = "",
    gcs_prefix: str = "uc1",
) -> dict[str, Any]:
    """Build a mapping record in the exact shape ``setup_gcp.py`` produces."""
    subfolder = SUBFOLDERS.get(doc_type, "protocols")
    return {
        "id": slugify_doc_id(filename),
        "filename": filename,
        "short_title": short_title.strip(),
        "title": (title or short_title).strip(),
        "doc_type": doc_type,
        "subfolder": subfolder,
        "hospitals": [h for h in HOSPITALS if h in hospitals],
        "gcs_uri": f"gs://{bucket_name}/{gcs_prefix}/{subfolder}/{filename}",
    }


def upsert_document(documents: list[dict], record: dict) -> list[dict]:
    """Return ``documents`` with ``record`` replacing any same-id entry, else appended.

    Re-uploading a revised protocol must update the existing entry (same
    slugified id) instead of creating a duplicate checkbox in the sidebar.
    """
    updated = [dict(d) for d in documents if d.get("id") != record["id"]]
    updated.append(dict(record))
    return updated


def build_metadata_jsonl(documents: list[dict]) -> str:
    """Render the Vertex AI Search import manifest (one JSON object per line)."""
    lines = []
    for doc in documents:
        lines.append(
            json.dumps(
                {
                    "id": doc["id"],
                    "structData": {
                        "filename": doc["filename"],
                        "short_title": doc["short_title"],
                        "title": doc["title"],
                        "doc_type": doc["doc_type"],
                        "hospitals": doc["hospitals"],
                    },
                    "content": {"mimeType": "application/pdf", "uri": doc["gcs_uri"]},
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) + "\n" if lines else ""


# --- GCP-facing primitives ------------------------------------------------


class _Bucket(Protocol):
    def blob(self, name: str) -> Any: ...


def upload_pdf(
    bucket: _Bucket, file_bytes: bytes, filename: str, doc_type: str, gcs_prefix: str = "uc1"
) -> str:
    """Upload the PDF to its doc-type subfolder and return the resulting ``gs://`` URI."""
    subfolder = SUBFOLDERS.get(doc_type, "protocols")
    blob_path = f"{gcs_prefix}/{subfolder}/{filename}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(file_bytes, content_type="application/pdf")
    return f"gs://{getattr(bucket, 'name', '')}/{blob_path}"


def upload_mapping(bucket: _Bucket, mapping: dict, gcs_prefix: str = "uc1") -> str:
    """Upload the refreshed ``hospital_mapping.json`` and return its blob path."""
    blob_path = f"{gcs_prefix}/metadata/hospital_mapping.json"
    bucket.blob(blob_path).upload_from_string(
        json.dumps(mapping, indent=2, ensure_ascii=False), content_type="application/json"
    )
    return blob_path


def upload_metadata_jsonl(bucket: _Bucket, jsonl: str, gcs_prefix: str = "uc1") -> str:
    """Upload the refreshed ``metadata.jsonl`` and return its ``gs://`` URI."""
    blob_path = f"{gcs_prefix}/metadata/metadata.jsonl"
    bucket.blob(blob_path).upload_from_string(jsonl, content_type="application/x-ndjson")
    return f"gs://{getattr(bucket, 'name', '')}/{blob_path}"


def _access_token() -> str:
    """Fetch an OAuth token, preferring the agent's credential bridging."""
    try:
        from agent.config import get_gcp_credentials
        from google.auth.transport.requests import Request

        creds, _ = get_gcp_credentials()
        if not getattr(creds, "token", None):
            creds.refresh(Request())
        if creds.token:
            return str(creds.token)
    except Exception:
        pass
    return subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()


def _default_api_request(method: str, url: str, payload: dict | None, project_id: str) -> dict:
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "x-goog-user-project": project_id,
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code} on {method} {url}: {detail}") from exc


def trigger_incremental_import(
    jsonl_gcs_uri: str,
    project_id: str,
    data_store_id: str,
    location: str = "global",
    request_fn: Callable[[str, str, dict | None, str], dict] | None = None,
) -> str:
    """Re-index the data store from ``metadata.jsonl`` and return the operation name.

    ``INCREMENTAL`` reconciliation upserts the documents in the manifest and
    leaves everything else untouched, so a single-document admin upload does
    not require a full re-import.
    """
    request = request_fn or _default_api_request
    host = (
        "https://discoveryengine.googleapis.com"
        if location == "global"
        else f"https://{location}-discoveryengine.googleapis.com"
    )
    ds_name = (
        f"projects/{project_id}/locations/{location}"
        f"/collections/default_collection/dataStores/{data_store_id}"
    )
    payload = {
        "gcsSource": {"inputUris": [jsonl_gcs_uri], "dataSchema": "document"},
        "reconciliationMode": "INCREMENTAL",
    }
    operation = request("POST", f"{host}/v1/{ds_name}/branches/0/documents:import", payload, project_id)
    return str(operation.get("name", ""))


def get_storage_bucket(bucket_name: str = "", project_id: str = ""):
    """Build an authenticated GCS bucket handle using the agent's credentials."""
    from google.cloud import storage

    settings = _settings()
    bucket_name = bucket_name or settings["bucket_name"]
    project_id = project_id or settings["project_id"]
    try:
        from agent.config import get_gcp_credentials

        creds, project_id = get_gcp_credentials(project_id)
        client = storage.Client(project=project_id, credentials=creds)
    except Exception:
        client = storage.Client(project=project_id)
    return client.bucket(bucket_name)


# --- Orchestration --------------------------------------------------------


@dataclass
class SyncResult:
    """Outcome of a "Sync & Re-index" run, with the stage that failed (if any)."""

    ok: bool
    stage: str
    message: str
    gcs_uri: str = ""
    operation_name: str = ""
    document_count: int = 0
    errors: list[str] = field(default_factory=list)


def sync_document(
    file_bytes: bytes,
    filename: str,
    short_title: str,
    hospitals: list[str],
    doc_type: str = "protocol",
    title: str = "",
    *,
    bucket=None,
    mapping: dict | None = None,
    request_fn: Callable[[str, str, dict | None, str], dict] | None = None,
    settings: dict | None = None,
    write_local: bool = True,
) -> SyncResult:
    """Run the full admin flow: upload -> update mapping/manifest -> re-index.

    Stages are executed in order and the first failure short-circuits, so the
    returned :class:`SyncResult` always names the stage the operator needs to
    look at (``validate``, ``upload``, ``mapping`` or ``reindex``).
    """
    errors = validate_upload(filename, short_title, hospitals, doc_type)
    if errors:
        return SyncResult(False, "validate", "Please fix the form errors.", errors=errors)

    cfg = settings or _settings()

    try:
        bucket = bucket if bucket is not None else get_storage_bucket(
            cfg["bucket_name"], cfg["project_id"]
        )
    except Exception as exc:
        return SyncResult(False, "upload", f"Could not reach GCS: {exc}")

    record = build_document_record(
        filename=filename,
        short_title=short_title,
        hospitals=hospitals,
        doc_type=doc_type,
        title=title,
        bucket_name=cfg["bucket_name"],
        gcs_prefix=cfg["gcs_prefix"],
    )

    try:
        gcs_uri = upload_pdf(bucket, file_bytes, filename, doc_type, cfg["gcs_prefix"])
    except Exception as exc:
        return SyncResult(False, "upload", f"PDF upload failed: {exc}")

    try:
        mapping = dict(mapping if mapping is not None else _load_raw_mapping(cfg))
        mapping["documents"] = upsert_document(mapping.get("documents", []), record)
        mapping.setdefault("hospitals", list(HOSPITALS))
        mapping["bucket_name"] = cfg["bucket_name"]
        mapping["gcs_prefix"] = cfg["gcs_prefix"]

        upload_mapping(bucket, mapping, cfg["gcs_prefix"])
        jsonl_uri = upload_metadata_jsonl(
            bucket, build_metadata_jsonl(mapping["documents"]), cfg["gcs_prefix"]
        )
        if write_local:
            _write_local_mapping(mapping)
    except Exception as exc:
        return SyncResult(False, "mapping", f"Mapping update failed: {exc}", gcs_uri=gcs_uri)

    try:
        operation = trigger_incremental_import(
            jsonl_gcs_uri=jsonl_uri,
            project_id=cfg["project_id"],
            data_store_id=cfg["data_store_id"],
            location=cfg["location"],
            request_fn=request_fn,
        )
    except Exception as exc:
        return SyncResult(
            False,
            "reindex",
            f"Upload and mapping succeeded, but re-indexing failed: {exc}",
            gcs_uri=gcs_uri,
            document_count=len(mapping["documents"]),
        )

    return SyncResult(
        True,
        "done",
        f"'{record['short_title']}' is uploaded, mapped to "
        f"{len(record['hospitals'])} hospital(s) and queued for re-indexing.",
        gcs_uri=gcs_uri,
        operation_name=operation,
        document_count=len(mapping["documents"]),
    )


def _load_raw_mapping(cfg: dict) -> dict:
    """Load the un-normalised mapping (``documents`` list) the sync flow mutates."""
    try:
        from agent.config import load_hospital_mapping

        return load_hospital_mapping()
    except Exception:
        if LOCAL_MAPPING_PATH.exists():
            return json.loads(LOCAL_MAPPING_PATH.read_text(encoding="utf-8"))
        return {
            "hospitals": list(HOSPITALS),
            "bucket_name": cfg["bucket_name"],
            "gcs_prefix": cfg["gcs_prefix"],
            "documents": [],
        }


def _write_local_mapping(mapping: dict) -> None:
    """Mirror the mapping locally so the agent picks up the new document."""
    LOCAL_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_MAPPING_PATH.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8"
    )
