#!/usr/bin/env python3
"""Simple test client script to query a deployed Vertex AI Agent Engine reasoning engine instance."""

from __future__ import annotations

import argparse
import os
import sys
import google.auth
from google.auth.transport.requests import Request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query the deployed BSPHO Agent Engine / Reasoning Engine endpoint."
    )
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-01-a76134d9a6dc"),
        help="Google Cloud Project ID.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1"),
        help="Region where Agent Engine is deployed (e.g. europe-west1, europe-west1).",
    )
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Resource ID or full resource name of the Reasoning Engine instance (e.g. 1234567890123456789).",
    )
    parser.add_argument(
        "-q", "--query",
        default="I have a patient with 150 000 WBC, what are the next steps for acute management?",
        help="Clinical question to ask.",
    )
    parser.add_argument(
        "--hospital",
        default="UZG",
        help="Selected Belgian hospital context (e.g. UZG, UZA, UZL, CHU, UCL, HUDERF).",
    )
    parser.add_argument(
        "--protocols",
        nargs="*",
        default=None,
        help="Optional subset of protocol short titles or filenames to filter on.",
    )
    parser.add_argument(
        "--subprotocols",
        action="store_true",
        help="Enable deep search into subprotocols.",
    )
    return parser.parse_args()


def call_with_agentplatform(project: str, region: str, agent_id: str, query: str, state_delta: dict) -> None:
    """Invokes Agent Engine using the official Google Cloud Agent Platform / Reasoning Engine SDK."""
    from agentplatform import Client

    print(f"Connecting to Agent Platform client ({project}, {region})...")
    client = Client(project=project, location=region)
    resource_name = agent_id if "/" in agent_id else f"reasoningEngines/{agent_id}"

    print(f"Getting Agent Engine instance: {resource_name} ...")
    runtime = client.runtimes.get(name=resource_name)

    # 1. Create a session on the Agent Engine instance
    user_id = "clinician-user"
    print(f"Creating session for user '{user_id}'...")
    session = runtime.create_session(user_id=user_id)
    session_id = session.get("id") or session.get("name") or session.get("session_id")
    print(f"Active Session ID: {session_id}")

    print("Streaming agent response:\n" + "=" * 70)
    for event in runtime.stream_query(
        message=query,
        user_id=user_id,
        session_id=session_id,
        state_delta=state_delta,
    ):
        if isinstance(event, dict):
            # Print model text parts or tool call notices
            content = event.get("content") or {}
            parts = content.get("parts", [])
            for p in parts:
                if "text" in p:
                    print(p["text"], end="", flush=True)
                elif "function_call" in p:
                    fn = p["function_call"]
                    print(f"\n[Tool Executing: {fn.get('name')}]", flush=True)
            if "error_message" in event:
                print(f"\n[Error: {event['error_message']}]", flush=True)
        else:
            print(event, end="", flush=True)
    print("\n" + "=" * 70)


def call_with_rest_api(project: str, region: str, agent_id: str, query: str, state: dict) -> None:
    """Fallback: Invokes the Vertex AI Reasoning Engines REST API directly with IAM Bearer Token."""
    import requests

    resource_id = agent_id.split("/")[-1]
    url = f"https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{region}/reasoningEngines/{resource_id}:query"

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "query": query,
            "session_id": "test-session-cli",
            "state": state,
        }
    }

    print(f"Calling REST API: {url}")
    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    if resp.status_code != 200:
        print(f"API Error ({resp.status_code}): {resp.text}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(" Agent Response (Raw REST):")
    print("=" * 70)
    print(resp.json())


def main() -> None:
    args = parse_args()
    state = {
        "selected_hospital": args.hospital,
        "selected_protocols": args.protocols,
        "include_subprotocols": args.subprotocols,
    }
    print(f"Querying Agent Engine for hospital '{args.hospital}'...")
    print(f"Prompt: {args.query}\n")
    call_with_agentplatform(args.project, args.region, args.agent_id, args.query, state)


if __name__ == "__main__":
    main()

