from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_query_plan
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


async def query_plan(state: QueryGraphState) -> dict[str, Any]:
    ctx = state.query.docs_context
    prefs = state.memory.preferences
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None
    try:
        plan = await build_query_plan(
            state.user_input or "",
            schema_docs_context=ctx if isinstance(ctx, dict) else None,
            preferences=prefs if isinstance(prefs, dict) else None,
            conversation_history=history_dicts,
        )
    except Exception as exc:
        msg = f"Query plan LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Query plan LLM call failed: %s", msg)
        return {"steps": ["query_plan"], "query": {"plan": {}}, "last_error": msg}

    return {"steps": ["query_plan"], "query": {"plan": plan}}
