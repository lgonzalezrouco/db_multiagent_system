"""Schema-presence gate: graph branches, DbSchemaPresence."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.types import Command

from graph import (
    DbSchemaPresence,
    SchemaPresenceResult,
    get_compiled_graph,
    graph_run_config,
)
from graph.invoke_v2 import unwrap_graph_v2
from memory.preferences import default_preferences
from tests.schema_presence_stubs import NotReadySchemaPresence, ReadySchemaPresence

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

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="gate-query-1", run_kind="pytest")
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


def test_db_schema_presence_check_soft_fails_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DbSchemaPresence.check() returns ready=False when app_memory is down."""
    monkeypatch.setenv("APP_MEMORY_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_MEMORY_PORT", "65535")
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


_PIVOT_STEPS = [
    "gate:schema_path",
    "schema_inspect",
    "schema_draft",
    "schema_hitl",
    "schema_persist",
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


@pytest.mark.asyncio
async def test_schema_persist_pivots_to_query_when_user_input_present(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema_persist with non-empty user_input pivots into the query pipeline."""

    # --- Shared in-memory SchemaDocsStore (used by both schema_persist and
    #     memory_load_user so that docs written by persist are visible on read) ---
    class _SharedSchemaDocsStore:
        _payload: dict | None = None

        def __init__(self, settings=None) -> None:
            pass

        def upsert_approved(
            self,
            payload: dict,
            metadata_fingerprint: str | None = None,
        ) -> None:
            _SharedSchemaDocsStore._payload = payload

        def get_payload(self) -> dict | None:
            return _SharedSchemaDocsStore._payload

        def is_ready(self) -> bool:
            return _SharedSchemaDocsStore._payload is not None

    _SharedSchemaDocsStore._payload = None  # reset between test runs

    class _FakePrefsStore:
        def __init__(self, settings=None) -> None:
            pass

        def get(self, user_id: str) -> dict[str, Any]:
            return default_preferences()

    schema_persist_mod = importlib.import_module(
        "graph.nodes.schema_nodes.schema_persist"
    )
    memory_nodes_mod = importlib.import_module("graph.memory_nodes")
    monkeypatch.setattr(schema_persist_mod, "SchemaDocsStore", _SharedSchemaDocsStore)
    monkeypatch.setattr(memory_nodes_mod, "SchemaDocsStore", _SharedSchemaDocsStore)
    monkeypatch.setattr(memory_nodes_mod, "UserPreferencesStore", _FakePrefsStore)

    # --- MCP client serving both schema inspection and SQL execution ---
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
                    }
                ],
            }

    class _ExecTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"count": 200}],
                "columns": ["count"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[Any]:
            return [_InspectTool(), _ExecTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # --- Graph setup ---
    app = get_compiled_graph(presence=NotReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="gate-pivot-1", run_kind="pytest")

    # Turn 1: schema path starts, pauses at HITL for user approval
    out1 = await app.ainvoke(
        {
            "user_input": "how many actors are in the database?",
            "steps": [],
            **state_seed,
        },
        config=cfg,
        version="v2",
    )
    state1, interrupts1 = unwrap_graph_v2(out1)
    assert interrupts1, "expected HITL interrupt before schema_persist"
    assert state1.get("steps") == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
    ]

    # Turn 2: user approves draft → schema_persist → route_after_persist pivots to
    #          memory_load_user → full query pipeline runs
    resume_tables = [
        {
            "schema": "public",
            "name": "actor",
            "description": "Stores actor records.",
            "columns": [{"name": "actor_id", "description": "Primary key."}],
        }
    ]
    out2 = await app.ainvoke(
        Command(resume={"tables": resume_tables}),
        config=cfg,
        version="v2",
    )
    state2, interrupts2 = unwrap_graph_v2(out2)
    assert not interrupts2, "expected no further interrupts after HITL resume"

    assert state2.get("steps") == _PIVOT_STEPS
    assert state2.get("gate_decision") == "query_path"
    assert state2.get("schema_ready") is True
    assert state2.get("persist_error") is None

    lr = state2.get("last_result")
    assert isinstance(lr, dict), f"last_result should be a dict, got: {lr!r}"
    assert lr.get("kind") == "query_answer"
