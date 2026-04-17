"""Schema-presence gate: graph branches, file marker, gate logging."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from graph import get_compiled_graph, graph_run_config
from graph.presence import FileSchemaPresence, SchemaPresenceResult
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


@pytest.mark.asyncio
async def test_query_path_runs_query_stub_when_ready(
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
    result = await app.ainvoke(
        {"user_input": "count something", "steps": []},
        config=graph_run_config(thread_id="gate-query-1"),
    )

    assert result.get("steps") == ["gate:query_path", "query_stub"]
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("success") is True


def test_file_schema_presence_ready(tmp_path: Path) -> None:
    path = tmp_path / "schema_presence.json"
    path.write_text(
        json.dumps(
            {"version": 1, "ready": True, "updated_at": "2026-01-01T00:00:00Z"},
        ),
        encoding="utf-8",
    )
    presence = FileSchemaPresence(path)
    assert presence.check() == SchemaPresenceResult(True, None)


def test_file_schema_presence_missing(tmp_path: Path) -> None:
    presence = FileSchemaPresence(tmp_path / "nope.json")
    assert presence.check() == SchemaPresenceResult(False, "missing file")


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
        await app.ainvoke(
            {"user_input": "ignored for routing", "steps": []},
            config=graph_run_config(thread_id="gate-log-1"),
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
