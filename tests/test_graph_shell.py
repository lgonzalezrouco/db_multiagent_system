"""Unit tests for LangGraph shell: compile, ainvoke, mocked MCP, logging."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from graph import get_compiled_graph
from tests.schema_presence_stubs import ReadySchemaPresence


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8000")


@pytest.fixture
def mcp_only_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")


def test_graph_compiles() -> None:
    app = get_compiled_graph()
    assert app is not None


@pytest.mark.asyncio
async def test_graph_ainvoke_smoke_mocked_mcp(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [[200]],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke({"user_input": "ping", "steps": []})

    assert result.get("steps") == ["gate:query_path", "query_stub"]
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("success") is True
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_graph_ainvoke_works_without_postgres_env_vars(
    mcp_only_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph node should not require POSTGRES_* when only using MCP client."""

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [[1]],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke({"user_input": "ping", "steps": []})

    assert result.get("steps") == ["gate:query_path", "query_stub"]
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("success") is True
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_query_stub_logs_enter_exit(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {"success": True, "rows_returned": 0, "rows": [], "columns": []}

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    with caplog.at_level(logging.INFO, logger="graph.nodes"):
        app = get_compiled_graph(presence=ReadySchemaPresence())
        await app.ainvoke({"user_input": "hello", "steps": []})

    phases = [
        r.graph_phase
        for r in caplog.records
        if getattr(r, "graph_phase", None) is not None
    ]
    assert "enter" in phases
    assert "exit" in phases
    nodes = [r.graph_node for r in caplog.records if hasattr(r, "graph_node")]
    assert all(n == "query_stub" for n in nodes)


@pytest.mark.asyncio
async def test_query_stub_clears_last_result_on_error(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: node must not allow stale last_result to persist on failure."""

    class _FakeClient:
        async def get_tools(self) -> list[Any]:
            return []

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke(
        {
            "user_input": "ping",
            "steps": [],
            "last_result": {"success": True, "rows_returned": 123},
        }
    )

    assert result.get("steps") == ["gate:query_path", "query_stub"]
    assert result.get("last_error") is not None
    assert result.get("last_result") is None
