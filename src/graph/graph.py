"""LangGraph workflow: schema-presence gate → schema pipeline or query_stub."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes import query_stub
from graph.presence import FileSchemaPresence, SchemaPresence
from graph.schema_pipeline import (
    schema_draft,
    schema_hitl,
    schema_inspect,
    schema_persist,
)
from graph.state import GraphState

logger = logging.getLogger(__name__)


def graph_run_config(*, thread_id: str = "default-thread") -> RunnableConfig:
    """``config`` for ``invoke`` / ``ainvoke`` (checkpointer requires ``thread_id``)."""
    return {"configurable": {"thread_id": thread_id}}


def _graph_debug() -> bool:
    return os.environ.get("GRAPH_DEBUG", "").lower() in ("1", "true", "yes")


def build_graph(*, presence: SchemaPresence | None = None) -> StateGraph:
    """Build workflow with conditional routing from ``START``"""
    resolved: SchemaPresence = presence or FileSchemaPresence.from_env()

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
        if _graph_debug() and isinstance(resolved, FileSchemaPresence):
            logger.debug(
                "graph_gate_debug",
                extra={
                    "graph_phase": "gate_debug",
                    "schema_presence_path": str(resolved.path),
                },
            )
        return decision

    workflow: StateGraph = StateGraph(GraphState)
    workflow.add_node("schema_inspect", schema_inspect)
    workflow.add_node("schema_draft", schema_draft)
    workflow.add_node("schema_hitl", schema_hitl)
    workflow.add_node("schema_persist", schema_persist)
    workflow.add_node("query_stub", query_stub)
    workflow.add_conditional_edges(
        START,
        route_after_start,
        {
            "schema_path": "schema_inspect",
            "query_path": "query_stub",
        },
    )
    workflow.add_edge("schema_inspect", "schema_draft")
    workflow.add_edge("schema_draft", "schema_hitl")
    workflow.add_edge("schema_hitl", "schema_persist")
    workflow.add_edge("schema_persist", END)
    workflow.add_edge("query_stub", END)
    return workflow


def get_compiled_graph(
    *,
    presence: SchemaPresence | None = None,
    checkpointer: Any | None = None,
):
    """Return a compiled graph with ``MemorySaver`` by default (required for HITL)."""
    ckpt = checkpointer if checkpointer is not None else MemorySaver()
    return build_graph(presence=presence).compile(checkpointer=ckpt)
