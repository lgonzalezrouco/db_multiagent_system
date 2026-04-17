"""LangGraph workflow: schema-presence gate → schema_stub or query_stub."""

from __future__ import annotations

import logging
import os

from langgraph.graph import END, START, StateGraph

from graph.nodes import query_stub, schema_stub
from graph.presence import FileSchemaPresence, SchemaPresence
from graph.state import GraphState

logger = logging.getLogger(__name__)


def _graph_debug() -> bool:
    return os.environ.get("GRAPH_DEBUG", "").lower() in ("1", "true", "yes")


def build_graph(*, presence: SchemaPresence | None = None) -> StateGraph:
    """Build workflow with conditional routing from ``START`` (Spec 04)."""
    resolved: SchemaPresence = presence or FileSchemaPresence.from_env()

    def route_after_start(_state: GraphState) -> str:
        ready = resolved.is_ready()
        decision = "query_path" if ready else "schema_path"
        logger.info(
            "graph_gate_decision",
            extra={
                "graph_phase": "gate",
                "gate_decision": decision,
                "schema_ready": ready,
                "presence_reason": resolved.reason(),
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
    workflow.add_node("schema_stub", schema_stub)
    workflow.add_node("query_stub", query_stub)
    workflow.add_conditional_edges(
        START,
        route_after_start,
        {
            "schema_path": "schema_stub",
            "query_path": "query_stub",
        },
    )
    workflow.add_edge("schema_stub", END)
    workflow.add_edge("query_stub", END)
    return workflow


def get_compiled_graph(*, presence: SchemaPresence | None = None):
    """Return a compiled graph ready for ``ainvoke`` / ``invoke``."""
    return build_graph(presence=presence).compile()
