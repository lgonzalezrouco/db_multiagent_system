"""Exercise the LangGraph shell: compile and ``ainvoke`` the query pipeline via MCP.

Requires a running MCP HTTP server (e.g. ``docker compose up``) and ``.env``
pointing at it, same as ``mcp_demo.py``. The graph node calls
``execute_readonly_sql`` through the MCP client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from config import MCPSettings
from graph import GraphState, get_compiled_graph, graph_run_config
from graph.presence import SchemaPresence, SchemaPresenceResult

logger = logging.getLogger(__name__)


class _GraphDemoQueryPathPresence:
    """Force the query branch so this demo exercises ``execute_readonly_sql``."""

    def check(self) -> SchemaPresenceResult:
        return SchemaPresenceResult(
            True,
            "graph_demo: force query path (marker file optional)",
        )


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _dump(label: str, data: dict[str, Any]) -> None:
    print(label)
    print(json.dumps(data, indent=2, default=str))


async def run_async() -> int:
    try:
        MCPSettings()
    except ValidationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    _print_section("LangGraph shell — compile and ainvoke (query pipeline → MCP SQL)")
    presence: SchemaPresence = _GraphDemoQueryPathPresence()
    app = get_compiled_graph(presence=presence)
    cfg, state_seed = graph_run_config(thread_id="graph-demo-query")
    initial: GraphState = {
        "user_input": "graph shell demo: count actors via MCP",
        "steps": [],
        **state_seed,
    }
    result = await app.ainvoke(initial, config=cfg)
    _dump("Graph result:", dict(result))

    last_error = result.get("last_error")
    if last_error:
        logger.error("graph run failed: %s", last_error)
        return 1

    last_result = result.get("last_result")
    if not isinstance(last_result, dict) or last_result.get("kind") != "query_answer":
        logger.error(
            "expected last_result.kind == 'query_answer', got %r",
            last_result,
        )
        return 1

    _print_section("graph_demo_ok")
    print("LangGraph shell completed; MCP-backed SELECT succeeded.")
    return 0


def run() -> int:
    return asyncio.run(run_async())
