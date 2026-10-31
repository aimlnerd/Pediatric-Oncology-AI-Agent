#!/usr/bin/env python3
"""
Step 1: GCP & Dataset Setup Script for BSPHO Pediatric Oncology Virtual Assistant (UC1).

What this script does:
1. Parses `data/1. Virtual Assistant/SwG-UC1-Golden Dataset and mapping table.xlsx`
   to extract the 27 master protocols, 5 subprotocols, and hospital participation matrix
   (UZA, UZL, CHU, UCL, HUDERF, UZG).
2. Writes `data/processed/hospital_mapping.json` and `data/processed/golden_dataset.json`
   for fast local/UI lookup and evaluation.
3. Creates or reuses the GCS bucket and uploads:
   - All 27 master protocol PDFs -> `gs://<bucket>/uc1/protocols/`
   - All 5 subprotocol PDFs -> `gs://<bucket>/uc1/subprotocols/`
   - `hospital_mapping.json` -> `gs://<bucket>/uc1/metadata/hospital_mapping.json`
   - `metadata.jsonl` -> `gs://<bucket>/uc1/metadata/metadata.jsonl`
4. Creates the Vertex AI Search (Discovery Engine) Unstructured Data Store (with
   layout-based chunking enabled) and triggers the document import from `metadata.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

# Default configuration
DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-01-a76134d9a6dc")
DEFAULT_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", f"use-cases-{DEFAULT_PROJECT_ID}")
DEFAULT_GCS_PREFIX = os.environ.get("GCS_PREFIX", "uc1")
DEFAULT_LOCATION = os.environ.get("DISCOVERY_ENGINE_LOCATION", "global")
DEFAULT_DATA_STORE_ID = os.environ.get("VERTEX_SEARCH_DATA_STORE_ID", "bspho-oncoprotocols-ds")

HOSPITALS = ["UZA", "UZL", "CHU", "UCL", "HUDERF", "UZG"]

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "1. Virtual Assistant"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"


def slugify_doc_id(filename: str) -> str:
    """Create a valid Vertex AI Search document ID ([a-zA-Z0-9-_] up to 128 chars)."""
    stem = Path(filename).stem
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-")
    return slug[:120]


def _parse_xlsx_sheet_rows(xlsx_path: Path, sheet_index: int) -> list[dict[str, str]]:
    """Parse an XLSX worksheet into a list of column-letter -> string dicts using stdlib."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(xlsx_path) as z:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{ns}si"):
                strings.append("".join(si.itertext()))

        sheet_xml = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet_index}.xml"))
        rows_out: list[dict[str, str]] = []
        for row in sheet_xml.findall(f".//{ns}row"):
            row_dict: dict[str, str] = {}
            for cell in row.findall(f"{ns}c"):
                ref = cell.attrib.get("r", "")
                col_letter = re.sub(r"[0-9]+", "", ref)
                cell_type = cell.attrib.get("t", "")
                v_el = cell.find(f"{ns}v")
                val = v_el.text if v_el is not None and v_el.text is not None else ""
                if cell_type == "s" and val != "":
                    val = strings[int(val)]
                if val.strip():
                    row_dict[col_letter] = val.strip()
            if row_dict:
                rows_out.append(row_dict)
        return rows_out


