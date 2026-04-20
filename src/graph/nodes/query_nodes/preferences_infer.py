"""Query-pipeline node: infer whether the user wants to change a preference."""

from __future__ import annotations

from typing import Any

from agents.query_agent import infer_preferences_delta
from graph.state import QueryGraphState


async def preferences_infer(state: QueryGraphState) -> dict[str, Any]:
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
