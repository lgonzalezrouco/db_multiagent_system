"""LangGraph workflows: schema pipeline (HITL) and query pipeline (separate graphs)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.memory_settings import AppMemorySettings
from graph.memory_nodes import memory_load_user, memory_update_session
from graph.nodes.query_nodes import (
    preferences_hitl,
    preferences_infer,
    preferences_persist,
    query_critic,
    query_enforce_limit,
    query_execute,
    query_explain,
    query_generate_sql,
    query_load_context,
    query_plan,
    query_refine_cap,
    route_after_critic,
    route_after_preferences_hitl,
    route_after_preferences_infer,
)
from graph.nodes.schema_nodes import (
    route_after_schema_hitl,
    schema_draft,
    schema_hitl,
    schema_inspect,
    schema_persist,
)
from graph.state import QueryGraphState, SchemaGraphState

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


def build_schema_graph() -> StateGraph:
    """Schema agent: inspect → draft → HITL → persist (or end on reject)."""
    workflow: StateGraph = StateGraph(SchemaGraphState)
    workflow.add_node("schema_inspect", schema_inspect)
    workflow.add_node("schema_draft", schema_draft)
    workflow.add_node("schema_hitl", schema_hitl)
    workflow.add_node("schema_persist", schema_persist)

    workflow.add_edge(START, "schema_inspect")
    workflow.add_edge("schema_inspect", "schema_draft")
    workflow.add_edge("schema_draft", "schema_hitl")
    workflow.add_conditional_edges(
        "schema_hitl",
        route_after_schema_hitl,
        {"persist": "schema_persist", "end": END},
    )
    workflow.add_edge("schema_persist", END)
    return workflow


def build_query_graph() -> StateGraph:
    """Query agent: memory → preferences → plan → SQL → critic → execute → explain."""
    workflow: StateGraph = StateGraph(QueryGraphState)

    workflow.add_node("memory_load_user", memory_load_user)
    workflow.add_node("query_load_context", query_load_context)
    workflow.add_node("preferences_infer", preferences_infer)
    workflow.add_node("preferences_hitl", preferences_hitl)
    workflow.add_node("preferences_persist", preferences_persist)
    workflow.add_node("query_plan", query_plan)
    workflow.add_node("query_generate_sql", query_generate_sql)
    workflow.add_node("query_enforce_limit", query_enforce_limit)
    workflow.add_node("query_critic", query_critic)
    workflow.add_node("query_execute", query_execute)
    workflow.add_node("query_explain", query_explain)
    workflow.add_node("query_refine_cap", query_refine_cap)
    workflow.add_node("memory_update_session", memory_update_session)

    workflow.add_edge(START, "memory_load_user")
    workflow.add_edge("memory_load_user", "query_load_context")
    workflow.add_edge("query_load_context", "preferences_infer")
    workflow.add_conditional_edges(
        "preferences_infer",
        route_after_preferences_infer,
        {"preferences_hitl": "preferences_hitl", "query_plan": "query_plan"},
    )
    workflow.add_conditional_edges(
        "preferences_hitl",
        route_after_preferences_hitl,
        {"preferences_persist": "preferences_persist", "query_plan": "query_plan"},
    )
    workflow.add_edge("preferences_persist", "query_plan")
    workflow.add_edge("query_plan", "query_generate_sql")
    workflow.add_edge("query_generate_sql", "query_enforce_limit")
    workflow.add_edge("query_enforce_limit", "query_critic")
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


def get_compiled_schema_graph(*, checkpointer: Any | None = None):
    """Compile schema graph with ``MemorySaver`` by default."""
    ckpt = checkpointer if checkpointer is not None else MemorySaver()
    return build_schema_graph().compile(checkpointer=ckpt)


def get_compiled_query_graph(*, checkpointer: Any | None = None):
    """Compile query graph with ``MemorySaver`` by default."""
    ckpt = checkpointer if checkpointer is not None else MemorySaver()
    return build_query_graph().compile(checkpointer=ckpt)
