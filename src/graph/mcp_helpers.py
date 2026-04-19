"""MCP client factory and shared helpers for graph pipelines."""

from __future__ import annotations

import json
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from config import MCPSettings
from graph.state import GraphState


def inspect_schema_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
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


def tool_result_to_dict(raw: Any) -> dict[str, Any] | None:
    """Normalize LangChain MCP tool output to a dict when possible."""

    def json_object_from_text(raw_text: str) -> dict[str, Any] | None:
        try:
            parsed: Any = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def text_from_mcp_content_blocks(raw_list: list[Any]) -> str | None:
        parts = [
            str(block.get("text", ""))
            for block in raw_list
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts) if parts else None

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json_object_from_text(raw)
    if isinstance(raw, list):
        combined = text_from_mcp_content_blocks(raw)
        if combined is None:
            return None
        return json_object_from_text(combined)
    return None


def user_input_preview(state: GraphState, *, max_len: int = 120) -> str:
    raw = state.get("user_input", "") or ""
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3] + "..."


def format_settings_validation_error(exc: ValidationError) -> str:
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

    def streamable_http_url() -> str:
        if settings.mcp_server_url:
            return settings.mcp_server_url.strip().rstrip("/")
        bind = settings.mcp_host
        connect_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
        return f"http://{connect_host}:{settings.mcp_port}/mcp"

    url = streamable_http_url()
    connections: Any = {"dvdrental": {"transport": "http", "url": url}}
    return MultiServerMCPClient(connections)
