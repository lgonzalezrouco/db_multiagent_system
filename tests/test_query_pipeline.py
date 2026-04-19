"""Query pipeline: critic retry loop, refinement cap, schema docs soft failure."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from agents.schemas.query_outputs import QueryCritiqueOutput, QueryExplanationOutput
from graph import get_compiled_graph, graph_run_config
from graph.nodes.query_nodes import validate_sql_for_execution
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


def test_validate_sql_for_execution_requires_limit_and_readonly() -> None:
    ok, fb = validate_sql_for_execution("SELECT 1")
    assert ok is False
    assert "LIMIT" in fb

    ok2, _fb2 = validate_sql_for_execution("SELECT 1 LIMIT 1")
    assert ok2 is True


def test_validate_sql_limit_not_satisfied_by_comment_decoy() -> None:
    ok, fb = validate_sql_for_execution(
        "SELECT * FROM public.actor -- LIMIT 1",
    )
    assert ok is False
    assert "LIMIT" in fb


def test_validate_sql_limit_not_satisfied_by_string_literal_decoy() -> None:
    ok, fb = validate_sql_for_execution(
        "SELECT * FROM public.actor WHERE note = 'LIMIT 999'",
    )
    assert ok is False


def test_validate_sql_limit_not_satisfied_by_block_comment_decoy() -> None:
    ok, fb = validate_sql_for_execution(
        "SELECT * FROM public.actor /* LIMIT 50 */",
    )
    assert ok is False


def test_validate_sql_limit_with_literal_containing_word_limit() -> None:
    ok, _fb = validate_sql_for_execution(
        "SELECT * FROM public.actor WHERE slug = 'LIMIT' LIMIT 10",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_critic_retry_then_success(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-retry-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "count actors", "steps": [], **state_seed},
        config=cfg,
    )

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
async def test_refinement_cap_skips_mcp_execute(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-cap-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "never lands", "steps": [], **state_seed},
        config=cfg,
    )

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
async def test_missing_schema_docs_sets_warning(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema_docs_warning is set when no approved docs are in app_memory."""

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

    # Patch memory stores so preferences load cleanly but schema docs are absent
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-docs-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "smoke", "steps": [], **state_seed},
        config=cfg,
    )

    warn = result.get("schema_docs_warning")
    assert isinstance(warn, str) and warn
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    lim = lr.get("limitations")
    assert isinstance(lim, str) and warn in lim


@pytest.mark.asyncio
async def test_query_explain_uses_mcp_error_message(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="query-mcp-err-1", run_kind="pytest")
    result = await app.ainvoke(
        {"user_input": "broken query path", "steps": [], **state_seed},
        config=cfg,
    )

    assert result.get("last_error") == expected_msg
    assert result.get("last_result") is None


@pytest.mark.asyncio
async def test_semantic_critic_rejects_then_retry_succeeds(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(
        thread_id="query-semantic-retry-1",
        run_kind="pytest",
    )
    result = await app.ainvoke(
        {"user_input": "count rentals", "steps": [], **state_seed},
        config=cfg,
    )

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


@pytest.mark.asyncio
async def test_query_explain_falls_back_when_llm_fails(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(
        thread_id="query-explain-fallback-1",
        run_kind="pytest",
    )
    result = await app.ainvoke(
        {"user_input": "show actor count", "steps": [], **state_seed},
        config=cfg,
    )

    assert result.get("last_error") is None
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    explanation = lr.get("explanation")
    limitations = lr.get("limitations")
    assert isinstance(explanation, str)
    assert "show actor count" in explanation
    assert isinstance(limitations, str)
    assert "Read-only SELECT with LIMIT" in limitations
