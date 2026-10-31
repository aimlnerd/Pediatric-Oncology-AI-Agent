#!/usr/bin/env bash
# Shared deployment variables for the BSPHO Virtual Assistant.
# Usage:  source deploy/env.sh
#
# Every gcloud command in SINGLE_CLOUD_RUN_DEPLOYMENT.md assumes these are exported.

# --- Project & region ---------------------------------------------------
export PROJECT_ID="qwiklabs-gcp-01-a76134d9a6dc"
export PROJECT_NUMBER="831213015144"
export REGION="europe-west4"          # Cloud Run + Agent Runtime + Artifact Registry

# --- Vertex AI ----------------------------------------------------------
# Gemini is called through the Vertex global endpoint; switch to $REGION only
# if you need data-residency pinning AND the model is served there.
export GOOGLE_CLOUD_LOCATION="global"
export AGENT_MODEL_NAME="gemini-3.8-flash"

# --- Vertex AI Search (Discovery Engine) --------------------------------
# Existing data stores in this project:
#   global/bspho-oncoprotocols-ds   <- used by the agent
#   eu/bspho-protocols-eu           <- EU-resident variant
export DISCOVERY_ENGINE_LOCATION="global"
export VERTEX_SEARCH_DATA_STORE_ID="bspho-oncoprotocols-ds"

# --- Cloud Storage ------------------------------------------------------
export GCS_BUCKET_NAME="use-cases-${PROJECT_ID}"
export GCS_PREFIX="uc1"

# --- Deployment targets -------------------------------------------------
export AR_REPO="va"                        # Artifact Registry repository
export RUNTIME_SA="va-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
export UI_SERVICE="va-ui"                  # Cloud Run: Streamlit clinician UI
