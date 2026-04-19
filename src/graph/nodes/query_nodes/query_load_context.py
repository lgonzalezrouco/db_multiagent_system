from __future__ import annotations

import logging
from typing import Any

from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_load_context(state: GraphState) -> dict[str, Any]:
    gate_decision = "query_path"

    return {
        "steps": [f"gate:{gate_decision}", "query_load_context"],
        "gate_decision": gate_decision,
        "schema": {"ready": True},
        "query": {
            "refinement_count": 0,
            "critic_status": None,
            "critic_feedback": None,
            "generated_sql": None,
            "plan": None,
            "execution_result": None,
            "explanation": None,
        },
        "last_error": None,
        "last_result": None,
    }
