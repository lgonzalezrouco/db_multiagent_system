from __future__ import annotations

import logging
from typing import Any

from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_load_context(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    gate_decision = "query_path"
    steps.append(f"gate:{gate_decision}")
    steps.append("query_load_context")

    schema_docs_context: dict[str, Any] | None = state.get("schema_docs_context")
    schema_docs_warning: str | None = state.get("schema_docs_warning")

    return {
        "steps": steps,
        "gate_decision": gate_decision,
        "schema_ready": True,
        "schema_docs_context": schema_docs_context,
        "schema_docs_warning": schema_docs_warning,
        "refinement_count": 0,
        "critic_status": None,
        "critic_feedback": None,
        "generated_sql": None,
        "query_plan": None,
        "query_execution_result": None,
        "query_explanation": None,
        "last_error": None,
        "last_result": None,
    }
