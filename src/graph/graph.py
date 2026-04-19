"""LangGraph workflow: schema-presence gate → schema pipeline or query pipeline."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.memory_settings import AppMemorySettings
from graph.memory_nodes import memory_load_user, memory_update_session
from graph.nodes.query_nodes import (
    query_critic,
    query_execute,
    query_explain,
    query_generate_sql,
    query_load_context,
    query_plan,
    query_refine_cap,
    route_after_critic,
)
from graph.nodes.schema_nodes import (
    schema_draft,
    schema_hitl,
    schema_inspect,
    schema_persist,
)
from graph.presence import DbSchemaPresence, SchemaPresence
from graph.state import GraphState

logger = logging.getLogger(__name__)


def _merge_trace_tags(base_tags: list[str] | None, *, run_kind: str) -> list[str]:
    """Extend caller tags with defaults; dedupe while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for t in base_tags or []:
        if t not in seen:
            seen.add(t)
            out.append(t)
    for t in ("dvdrental-agent", "langgraph", run_kind):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def build_traceable_config(
    *,
    base: RunnableConfig,
    user_id: str,
    session_id: str,
    thread_id: str,
    run_kind: str = "cli",
) -> RunnableConfig:
    """Merge LangGraph/LangSmith trace fields without dropping upstream config."""
    merged_meta = {
        **(base.get("metadata") or {}),
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "run_kind": run_kind,
    }
    configurable = {**(base.get("configurable") or {}), "thread_id": thread_id}
    tags = _merge_trace_tags(base.get("tags"), run_kind=run_kind)
    return {
        **base,
        "configurable": configurable,
        "metadata": merged_meta,
        "tags": tags,
    }


def graph_run_config(
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    run_kind: str = "cli",
) -> tuple[RunnableConfig, dict]:
    """Return (config, initial_state_overrides) for invoke/ainvoke.

    thread_id goes into configurable (required by MemorySaver).
    user_id goes into initial state so routing never reads from configurable.
    session_id defaults to thread_id for an optional display/logging label.

    ``metadata`` and ``tags`` are set for LangSmith filtering (including ``run_kind``
    for cli vs pytest, etc.).
    """
    s = AppMemorySettings()
    tid = thread_id or s.default_thread_id
    uid = user_id or s.default_user_id
    sid = session_id if session_id is not None else tid
    base: RunnableConfig = {"configurable": {"thread_id": tid}}
    config = build_traceable_config(
        base=base,
        user_id=uid,
        session_id=sid,
        thread_id=tid,
        run_kind=run_kind,
    )
    state_seed: dict = {"user_id": uid, "session_id": sid}
    return config, state_seed


def route_after_persist(state: GraphState) -> str:
    """After schema_persist: pivot to query pipeline if a user query is waiting."""
    if state.get("persist_error"):
        logger.warning(
            "schema_to_query_pivot skipped: persist_error=%r",
            state.get("persist_error"),
        )
        return "end"
    if (state.get("user_input") or "").strip():
        return "query_path"
    return "end"


def build_graph(*, presence: SchemaPresence | None = None) -> StateGraph:
    """Build workflow with conditional routing from ``START``."""
    resolved: SchemaPresence = presence or DbSchemaPresence.from_settings()

    def route_after_start(_state: GraphState) -> str:
        presence_result = resolved.check()
        ready = presence_result.ready
        return "query_path" if ready else "schema_path"

    workflow: StateGraph = StateGraph(GraphState)

    # Schema path nodes
    workflow.add_node("schema_inspect", schema_inspect)
    workflow.add_node("schema_draft", schema_draft)
    workflow.add_node("schema_hitl", schema_hitl)
    workflow.add_node("schema_persist", schema_persist)

    # Query path nodes
    workflow.add_node("memory_load_user", memory_load_user)
    workflow.add_node("query_load_context", query_load_context)
    workflow.add_node("query_plan", query_plan)
    workflow.add_node("query_generate_sql", query_generate_sql)
    workflow.add_node("query_critic", query_critic)
    workflow.add_node("query_execute", query_execute)
    workflow.add_node("query_explain", query_explain)
    workflow.add_node("query_refine_cap", query_refine_cap)
    workflow.add_node("memory_update_session", memory_update_session)

    workflow.add_conditional_edges(
        START,
        route_after_start,
        {
            "schema_path": "schema_inspect",
            "query_path": "memory_load_user",
        },
    )

    # Schema path edges
    workflow.add_edge("schema_inspect", "schema_draft")
    workflow.add_edge("schema_draft", "schema_hitl")
    workflow.add_edge("schema_hitl", "schema_persist")
    workflow.add_conditional_edges(
        "schema_persist",
        route_after_persist,
        {"query_path": "memory_load_user", "end": END},
    )

    # Query path edges
    workflow.add_edge("memory_load_user", "query_load_context")
    workflow.add_edge("query_load_context", "query_plan")
    workflow.add_edge("query_plan", "query_generate_sql")
    workflow.add_edge("query_generate_sql", "query_critic")
    workflow.add_conditional_edges(
        "query_critic",
        route_after_critic,
        {
            "execute": "query_execute",
            "retry": "query_generate_sql",
            "cap": "query_refine_cap",
        },
    )
    workflow.add_edge("query_execute", "query_explain")
    workflow.add_edge("query_explain", "memory_update_session")
    workflow.add_edge("query_refine_cap", "memory_update_session")
    workflow.add_edge("memory_update_session", END)

    return workflow


def get_compiled_graph(
    *,
    presence: SchemaPresence | None = None,
    checkpointer: Any | None = None,
):
    """Return a compiled graph with ``MemorySaver`` by default (required for HITL)."""
    ckpt = checkpointer if checkpointer is not None else MemorySaver()
    return build_graph(presence=presence).compile(checkpointer=ckpt)
