"""Spec 05/07: schema pipeline HITL interrupt/resume, persist via DB store."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.types import Command

from graph import get_compiled_graph, graph_run_config
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


class _FakeSchemaDocsStore:
    """In-memory SchemaDocsStore stub that captures upsert_approved calls."""

    captured: list[dict[str, Any]]

    def __init__(self, settings=None) -> None:
        pass

    def upsert_approved(
        self,
        payload: dict[str, Any],
        metadata_fingerprint: str | None = None,
    ) -> None:
        _FakeSchemaDocsStore.captured.append(
            {"payload": payload, "fingerprint": metadata_fingerprint}
        )


@pytest.mark.asyncio
async def test_schema_path_interrupt_resume_persist(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSchemaDocsStore.captured = []
    schema_persist_mod = importlib.import_module(
        "graph.nodes.schema_nodes.schema_persist"
    )
    monkeypatch.setattr(schema_persist_mod, "SchemaDocsStore", _FakeSchemaDocsStore)

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

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    cfg, _ = graph_run_config(thread_id="schema-hitl-unit-1", run_kind="pytest")
    app = get_compiled_graph(presence=NotReadySchemaPresence())

    out1 = await app.ainvoke(
        {"user_input": "", "steps": []},
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

    assert len(_FakeSchemaDocsStore.captured) == 1
    stored = _FakeSchemaDocsStore.captured[0]["payload"]
    assert stored.get("version") == 1
    assert stored.get("source") == "schema_agent_hitl"
    assert stored.get("tables") == resume_tables


@pytest.mark.asyncio
async def test_inspect_schema_called_once_across_hitl_resume(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSchemaDocsStore.captured = []
    schema_persist_mod = importlib.import_module(
        "graph.nodes.schema_nodes.schema_persist"
    )
    monkeypatch.setattr(schema_persist_mod, "SchemaDocsStore", _FakeSchemaDocsStore)

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

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    cfg, _ = graph_run_config(thread_id="schema-hitl-unit-2", run_kind="pytest")
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
