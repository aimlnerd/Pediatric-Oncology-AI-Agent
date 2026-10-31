"""Print a Vertex AI Search CHUNKS response (JSON on stdin) as readable citations."""

import json
import sys

response = json.load(sys.stdin)
if "error" in response:
    sys.exit(json.dumps(response["error"], indent=2))

results = response.get("results", [])
for number, result in enumerate(results, 1):
    chunk = result["chunk"]
    meta = chunk.get("documentMetadata", {})
    data = meta.get("structData", {})
    span = chunk.get("pageSpan", {})
    title = data.get("short_title", meta.get("title"))
    print(
        f"[{number}] {title} v{data.get('version', '?')} "
        f"pp. {span.get('pageStart')}-{span.get('pageEnd')}  ({data.get('filename')})"
    )
    print("    chunk:", chunk.get("id"))
    print("    " + " ".join(chunk.get("content", "").split())[:260] + "\n")
print(f"{len(results)} chunks")
