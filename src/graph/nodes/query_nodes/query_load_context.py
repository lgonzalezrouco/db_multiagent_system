from __future__ import annotations

import logging
from typing import Any

from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


async def query_load_context(state: QueryGraphState) -> dict[str, Any]:
    return {
        "steps": ["query_load_context"],
        "memory": {
            "preferences_proposed_delta": None,
            "preferences_rationale": None,
        },
        "query": {
            "refinement_count": 0,
            "critic_status": None,
            "critic_feedback": None,
            "generated_sql": None,
            "plan": None,
            "execution_result": None,
            "explanation": None,
            "topic_in_scope": None,
            "guardrail_reason": None,
            "guardrail_canned_response": None,
            "outcome": None,
        },
        "last_error": None,
        "last_result": None,
    }
