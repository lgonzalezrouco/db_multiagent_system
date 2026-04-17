"""LangGraph nodes; async for compatibility with MCP HTTP clients."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import Settings
from graph.state import GraphState

logger = logging.getLogger(__name__)

_STUB_SQL = "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 1"


def _graph_debug() -> bool:
    return os.environ.get("GRAPH_DEBUG", "").lower() in ("1", "true", "yes")


def _mcp_streamable_http_url(settings: Settings) -> str:
    """URL for MultiServerMCPClient (streamable HTTP ``/mcp``)."""
    if settings.mcp_server_url:
        return settings.mcp_server_url.strip().rstrip("/")
    bind = settings.mcp_host
    connect_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
    return f"http://{connect_host}:{settings.mcp_port}/mcp"


def _tool_result_to_dict(raw: Any) -> dict[str, Any] | None:
    """Normalize LangChain MCP tool output to a dict when possible."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        if parts:
            combined = "".join(parts)
            try:
                parsed: Any = json.loads(combined)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
            return None
    if isinstance(raw, str):
        try:
            p = json.loads(raw)
            return p if isinstance(p, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _user_input_preview(state: GraphState, *, max_len: int = 120) -> str:
    raw = state.get("user_input", "") or ""
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3] + "..."


async def get_mcp_client(settings: Settings) -> MultiServerMCPClient:
    """Build an MCP client pointed at ``Settings.mcp_server_url`` (or host/port)."""
    url = _mcp_streamable_http_url(settings)
    return MultiServerMCPClient(
        {"dvdrental": {"transport": "http", "url": url}},
    )


async def query_stub(state: GraphState) -> dict[str, Any]:
    """Run a fixed safe SELECT via MCP ``execute_readonly_sql``."""
    steps = list(state.get("steps", []))
    settings = Settings()

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "query_stub",
            "graph_phase": "enter",
            "user_input_preview": _user_input_preview(state),
            "steps_count": len(steps),
        },
    )
    if _graph_debug():
        logger.debug(
            "graph_node_debug_snapshot",
            extra={
                "graph_node": "query_stub",
                "graph_phase": "enter_debug",
                "state_keys": sorted(state.keys()),
            },
        )

    steps.append("query_stub")
    out: dict[str, Any] = {"steps": steps, "last_error": None}

    try:
        client = await get_mcp_client(settings)
        tools = await client.get_tools()
        exec_tool = next((t for t in tools if t.name == "execute_readonly_sql"), None)
        if exec_tool is None:
            msg = "MCP tool execute_readonly_sql not found"
            out["last_error"] = msg
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "query_stub",
                    "graph_phase": "exit",
                    "mcp_status": "error",
                    "steps_count": len(steps),
                    "result_summary": "tool_missing",
                },
            )
            return out

        raw = await exec_tool.ainvoke({"sql": _STUB_SQL})
        payload = _tool_result_to_dict(raw)
        if payload and payload.get("success"):
            rows = payload.get("rows_returned", 0)
            out["last_result"] = {
                "success": True,
                "rows_returned": rows,
            }
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "query_stub",
                    "graph_phase": "exit",
                    "mcp_status": "success",
                    "steps_count": len(steps),
                    "result_summary": f"rows_returned={rows}",
                },
            )
        else:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            err_type = (
                err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            )
            out["last_error"] = (
                f"MCP execute_readonly_sql failed ({err_type})"
                if payload
                else "could not parse MCP tool result"
            )
            out["last_result"] = payload
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "query_stub",
                    "graph_phase": "exit",
                    "mcp_status": "error",
                    "steps_count": len(steps),
                    "result_summary": f"mcp_error_type={err_type}",
                },
            )
    except OSError as exc:
        out["last_error"] = f"MCP connection error: {type(exc).__name__}"
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "query_stub",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": "connection_error",
            },
        )
    except Exception as exc:
        exc_name = type(exc).__name__
        out["last_error"] = f"Unexpected error: {exc_name}"
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "query_stub",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": exc_name,
            },
        )

    return out
