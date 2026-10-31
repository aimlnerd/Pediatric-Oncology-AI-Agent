"""Shared pytest configuration for the UI package.

Forces the offline backends so the suite never contacts Vertex AI or GCS and
always asserts against the bundled mock mapping / canned responses.
"""

from __future__ import annotations

import os

import pytest

# Must be set before ``ui.data`` / ``ui.agent_client`` read them.
os.environ["UI_MAPPING_SOURCE"] = "mock"
os.environ["UI_AGENT_BACKEND"] = "mock"


@pytest.fixture(autouse=True)
def _reset_mapping_cache():
    """Drop the cached mapping between tests so env overrides always apply."""
    from ui.data import load_hospital_mapping

    load_hospital_mapping.cache_clear()
    yield
    load_hospital_mapping.cache_clear()
