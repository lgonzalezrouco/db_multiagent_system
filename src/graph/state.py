"""Shared LangGraph state for the DB multi-agent system."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Reducers
# ---------------------------------------------------------------------------


def append_steps(current: list[str], update: list[str]) -> list[str]:
    """Extend the steps list with new entries from a node update."""
    return current + (update or [])


def merge_submodel(current: BaseModel, update: BaseModel | dict | None) -> BaseModel:
    """Deep-merge a partial dict or sub-model into the current sub-model.

    Only the keys explicitly provided in *update* are overwritten; all other
    sub-fields retain their current values.
    """
    if update is None:
        return current
    if isinstance(update, dict):
        return current.model_copy(update=update)
    return current.model_copy(update=update.model_dump(exclude_unset=True))


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class SchemaPipelineState(BaseModel):
    """State owned by the schema pipeline (inspect → draft → HITL → persist)."""

    ready: bool = False  # formerly schema_ready
    metadata: dict | None = None  # formerly schema_metadata
    draft: dict | None = None  # formerly schema_draft
    approved: dict | None = None  # formerly schema_approved
    hitl_prompt: dict | None = None
    persist_error: str | None = None


class QueryPipelineState(BaseModel):
    """State owned by the query pipeline (plan → SQL → critic → execute → explain)."""

    docs_context: dict | None = None  # formerly schema_docs_context
    docs_warning: str | None = None  # formerly schema_docs_warning
    plan: dict | None = None  # formerly query_plan
    generated_sql: str | None = None
    critic_status: str | None = None
    critic_feedback: str | None = None
    refinement_count: int = 0
    execution_result: dict | None = None  # formerly query_execution_result
    explanation: str | None = None  # formerly query_explanation


class ConversationTurn(BaseModel):
    """A single completed query turn stored in the conversation history."""

    user_input: str
    sql: str | None = None
    row_count: int | None = None
    rows_preview: list[dict] = Field(
        default_factory=list
    )  # up to 3 rows, values trimmed
    explanation: str | None = None


class MemoryState(BaseModel):
    """State owned by the memory/session layer."""

    preferences: dict | None = None
    preferences_dirty: bool = False
    conversation_history: list[ConversationTurn] = Field(
        default_factory=list
    )  # capped at 5
    warning: str | None = None  # formerly memory_warning


# ---------------------------------------------------------------------------
# Top-level graph state
# ---------------------------------------------------------------------------


class GraphState(BaseModel):
    """LangGraph state: schema gate, schema HITL, query pipeline, and memory fields."""

    user_input: str = ""
    steps: Annotated[list[str], append_steps] = Field(default_factory=list)
    gate_decision: str | None = None
    user_id: str = "default"
    session_id: str | None = None
    last_result: str | dict | None = None  # shared output channel (UI/CLI)
    last_error: str | None = None  # shared error channel  (UI/CLI)

    schema: Annotated[SchemaPipelineState, merge_submodel] = Field(
        default_factory=SchemaPipelineState
    )
    query: Annotated[QueryPipelineState, merge_submodel] = Field(
        default_factory=QueryPipelineState
    )
    memory: Annotated[MemoryState, merge_submodel] = Field(default_factory=MemoryState)
