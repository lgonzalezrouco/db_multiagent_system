"""Integration tests: MCP server + live database (docker compose)."""

from __future__ import annotations

import asyncio
import socket

import pytest
import uvicorn
from pydantic import ValidationError

from config import PostgresSettings, Settings
from graph import get_compiled_graph, graph_run_config
from graph.invoke_v2 import unwrap_graph_v2
from mcp_server.main import build_app
from mcp_server.readonly_sql import execute_readonly_sql
from mcp_server.schema_metadata import fetch_schema_metadata
from tests.schema_presence_stubs import ReadySchemaPresence


def _postgres_settings_or_skip() -> PostgresSettings:
    try:
        return PostgresSettings()
    except ValidationError:
        pytest.skip("Postgres / MCP settings missing or invalid (.env not found?)")


def _settings_or_skip() -> Settings:
    try:
        return Settings()
    except ValidationError:
        pytest.skip("Postgres settings missing/invalid (.env not found?)")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_query_pipeline_via_live_mcp_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full query pipeline executes through live MCP HTTP server."""
    # Given: in-process MCP server on free port
    settings = _postgres_settings_or_skip()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    mcp = build_app(settings)
    starlette_app = mcp.streamable_http_app()
    config = uvicorn.Config(
        starlette_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    task: asyncio.Task[None] = asyncio.create_task(server.serve())

    async def _wait_for_server_started() -> None:
        while not server.started:
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(_wait_for_server_started(), timeout=10.0)
        monkeypatch.setenv("MCP_SERVER_URL", f"http://127.0.0.1:{port}/mcp")

        # When: invoking the graph
        app = get_compiled_graph(presence=ReadySchemaPresence())
        cfg, state_seed = graph_run_config(
            thread_id="mcp-integration-1", run_kind="pytest"
        )
        out = await app.ainvoke(
            {"user_input": "integration", "steps": [], **state_seed},
            config=cfg,
        )

        # Then: query answer is returned
        state, _ = unwrap_graph_v2(out)
        if state.last_error:
            qer = state.query.execution_result
            nested: dict = {}
            if isinstance(qer, dict):
                err = qer.get("error")
                if isinstance(err, dict):
                    nested = err
            if nested.get("type") == "connection_error":
                pytest.skip("Postgres unreachable (is docker compose up?)")
            pytest.fail(f"graph left last_error set: {state!r}")

        lr = state.last_result
        assert isinstance(lr, dict)
        assert lr.get("kind") == "query_answer"
        assert state.steps == [
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
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_inspect_schema_returns_film_table_columns() -> None:
    """Schema inspection returns table metadata for film table."""
    # Given: live database connection
    settings = _settings_or_skip()

    # When: fetching schema metadata for film table
    result = await fetch_schema_metadata(
        settings,
        schema_name="public",
        table_name="film",
    )

    # Then: film table columns are returned
    if not result.get("success"):
        err = result.get("error", {})
        if err.get("type") == "connection_error":
            pytest.skip("Postgres unreachable (is docker compose up running?)")
        raise AssertionError(result)
    tables = result.get("tables", [])
    assert len(tables) == 1
    assert tables[0]["table_name"] == "film"
    col_names = {c["name"] for c in tables[0]["columns"]}
    assert "title" in col_names
    assert "film_id" in col_names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execute_readonly_sql_returns_results() -> None:
    """Readonly SQL execution returns query results."""
    # Given: live database connection
    settings = _settings_or_skip()
    sql = "SELECT film_id, title FROM film ORDER BY film_id LIMIT 3"

    # When: executing readonly SQL
    result = await execute_readonly_sql(settings, sql)

    # Then: results are returned
    if not result.get("success"):
        err = result.get("error", {})
        if err.get("type") == "connection_error":
            pytest.skip("Postgres unreachable (is docker compose up running?)")
        raise AssertionError(result)
    assert result["rows_returned"] <= 3
    assert "film_id" in result["columns"]
    assert len(result["rows"]) <= 3


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execute_readonly_sql_rejects_mutations_before_db() -> None:
    """Readonly SQL rejects mutation queries before hitting database."""
    # Given: live database connection
    settings = _settings_or_skip()
    sql = "DELETE FROM film WHERE film_id = 1"

    # When: attempting to execute mutation
    result = await execute_readonly_sql(settings, sql)

    # Then: validation error is returned (not DB error)
    assert result["success"] is False
    assert result["error"]["type"] == "validation_error"
