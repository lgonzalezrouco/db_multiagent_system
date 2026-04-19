"""Helpers for ``ainvoke(..., version='v2')`` return shapes (interrupts + state)."""

from __future__ import annotations

from typing import Any


def unwrap_graph_v2(result: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    """Return ``(state_dict, interrupts)`` for v2 graph invocation results."""
    if isinstance(result, dict):
        return result, ()
    value = getattr(result, "value", None)
    interrupts = getattr(result, "interrupts", ()) or ()
    if not isinstance(value, dict):
        msg = f"unexpected graph result type: {type(result).__name__}"
        raise TypeError(msg)
    return value, interrupts
