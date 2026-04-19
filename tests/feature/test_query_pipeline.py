"""Query pipeline: critic retry loop, refinement cap, schema docs soft failure."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from agents.schemas.query_outputs import QueryCritiqueOutput, QueryExplanationOutput
from graph import get_compiled_graph, graph_run_config
from graph.nodes.query_nodes import validate_sql_for_execution
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


@pytest.fixture
def mcp_only_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")


# ---------------------------------------------------------------------------
# SQL Validation
# ---------------------------------------------------------------------------


def test_validate_sql_requires_limit_clause() -> None:
    """SQL validation rejects queries without LIMIT clause."""
    # Given: a SQL query without LIMIT
    sql_without_limit = "SELECT 1"

    # When: validating the SQL
    ok, fb = validate_sql_for_execution(sql_without_limit)

    # Then: validation fails with LIMIT feedback
    assert ok is False
    assert "LIMIT" in fb


def test_validate_sql_accepts_query_with_limit() -> None:
    """SQL validation accepts queries with LIMIT clause."""
    # Given: a SQL query with LIMIT
    sql_with_limit = "SELECT 1 LIMIT 1"

    # When: validating the SQL
    ok, _fb = validate_sql_for_execution(sql_with_limit)

    # Then: validation passes
    assert ok is True


def test_validate_sql_rejects_limit_in_comment() -> None:
    """SQL validation rejects LIMIT appearing only in comments."""
    # Given: SQL with LIMIT only in a comment
    sql = "SELECT * FROM public.actor -- LIMIT 1"

    # When: validating the SQL
    ok, fb = validate_sql_for_execution(sql)

    # Then: validation fails
    assert ok is False
    assert "LIMIT" in fb


def test_validate_sql_rejects_limit_in_string_literal() -> None:
    """SQL validation rejects LIMIT appearing only in string literals."""
    # Given: SQL with LIMIT only in a string
    sql = "SELECT * FROM public.actor WHERE note = 'LIMIT 999'"

    # When: validating the SQL
    ok, _fb = validate_sql_for_execution(sql)

    # Then: validation fails
    assert ok is False


def test_validate_sql_rejects_limit_in_block_comment() -> None:
    """SQL validation rejects LIMIT appearing only in block comments."""
    # Given: SQL with LIMIT only in a block comment
    sql = "SELECT * FROM public.actor /* LIMIT 50 */"

    # When: validating the SQL
    ok, _fb = validate_sql_for_execution(sql)

    # Then: validation fails
    assert ok is False


def test_validate_sql_accepts_real_limit_with_limit_in_literal() -> None:
    """SQL validation accepts real LIMIT even with LIMIT word in literals."""
    # Given: SQL with real LIMIT and LIMIT in string
    sql = "SELECT * FROM public.actor WHERE slug = 'LIMIT' LIMIT 10"

    # When: validating the SQL
    ok, _fb = validate_sql_for_execution(sql)

    # Then: validation passes
    assert ok is True


# ---------------------------------------------------------------------------
# Graph Compilation and Basic Invocation
# ---------------------------------------------------------------------------


def test_graph_compiles_successfully() -> None:
    """Graph compiles without errors."""
    # Given: no preconditions

    # When: compiling the graph
    app = get_compiled_graph()

    # Then: app is returned
    assert app is not None


@pytest.mark.asyncio
async def test_graph_ainvoke_completes_query_pipeline_with_mocked_mcp(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph executes full query pipeline with mocked MCP client."""

    # Given: mocked MCP client returning success
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

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="shell-smoke-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "ping", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: query pipeline completes successfully
    assert result.get("steps") == _QUERY_SUCCESS_STEPS
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_graph_works_without_postgres_env_vars(
    mcp_only_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph executes using MCP_SERVER_URL without POSTGRES_* variables."""

    # Given: only MCP_SERVER_URL set (no POSTGRES_* vars)
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

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="shell-smoke-2", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "ping", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: query pipeline completes successfully
    assert result.get("steps") == _QUERY_SUCCESS_STEPS
    assert result.get("gate_decision") == "query_path"
    assert result.get("schema_ready") is True
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_pipeline_clears_last_result_on_tool_error(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline clears stale last_result when tool execution fails."""

    # Given: mocked MCP client with no tools
    class _FakeClient:
        async def get_tools(self) -> list[Any]:
            return []

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # When: invoking with pre-existing last_result
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="shell-error-1", run_kind="pytest")
    result = await app.ainvoke(
        {
            "user_input": "ping",
            "steps": [],
            "last_result": {"kind": "query_answer", "sql": "SELECT 1"},
            **state_seed,
        },
        config=cfg,
    )

    # Then: last_result is cleared and error is set
    assert result.get("steps") == _QUERY_SUCCESS_STEPS
    assert result.get("last_error") is not None
    assert result.get("last_result") is None


