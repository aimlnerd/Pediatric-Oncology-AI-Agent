# Single Cloud Run Deployment — BSPHO Virtual Assistant

End-to-end `gcloud` instructions for deploying this repository to Google Cloud
as **one Cloud Run service**. Replaces the ad-hoc manual commands used so far.

The UI and the ADK agent ship in the **same container**: `ui` depends on the
workspace `agent` package, so `uv sync --package ui` installs both and the agent
runs in-process inside the Streamlit server. There is no separate agent service
and no network hop between them.

## What gets deployed

| # | Component | Source | Target | Name |
| --- | --- | --- | --- | --- |
| 1 | Knowledge base | `scripts/setup_gcp.py` | GCS + Vertex AI Search | `use-cases-<project>`, `bspho-oncoprotocols-ds` |
| 2 | Clinician UI | `deploy/Dockerfile.ui` | **Cloud Run** | `va-ui` |

> **Agent topology.** The UI embeds the ADK agent **in-process** (see
> `UI_AGENT_BACKEND` in [`packages/ui/README.md`](packages/ui/README.md)) — it
> does not call a remote agent endpoint. `va-ui` is therefore self-contained and
> is the only service that needs deploying.
>
> Deploying the agent as its own surface (a standalone ADK API server on Cloud
> Run, or Vertex AI **Agent Runtime**) is possible but deliberately not covered
> here: it needs a `requirements.txt` and `.agent_engine_config.json` in
> `packages/agent/src/agent/`, plus a fix to `WORKSPACE_ROOT` in
> [`config.py`](packages/agent/src/agent/config.py), which currently uses
> `parents[4]` and raises `IndexError` in the flatter Agent Runtime layout.

## Current state of `qwiklabs-gcp-01-a76134d9a6dc`

**Live:** <https://va-ui-831213015144.europe-west4.run.app> (`va-ui`, public,
revision `va-ui-00001-rzl`, built from `deploy/Dockerfile.ui`)

Already provisioned — do **not** recreate:

- GCS bucket `use-cases-qwiklabs-gcp-01-a76134d9a6dc` with the `uc1/` prefix
- Vertex AI Search data store `bspho-oncoprotocols-ds` (location `global`)
- Vertex AI Search data store `bspho-protocols-eu` (location `eu`)
- Artifact Registry repo `va` (`europe-west4`)
- Service account `va-runtime@…` with the §1b roles
- Cloud Run service `va-ui`

The `remote-browser-vm1…5` Cloud Run services in the project are qwiklabs lab
infrastructure. **Leave them alone.**

> **Lab-project constraints hit during the first deploy** — both are already
> handled in the committed files, but are worth knowing if you move projects:
> Cloud Build rejects a custom `machineType`, and its `docker` builder runs the
> legacy engine, so `RUN --mount=type=cache` (BuildKit-only) is not available.

---

## 0. Prerequisites

```bash
gcloud auth login
gcloud auth application-default login
```

Load the shared variables used by every command below:

```bash
source deploy/env.sh
gcloud config set project "$PROJECT_ID"
```

Enable the required APIs (idempotent — most are already on):

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT_ID"
```

> **uv note.** This workspace resolves from PyPI only and needs no keyring.
> If your machine-level `~/.config/uv/uv.toml` enables a keyring provider or a
> private index, create a **local, gitignored** `uv.toml` at the repo root so
> `uv run …` never prompts for credentials:
>
> ```toml
> keyring-provider = "disabled"
>
> [[index]]
> name = "pypi"
> url = "https://pypi.org/simple"
> default = true
>
> # Redeclare any private index by name with `explicit = true` so it is only
> # consulted for packages that opt in — nothing here does. uv MERGES this file
> # with the machine-level config, so shadowing is required; overriding
> # `keyring-provider` alone would leave the index queried and failing on 401.
> [[index]]
> name = "private"
> url = "https://<your-private-index>/simple/"
> explicit = true
> ```

---

## 1. One-time project setup

### 1a. Artifact Registry

```bash
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="BSPHO Virtual Assistant container images" \
  --project="$PROJECT_ID"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
```

### 1b. Runtime service account

```bash
gcloud iam service-accounts create va-runtime \
  --display-name="BSPHO Virtual Assistant runtime" \
  --project="$PROJECT_ID"

# Base roles — enough for the agent to answer questions (read-only).
for ROLE in \
  roles/aiplatform.user \
  roles/discoveryengine.viewer \
  roles/storage.objectViewer \
  roles/logging.logWriter ; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="$ROLE" \
    --condition=None
done
```

The UI's **Admin tab** (`packages/ui/src/ui/gcs_sync.py`) additionally uploads
PDFs, rewrites `hospital_mapping.json` / `metadata.jsonl`, and triggers an
incremental Vertex AI Search import. That needs write access:

```bash
for ROLE in \
  roles/storage.objectAdmin \
  roles/discoveryengine.editor ; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="$ROLE" \
    --condition=None
done
```

Scope `roles/storage.objectAdmin` to just the one bucket instead of the whole
project if you prefer:

```bash
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET_NAME}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin"
```

### 1c. Let Cloud Build build and push the image

```bash
for ROLE in \
  roles/cloudbuild.builds.builder \
  roles/artifactregistry.writer ; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="$ROLE" \
    --condition=None
