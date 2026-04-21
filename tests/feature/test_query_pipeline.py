"""Feature tests for migrated query pipeline topology."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from graph import get_compiled_query_graph, graph_run_config
from graph.invoke_v2 import unwrap_query_graph_v2

_QUERY_SUCCESS_STEPS = [
    "memory_load_user",
    "query_load_context",
    "guardrail_node",
    "query_plan",
    "query_generate_sql",
    "query_enforce_limit",
    "query_critic",
    "query_execute",
    "query_explain",
    "persist_prefs_node",
]


class _FakeTool:
    name = "execute_readonly_sql"

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls = 0

    async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
        idx = min(self.calls, len(self.payloads) - 1)
        self.calls += 1
        return self.payloads[idx]


class _FakeClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.tool = _FakeTool(payloads)

    async def get_tools(self) -> list[_FakeTool]:
        return [self.tool]


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")


def _setup_mcp(
    monkeypatch: pytest.MonkeyPatch, payloads: list[dict[str, Any]]
) -> _FakeClient:
    client = _FakeClient(payloads)

    async def _fake_client(_settings: Any) -> _FakeClient:
        return client

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)
    return client


@pytest.mark.asyncio
async def test_off_topic_question_short_circuits(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _setup_mcp(monkeypatch, [{"success": True, "rows": [], "columns": []}])
    guardrail_mod = importlib.import_module("graph.nodes.query_nodes.guardrail")

    async def _guardrail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "in_scope": False,
            "reason": "not dvdrental",
            "canned_response": "",
            "used_llm": True,
        }

    monkeypatch.setattr(guardrail_mod, "classify_topic", _guardrail)
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="off-topic-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "weather in madrid", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert state.steps == [
        "memory_load_user",
        "query_load_context",
        "guardrail_node",
        "off_topic_node",
        "persist_prefs_node",
    ]
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "off_topic"
    assert client.tool.calls == 0


@pytest.mark.asyncio
async def test_valid_query_success_path(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mcp(
        monkeypatch,
        [{"success": True, "rows_returned": 1, "rows": [{"n": 1}], "columns": ["n"]}],
    )
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="query-success-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert state.steps == _QUERY_SUCCESS_STEPS
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_answer"


@pytest.mark.asyncio
async def test_validator_retry_then_success(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mcp(
        monkeypatch,
        [{"success": True, "rows_returned": 1, "rows": [{"n": 2}], "columns": ["n"]}],
    )
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql"
    )

    async def _sql(*args: Any, **kwargs: Any) -> str:
        rc = kwargs.get("refinement_count")
        if rc is None and len(args) >= 4:
            rc = args[3]
        if int(rc or 0) == 0:
            return "DROP TABLE public.actor"
        return "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10"

    monkeypatch.setattr(query_gen_mod, "build_sql", _sql)
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="validator-retry-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert state.steps.count("query_generate_sql") == 2
    assert state.query.refinement_count == 1
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_answer"


@pytest.mark.asyncio
async def test_validator_max_attempts_produces_failure_explanation(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUERY_MAX_REFINEMENTS", "3")
    _setup_mcp(monkeypatch, [{"success": True, "rows": [], "columns": []}])
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql"
    )

    async def _always_bad(*args: Any, **kwargs: Any) -> str:
        return "DELETE FROM public.actor"

    monkeypatch.setattr(query_gen_mod, "build_sql", _always_bad)
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="validator-cap-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_failure"
    assert state.last_result.get("subtype") == "max_attempts"


@pytest.mark.asyncio
async def test_db_execution_error_regenerates_then_succeeds(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mcp(
        monkeypatch,
        [
            {
                "success": False,
                "error": {"type": "database_error", "message": "column x not found"},
            },
            {"success": True, "rows_returned": 1, "rows": [{"n": 2}], "columns": ["n"]},
        ],
    )
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="db-retry-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert state.steps.count("query_execute") == 2
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_answer"


@pytest.mark.asyncio
async def test_db_execution_error_cap_produces_db_failure(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUERY_MAX_REFINEMENTS", "2")
    _setup_mcp(
        monkeypatch,
        [
            {
                "success": False,
                "error": {"type": "database_error", "message": "relation missing"},
            },
            {
                "success": False,
                "error": {"type": "database_error", "message": "relation missing"},
            },
        ],
    )
    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="db-cap-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert state.query.refinement_count == 2
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_failure"
    assert state.last_result.get("subtype") == "db_failure"


@pytest.mark.asyncio
async def test_persist_prefs_failure_does_not_break_response(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mcp(
        monkeypatch,
        [{"success": True, "rows_returned": 1, "rows": [{"n": 1}], "columns": ["n"]}],
    )
    plan_mod = importlib.import_module("graph.nodes.query_nodes.query_plan")

    async def _plan(*args: Any, **kwargs: Any):
        return ({"intent": "lookup"}, {"output_format": "json"}, "rationale")

    monkeypatch.setattr(plan_mod, "build_plan_and_preferences_delta", _plan)

    class _BoomStore:
        def __init__(self, settings=None) -> None:
            pass

        def patch(self, user_id: str, delta: dict) -> dict:
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "graph.nodes.query_nodes.persist_prefs.UserPreferencesStore", _BoomStore
    )

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="persist-fail-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert isinstance(state.last_result, dict)
    assert state.last_result.get("kind") == "query_answer"
    assert state.last_error is None
    assert state.memory.warning == "could not persist preferences"


@pytest.mark.asyncio
async def test_persist_prefs_still_records_session_history_when_prefs_persist_fails(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mcp(
        monkeypatch,
        [{"success": True, "rows_returned": 1, "rows": [{"n": 1}], "columns": ["n"]}],
    )
    plan_mod = importlib.import_module("graph.nodes.query_nodes.query_plan")

    async def _plan(*args: Any, **kwargs: Any):
        return ({"intent": "lookup"}, {"output_format": "json"}, "rationale")

    monkeypatch.setattr(plan_mod, "build_plan_and_preferences_delta", _plan)

    class _BoomStore:
        def __init__(self, settings=None) -> None:
            pass

        def patch(self, user_id: str, delta: dict) -> dict:
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "graph.nodes.query_nodes.persist_prefs.UserPreferencesStore", _BoomStore
    )

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="persist-history-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **seed}, config=cfg
    )
    state = unwrap_query_graph_v2(out)[0]
    assert len(state.memory.conversation_history) >= 1
