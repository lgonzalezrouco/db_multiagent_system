"""Integration tests against live dvdrental (docker compose)."""

import pytest
from pydantic import ValidationError

from config import Settings
from mcp_server.readonly_sql import execute_readonly_sql
from mcp_server.schema_metadata import fetch_schema_metadata


def _settings_or_skip() -> Settings:
    try:
        return Settings()
    except ValidationError:
        pytest.skip("Postgres settings missing/invalid (.env not found?)")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_inspect_schema_film_table() -> None:
    settings = _settings_or_skip()
    result = await fetch_schema_metadata(
        settings,
        schema_name="public",
        table_name="film",
    )
    if not result.get("success"):
        pytest.skip("Postgres unreachable (is docker compose up running?)")
    tables = result.get("tables", [])
    assert len(tables) == 1
    assert tables[0]["table_name"] == "film"
    col_names = {c["name"] for c in tables[0]["columns"]}
    assert "title" in col_names
    assert "film_id" in col_names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execute_readonly_select() -> None:
    settings = _settings_or_skip()
    sql = "SELECT film_id, title FROM film ORDER BY film_id LIMIT 3"
    result = await execute_readonly_sql(settings, sql)
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
async def test_execute_readonly_sql_rejects_forbidden_before_db() -> None:
    settings = _settings_or_skip()
    sql = "DELETE FROM film WHERE film_id = 1"
    result = await execute_readonly_sql(settings, sql)
    assert result["success"] is False
    assert result["error"]["type"] == "validation_error"
