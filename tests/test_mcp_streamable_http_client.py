"""Smoke-test streamable HTTP MCP client"""

from __future__ import annotations

import asyncio
import socket

import pytest


@pytest.mark.asyncio
async def test_streamable_http_lists_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-this-test")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    monkeypatch.setenv("MCP_PORT", str(port))

    import uvicorn
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from config.postgres_settings import PostgresSettings
    from mcp_server.main import build_app

    settings = PostgresSettings()
    mcp = build_app(settings)
    starlette_app = mcp.streamable_http_app()
    config = uvicorn.Config(
        starlette_app,
        host=settings.mcp_host,
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
        url = f"http://127.0.0.1:{port}/mcp"
        client = MultiServerMCPClient(
            {"dvd": {"transport": "http", "url": url}},
        )
        tools = await client.get_tools()
        names = {t.name for t in tools}
        assert "inspect_schema" in names
        assert "execute_readonly_sql" in names
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10.0)
