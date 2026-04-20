from __future__ import annotations

import logging
from typing import Any

from agents.schema_agent import build_schema_draft
from graph.state import SchemaGraphState

logger = logging.getLogger(__name__)


async def schema_draft(state: SchemaGraphState) -> dict[str, Any]:
    """Build ``schema_pipeline.draft`` from ``schema_pipeline.metadata`` via LLM."""
    meta = state.schema_pipeline.metadata
    meta_dict = meta if isinstance(meta, dict) else None
    try:
        draft = await build_schema_draft(
            meta_dict,
            user_input="",
            preferences=None,
        )
    except Exception as exc:
        msg = f"Schema draft LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Schema draft LLM call failed: %s", msg)
        return {
            "schema_pipeline": {"draft": None},
            "steps": ["schema_draft"],
            "last_error": msg,
        }

    return {"schema_pipeline": {"draft": draft}, "steps": ["schema_draft"]}
