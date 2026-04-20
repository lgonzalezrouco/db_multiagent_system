"""Schema pipeline: gate routing, HITL interrupt/resume, persist via DB store."""

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
    "preferences_infer",
    "query_plan",
    "query_generate_sql",
    "query_enforce_limit",
    "query_critic",
    "query_execute",
    "query_explain",
    "memory_update_session",
]

_PIVOT_STEPS = [
    "gate:schema_path",
    "schema_inspect",
    "schema_draft",
    "schema_hitl",
    "schema_persist",
    "memory_load_user",
    "gate:query_path",
    "query_load_context",
    "preferences_infer",
    "query_plan",
    "query_generate_sql",
    "query_enforce_limit",
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
async def test_query_path_runs_when_schema_ready(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate routes to query path when schema is ready."""

    # Given: MCP client that returns successful execution
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

    # When: invoking the graph with ready schema
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="gate-query-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count something", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: query pipeline executes
    state = unwrap_graph_v2(out)[0]
    assert state.steps == _QUERY_SUCCESS_STEPS
    assert state.gate_decision == "query_path"
    assert state.schema_pipeline.ready is True
    lr = state.last_result
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"


def test_db_schema_presence_soft_fails_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DbSchemaPresence returns not ready when app_memory DB is unreachable."""
    # Given: app_memory pointing to unreachable host
    monkeypatch.setenv("APP_MEMORY_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_MEMORY_PORT", "65535")

    # When: checking schema presence
    presence = DbSchemaPresence.from_settings()
    result = presence.check()

    # Then: result indicates not ready with reason
    assert isinstance(result, SchemaPresenceResult)
    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_returns_ready_with_ready_store() -> None:
    """DbSchemaPresence returns ready when store reports ready."""

    # Given: store that reports ready
    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    # When: checking schema presence
    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()

    # Then: result indicates ready
    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_returns_not_ready_with_not_ready_store() -> None:
    """DbSchemaPresence returns not ready when store reports not ready."""

    # Given: store that reports not ready
    class _NotReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return False

    # When: checking schema presence
    presence = DbSchemaPresence(store=_NotReadyStore())
    result = presence.check()

    # Then: result indicates not ready with reason
    assert result.ready is False
    assert result.reason is not None


@pytest.mark.asyncio
async def test_schema_path_interrupt_resume_persist(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema path interrupts for HITL and resumes to persist."""
    # Given: fake stores and MCP client
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

    # When: first invoke triggers interrupt
    out1 = await app.ainvoke(
        {"user_input": "", "steps": []},
        config=cfg,
        version="v2",
    )

    # Then: interrupt before persist
    assert out1.interrupts, "expected interrupt before persist"
    st1 = unwrap_graph_v2(out1)[0]
    assert st1.steps == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
    ]

    # When: resume with approved tables
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

    # Then: completes without further interrupts
    assert not out2.interrupts
    final = unwrap_graph_v2(out2)[0]
    assert final.steps == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
        "schema_hitl",
        "schema_persist",
    ]
    assert final.gate_decision == "schema_path"
    assert final.schema_pipeline.ready is True
    lr = final.last_result
    assert isinstance(lr, dict)
    assert lr.get("kind") == "schema_persist"
    assert lr.get("success") is True
    assert final.last_error is None
    assert final.schema_pipeline.persist_error is None

    # Verify store was called correctly
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
    """Schema inspection is only called once despite HITL resume."""
    # Given: inspection tool that tracks calls
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

    # When: invoke and resume
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

    # Then: inspection was called exactly once
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_schema_persist_pivots_to_query_when_user_input_present(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema persist with user_input pivots into query pipeline."""

    # Given: shared store for schema docs visibility
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

    _SharedSchemaDocsStore._payload = None

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

    app = get_compiled_graph(presence=NotReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="gate-pivot-1", run_kind="pytest")

    # When: first invoke with user question (triggers schema path, pauses at HITL)
    out1 = await app.ainvoke(
        {
            "user_input": "how many actors are in the database?",
            "steps": [],
            **state_seed,
        },
        config=cfg,
        version="v2",
    )

    # Then: interrupted before persist
    state1, interrupts1 = unwrap_graph_v2(out1)
    assert interrupts1, "expected HITL interrupt before schema_persist"
    assert state1.steps == [
        "gate:schema_path",
        "schema_inspect",
        "schema_draft",
    ]

    # When: user approves draft
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

    # Then: pivots to query pipeline and completes
    state2, interrupts2 = unwrap_graph_v2(out2)
    assert not interrupts2, "expected no further interrupts after HITL resume"

    assert state2.steps == _PIVOT_STEPS
    assert state2.gate_decision == "query_path"
    assert state2.schema_pipeline.ready is True
    assert state2.schema_pipeline.persist_error is None

    lr = state2.last_result
    assert isinstance(lr, dict), f"last_result should be a dict, got: {lr!r}"
    assert lr.get("kind") == "query_answer"
