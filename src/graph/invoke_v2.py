"""Helpers for ``ainvoke(..., version='v2')`` return shapes (interrupts + state)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from graph.state import GraphState


def unwrap_graph_v2(
    result: Any,
) -> tuple[GraphState, tuple[Any, ...]]:
    """Return ``(state, interrupts)`` for v2 graph invocation results.

    LangGraph returns either:
    - a plain ``dict`` (older behaviour / direct state seed)
    - a ``GraphOutput`` object with ``.value`` (a ``GraphState``) and ``.interrupts``
    """
    if isinstance(result, GraphState):
        return result, ()
    if isinstance(result, dict):
        return GraphState(**result), ()
    value = getattr(result, "value", None)
    interrupts = getattr(result, "interrupts", ()) or ()
    if isinstance(value, GraphState):
        return value, interrupts
    if isinstance(value, BaseModel):
        return GraphState.model_validate(value.model_dump()), interrupts
    if isinstance(value, dict):
        return GraphState(**value), interrupts
    msg = f"unexpected graph result type: {type(result).__name__}"
    raise TypeError(msg)
