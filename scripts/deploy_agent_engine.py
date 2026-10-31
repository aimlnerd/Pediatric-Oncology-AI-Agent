#!/usr/bin/env python3
"""Lightweight deployment script for deploying the BSPHO ADK Agent to Vertex AI Agent Engine."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Workspace paths
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_PKG_DIR = REPO_ROOT / "packages" / "agent"
AGENT_SRC_DIR = AGENT_PKG_DIR / "src" / "agent"
PROCESSED_DATA_DIR = REPO_ROOT / "data" / "processed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy BSPHO ADK Agent to Vertex AI Agent Engine (Agent Runtime)."
    )
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-01-a76134d9a6dc"),
        help="Google Cloud Project ID (default: $GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="Agent Engine supported region (e.g. us-central1, europe-west1, europe-west4).",
    )
    parser.add_argument(
        "--display-name",
        default="bspho-oncoprotocol-agent",
        help="Display name for the Reasoning Engine / Agent Engine instance.",
    )
    parser.add_argument(
        "--service-account",
        default=os.getenv("AGENT_ENGINE_SERVICE_ACCOUNT"),
        help="Optional runtime service account email for Agent Engine.",
    )
    parser.add_argument(
        "--agent-engine-id",
        default=None,
        help="Optional ID of existing Agent Engine instance to update. If omitted, creates a new one.",
    )
    parser.add_argument(
        "--data-store-id",
        default=os.getenv("VERTEX_SEARCH_DATA_STORE_ID", "bspho-oncoprotocols-ds"),
        help="Vertex AI Search Data Store ID used by the agent.",
    )
    parser.add_argument(
        "--discovery-location",
        default=os.getenv("DISCOVERY_ENGINE_LOCATION", "global"),
        help="Vertex AI Search location (usually 'global').",
    )
    parser.add_argument(
        "--otel",
        action="store_true",
        default=True,
        help="Enable OpenTelemetry tracing to Google Cloud Trace (default: True).",
    )
    return parser.parse_args()


def prepare_staging_dir(staging_dir: Path, data_store_id: str, discovery_location: str, project_id: str) -> None:
    """Prepares the self-contained agent bundle that ADK packages into Agent Engine."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    # 1. Copy the source files directly into the staging root so it acts as the top-level app module
    for item in AGENT_SRC_DIR.iterdir():
        if item.name in ("__pycache__", ".adk"):
            continue
        dest = staging_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy(item, dest)

    # 2. Package processed mapping data (hospital_mapping.json) so it is bundled with the agent
    dest_data_dir = staging_dir / "data" / "processed"
    dest_data_dir.mkdir(parents=True)
    mapping_file = PROCESSED_DATA_DIR / "hospital_mapping.json"
    if mapping_file.exists():
        shutil.copy(mapping_file, dest_data_dir / "hospital_mapping.json")
        print(f"✓ Bundled {mapping_file.name} into staging payload.")
    else:
        print("! Warning: data/processed/hospital_mapping.json not found locally.")

    # 3. Create requirements.txt for Agent Engine container
    requirements_content = (
        "google-adk>=2.13.0\n"
        "google-cloud-discoveryengine>=0.13.0\n"
        "google-cloud-storage>=2.14.0\n"
        "pydantic>=2.0.0\n"
        "python-dotenv>=1.0.0\n"
        "opentelemetry-exporter-gcp-trace>=1.9.0\n"
    )
    (staging_dir / "requirements.txt").write_text(requirements_content, encoding="utf-8")

    # 4. Generate .env file for the Agent Engine runtime
    env_content = (
        f"GOOGLE_CLOUD_PROJECT={project_id}\n"
        f"GOOGLE_CLOUD_LOCATION=eu\n"
        f"VERTEX_SEARCH_DATA_STORE_ID={data_store_id}\n"
        f"DISCOVERY_ENGINE_LOCATION={discovery_location}\n"
        "GOOGLE_GENAI_USE_VERTEXAI=1\n"
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true\n"
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=true\n"
    )
    (staging_dir / ".env").write_text(env_content, encoding="utf-8")
    print(f"✓ Staging directory prepared at: {staging_dir}")


def main() -> None:
    args = parse_args()
    print("=" * 70)
    print(" Deploying BSPHO Agent to Vertex AI Agent Engine (with Tracing)")
    print("=" * 70)
    print(f" Project ID     : {args.project}")
    print(f" Region         : {args.region}")
    print(f" Display Name   : {args.display_name}")
    print(f" Data Store ID  : {args.data_store_id}")
    print(f" Cloud Tracing  : {'Enabled (OpenTelemetry)' if args.otel else 'Disabled'}")
    if args.service_account:
        print(f" Service Account: {args.service_account}")
    print("=" * 70)

    # Check for agentplatform extra in google-adk
    res = subprocess.run(
        [sys.executable, "-c", "import agentplatform"],
        capture_output=True,
    )
    if res.returncode != 0:
        print("Notice: 'agentplatform' dependency not found in current environment.")
        print("Installing `google-cloud-agentplatform[agent-engines]` for deployment...")
        subprocess.check_call(
            ["uv", "pip", "install", "google-cloud-agentplatform[agent-engines]>=2.2"],
            cwd=str(REPO_ROOT),
        )

    staging_dir = REPO_ROOT / "agent_engine_staging"
    try:
        prepare_staging_dir(
            staging_dir=staging_dir,
            data_store_id=args.data_store_id,
            discovery_location=args.discovery_location,
            project_id=args.project,
        )

        # Build adk deploy agent_engine command
        cmd = [
            "uv", "run", "adk", "deploy", "agent_engine",
            "--project", args.project,
            "--region", args.region,
            "--display_name", args.display_name,
        ]

        if args.otel:
            cmd.append("--otel_to_cloud")

        if args.service_account:
            cmd.extend(["--service_account", args.service_account])

        if args.agent_engine_id:
            cmd.extend(["--agent_engine_id", args.agent_engine_id])

        cmd.append(str(staging_dir))

        print(f"\nExecuting command:\n{' '.join(cmd)}\n")
        subprocess.check_call(cmd, cwd=str(REPO_ROOT))
        print("\n✓ Deployment completed successfully!")
        print("Traces and telemetry are viewable in Google Cloud Console -> Vertex AI -> Agent Engine -> Tracing")

    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
            print("✓ Cleaned up local staging directory.")


if __name__ == "__main__":
    main()

