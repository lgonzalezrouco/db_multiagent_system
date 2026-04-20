"""Schema graph: HITL interrupt/resume, reject path, persist via DB store."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.types import Command

from graph import (
    DbSchemaPresence,
    SchemaPresenceResult,
    get_compiled_schema_graph,
    graph_run_config,
)
from graph.invoke_v2 import unwrap_schema_graph_v2


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8000")


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


def test_db_schema_presence_soft_fails_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DbSchemaPresence returns not ready when app_memory DB is unreachable."""
    monkeypatch.setenv("APP_MEMORY_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_MEMORY_PORT", "65535")

    presence = DbSchemaPresence.from_settings()
    result = presence.check()

    assert isinstance(result, SchemaPresenceResult)
    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_returns_ready_with_ready_store() -> None:
    """DbSchemaPresence returns ready when store reports ready."""

    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()

    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_returns_not_ready_with_not_ready_store() -> None:
    """DbSchemaPresence returns not ready when store reports not ready."""

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
async def test_schema_path_interrupt_resume_persist(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema graph interrupts for HITL and resumes to persist."""
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

    cfg, state_seed = graph_run_config(
        thread_id="schema-hitl-unit-1",
        run_kind="pytest",
    )
    app = get_compiled_schema_graph()

    out1 = await app.ainvoke(
        {"steps": [], **state_seed},
        config=cfg,
        version="v2",
    )

    assert out1.interrupts, "expected interrupt before persist"
    st1 = unwrap_schema_graph_v2(out1)[0]
    assert st1.steps == [
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
    final = unwrap_schema_graph_v2(out2)[0]
    assert final.steps == [
        "schema_inspect",
        "schema_draft",
        "schema_hitl",
        "schema_persist",
    ]
    assert final.schema_pipeline.ready is True
    lr = final.last_result
    assert isinstance(lr, dict)
    assert lr.get("kind") == "schema_persist"
    assert lr.get("success") is True
    assert final.last_error is None
    assert final.schema_pipeline.persist_error is None
    assert final.schema_pipeline.rejected is False

    assert len(_FakeSchemaDocsStore.captured) == 1
    stored = _FakeSchemaDocsStore.captured[0]["payload"]
    assert stored.get("version") == 1
    assert stored.get("source") == "schema_agent_hitl"
    assert stored.get("tables") == resume_tables


@pytest.mark.asyncio
async def test_schema_hitl_reject_skips_persist(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject sentinel ends graph without calling SchemaDocsStore."""
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

    cfg, state_seed = graph_run_config(thread_id="schema-reject-1", run_kind="pytest")
    app = get_compiled_schema_graph()

    out1 = await app.ainvoke(
        {"steps": [], **state_seed},
        config=cfg,
        version="v2",
    )
    assert out1.interrupts

    out2 = await app.ainvoke(
        Command(resume="reject"),
        config=cfg,
        version="v2",
    )
    assert not out2.interrupts
    final = unwrap_schema_graph_v2(out2)[0]
    assert "schema_persist" not in final.steps
    assert final.schema_pipeline.rejected is True
    assert _FakeSchemaDocsStore.captured == []
    lr = final.last_result
    assert isinstance(lr, dict)
    assert lr.get("kind") == "schema_persist"
    assert lr.get("success") is False


@pytest.mark.asyncio
async def test_inspect_schema_called_once_across_hitl_resume(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema inspection is only called once despite HITL resume."""
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

    cfg, state_seed = graph_run_config(
        thread_id="schema-hitl-unit-2",
        run_kind="pytest",
    )
    app = get_compiled_schema_graph()

    await app.ainvoke({"steps": [], **state_seed}, config=cfg, version="v2")
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