done
```

---

## 2. Knowledge base (GCS + Vertex AI Search)

Already provisioned. Re-run only when the protocol PDFs or the mapping table
change — the script is idempotent and reuses the existing bucket and data store:

```bash
uv run python scripts/setup_gcp.py \
  --project-id "$PROJECT_ID" \
  --bucket-name "$GCS_BUCKET_NAME" \
  --data-store-id "$VERTEX_SEARCH_DATA_STORE_ID"
```

Verify the data store and the mapping file the agent depends on at runtime:

```bash
gcloud storage ls "gs://${GCS_BUCKET_NAME}/${GCS_PREFIX}/metadata/"

curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
     -H "x-goog-user-project: ${PROJECT_ID}" \
  "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/collections/default_collection/dataStores/${VERTEX_SEARCH_DATA_STORE_ID}"
```

> The container never ships the protocol PDFs or `hospital_mapping.json`. The
> agent downloads the mapping from
> `gs://$GCS_BUCKET_NAME/$GCS_PREFIX/metadata/hospital_mapping.json` on startup,
> so that object **must** exist before §3.

---

## 3. Cloud Run — clinician UI (`va-ui`)

Streamlit UI with the ADK agent in-process.

### Build

Two Dockerfiles exist for this single service. **Use `deploy/Dockerfile.ui`** —
it is what production runs.

| Dockerfile | Shape | Status |
| --- | --- | --- |
| `deploy/Dockerfile.ui` | Single-stage on the `uv` base image, runs as **root** | ✅ **In use.** Builds and runs correctly |
| `Dockerfile` (root) | Multi-stage, non-root `appuser`, `uv sync --locked --no-dev`, `HEALTHCHECK`, Streamlit XSRF/CORS for Cloud Run | ⚠️ **Does not work yet** — see below |

```bash
export UI_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${UI_SERVICE}:$(git rev-parse --short HEAD)"

gcloud builds submit . \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --config=deploy/cloudbuild.yaml \
  --substitutions=_IMAGE="$UI_IMAGE"
```

[`deploy/cloudbuild.yaml`](deploy/cloudbuild.yaml) exists so the build can pick
which Dockerfile to use — `gcloud builds submit --tag` always assumes the root
`Dockerfile` and offers no way to select another. It defaults to
`deploy/Dockerfile.ui`; override with `_DOCKERFILE=…`.

#### Why the root `Dockerfile` is not used

It is the better-engineered image and **builds fine**, but it fails at runtime.
It runs as the unprivileged `appuser`, while `WORKDIR /app` creates `/app` owned
by root (`--chown` on `COPY` applies to the copied contents, not the directory).
The agent's GCS mapping fallback in
[`config.py`](packages/agent/src/agent/config.py) then does an unguarded:

```python
LOCAL_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
```

…which raises `PermissionError: [Errno 13] Permission denied: '/app/data'`. The
UI silently degrades to the bundled **13-document sample mapping** instead of
the live 32-document one, and chat requests fail. Confirmed on Cloud Run
revision `va-ui-00002-jfr`, which was rolled back.

To adopt it, do **one** of:

- wrap that write in `try/except OSError` in `config.py` (it is only a cache), or
- add `RUN mkdir -p /app/data && chown -R appuser:appuser /app` before `USER appuser`.

> **Neither Dockerfile may use `RUN --mount=type=cache`.** It requires BuildKit,
> and Cloud Build's `gcr.io/cloud-builders/docker` runs the legacy engine, which
> fails with *"the --mount option requires BuildKit"*. It builds fine locally
> with Docker Desktop, so this only shows up in Cloud Build.

### Deploy

```bash
gcloud run deploy "$UI_SERVICE" \
  --image="$UI_IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$RUNTIME_SA" \
  --port=8080 \
  --cpu=2 --memory=2Gi \
  --min-instances=0 --max-instances=4 \
  --timeout=900 \
  --session-affinity \
  --allow-unauthenticated \
  --set-env-vars="^@^GOOGLE_CLOUD_PROJECT=${PROJECT_ID}@GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}@GOOGLE_GENAI_USE_VERTEXAI=1@AGENT_MODEL_NAME=${AGENT_MODEL_NAME}@DISCOVERY_ENGINE_LOCATION=${DISCOVERY_ENGINE_LOCATION}@VERTEX_SEARCH_DATA_STORE_ID=${VERTEX_SEARCH_DATA_STORE_ID}@GCS_BUCKET_NAME=${GCS_BUCKET_NAME}@GCS_PREFIX=${GCS_PREFIX}@UI_AGENT_BACKEND=adk@UI_MAPPING_SOURCE=auto"
```

The `^@^` prefix sets `@` as the delimiter so the values stay intact.

`--session-affinity` keeps a clinician's Streamlit session pinned to one
instance; the ADK session service is in-memory per process.

### Locking it down

The service is currently **public**. To require authentication instead:

