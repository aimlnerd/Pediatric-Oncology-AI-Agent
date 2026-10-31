from collections.abc import Iterator

import pytest

from agent.search import set_searcher


@pytest.fixture(autouse=True)
def _reset_searcher() -> Iterator[None]:
    yield
    set_searcher(None)
