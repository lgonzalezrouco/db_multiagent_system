from __future__ import annotations

from typing import Any

from agents.query_agent import classify_topic
from graph.state import QueryGraphState


async def guardrail_node(state: QueryGraphState) -> dict[str, Any]:
    prefs = (
        state.memory.preferences if isinstance(state.memory.preferences, dict) else None
    )
    ctx = (
        state.query.docs_context if isinstance(state.query.docs_context, dict) else None
    )
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None

    result = await classify_topic(
        state.user_input or "",
        schema_docs_context=ctx,
        preferences=prefs,
        conversation_history=history_dicts,
    )

    in_scope = bool(result.get("in_scope", True))
    reason = str(result.get("reason") or "").strip() or None

    return {
        "steps": ["guardrail_node"],
        "query": {
            "topic_in_scope": in_scope,
            "guardrail_reason": reason,
        },
    }
