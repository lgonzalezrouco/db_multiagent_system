"""Shared LangGraph state for the DB multi-agent system."""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """LangGraph state including schema-presence gate fields (Spec 04)."""

    user_input: str
    steps: list[str]
    schema_ready: bool | None
    gate_decision: str | None
    last_result: str | dict | None
    last_error: str | None
