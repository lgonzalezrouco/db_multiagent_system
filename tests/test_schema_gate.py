"""Schema-presence gate: graph branches, DbSchemaPresence, gate logging."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from graph import (
    DbSchemaPresence,
    SchemaPresenceResult,
    get_compiled_graph,
    graph_run_config,
)
from tests.schema_presence_stubs import ReadySchemaPresence

_QUERY_SUCCESS_STEPS = [
    "memory_load_user",
    "gate:query_path",
    "query_load_context",
    "query_plan",
    "query_generate_sql",
    "query_critic",
    "query_execute",
    "query_explain",
    "memory_update_session",
]


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8000")


@pytest.mark.asyncio
async def test_query_path_runs_query_pipeline_when_ready(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExecTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [[1]],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_ExecTool]:
            return [_ExecTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="gate-query-1")
    result = await app.ainvoke(
        {"user_input": "count something", "steps": [], **state_seed},
        config=cfg,
    )

    assert result.get("steps") == _QUERY_SUCCESS_STEPS
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"


def test_db_schema_presence_check_soft_fails_when_db_unreachable() -> None:
    """DbSchemaPresence.check() returns ready=False when app_memory is down."""
    presence = DbSchemaPresence.from_settings()
    result = presence.check()
    assert isinstance(result, SchemaPresenceResult)
    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_with_ready_store() -> None:
    """DbSchemaPresence.check() returns ready=True when store reports ready."""

    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()
    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_with_not_ready_store() -> None:
    """DbSchemaPresence.check() returns ready=False when store reports not ready."""

    class _NotReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return False

    presence = DbSchemaPresence(store=_NotReadyStore())
    result = presence.check()
    assert result.ready is False
    assert result.reason is not None


@pytest.mark.asyncio
async def test_gate_router_logs_decision(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _ExecTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {"success": True, "rows_returned": 0, "rows": [], "columns": []}

    class _FakeClient:
        async def get_tools(self) -> list[_ExecTool]:
            return [_ExecTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    with caplog.at_level(logging.INFO, logger="graph.graph"):
        app = get_compiled_graph(presence=ReadySchemaPresence())
        cfg, state_seed = graph_run_config(thread_id="gate-log-1")
        await app.ainvoke(
            {"user_input": "ignored for routing", "steps": [], **state_seed},
            config=cfg,
        )

    gate_records = [
        r
        for r in caplog.records
        if r.name == "graph.graph" and getattr(r, "graph_phase", None) == "gate"
    ]
    assert gate_records, "expected graph_gate_decision log"
    last = gate_records[-1]
    assert getattr(last, "gate_decision", None) == "query_path"
    assert getattr(last, "schema_ready", None) is True
