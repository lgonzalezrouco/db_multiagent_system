"""Compile the linear LangGraph shell"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes import query_stub
from graph.state import GraphState


def build_graph() -> StateGraph:
    """Linear workflow: a single MCP-backed query stub."""
    workflow: StateGraph = StateGraph(GraphState)
    workflow.add_node("query_stub", query_stub)
    workflow.add_edge(START, "query_stub")
    workflow.add_edge("query_stub", END)
    return workflow


def get_compiled_graph():
    """Return a compiled graph ready for ``ainvoke`` / ``invoke``."""
    return build_graph().compile()
