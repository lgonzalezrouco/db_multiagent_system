from __future__ import annotations

import logging
from typing import Any

from agents.schema_agent import build_schema_draft
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def schema_draft(state: GraphState) -> dict[str, Any]:
    """Build ``schema.draft`` from ``schema.metadata`` via structured LLM output."""
    meta = state.schema.metadata
    meta_dict = meta if isinstance(meta, dict) else None
    prefs = state.memory.preferences
    prefs = prefs if isinstance(prefs, dict) else None
    try:
        draft = await build_schema_draft(
            meta_dict,
            user_input=state.user_input or "",
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"Schema draft LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Schema draft LLM call failed: %s", msg)
        return {"schema": {"draft": None}, "steps": ["schema_draft"], "last_error": msg}

    return {"schema": {"draft": draft}, "steps": ["schema_draft"]}
