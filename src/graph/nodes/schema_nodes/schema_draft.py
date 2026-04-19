from __future__ import annotations

import logging
from typing import Any

from agents.schema_agent import build_schema_draft
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def schema_draft(state: GraphState) -> dict[str, Any]:
    """Build ``schema_draft`` from ``schema_metadata`` via structured LLM output."""
    steps = list(state.get("steps", []))
    steps.append("schema_draft")
    meta = state.get("schema_metadata")
    meta_dict = meta if isinstance(meta, dict) else None
    raw_prefs = state.get("preferences")
    prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    try:
        draft = await build_schema_draft(
            meta_dict,
            user_input=state.get("user_input", "") or "",
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"Schema draft LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Schema draft LLM call failed: %s", msg)
        return {"schema_draft": None, "steps": steps, "last_error": msg}

    return {"schema_draft": draft, "steps": steps}
