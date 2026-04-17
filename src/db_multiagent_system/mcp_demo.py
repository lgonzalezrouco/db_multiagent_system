"""Exercise DB helpers and an MCP HTTP client against a running MCP server.

After ``docker compose up``, the ``mcp-server`` service exposes streamable HTTP
on port 8000. Point ``MCP_SERVER_URL`` (or ``MCP_HOST`` / ``MCP_PORT``) in
``.env`` at that endpoint so this script talks to the container, not Postgres
directly for tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from config import PostgresSettings
from mcp_server.readonly_sql import execute_readonly_sql, validate_readonly_sql
from mcp_server.schema_metadata import fetch_schema_metadata

logger = logging.getLogger(__name__)


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _dump(label: str, data: dict[str, Any]) -> None:
    print(label)
    print(json.dumps(data, indent=2, default=str))


def _mcp_tool_result_to_dict(raw: Any) -> dict[str, Any] | None:
    """Normalize LangChain MCP tool output (often a list of text blocks) to a dict."""
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


def _print_mcp_tool_raw(label: str, raw: Any) -> None:
    print(label)
    parsed = _mcp_tool_result_to_dict(raw)
    if parsed is not None:
        print(json.dumps(parsed, indent=2, default=str))
    else:
        print(json.dumps(raw, indent=2, default=str))


def _mcp_streamable_http_url(settings: PostgresSettings) -> str:
    """URL for MultiServerMCPClient (streamable HTTP ``/mcp`` on the running server)."""
    if settings.mcp_server_url:
        return settings.mcp_server_url.strip().rstrip("/")
    bind = settings.mcp_host
    connect_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
    return f"http://{connect_host}:{settings.mcp_port}/mcp"


async def _mcp_http_inspect_schema_demo(settings: PostgresSettings) -> int:
    """Call ``inspect_schema`` on the MCP server already bound (e.g. docker compose)."""
    url = _mcp_streamable_http_url(settings)
    print(f"MCP endpoint: {url}")
    client = MultiServerMCPClient(
        {"dvd": {"transport": "http", "url": url}},
    )
    tools = await client.get_tools()
    names = {t.name for t in tools}
    print(f"MCP tool names: {sorted(names)}")
    if "inspect_schema" not in names:
        logger.error("inspect_schema not exposed by MCP HTTP server")
        return 1
    inspect_tool = next(t for t in tools if t.name == "inspect_schema")
    raw = await inspect_tool.ainvoke(
        {"schema_name": "public", "table_name": "film"},
    )
    _print_mcp_tool_raw("inspect_schema response (via MCP HTTP):", raw)
    payload = _mcp_tool_result_to_dict(raw)
    if payload is None:
        logger.error("could not parse inspect_schema MCP response as JSON object")
        return 1
    if not payload.get("success"):
        logger.error("inspect_schema via MCP HTTP returned success=false")
        return 1
    return 0


async def run_async() -> int:
    try:
        settings = PostgresSettings()
    except ValidationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    _print_section(
        "1) MCP streamable HTTP — client to running server (inspect_schema tool)",
    )
    code = await _mcp_http_inspect_schema_demo(settings)
    if code != 0:
        return code

    _print_section("2) inspect_schema — direct fetch_schema_metadata (same handler)")
    schema_result = await fetch_schema_metadata(
        settings,
        schema_name="public",
        table_name="film",
    )
    _dump("Response:", schema_result)
    if not schema_result.get("success"):
        logger.error("inspect_schema failed (is Postgres up and .env correct?)")
        return 1

    _print_section("3) execute_readonly_sql — SELECT with LIMIT")
    sql = "SELECT film_id, title FROM film ORDER BY film_id LIMIT 3"
    select_result = await execute_readonly_sql(settings, sql)
    _dump("Response:", select_result)
    if not select_result.get("success"):
        logger.error("execute_readonly_sql (SELECT) failed")
        return 1

    _print_section("4) Read-only guard — DELETE rejected before execution")
    bad_sql = "DELETE FROM film WHERE film_id = 1"
    ok, err = validate_readonly_sql(bad_sql)
    print(f"validate_readonly_sql ok={ok}")
    if err is not None:
        _dump("Validation payload:", err)
    blocked = await execute_readonly_sql(settings, bad_sql)
    _dump("execute_readonly_sql (DELETE) response:", blocked)
    if blocked.get("success") is not False:
        logger.error("expected DELETE to be blocked")
        return 1
    err_type = (blocked.get("error") or {}).get("type")
    if err_type != "validation_error":
        logger.error("expected validation_error, got %s", err_type)
        return 1

    _print_section("mcp_demo_ok")
    print("MCP-backed features responded as expected.")
    return 0


def run() -> int:
    return asyncio.run(run_async())
