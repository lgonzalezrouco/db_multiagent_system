from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_sql
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_generate_sql(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_generate_sql")

    ctx = state.get("schema_docs_context")
    raw_prefs = state.get("preferences")
    prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    cf = (
        state.get("critic_feedback")
        if isinstance(state.get("critic_feedback"), str)
        else None
    )
    try:
        qp = (
            state.get("query_plan")
            if isinstance(state.get("query_plan"), dict)
            else None
        )
        sql = await build_sql(
            state.get("user_input", "") or "",
            qp,
            ctx if isinstance(ctx, dict) else None,
            int(state.get("refinement_count") or 0),
            critic_feedback=cf,
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"SQL generation LLM error: {type(exc).__name__}: {exc}"
        logger.exception("SQL generation LLM call failed: %s", msg)
        return {"steps": steps, "generated_sql": "", "last_error": msg}

    return {"steps": steps, "generated_sql": sql}
