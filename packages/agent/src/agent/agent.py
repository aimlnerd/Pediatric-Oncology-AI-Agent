"""Entry point for `adk web`: exposes the workflow as `root_agent`."""

from agent.workflow import build_workflow

root_agent = build_workflow()
