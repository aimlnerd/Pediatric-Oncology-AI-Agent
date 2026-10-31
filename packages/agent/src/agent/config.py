"""Configuration, GCP credential bridging, and Hospital-Protocol mapping loader."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import google.auth
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials

load_dotenv()

DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-01-a76134d9a6dc")
DEFAULT_VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "eu")
DEFAULT_DISCOVERY_LOCATION = os.environ.get("DISCOVERY_ENGINE_LOCATION", "global")
DEFAULT_DATA_STORE_ID = os.environ.get("VERTEX_SEARCH_DATA_STORE_ID", "bspho-oncoprotocols-ds")
DEFAULT_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", f"use-cases-{DEFAULT_PROJECT_ID}")
DEFAULT_GCS_PREFIX = os.environ.get("GCS_PREFIX", "uc1")
DEFAULT_MODEL_NAME = os.environ.get("AGENT_MODEL_NAME", "gemini-3.8-flash")

HOSPITALS = ["UZG", "UZL", "UCL", "HUDERF", "UZA", "CHU"]

# Resolve workspace root (`swg_2026_va/`) if available, or fall back to container app directory
def _resolve_mapping_path() -> Path:
    current = Path(__file__).resolve()
    # If installed in local monorepo (swg_2026_va/packages/agent/src/agent/config.py)
    if len(current.parents) >= 5 and (current.parents[4] / "data" / "processed" / "hospital_mapping.json").exists():
        return current.parents[4] / "data" / "processed" / "hospital_mapping.json"
    # Search upwards for data/processed/hospital_mapping.json (e.g. in container /app/...)
    for parent in current.parents:
        candidate = parent / "data" / "processed" / "hospital_mapping.json"
        if candidate.exists():
            return candidate
    # Default fallback relative to current file's parent or directory
    return current.parent / "data" / "processed" / "hospital_mapping.json"

LOCAL_MAPPING_PATH = _resolve_mapping_path()

_original_google_auth_default = google.auth.default
_cached_gcloud_creds: tuple[Credentials, float] | None = None
_auth_patched = False


def get_gcp_credentials(project_id: str = DEFAULT_PROJECT_ID) -> tuple[ Any, str]:
    """
    Return valid GCP credentials and project ID.
    On Cloud Run, standard Application Default Credentials (ADC) are used.
    Locally, if ADC points to a different personal project, falls back to active `gcloud auth print-access-token`.
    """
    global _cached_gcloud_creds
    try:
        creds, adc_project = _original_google_auth_default()
        quota_proj = getattr(creds, "quota_project_id", None)
        # If running on Cloud Run / Compute Engine or ADC matches our project, use ADC directly
        if quota_proj == project_id or (quota_proj is None and adc_project == project_id):
            return creds, project_id
    except Exception:
        pass

    # Fallback to active `gcloud` CLI token (cached for 45 minutes)
    now = time.time()
    if _cached_gcloud_creds is not None and (now - _cached_gcloud_creds[1]) < 2700:
        return _cached_gcloud_creds[0], project_id

    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if token:
            gcloud_creds = Credentials(token=token, quota_project_id=project_id)
            _cached_gcloud_creds = (gcloud_creds, now)
            return gcloud_creds, project_id
    except Exception:
        pass

    creds, _ = _original_google_auth_default()
    return creds, project_id


def ensure_vertex_env_and_auth(
    project_id: str = DEFAULT_PROJECT_ID,
    location: str = DEFAULT_VERTEX_LOCATION,
) -> None:
    """Configure environment variables for Google ADK / GenAI SDK and patch ADC if needed."""
    global _auth_patched
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", location)

    if not _auth_patched:
        def _patched_default(*args: Any, **kwargs: Any) -> tuple[Any, str]:
            return get_gcp_credentials(project_id)

        google.auth.default = _patched_default
        _auth_patched = True


def load_hospital_mapping() -> dict[str, Any]:
    """Load `hospital_mapping.json` from local disk (or GCS fallback if running in container without local data)."""
    if LOCAL_MAPPING_PATH.exists():
        return json.loads(LOCAL_MAPPING_PATH.read_text(encoding="utf-8"))

    # Fallback: fetch from GCS bucket
    from google.cloud import storage

    creds, project_id = get_gcp_credentials()
    client = storage.Client(project=project_id, credentials=creds)
    bucket = client.bucket(DEFAULT_BUCKET_NAME)
    blob = bucket.blob(f"{DEFAULT_GCS_PREFIX}/metadata/hospital_mapping.json")
    if blob.exists():
        data = json.loads(blob.download_as_text(encoding="utf-8"))
        LOCAL_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_MAPPING_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data

    return {"hospitals": HOSPITALS, "documents": []}


def get_documents_for_hospital(
    hospital_code: str,
    include_subprotocols: bool = False,
) -> list[dict[str, Any]]:
    """Return all protocol (and optionally subprotocol) metadata records active at `hospital_code`."""
    mapping = load_hospital_mapping()
    docs = mapping.get("documents", [])
    results: list[dict[str, Any]] = []
    for doc in docs:
        if hospital_code not in doc.get("hospitals", []):
            continue
        if doc.get("doc_type") == "subprotocol" and not include_subprotocols:
            continue
        results.append(doc)
    return results

