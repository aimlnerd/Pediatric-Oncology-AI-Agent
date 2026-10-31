"""The retriever's only tool. It takes query text and nothing else from the model."""

from typing import Any

from google.adk.tools import ToolContext

from agent.citations import chunks_from_state, chunks_to_state
from agent.config import get_settings
from agent.extract import to_chunks
from agent.scope import ScopeError, resolve_scope
from agent.search import get_searcher
from agent.state import RETRIEVED_CHUNKS, SEARCH_COUNT


def search_protocols(query: str, tool_context: ToolContext) -> dict[str, Any]:
    """Search the clinical trial protocols selected by the clinician.

    Args:
        query: What to look for, in clinical terms (e.g. "hyperleukocytosis leukostasis
            management WBC > 100"). Search once per distinct aspect of the question.

    Returns:
        The retrieved passages (ids, protocol, pages, preview) or an error message.
    """
    settings = get_settings()
    state = tool_context.state
    try:
        scope = resolve_scope(state)
    except ScopeError as error:
        return {"error": str(error)}

    count = int(state.get(SEARCH_COUNT) or 0)
    if count >= settings.max_searches_per_turn:
        return {"error": "Search limit reached for this question. Stop searching."}
    state[SEARCH_COUNT] = count + 1

    hits = get_searcher().search(query, scope.protocol_ids, settings.search_page_size)

    stored = chunks_from_state(state.get(RETRIEVED_CHUNKS))
    new_chunks = to_chunks(hits, existing=stored)
    stored.update({chunk.id: chunk for chunk in new_chunks})
    state[RETRIEVED_CHUNKS] = chunks_to_state(stored)

    return {
        "searched_protocols": [p.short_title for p in scope.protocols],
        "new_passages": [
            {
                "id": c.id,
                "protocol": c.short_title,
                "pages": c.pages,
                "preview": " ".join(c.text.split())[:200],
            }
            for c in new_chunks
        ],
        "total_passages_collected": len(stored),
    }
