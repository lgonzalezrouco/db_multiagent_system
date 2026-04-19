from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_query_plan
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_plan(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_plan")
    ctx = state.get("schema_docs_context")

    raw_prefs = state.get("preferences")
    prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    try:
        plan = await build_query_plan(
            state.get("user_input", "") or "",
            schema_docs_context=ctx if isinstance(ctx, dict) else None,
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"Query plan LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Query plan LLM call failed: %s", msg)
        return {"steps": steps, "query_plan": {}, "last_error": msg}

    return {"steps": steps, "query_plan": plan}
