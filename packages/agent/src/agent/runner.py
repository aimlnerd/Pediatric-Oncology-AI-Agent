"""Programmatic & CLI ADK Agent Runner for testing and UI integration."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import root_agent
from .config import HOSPITALS, ensure_vertex_env_and_auth


class OncoProtocolAgentRunner:
    """
    Stateful helper that wraps the Google ADK `Runner` and `InMemorySessionService`
    so you can test the agent programmatically or call it from the Streamlit UI.
    """

    def __init__(self, app_name: str = "bspho_oncoprotocol_app") -> None:
        ensure_vertex_env_and_auth()
        self.app_name = app_name
        self.session_service = InMemorySessionService()
        self.runner = Runner(
            agent=root_agent,
            app_name=self.app_name,
            session_service=self.session_service,
        )
        self._loop = asyncio.new_event_loop()

    async def ask_async(
        self,
        query: str,
        user_id: str = "clinician",
        session_id: str = "default_session",
        selected_hospital: str = "UZG",
        selected_protocols: list[str] | None = None,
        include_subprotocols: bool = False,
    ) -> dict[str, Any]:
        """Run a single conversational turn through the ADK agent and return answer + citations."""
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        state_updates: dict[str, Any] = {
            "selected_hospital": selected_hospital,
            "selected_protocols": selected_protocols,
            "include_subprotocols": include_subprotocols,
            "last_citations": [],
        }

        if session is None:
            session = await self.session_service.create_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=session_id,
                state=state_updates,
            )
        else:
            session.state.update(state_updates)

        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=query)],
        )

        final_text_parts: list[str] = []
        tool_calls_made: list[str] = []

        async for event in self.runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            for fc in event.get_function_calls():
                tool_calls_made.append(f"{fc.name}({fc.args})")
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        final_text_parts.append(part.text)

        updated_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        citations = (
            updated_session.state.get("last_citations", [])
            if updated_session and updated_session.state
            else []
        )

        return {
            "answer": "\n".join(final_text_parts).strip(),
            "citations": citations,
            "tool_calls": tool_calls_made,
            "selected_hospital": selected_hospital,
        }

    def ask(
        self,
        query: str,
        user_id: str = "clinician",
        session_id: str = "default_session",
        selected_hospital: str = "UZG",
        selected_protocols: list[str] | None = None,
        include_subprotocols: bool = False,
    ) -> dict[str, Any]:
        """Synchronous wrapper around `ask_async` using a persistent event loop."""
        return self._loop.run_until_complete(
            self.ask_async(
                query=query,
                user_id=user_id,
                session_id=session_id,
                selected_hospital=selected_hospital,
                selected_protocols=selected_protocols,
                include_subprotocols=include_subprotocols,
            )
        )


def _print_result(result: dict[str, Any]) -> None:
    """Pretty-print an agent turn result to the terminal."""
    if result.get("tool_calls"):
        print(f"\n[Tool Calls]: {', '.join(result['tool_calls'])}")
    print(f"\n[Assistant ({result['selected_hospital']})]:\n{result['answer']}")
    citations = result.get("citations", [])
    if citations:
        unique_tags = list(dict.fromkeys(c["citation"] for c in citations))
        print(f"\n[Retrieved Chunks ({len(citations)} total)]:")
        for tag in unique_tags[:6]:
            print(f"  - {tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Programmatic & CLI test runner for the BSPHO ADK Agent.")
    parser.add_argument(
        "-q",
        "--query",
        default="",
        help="Run a single query and exit.",
    )
    parser.add_argument(
        "--hospital",
        default="UZG",
        choices=HOSPITALS,
        help="Belgian hospital center for Tier-1 filtering (default: UZG).",
    )
    parser.add_argument(
        "--protocols",
        nargs="*",
        default=None,
        help="Optional list of protocol short titles to check (Tier-2 filter).",
    )
    parser.add_argument(
        "--subprotocols",
        action="store_true",
        help="Enable subprotocol deep search.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Start an interactive multi-turn CLI chat session.",
    )
    parser.add_argument(
        "--golden-smoke",
        action="store_true",
        help="Run a quick smoke test across Golden Dataset scenarios (Q3, Q4, Q5).",
    )
    args = parser.parse_args()

    runner = OncoProtocolAgentRunner()

    if args.golden_smoke:
        scenarios = [
            (
                "Q3 (FaR-RMS Staging / Lung Nodules)",
                "What staging investigations are required by the FAR-RMS protocol if a patient is suspected of having rhabdomyosarcoma, and when is lung biopsy indicated?",
            ),
            (
                "Q4 (150,000 WBC Hyperleukocytosis - Proactive Clarification)",
                "I have a patient with 150 000 WBC, what are the next steps for acute management?",
            ),
            (
                "Q5 (Out-of-Scope Refusal - Adult Cardiology)",
                "What is the recommended first-line antihypertensive medication for a 65-year-old male patient with a history of myocardial infarction?",
            ),
        ]
        for idx, (label, q) in enumerate(scenarios, start=1):
            print("=" * 80)
            print(f"SCENARIO {idx}: {label}")
            print(f"Query: {q}")
            res = runner.ask(
                query=q,
                session_id=f"golden_{idx}",
                selected_hospital=args.hospital,
                selected_protocols=args.protocols,
                include_subprotocols=args.subprotocols,
            )
            _print_result(res)
            print()
        return

    if args.interactive:
        print(f"=== BSPHO Pediatric Oncology Assistant (Hospital: {args.hospital}) ===")
        print("Type your clinical question (or 'exit' to quit):\n")
        while True:
            try:
                user_q = input("Clinician > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            if not user_q or user_q.lower() in {"exit", "quit", "q"}:
                break
            res = runner.ask(
                query=user_q,
                session_id="interactive_cli",
                selected_hospital=args.hospital,
                selected_protocols=args.protocols,
                include_subprotocols=args.subprotocols,
            )
            _print_result(res)
            print("-" * 60)
        return

    query = args.query or "What are the hematological and non-hematological conditions before starting dinutuximab?"
    print(f"Query ({args.hospital}): {query}")
    res = runner.ask(
        query=query,
        session_id="single_cli",
        selected_hospital=args.hospital,
        selected_protocols=args.protocols,
        include_subprotocols=args.subprotocols,
    )
    _print_result(res)


if __name__ == "__main__":
    main()

