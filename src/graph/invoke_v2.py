"""Helpers for ``ainvoke(..., version='v2')`` return shapes (interrupts + state)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from graph.state import QueryGraphState, SchemaGraphState


def _unwrap_v2[S: BaseModel](
    result: Any,
    state_cls: type[S],
) -> tuple[S, tuple[Any, ...]]:
    if isinstance(result, state_cls):
        return result, ()
    if isinstance(result, dict):
        return state_cls(**result), ()
    value = getattr(result, "value", None)
    interrupts = getattr(result, "interrupts", ()) or ()
    if isinstance(value, state_cls):
        return value, interrupts
    if isinstance(value, BaseModel):
        return state_cls.model_validate(value.model_dump()), interrupts
    if isinstance(value, dict):
        return state_cls(**value), interrupts
    msg = f"unexpected graph result type: {type(result).__name__}"
    raise TypeError(msg)


def unwrap_query_graph_v2(result: Any) -> tuple[QueryGraphState, tuple[Any, ...]]:
    """Return ``(state, interrupts)`` for the query graph v2 invocation."""
    return _unwrap_v2(result, QueryGraphState)


def unwrap_schema_graph_v2(result: Any) -> tuple[SchemaGraphState, tuple[Any, ...]]:
    """Return ``(state, interrupts)`` for the schema graph v2 invocation."""
    return _unwrap_v2(result, SchemaGraphState)
