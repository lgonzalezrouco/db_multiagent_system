from __future__ import annotations

from typing import Any, Literal

from langgraph.types import interrupt

from graph.state import SchemaGraphState


def route_after_schema_hitl(state: SchemaGraphState) -> Literal["persist", "end"]:
    """After HITL: persist approved tables, or end when user rejected."""
    if state.schema_pipeline.rejected:
        return "end"
    return "persist"


def schema_hitl(state: SchemaGraphState) -> dict[str, Any]:
    """HITL: ``interrupt()`` with draft; resume sets ``approved`` or rejects."""
    draft = state.schema_pipeline.draft
    hitl_payload: dict[str, Any] = {
        "kind": "schema_review",
        "draft": draft,
    }
    approved = interrupt(hitl_payload)

    if approved == "reject":
        return {
            "schema_pipeline": {
                "approved": None,
                "rejected": True,
                "hitl_prompt": hitl_payload,
            },
            "last_result": {
                "kind": "schema_persist",
                "success": False,
                "message": "rejected by user",
            },
            "last_error": None,
            "steps": ["schema_hitl"],
        }

    return {
        "schema_pipeline": {
            "approved": approved,
            "rejected": False,
            "hitl_prompt": hitl_payload,
        },
        "steps": ["schema_hitl"],
    }