```bash
gcloud run services update "$UI_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" --no-allow-unauthenticated

gcloud run services add-iam-policy-binding "$UI_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --member="user:clinician@example.org" \
  --role="roles/run.invoker"

# Then reach it through an authenticated proxy:
gcloud run services proxy "$UI_SERVICE" --region="$REGION" --project="$PROJECT_ID" --port=8080
# -> http://localhost:8080
```

### Verify

A `200` from the service proves very little: the UI **silently falls back to an
offline mock agent** if the real one cannot be constructed. Check the sidebar
badge in a browser — it must read 🟢 *Vertex AI Search + ADK agent* with a
document count, not 🟡 *Offline mock agent*.

```bash
URL=$(gcloud run services describe "$UI_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
curl -s "$URL/_stcore/health"   # -> ok
echo "$URL"                      # open this and check the badge
```

Then ask a grounded clinical question (e.g. *"Hodgkin lymphoma, EuroNet-PHL-C2:
how is early response assessment done with PET?"*). A correct deployment
returns an answer with inline page citations such as
`[EuroNet-PHL-C2, pp. 79, 156]`, which exercises Gemini, Vertex AI Search and
GCS under the runtime service account.

---

## 4. Redeploying after a code change

```bash
source deploy/env.sh
export UI_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${UI_SERVICE}:$(git rev-parse --short HEAD)"

gcloud builds submit . --region="$REGION" --project="$PROJECT_ID" \
  --config=deploy/cloudbuild.yaml \
  --substitutions=_IMAGE="$UI_IMAGE"

gcloud run deploy "$UI_SERVICE" --image="$UI_IMAGE" \
  --region="$REGION" --project="$PROJECT_ID"
```

Roll back to the previous revision:

```bash
gcloud run revisions list --service="$UI_SERVICE" --region="$REGION" --project="$PROJECT_ID"
gcloud run services update-traffic "$UI_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --to-revisions=<PREVIOUS_REVISION>=100
```

---

## 5. Operations

```bash
# Live logs
gcloud beta run services logs tail "$UI_SERVICE" --region="$REGION" --project="$PROJECT_ID"

# Recent errors
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=${UI_SERVICE} AND severity>=ERROR" \
  --limit=50 --project="$PROJECT_ID" --freshness=1h

# Env vars on a live service
gcloud run services describe "$UI_SERVICE" --region="$REGION" --project="$PROJECT_ID" \
  --format='value(spec.template.spec.containers[0].env)'
```

---

## 6. Build locally (optional)

```bash
source deploy/env.sh
docker build -f deploy/Dockerfile.ui -t va-ui .

docker run --rm -p 8080:8080 \
  -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
  -e GOOGLE_CLOUD_PROJECT="$PROJECT_ID" \
  -e GOOGLE_CLOUD_LOCATION="$GOOGLE_CLOUD_LOCATION" \
  -e VERTEX_SEARCH_DATA_STORE_ID="$VERTEX_SEARCH_DATA_STORE_ID" \
  -e GCS_BUCKET_NAME="$GCS_BUCKET_NAME" \
  va-ui
```

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| UI badge shows 🟡 *Offline mock agent* | The agent failed to construct (bad creds, missing mapping) | Check logs; `UI_AGENT_BACKEND=adk` is set on the service so the error surfaces instead of silently falling back |
| `403` from `discoveryengine.googleapis.com` | Runtime SA lacks data store access | Grant `roles/discoveryengine.viewer` (§1b) |
| Agent answers "not covered" for everything | `hospital_mapping.json` missing in GCS, so the mapping is empty | Re-run §2 and verify the object exists |
| `404` on the model | `gemini-3.8-flash` not served at `GOOGLE_CLOUD_LOCATION` | Keep `GOOGLE_CLOUD_LOCATION=global`, or set `AGENT_MODEL_NAME` to a model available in `$REGION` |
| Cloud Build permission denied | Compute SA missing the builder/writer roles | Apply §1c |
| Build fails: `unable to specify a machine type` | Lab/tenant project forbids custom Cloud Build workers | Do not add `machineType` to `deploy/cloudbuild.yaml` |
| Build fails: `--mount option requires BuildKit` | Cloud Build's docker builder uses the legacy engine | Avoid `RUN --mount=type=cache` in the Dockerfile |
| Streamlit session resets between messages | Requests hitting different instances | Ensure `--session-affinity` is set on `va-ui` |
| Badge shows a small document count / "sample mapping" | The GCS mapping fetch crashed, so the UI fell back to the bundled sample | Check logs for `PermissionError … '/app/data'` — the container user cannot write the mapping cache (see "Why the root `Dockerfile` is not used" in §3) |
| Admin "Sync & Re-index" fails with `403` | Runtime SA is read-only | Grant `roles/storage.objectAdmin` + `roles/discoveryengine.editor` (§1b) |
| Admin upload succeeds but other instances don't see it | `hospital_mapping.json` is cached per instance after the write-back | Re-deploy or scale to zero; GCS holds the source of truth |
| `uv` prompts for private-index credentials | Machine-level uv config enables keyring / a private index | Add the local `uv.toml` shown in §0 |

---

## Link to Cloud Run service

**https://va-ui-831213015144.europe-west4.run.app**