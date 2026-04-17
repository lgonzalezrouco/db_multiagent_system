"""Query pipeline: critic retry loop, refinement cap, schema docs soft failure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graph import get_compiled_graph, graph_run_config
from graph.query_pipeline import validate_sql_for_execution
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

    def _build_sql(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        rc: int,
    ) -> str:
        calls.append(rc)
        if rc == 0:
            return "SELECT COUNT(*) FROM public.actor"
        return "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10"

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)
    monkeypatch.setattr("graph.query_pipeline.build_sql", _build_sql)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke(
        {"user_input": "count actors", "steps": []},
        config=graph_run_config(thread_id="query-retry-1"),
    )

    assert result.get("steps") == [
        "gate:query_path",
        "query_load_context",
        "query_plan",
        "query_generate_sql",
        "query_critic",
        "query_generate_sql",
        "query_critic",
        "query_execute",
        "query_explain",
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

    def _bad_sql(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        _rc: int,
    ) -> str:
        return "SELECT 1"

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)
    monkeypatch.setattr("graph.query_pipeline.build_sql", _bad_sql)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke(
        {"user_input": "never lands", "steps": []},
        config=graph_run_config(thread_id="query-cap-1"),
    )

    assert result.get("steps") == [
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
    ]
    assert result.get("last_error") == (
        "Critic rejected SQL after max refinement attempts."
    )
    assert result.get("last_result") is None


@pytest.mark.asyncio
async def test_missing_schema_docs_sets_warning(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no_schema_docs_here.json"
    monkeypatch.setenv("SCHEMA_DOCS_PATH", str(missing))

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

    monkeypatch.setattr("graph.nodes.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    result = await app.ainvoke(
        {"user_input": "smoke", "steps": []},
        config=graph_run_config(thread_id="query-docs-1"),
    )

    warn = result.get("schema_docs_warning")
    assert isinstance(warn, str) and warn
    lr = result.get("last_result")
    assert isinstance(lr, dict)
    assert lr.get("kind") == "query_answer"
    lim = lr.get("limitations")
    assert isinstance(lim, str) and warn in lim
