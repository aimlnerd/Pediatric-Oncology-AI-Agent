#!/usr/bin/env bash
# Create and fill the Vertex AI Search data store the ADK agent searches.
#
#   scripts/search_index.sh create   # data store (layout parser + annotations, chunking) + schema
#   scripts/search_index.sh import   # manifest -> documents.jsonl -> GCS -> incremental import
#   scripts/search_index.sh status   # data store config + indexed document count
#   scripts/search_index.sh operations                     # import progress, failures, error samples
#   scripts/search_index.sh verify "query" [protocol_id..] # chunk search with page numbers + metadata
#
# Uses gcloud + REST so it runs without Python HTTPS (blocked on some managed Windows hosts).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
LOCATION="${SEARCH_LOCATION:-eu}"
DATA_STORE_ID="${DATA_STORE_ID:-bspho-protocols-eu}"
BUCKET="${BUCKET:-use-cases-${PROJECT_ID}}"
PDF_PREFIX="gs://${BUCKET}/uc1/protocols"
JSONL_URI="gs://${BUCKET}/uc1/metadata/${DATA_STORE_ID}-documents.jsonl"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${ROOT}/packages/agent/src/agent/data/protocol_manifest.json"
API="https://${LOCATION}-discoveryengine.googleapis.com/v1"
[[ "${LOCATION}" == "global" ]] && API="https://discoveryengine.googleapis.com/v1"
COLLECTION="projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection"
STORE="${COLLECTION}/dataStores/${DATA_STORE_ID}"

call() {  # call METHOD PATH [JSON_BODY]
  curl -sS -X "$1" "${API}/$2" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    ${3:+--data "$3"}
}

create() {
  call POST "${COLLECTION}/dataStores?dataStoreId=${DATA_STORE_ID}" '{
    "displayName": "BSPHO Protocols (eu)",
    "industryVertical": "GENERIC",
    "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
    "contentConfig": "CONTENT_REQUIRED",
    "documentProcessingConfig": {
      "chunkingConfig": {"layoutBasedChunkingConfig": {"chunkSize": 500, "includeAncestorHeadings": true}},
      "defaultParsingConfig": {"layoutParsingConfig": {"enableTableAnnotation": true, "enableImageAnnotation": true}}
    }
  }'
  echo
  # Filterable/retrievable metadata. Center participation is deliberately absent (ADR 0002).
  local schema
  schema=$(cat <<'EOF'
{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{
 "protocol_id":{"type":"string","indexable":true,"retrievable":true,"dynamicFacetable":true},
 "short_title":{"type":"string","searchable":true,"retrievable":true},
 "title":{"type":"string","retrievable":true,"keyPropertyMapping":"title"},
 "filename":{"type":"string","retrievable":true},
 "version":{"type":"string","retrievable":true},
 "version_date":{"type":"string","retrievable":true},
 "doc_type":{"type":"string","indexable":true,"retrievable":true}}}
EOF
)
  call PATCH "${STORE}/schemas/default_schema" \
    "$(printf '%s' "${schema}" | uv run --no-project python -I -c 'import json,sys; print(json.dumps({"jsonSchema": json.dumps(json.load(sys.stdin))}))')"
  echo
}

import_documents() {
  local jsonl
  jsonl="$(mktemp)"
  # ASCII-escaped JSON: stdout on Windows is cp1252, which corrupts titles with "–" or "’".
  # ONLY_IDS="a b" limits the import to those protocol_ids (e.g. to retry failures).
  uv run --no-project python -I - "${MANIFEST}" "${PDF_PREFIX}" "${ONLY_IDS:-}" > "${jsonl}" <<'EOF'
import json, sys
manifest, prefix, only = sys.argv[1], sys.argv[2], set(sys.argv[3].split())
for e in json.load(open(manifest, encoding="utf-8")):
    if only and e["protocol_id"] not in only:
        continue
    meta = {k: e[k] for k in ("protocol_id", "short_title", "title", "filename", "version", "version_date", "doc_type") if e[k] is not None}
    print(json.dumps({"id": e["protocol_id"], "structData": meta,
                      "content": {"mimeType": "application/pdf", "uri": f"{prefix}/{e['filename']}"}}))
EOF
  echo "Documents: $(wc -l < "${jsonl}") -> ${JSONL_URI}"
  gcloud storage cp "${jsonl}" "${JSONL_URI}"
  call POST "${STORE}/branches/default_branch/documents:import" "{
    \"gcsSource\": {\"inputUris\": [\"${JSONL_URI}\"], \"dataSchema\": \"document\"},
    \"reconciliationMode\": \"INCREMENTAL\"
  }"
  echo
}

status() {
  call GET "${STORE}/branches/default_branch/documents?pageSize=100" \
    | uv run --no-project python -I -c '
import json, sys
docs = json.load(sys.stdin).get("documents", [])
pending = [d["id"] for d in docs if not d.get("indexTime")]
print(f"Documents with an indexTime: {len(docs) - len(pending)} of {len(docs)} (this lags behind search; confirm a protocol with verify)")
for doc_id in pending:
    print("  no indexTime yet:", doc_id)'
}

operations() {  # recent import operations with success/failure counts and error samples
  call GET "${STORE}/branches/default_branch/operations?pageSize=5"
  echo
}

verify() {  # verify "QUERY" [protocol_id ...] -> chunk search the agent will run
  local query="$1"; shift
  local filter=""
  if (( $# )); then
    filter="protocol_id: ANY($(printf '"%s",' "$@" | sed 's/,$//'))"
  fi
  local body
  body=$(uv run --no-project python -I -c '
import json, sys
print(json.dumps({"query": sys.argv[1], "pageSize": 5, "filter": sys.argv[2],
  "contentSearchSpec": {"searchResultMode": "CHUNKS",
                        "chunkSpec": {"numPreviousChunks": 0, "numNextChunks": 0}}}))' "${query}" "${filter}")
  call POST "${STORE}/servingConfigs/default_search:search" "${body}" \n    | uv run --no-project python -I "${ROOT}/scripts/format_chunks.py"
}

case "${1:-}" in
  create) create ;;
  import) import_documents ;;
  status) status ;;
  operations) operations ;;
  verify) shift; verify "$@" ;;
  *) echo "usage: $0 {create|import|status|operations|verify \"query\" [protocol_id...]}" >&2; exit 2 ;;
esac
