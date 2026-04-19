"""Query-pipeline node: infer whether the user wants to change a preference."""

from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import infer_preferences_delta
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def preferences_infer(state: GraphState) -> dict[str, Any]:
    """Call the preferences-inference LLM and store the proposed delta in state.

    Runs every turn before ``query_plan``.  Returns a no-op update
    (``proposed_delta=None``) when the user input contains no persistent
    preference-change intent, so the downstream router skips HITL.

    Never raises: any LLM error produces a null delta (soft-fail inside
    ``infer_preferences_delta``).
    """
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None

    result = await infer_preferences_delta(
        state.user_input or "",
        current_preferences=state.memory.preferences,
        conversation_history=history_dicts,
    )

    return {
        "steps": ["preferences_infer"],
        "memory": {
            "preferences_proposed_delta": result.proposed_delta,
            "preferences_rationale": result.rationale,
        },
    }
