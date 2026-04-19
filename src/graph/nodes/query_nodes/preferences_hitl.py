"""Query-pipeline node: HITL approval for a proposed preference delta."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from graph.state import GraphState

logger = logging.getLogger(__name__)


def preferences_hitl(state: GraphState) -> dict[str, Any]:
    """Pause the graph so the user can approve, edit, or reject the proposed delta.

    The interrupt payload sent to the UI is::

        {
            "kind": "preferences_review",
            "current": {...},          # full current prefs
            "proposed_delta": {...},   # keys the LLM wants to change
            "rationale": "..."         # LLM explanation
        }

    The resume value is the *approved* delta dict (possibly modified by the user),
    or ``None`` / ``{}`` to reject without persisting anything.

    On resume the approved delta is stored in
    ``state.memory.preferences_proposed_delta`` so ``preferences_persist`` can
    read it.  The rationale is cleared.
    """
    hitl_payload: dict[str, Any] = {
        "kind": "preferences_review",
        "current": state.memory.preferences or {},
        "proposed_delta": state.memory.preferences_proposed_delta or {},
        "rationale": state.memory.preferences_rationale or "",
    }
    approved_delta = interrupt(hitl_payload)

    # Normalise: None or empty dict both mean "reject"
    if not approved_delta:
        approved_delta = None

    return {
        "steps": ["preferences_hitl"],
        "memory": {
            "preferences_proposed_delta": approved_delta,
            "preferences_rationale": None,
        },
    }
