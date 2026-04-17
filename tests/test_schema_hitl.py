"""Spec 05: schema pipeline HITL interrupt/resume, persist, presence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from graph import FileSchemaPresence, get_compiled_graph, graph_run_config
from tests.schema_presence_stubs import NotReadySchemaPresence


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8000")


def _state_dict(out: Any) -> dict[str, Any]:
    if isinstance(out, dict):
        return out
    return dict(out.value)


@pytest.mark.asyncio
async def test_schema_path_interrupt_resume_persist(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docs = tmp_path / "schema_docs.json"
    marker = tmp_path / "schema_presence.json"
    monkeypatch.setenv("SCHEMA_DOCS_PATH", str(docs))
    monkeypatch.setenv("SCHEMA_PRESENCE_PATH", str(marker))

    class _InspectTool:
        name = "inspect_schema"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "schema_name": "public",
                "table_filter": None,
                "tables": [
                    {
                        "table_name": "actor",
                        "schema_name": "public",
                        "columns": [{"name": "actor_id", "data_type": "integer"}],
                    },
                ],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_InspectTool]:
            return [_InspectTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    cfg = graph_run_config(thread_id="schema-hitl-unit-1")
    app = get_compiled_graph(presence=NotReadySchemaPresence())

    out1 = await app.ainvoke(
        {"user_input": "ignored for routing", "steps": []},
        config=cfg,
        version="v2",
    )
    assert out1.interrupts, "expected interrupt before persist"
    st1 = _state_dict(out1)
    assert st1.get("steps") == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
    ]

    resume_tables = [
        {
            "schema": "public",
            "name": "actor",
            "description": "Approved table description.",
            "columns": [
                {"name": "actor_id", "description": "Approved column description."},
            ],
        },
    ]
    out2 = await app.ainvoke(
        Command(resume={"tables": resume_tables}),
        config=cfg,
        version="v2",
    )
    assert not out2.interrupts
    final = _state_dict(out2)
    assert final.get("steps") == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
        "schema_hitl",
        "schema_persist",
    ]
    assert final.get("gate_decision") == "schema_path"
    assert final.get("schema_ready") is True
    lr = final.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "schema_persist"
    assert lr.get("success") is True
    assert final.get("last_error") is None
    assert final.get("persist_error") is None

    assert docs.is_file()
    loaded = json.loads(docs.read_text(encoding="utf-8"))
    assert loaded.get("version") == 1
    assert loaded.get("source") == "schema_agent_hitl"
    assert loaded.get("tables") == resume_tables

    presence = FileSchemaPresence(marker)
    assert presence.check().ready is True


@pytest.mark.asyncio
async def test_inspect_schema_called_once_across_hitl_resume(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs.json"
    marker = tmp_path / "marker.json"
    monkeypatch.setenv("SCHEMA_DOCS_PATH", str(docs))
    monkeypatch.setenv("SCHEMA_PRESENCE_PATH", str(marker))

    calls: list[int] = []

    class _InspectTool:
        name = "inspect_schema"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            calls.append(1)
            return {
                "success": True,
                "schema_name": "public",
                "table_filter": None,
                "tables": [
                    {
                        "table_name": "actor",
                        "schema_name": "public",
                        "columns": [],
                    },
                ],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_InspectTool]:
            return [_InspectTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    cfg = graph_run_config(thread_id="schema-hitl-unit-2")
    app = get_compiled_graph(presence=NotReadySchemaPresence())
    await app.ainvoke({"user_input": "", "steps": []}, config=cfg, version="v2")
    await app.ainvoke(
        Command(
            resume={
                "tables": [
                    {
                        "schema": "public",
                        "name": "actor",
                        "description": "d",
                        "columns": [],
                    },
                ],
            },
        ),
        config=cfg,
        version="v2",
    )
    assert len(calls) == 1