def parse_mapping_and_golden_dataset(xlsx_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract the Hospital-Protocol Mapping (Sheet 2) and Golden Dataset (Sheet 1)."""
    # 1. Parse Sheet 2: Hospital-Protocol Mapping
    sheet2_rows = _parse_xlsx_sheet_rows(xlsx_path, sheet_index=2)
    hospital_cols = {
        "F": "UZA",
        "G": "UZL",
        "H": "CHU",
        "I": "UCL",
        "J": "HUDERF",
        "K": "UZG",
    }

    documents: list[dict[str, Any]] = []
    for row in sheet2_rows[1:]:  # Skip header row
        filename = row.get("A", "")
        if not filename:
            continue
        is_subprotocol = bool(row.get("E", "").lower() == "x")
        doc_type = "subprotocol" if is_subprotocol else "protocol"
        short_title = row.get("B", "")
        file_title = row.get("C", "")
        if not short_title:
            # Subprotocols don't have Column B filled in the Excel; derive a clean short title
            short_title = (
                Path(filename)
                .stem.replace("_FINAL", "")
                .replace("_CLEAN_signed", "")
                .replace("_", " ")
                .strip()
            )

        participating_hospitals = [
            hosp_code
            for col_letter, hosp_code in hospital_cols.items()
            if row.get(col_letter, "").upper() == "X"
        ]

        subfolder = "subprotocols" if is_subprotocol else "protocols"
        local_path = DATA_DIR / subfolder / filename
        if not local_path.exists():
            raise FileNotFoundError(f"Expected PDF file not found on disk: {local_path}")

        documents.append(
            {
                "id": slugify_doc_id(filename),
                "filename": filename,
                "short_title": short_title,
                "title": file_title,
                "doc_type": doc_type,
                "subfolder": subfolder,
                "hospitals": participating_hospitals,
            }
        )

    # 2. Parse Sheet 1: Golden Dataset (Q1-Q9)
    sheet1_rows = _parse_xlsx_sheet_rows(xlsx_path, sheet_index=1)
    golden_questions: list[dict[str, Any]] = []
    for idx, row in enumerate(sheet1_rows[1:], start=1):
        question = row.get("B", "")
        if not question:
            continue
        golden_questions.append(
            {
                "id": f"Q{idx}",
                "scope": row.get("A", ""),
                "question": question.strip("“”\""),
                "detailed_response": row.get("C", ""),
                "shortened_response": row.get("D", ""),
            }
        )

    return documents, golden_questions


def write_local_artifacts(
    documents: list[dict[str, Any]],
    golden_questions: list[dict[str, Any]],
    bucket_name: str,
    gcs_prefix: str,
) -> tuple[Path, Path, Path]:
    """Write hospital_mapping.json, golden_dataset.json, and metadata.jsonl to data/processed/."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Enrich documents with their target GCS URI
    for doc in documents:
        doc["gcs_uri"] = f"gs://{bucket_name}/{gcs_prefix}/{doc['subfolder']}/{doc['filename']}"

    mapping_payload = {
        "hospitals": HOSPITALS,
        "bucket_name": bucket_name,
        "gcs_prefix": gcs_prefix,
        "documents": documents,
    }
    mapping_path = PROCESSED_DIR / "hospital_mapping.json"
    mapping_path.write_text(json.dumps(mapping_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    golden_path = PROCESSED_DIR / "golden_dataset.json"
    golden_path.write_text(json.dumps(golden_questions, indent=2, ensure_ascii=False), encoding="utf-8")

    # Build Vertex AI Search JSONL metadata file
    jsonl_lines: list[str] = []
    for doc in documents:
        record = {
            "id": doc["id"],
            "structData": {
                "filename": doc["filename"],
                "short_title": doc["short_title"],
                "title": doc["title"],
                "doc_type": doc["doc_type"],
                "hospitals": doc["hospitals"],
            },
            "content": {
                "mimeType": "application/pdf",
                "uri": doc["gcs_uri"],
            },
        }
        jsonl_lines.append(json.dumps(record, ensure_ascii=False))

    jsonl_path = PROCESSED_DIR / "metadata.jsonl"
    jsonl_path.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

    return mapping_path, golden_path, jsonl_path


def sync_to_gcs(bucket_name: str, gcs_prefix: str, mapping_path: Path, jsonl_path: Path) -> None:
    """Ensure the GCS bucket exists and sync protocols, subprotocols, and metadata."""
    print(f"\n[1/3] Checking GCS bucket gs://{bucket_name} ...")
    res = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"Creating bucket gs://{bucket_name} in europe-west1 ...")
        subprocess.run(
            ["gcloud", "storage", "buckets", "create", f"gs://{bucket_name}", "--location=europe-west1"],
            check=True,
        )
    else:
        print(f"Bucket gs://{bucket_name} already exists.")

    print(f"\n[2/3] Syncing 27 master protocols & 5 subprotocols to gs://{bucket_name}/{gcs_prefix}/ ...")
    subprocess.run(
        [
            "gcloud",
            "storage",
            "rsync",
            str(DATA_DIR / "protocols"),
            f"gs://{bucket_name}/{gcs_prefix}/protocols",
        ],
        check=True,
    )
    subprocess.run(
        [
            "gcloud",
            "storage",
            "rsync",
            str(DATA_DIR / "subprotocols"),
            f"gs://{bucket_name}/{gcs_prefix}/subprotocols",
        ],
        check=True,
    )

    print(f"\n[3/3] Uploading hospital_mapping.json & metadata.jsonl to gs://{bucket_name}/{gcs_prefix}/metadata/ ...")
    subprocess.run(
        [
            "gcloud",
            "storage",
            "cp",
            str(mapping_path),
            str(jsonl_path),
            f"gs://{bucket_name}/{gcs_prefix}/metadata/",
        ],
        check=True,
    )
    print("GCS sync complete!")


def setup_vertex_ai_search(
    project_id: str,
    location: str,
    data_store_id: str,
    jsonl_gcs_uri: str,
) -> None:
    """Create the Vertex AI Search Unstructured Data Store (with layout chunking) and trigger GCS import."""
    import urllib.error
    import urllib.request

    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    base_host = (
        f"https://{location}-discoveryengine.googleapis.com"
        if location != "global"
        else "https://discoveryengine.googleapis.com"
    )
    parent = f"projects/{project_id}/locations/{location}/collections/default_collection"
    ds_name = f"{parent}/dataStores/{data_store_id}"

    def _api_request(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "x-goog-user-project": project_id,
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code} on {method} {url}: {err_body}") from e

    print(f"\nChecking Vertex AI Search Data Store '{data_store_id}' in location '{location}' ...")
    try:
        ds = _api_request("GET", f"{base_host}/v1/{ds_name}")
        print(f"Data Store already exists: {ds.get('name')}")
    except RuntimeError as err:
        if "HTTP 404" not in str(err):
            raise
        print(f"Creating Data Store '{data_store_id}' with Layout Parser & Chunking enabled ...")
        create_url = f"{base_host}/v1/{parent}/dataStores?dataStoreId={data_store_id}"
        ds_payload = {
            "displayName": "BSPHO Pediatric Oncology Protocols",
            "industryVertical": "GENERIC",
            "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
            "contentConfig": "CONTENT_REQUIRED",
            "documentProcessingConfig": {
                "chunkingConfig": {
                    "layoutBasedChunkingConfig": {
                        "chunkSize": 500,
                        "includeAncestorHeadings": True,
                    }
                },
                "defaultParsingConfig": {
                    "layoutParsingConfig": {}
                },
            },
        }
        op = _api_request("POST", create_url, ds_payload)
        print(f"Create Data Store operation started: {op.get('name')}")
        time.sleep(5)

    # Trigger Incremental Document Import from metadata.jsonl
    print(f"\nTriggering document import from {jsonl_gcs_uri} into '{data_store_id}' ...")
    import_url = f"{base_host}/v1/{ds_name}/branches/0/documents:import"
    import_payload = {
        "gcsSource": {
            "inputUris": [jsonl_gcs_uri],
            "dataSchema": "document",
        },
        "reconciliationMode": "INCREMENTAL",
    }
    import_op = _api_request("POST", import_url, import_payload)
    op_name = import_op.get("name", "")
    print(f"Document import operation started successfully!\n  Operation: {op_name}")
    print(
        f"\nYou can check import status at any time with:\n"
        f"  python3 scripts/setup_gcp.py --check-operation \"{op_name}\""
    )


def check_operation_status(project_id: str, location: str, operation_name: str) -> None:
    """Check the status of a long-running Discovery Engine operation."""
    import urllib.request

    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    base_host = (
        f"https://{location}-discoveryengine.googleapis.com"
        if location != "global"
        else "https://discoveryengine.googleapis.com"
    )
    url = f"{base_host}/v1/{operation_name}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "x-goog-user-project": project_id,
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(data, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup GCS and Vertex AI Search for UC1.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--bucket-name", default=DEFAULT_BUCKET_NAME)
    parser.add_argument("--gcs-prefix", default=DEFAULT_GCS_PREFIX)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--data-store-id", default=DEFAULT_DATA_STORE_ID)
    parser.add_argument("--local-only", action="store_true", help="Only generate local JSON/JSONL files.")
    parser.add_argument("--check-operation", default="", help="Check status of a Discovery Engine LRO.")
    args = parser.parse_args()

    if args.check_operation:
        check_operation_status(args.project_id, args.location, args.check_operation)
        return

    xlsx_path = DATA_DIR / "SwG-UC1-Golden Dataset and mapping table.xlsx"
    documents, golden_questions = parse_mapping_and_golden_dataset(xlsx_path)

    protocols_count = sum(1 for d in documents if d["doc_type"] == "protocol")
    subprotocols_count = sum(1 for d in documents if d["doc_type"] == "subprotocol")
    print(f"Parsed {protocols_count} master protocols and {subprotocols_count} subprotocols from Excel.")
    print(f"Parsed {len(golden_questions)} Golden Dataset questions.")

    for hosp in HOSPITALS:
        active = [d["short_title"] for d in documents if d["doc_type"] == "protocol" and hosp in d["hospitals"]]
        print(f"  - {hosp}: {len(active)} active master protocols")

    mapping_path, golden_path, jsonl_path = write_local_artifacts(
        documents, golden_questions, args.bucket_name, args.gcs_prefix
    )
    print(f"\nGenerated local metadata files:")
    print(f"  - {mapping_path}")
    print(f"  - {golden_path}")
    print(f"  - {jsonl_path}")

    if args.local_only:
        return

    sync_to_gcs(args.bucket_name, args.gcs_prefix, mapping_path, jsonl_path)
    jsonl_gcs_uri = f"gs://{args.bucket_name}/{args.gcs_prefix}/metadata/metadata.jsonl"
    setup_vertex_ai_search(args.project_id, args.location, args.data_store_id, jsonl_gcs_uri)


if __name__ == "__main__":
    main()

