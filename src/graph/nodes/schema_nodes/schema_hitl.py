from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from graph.state import GraphState

logger = logging.getLogger(__name__)


def schema_hitl(state: GraphState) -> dict[str, Any]:
    """Dynamic HITL: ``interrupt()`` with draft; on resume, set ``schema.approved``.

    Metadata and draft are produced by prior nodes and already in checkpointed
    state, so this node stays safe to re-enter from the top.
    """
    draft = state.schema.draft
    hitl_payload: dict[str, Any] = {
        "kind": "schema_review",
        "draft": draft,
    }
    approved = interrupt(hitl_payload)
    return {
        "schema": {"approved": approved, "hitl_prompt": hitl_payload},
        "steps": ["schema_hitl"],
    }
