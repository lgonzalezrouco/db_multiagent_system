"""Shared LangGraph state for the DB multi-agent system."""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """LangGraph state: schema gate and schema HITL fields."""

    user_input: str
    steps: list[str]
    schema_ready: bool | None
    gate_decision: str | None
    last_result: str | dict | None
    last_error: str | None
    schema_metadata: dict | None
    schema_draft: dict | None
    schema_approved: dict | None
    hitl_prompt: dict | None
    persist_error: str | None
