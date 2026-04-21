"""MCP client factory and shared helpers for graph pipelines."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from config import MCPSettings

_CLIENTS_BY_URL: dict[str, MultiServerMCPClient] = {}
_CLIENT_LOCK = asyncio.Lock()
_TOOLS_BY_CLIENT_ID: dict[int, list[Any]] = {}
_TOOLS_LOCK = asyncio.Lock()


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
    """Return a cached MCP client for the configured server URL."""

    def streamable_http_url() -> str:
        if settings.mcp_server_url:
            return settings.mcp_server_url.strip().rstrip("/")
        bind = settings.mcp_host
        connect_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
        return f"http://{connect_host}:{settings.mcp_port}/mcp"

    url = streamable_http_url()
    async with _CLIENT_LOCK:
        cached = _CLIENTS_BY_URL.get(url)
        if cached is not None:
            return cached
        connections: Any = {"dvdrental": {"transport": "http", "url": url}}
        client = MultiServerMCPClient(connections)
        _CLIENTS_BY_URL[url] = client
        return client


async def get_mcp_tools(settings: MCPSettings) -> list[Any]:
    """Return cached MCP tools for the resolved client instance."""
    client = await get_mcp_client(settings)
    cache_key = id(client)
    async with _TOOLS_LOCK:
        cached = _TOOLS_BY_CLIENT_ID.get(cache_key)
        if cached is not None:
            return cached
        tools = await client.get_tools()
        _TOOLS_BY_CLIENT_ID[cache_key] = tools
        return tools


async def get_mcp_tool(settings: MCPSettings, *, name: str) -> Any | None:
    """Get a named MCP tool (cached tool listing)."""
    tools = await get_mcp_tools(settings)
    return next((t for t in tools if getattr(t, "name", None) == name), None)
