from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_plan_and_preferences_delta
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


async def query_plan(state: QueryGraphState) -> dict[str, Any]:
    ctx = state.query.docs_context
    prefs = state.memory.preferences
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None
    try:
        plan, pref_delta, pref_rationale = await build_plan_and_preferences_delta(
            state.user_input or "",
            schema_docs_context=ctx if isinstance(ctx, dict) else None,
            preferences=prefs if isinstance(prefs, dict) else None,
            conversation_history=history_dicts,
        )
    except Exception:
        logger.exception("planner_failed")
        return {
            "steps": ["query_plan"],
            "query": {"plan": {}},
            "memory": {
                "preferences_proposed_delta": None,
                "preferences_rationale": None,
            },
        }

    return {
        "steps": ["query_plan"],
        "query": {"plan": plan},
        "memory": {
            "preferences_proposed_delta": pref_delta,
            "preferences_rationale": pref_rationale,
        },
    }