# ---------------------------------------------------------------------------
# Critic Retry Loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critic_retry_then_success(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critic rejection triggers SQL regeneration which then succeeds."""

    # Given: SQL generator that fails first, succeeds second
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 200}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    calls: list[int] = []

    async def _build_sql(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        rc: int,
        **_kw: Any,
    ) -> str:
        calls.append(rc)
        if rc == 0:
            return "SELECT COUNT(*) FROM public.actor"
        return "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10"

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql"
    )
    monkeypatch.setattr(query_gen_mod, "build_sql", _build_sql)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-retry-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: retry occurred and succeeded
    assert result.get("steps") == [
        "memory_load_user",
        "gate:query_path",
        "query_load_context",
        "query_plan",
        "query_generate_sql",
        "query_critic",
        "query_generate_sql",
        "query_critic",
        "query_execute",
        "query_explain",
        "memory_update_session",
    ]
    assert int(result.get("refinement_count") or 0) == 1
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    assert calls == [0, 1]


@pytest.mark.asyncio
async def test_refinement_cap_prevents_infinite_retry(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refinement cap stops retry loop and reports error."""
    # Given: SQL generator that always produces invalid SQL
    monkeypatch.setenv("QUERY_MAX_REFINEMENTS", "3")

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("execute must not run when critic caps refinements")

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    async def _bad_sql(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        _rc: int,
        **_kw: Any,
    ) -> str:
        return "SELECT 1"

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql"
    )
    monkeypatch.setattr(query_gen_mod, "build_sql", _bad_sql)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-cap-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "never lands", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: cap is reached and error is reported
    assert result.get("steps") == [
        "memory_load_user",
        "gate:query_path",
        "query_load_context",
        "query_plan",
        "query_generate_sql",
        "query_critic",
        "query_generate_sql",
        "query_critic",
        "query_generate_sql",
        "query_critic",
        "query_refine_cap",
        "memory_update_session",
    ]
    assert result.get("last_error") == (
        "Critic rejected SQL after max refinement attempts."
    )
    assert result.get("last_result") is None


@pytest.mark.asyncio
async def test_semantic_critic_rejection_triggers_retry(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic critic rejection triggers SQL regeneration."""

    # Given: critic rejects first SQL semantically, accepts second
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 200}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    class _StructuredRunnable:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        async def ainvoke(self, _messages: list[Any]) -> Any:
            if self.kind == "critique":
                call_index = len(critique_calls)
                critique_calls.append(call_index)
                if call_index == 0:
                    return QueryCritiqueOutput(
                        verdict="reject",
                        feedback="SQL counts actors instead of rentals.",
                        risks=["Wrong table focus."],
                        assumptions=[],
                    )
                return QueryCritiqueOutput(
                    verdict="accept",
                    feedback="SQL matches the user request.",
                    risks=[],
                    assumptions=[],
                )
            if self.kind == "explain":
                return QueryExplanationOutput(
                    explanation=(
                        "The query returns a preview row with the computed count."
                    ),
                    limitations="Preview only; results may be truncated by LIMIT.",
                    follow_up_suggestions=[],
                )
            raise NotImplementedError(self.kind)

    class _FakeChatLiteLLM:
        def with_structured_output(self, schema: type[Any]) -> _StructuredRunnable:
            name = getattr(schema, "__name__", "")
            mapping = {
                "QueryCritiqueOutput": "critique",
                "QueryExplanationOutput": "explain",
            }
            if name not in mapping:
                raise NotImplementedError(name)
            return _StructuredRunnable(mapping[name])

    def _fake_create_chat_llm(
        settings: Any = None,
        *,
        temperature: float | None = None,
    ) -> _FakeChatLiteLLM:
        return _FakeChatLiteLLM()

    critique_calls: list[int] = []
    sql_calls: list[int] = []

    async def _build_sql(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        rc: int,
        **_kw: Any,
    ) -> str:
        sql_calls.append(rc)
        if rc == 0:
            return "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10"
        return "SELECT COUNT(*)::bigint AS n FROM public.rental LIMIT 10"

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql"
    )
    monkeypatch.setattr(query_gen_mod, "build_sql", _build_sql)
    monkeypatch.setattr("agents.query_agent.create_chat_llm", _fake_create_chat_llm)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(
        thread_id="query-semantic-retry-1",
        run_kind="pytest",
    )
    result = await app.ainvoke(
        {"user_input": "count rentals", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: retry occurred with correct SQL
    assert result.get("steps") == [
        "memory_load_user",
        "gate:query_path",
        "query_load_context",
        "query_plan",
        "query_generate_sql",
        "query_critic",
        "query_generate_sql",
        "query_critic",
        "query_execute",
        "query_explain",
        "memory_update_session",
    ]
    assert sql_calls == [0, 1]
    assert critique_calls == [0, 1]
    assert int(result.get("refinement_count") or 0) == 1
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("sql") == "SELECT COUNT(*)::bigint AS n FROM public.rental LIMIT 10"
    assert result.get("last_error") is None


# ---------------------------------------------------------------------------
# Schema Docs and Warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_schema_docs_sets_warning(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing schema docs sets warning and includes it in limitations."""

    # Given: memory stores with no schema docs
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 1}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    class _EmptyPrefsStore:
        def __init__(self, settings=None):
            pass

        def get(self, user_id: str) -> dict:
            from memory.preferences import default_preferences

            return default_preferences()

    class _NoSchemaDocsStore:
        def __init__(self, settings=None):
            pass

        def get_payload(self):
            return None

    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _EmptyPrefsStore)
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _NoSchemaDocsStore)
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-docs-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "smoke", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: warning is set and included in limitations
    warn = result.get("schema_docs_warning")
    assert isinstance(warn, str) and warn
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    lim = lr.get("limitations")
    assert isinstance(lim, str) and warn in lim


