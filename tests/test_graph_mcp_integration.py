"""Integration: LangGraph query_stub → live MCP HTTP → Postgres (docker compose)."""

from __future__ import annotations

import asyncio
import socket

import pytest
import uvicorn
from pydantic import ValidationError

from config import Settings
from graph import get_compiled_graph
from mcp_server.main import build_app


def _settings_or_skip() -> Settings:
    try:
        return Settings()
    except ValidationError:
        pytest.skip("Postgres / MCP settings missing or invalid (.env not found?)")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_query_stub_via_live_mcp_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process MCP server on a free port; graph calls execute_readonly_sql."""
    settings = _settings_or_skip()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    mcp = build_app(settings)
    starlette_app = mcp.streamable_http_app()
    config = uvicorn.Config(
        starlette_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    task: asyncio.Task[None] = asyncio.create_task(server.serve())

    async def _wait_for_server_started() -> None:
        while not server.started:
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(_wait_for_server_started(), timeout=10.0)
        monkeypatch.setenv("MCP_SERVER_URL", f"http://127.0.0.1:{port}/mcp")

        app = get_compiled_graph()
        result = await app.ainvoke({"user_input": "integration", "steps": []})

        if result.get("last_error"):
            err = result.get("last_result")
            if isinstance(err, dict):
                nested = err.get("error") or {}
                if nested.get("type") == "connection_error":
                    pytest.skip("Postgres unreachable (is docker compose up?)")
            pytest.fail(f"graph left last_error set: {result!r}")

        lr = result.get("last_result")
        assert isinstance(lr, dict)
        assert lr.get("success") is True
        assert result.get("steps") == ["query_stub"]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10.0)
