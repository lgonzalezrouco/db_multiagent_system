"""LangGraph workflow: schema-presence gate → schema pipeline or query pipeline."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.memory_settings import AppMemorySettings
from graph.memory_nodes import memory_load_user, memory_update_session
from graph.presence import DbSchemaPresence, SchemaPresence
from graph.query_pipeline import (
    query_critic,
    query_execute,
    query_explain,
    query_generate_sql,
    query_load_context,
    query_plan,
    query_refine_cap,
    route_after_critic,
)
from graph.schema_pipeline import (
    schema_draft,
    schema_hitl,
    schema_inspect,
    schema_persist,
)
from graph.state import GraphState

logger = logging.getLogger(__name__)


def graph_run_config(
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> tuple[RunnableConfig, dict]:
    """Return (config, initial_state_overrides) for invoke/ainvoke.

    thread_id goes into configurable (required by MemorySaver).
    user_id goes into initial state so routing never reads from configurable.
    session_id defaults to thread_id for an optional display/logging label.
    """
    s = AppMemorySettings()
    tid = thread_id or s.default_thread_id
    uid = user_id or s.default_user_id
    sid = session_id if session_id is not None else tid
    config: RunnableConfig = {"configurable": {"thread_id": tid}}
    state_seed: dict = {"user_id": uid, "session_id": sid}
    return config, state_seed


def _graph_debug() -> bool:
    return os.environ.get("GRAPH_DEBUG", "").lower() in ("1", "true", "yes")


def build_graph(*, presence: SchemaPresence | None = None) -> StateGraph:
    """Build workflow with conditional routing from ``START``."""
    resolved: SchemaPresence = presence or DbSchemaPresence.from_settings()

    def route_after_start(_state: GraphState) -> str:
        presence_result = resolved.check()
        ready = presence_result.ready
        decision = "query_path" if ready else "schema_path"
        logger.info(
            "graph_gate_decision",
            extra={
                "graph_phase": "gate",
                "gate_decision": decision,
                "schema_ready": ready,
                "presence_reason": presence_result.reason,
            },
        )
        return decision

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
    workflow.add_edge("schema_persist", END)

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
