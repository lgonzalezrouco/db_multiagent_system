"""Shared LangGraph state for the DB multi-agent system."""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """LangGraph state: schema gate, schema HITL, query pipeline, and memory fields."""

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
    schema_docs_context: dict | None
    schema_docs_warning: str | None
    query_plan: dict | None
    generated_sql: str | None
    critic_status: str | None
    critic_feedback: str | None
    refinement_count: int
    query_execution_result: dict | None
    query_explanation: str | None

    user_id: str
    session_id: str | None
    preferences: dict | None
    preferences_dirty: bool

    previous_user_input: str | None
    previous_sql: str | None
    assumptions: list[str] | None
    recent_filters: dict | None

    memory_warning: str | None