# ---------------------------------------------------------------------------
# MCP Error Handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_error_message_propagates_to_last_error(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP database error message is propagated to last_error."""
    # Given: MCP tool returns database error
    expected_msg = 'relation "missing_table" does not exist'

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": False,
                "error": {
                    "type": "database_error",
                    "message": expected_msg,
                },
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-mcp-err-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "broken query path", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: error message is in last_error
    assert result.get("last_error") == expected_msg
    assert result.get("last_result") is None


# ---------------------------------------------------------------------------
# Explanation Fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_falls_back_when_llm_fails(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explanation falls back to generic text when LLM fails."""

    # Given: explanation builder that raises error
    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 1}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    async def _boom(
        _user_input: str,
        _sql: str,
        *,
        query_execution_result: dict[str, Any],
        schema_docs_warning: str | None = None,
        query_plan: dict[str, Any] | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("explanation unavailable")

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)
    explain_mod = importlib.import_module("graph.nodes.query_nodes.query_explain")
    monkeypatch.setattr(explain_mod, "build_query_explanation", _boom)

    # When: invoking the graph
    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(
        thread_id="query-explain-fallback-1",
        run_kind="pytest",
    )
    result = await app.ainvoke(
        {"user_input": "show actor count", "steps": [], **state_seed},
        config=cfg,
    )

    # Then: fallback explanation is used
    assert result.get("last_error") is None
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    explanation = lr.get("explanation")
    limitations = lr.get("limitations")
    assert isinstance(explanation, str)
    assert "show actor count" in explanation
    assert isinstance(limitations, str)
    assert "Read-only SELECT with LIMIT" in limitations
