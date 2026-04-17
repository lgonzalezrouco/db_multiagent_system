"""LangGraph nodes; async for compatibility with MCP HTTP clients."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from config import MCPSettings
from graph.state import GraphState

logger = logging.getLogger(__name__)


def _inspect_schema_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Short summary for ``GraphState.last_result`` after ``inspect_schema``."""
    if not payload:
        return {"kind": "inspect_schema", "success": False, "detail": "no_payload"}
    if payload.get("success"):
        tables = payload.get("tables") or []
        return {
            "kind": "inspect_schema",
            "success": True,
            "table_count": len(tables),
        }
    err = payload.get("error")
    err_type = err.get("type", "unknown") if isinstance(err, dict) else "unknown"
    return {
        "kind": "inspect_schema",
        "success": False,
        "error_type": err_type,
    }


def _graph_debug() -> bool:
    return os.environ.get("GRAPH_DEBUG", "").lower() in ("1", "true", "yes")


def _mcp_streamable_http_url(settings: MCPSettings) -> str:
    """URL for MultiServerMCPClient (streamable HTTP ``/mcp``)."""
    if settings.mcp_server_url:
        return settings.mcp_server_url.strip().rstrip("/")
    bind = settings.mcp_host
    connect_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
    return f"http://{connect_host}:{settings.mcp_port}/mcp"


def _json_object_from_text(raw_text: str) -> dict[str, Any] | None:
    """Parse a JSON object from text.

    Returns ``None`` for non-objects/invalid JSON.
    """
    try:
        parsed: Any = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _text_from_mcp_content_blocks(raw: list[Any]) -> str | None:
    """Extract concatenated ``text`` values from MCP content blocks when present."""
    parts = [
        str(block.get("text", ""))
        for block in raw
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts) if parts else None


def _tool_result_to_dict(raw: Any) -> dict[str, Any] | None:
    """Normalize LangChain MCP tool output to a dict when possible."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return _json_object_from_text(raw)
    if isinstance(raw, list):
        combined = _text_from_mcp_content_blocks(raw)
        if combined is None:
            return None
        return _json_object_from_text(combined)
    return None


def _user_input_preview(state: GraphState, *, max_len: int = 120) -> str:
    raw = state.get("user_input", "") or ""
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3] + "..."


def _format_settings_validation_error(exc: ValidationError) -> str:
    """Make config errors actionable without leaking sensitive values."""
    fields: list[str] = []
    for err in exc.errors():
        loc = err.get("loc")
        if isinstance(loc, (tuple, list)) and loc:
            head = loc[0]
            if isinstance(head, str):
                fields.append(head)
        elif isinstance(loc, str):
            fields.append(loc)

    unique = sorted({f for f in fields if f})
    if unique:
        return (
            "Configuration error: invalid or missing settings for "
            f"{', '.join(unique)}. Set them via environment variables or .env."
        )
    return (
        "Configuration error: invalid or missing settings. "
        "Check environment variables or .env."
    )


async def get_mcp_client(settings: MCPSettings) -> MultiServerMCPClient:
    """Build an MCP client pointed at ``Settings.mcp_server_url`` (or host/port)."""
    url = _mcp_streamable_http_url(settings)
    return MultiServerMCPClient(
        {"dvdrental": {"transport": "http", "url": url}},
    )
