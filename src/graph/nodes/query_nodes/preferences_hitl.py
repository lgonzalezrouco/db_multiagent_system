"""Query-pipeline node: HITL approval for a proposed preference delta."""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from graph.state import GraphState


def preferences_hitl(state: GraphState) -> dict[str, Any]:
    hitl_payload: dict[str, Any] = {
        "kind": "preferences_review",
        "current": state.memory.preferences or {},
        "proposed_delta": state.memory.preferences_proposed_delta or {},
        "rationale": state.memory.preferences_rationale or "",
    }
    resume_value = interrupt(hitl_payload)

    if (
        resume_value == "reject"
        or not isinstance(resume_value, dict)
        or not resume_value
    ):
        approved_delta = None
    else:
        approved_delta = resume_value

    return {
        "steps": ["preferences_hitl"],
        "memory": {
            "preferences_proposed_delta": approved_delta,
            "preferences_rationale": None,
        },
    }
