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

    The resume value must be either:
    - A non-empty dict of preference keys to apply (approval, possibly edited).
    - The string ``"reject"`` to discard the proposal without persisting.

    ``None`` and empty dict are NOT valid resume values for LangGraph's
    ``interrupt()`` — use ``"reject"`` for explicit rejection.

    On resume the approved delta (or None) is stored in
    ``state.memory.preferences_proposed_delta`` so ``preferences_persist`` can
    read it.  The rationale is cleared.
    """
    hitl_payload: dict[str, Any] = {
        "kind": "preferences_review",
        "current": state.memory.preferences or {},
        "proposed_delta": state.memory.preferences_proposed_delta or {},
        "rationale": state.memory.preferences_rationale or "",
    }
    resume_value = interrupt(hitl_payload)

    # Normalise resume value: "reject" string or non-dict → treat as rejection.
    if resume_value == "reject" or not isinstance(resume_value, dict):
        approved_delta = None
    elif not resume_value:
        # Empty dict: also treated as rejection
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
