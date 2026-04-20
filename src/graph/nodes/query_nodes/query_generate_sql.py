from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_sql
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


async def query_generate_sql(state: QueryGraphState) -> dict[str, Any]:
    ctx = state.query.docs_context
    prefs = state.memory.preferences
    cf = (
        state.query.critic_feedback
        if isinstance(state.query.critic_feedback, str)
        else None
    )
    qp = state.query.plan if isinstance(state.query.plan, dict) else None
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None
    try:
        sql = await build_sql(
            state.user_input or "",
            qp,
            ctx if isinstance(ctx, dict) else None,
            int(state.query.refinement_count or 0),
            critic_feedback=cf,
            preferences=prefs if isinstance(prefs, dict) else None,
            conversation_history=history_dicts,
        )
    except Exception as exc:
        msg = f"SQL generation LLM error: {type(exc).__name__}: {exc}"
        logger.exception("SQL generation LLM call failed: %s", msg)
        return {
            "steps": ["query_generate_sql"],
            "query": {"generated_sql": ""},
            "last_error": msg,
        }

    return {"steps": ["query_generate_sql"], "query": {"generated_sql": sql}}
