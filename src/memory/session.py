from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def seed_session_fields(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return state deltas to initialise short-term session fields."""
    return {
        "previous_user_input": state.get("previous_user_input"),
        "previous_sql": state.get("previous_sql"),
        "assumptions": state.get("assumptions"),
        "recent_filters": state.get("recent_filters"),
    }


def snapshot_session_fields(state: Mapping[str, Any]) -> dict[str, Any]:
    """Extract session fields from completed run to carry into next turn."""
    last = state.get("last_result") or {}
    return {
        "previous_user_input": state.get("user_input"),
        "previous_sql": last.get("sql") if isinstance(last, dict) else None,
        "assumptions": state.get("assumptions"),
        "recent_filters": state.get("recent_filters"),
    }
