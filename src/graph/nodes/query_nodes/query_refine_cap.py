from __future__ import annotations

import logging
from typing import Any

from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_refine_cap(state: GraphState) -> dict[str, Any]:
    msg = "Critic rejected SQL after max refinement attempts."
    logger.warning("%s", msg)

    return {
        "steps": ["query_refine_cap"],
        "last_error": msg,
        "last_result": None,
    }
