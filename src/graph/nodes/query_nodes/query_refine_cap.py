from __future__ import annotations

import logging
from typing import Any

from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_refine_cap(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_refine_cap")

    msg = "Critic rejected SQL after max refinement attempts."
    logger.warning("%s", msg)

    return {
        "steps": steps,
        "last_error": msg,
        "last_result": None,
    }
