"""LangGraph state: separate models for schema and query graphs."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field


def append_steps(current: list[str], update: list[str] | None) -> list[str]:
    """Extend the steps list with new entries from a node update."""
    return current + (update or [])


def merge_submodel[T: BaseModel](current: T, update: BaseModel | dict | None) -> T:
    """Deep-merge a partial dict or sub-model into the current sub-model.

    Only the keys explicitly provided in *update* are overwritten; all other
    sub-fields retain their current values.
    """
    if update is None:
        return current
    if isinstance(update, dict):
        return current.model_copy(update=update)
    return current.model_copy(update=update.model_dump(exclude_unset=True))


class _BaseRunState(BaseModel):
    """Fields shared by schema and query graphs (trace + output channel)."""

    user_id: str = "default"
    session_id: str | None = None
    steps: Annotated[list[str], append_steps] = Field(default_factory=list)
    last_result: str | dict | None = None
    last_error: str | None = None


class SchemaPipelineState(BaseModel):
    """State owned by the schema pipeline (inspect → draft → HITL → persist)."""

    ready: bool = False
    metadata: dict | None = None
    draft: dict | None = None
    approved: dict | None = None
    rejected: bool = False
    hitl_prompt: dict | None = None
    persist_error: str | None = None


class SchemaGraphState(_BaseRunState):
    """State for the schema-only LangGraph."""

    schema_pipeline: Annotated[SchemaPipelineState, merge_submodel] = Field(
        default_factory=SchemaPipelineState,
    )


class QueryPipelineState(BaseModel):
    """State owned by the query pipeline (plan → SQL → critic → execute → explain)."""

    docs_context: dict | None = None
    docs_warning: str | None = None
    topic_in_scope: bool | None = None
    guardrail_reason: str | None = None
    guardrail_canned_response: str | None = None
    plan: dict | None = None
    generated_sql: str | None = None
    critic_status: str | None = None
    critic_feedback: str | None = None
    refinement_count: int = 0
    execution_result: dict[str, Any] | None = None
    explanation: str | None = None
    outcome: str | None = None


class ConversationTurn(BaseModel):
    """A single completed query turn stored in the conversation history."""

    user_input: str
    sql: str | None = None
    row_count: int | None = None
    rows_preview: list[dict] = Field(default_factory=list)
    explanation: str | None = None


class MemoryState(BaseModel):
    """State owned by the memory/session layer."""

    preferences: dict | None = None
    preferences_proposed_delta: dict | None = None
    preferences_rationale: str | None = None
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    warning: str | None = None


class QueryGraphState(_BaseRunState):
    """State for the query-only LangGraph."""

    user_input: str = ""
    query: Annotated[QueryPipelineState, merge_submodel] = Field(
        default_factory=QueryPipelineState,
    )
    memory: Annotated[MemoryState, merge_submodel] = Field(default_factory=MemoryState)
