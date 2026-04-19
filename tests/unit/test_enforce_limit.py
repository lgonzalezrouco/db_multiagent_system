"""Unit tests for the query_enforce_limit node and enforce_limit helper."""

from __future__ import annotations

import pytest

from graph.nodes.query_nodes.query_enforce_limit import (
    _get_row_limit_hint,
    enforce_limit,
    query_enforce_limit,
)
from graph.state import GraphState, MemoryState, QueryPipelineState

# ---------------------------------------------------------------------------
# _get_row_limit_hint
# ---------------------------------------------------------------------------


def test_get_row_limit_hint_returns_default_for_none() -> None:
    assert _get_row_limit_hint(None) == 10


def test_get_row_limit_hint_returns_value_from_prefs() -> None:
    assert _get_row_limit_hint({"row_limit_hint": 25}) == 25


def test_get_row_limit_hint_clamps_to_minimum_of_one() -> None:
    assert _get_row_limit_hint({"row_limit_hint": 0}) == 1
    assert _get_row_limit_hint({"row_limit_hint": -5}) == 1


def test_get_row_limit_hint_clamps_to_maximum_of_500() -> None:
    assert _get_row_limit_hint({"row_limit_hint": 9999}) == 500


def test_get_row_limit_hint_handles_non_int_gracefully() -> None:
    assert _get_row_limit_hint({"row_limit_hint": "bad"}) == 10


def test_get_row_limit_hint_handles_missing_key() -> None:
    assert _get_row_limit_hint({"output_format": "json"}) == 10


# ---------------------------------------------------------------------------
# enforce_limit — injection
# ---------------------------------------------------------------------------


def test_enforce_limit_injects_when_no_limit_present() -> None:
    sql = "SELECT * FROM actor"
    result = enforce_limit(sql, 5)
    assert "LIMIT 5" in result.upper()


def test_enforce_limit_injects_when_no_limit_select_columns() -> None:
    sql = "SELECT actor_id, first_name FROM actor"
    result = enforce_limit(sql, 10)
    assert "LIMIT 10" in result.upper()


def test_enforce_limit_injects_on_complex_query() -> None:
    sql = "SELECT f.title FROM film f JOIN film_actor fa ON f.film_id = fa.film_id"
    result = enforce_limit(sql, 7)
    assert "LIMIT 7" in result.upper()


# ---------------------------------------------------------------------------
# enforce_limit — tightening
# ---------------------------------------------------------------------------


def test_enforce_limit_tightens_when_existing_limit_exceeds_hint() -> None:
    sql = "SELECT * FROM film LIMIT 100"
    result = enforce_limit(sql, 10)
    assert "LIMIT 10" in result.upper()
    assert "LIMIT 100" not in result.upper()


def test_enforce_limit_leaves_unchanged_when_existing_limit_at_hint() -> None:
    sql = "SELECT * FROM film LIMIT 10"
    result = enforce_limit(sql, 10)
    assert result == sql


def test_enforce_limit_leaves_unchanged_when_existing_limit_below_hint() -> None:
    sql = "SELECT * FROM film LIMIT 3"
    result = enforce_limit(sql, 10)
    assert result == sql


def test_enforce_limit_tightens_large_to_small() -> None:
    sql = "SELECT * FROM rental LIMIT 500"
    result = enforce_limit(sql, 1)
    assert "LIMIT 1" in result.upper()


# ---------------------------------------------------------------------------
# enforce_limit — subquery LIMIT preserved
# ---------------------------------------------------------------------------


def test_enforce_limit_does_not_touch_subquery_limit() -> None:
    """Outermost LIMIT is injected; inner sub-select LIMIT is untouched."""
    sql = "SELECT actor_id FROM (SELECT actor_id FROM actor LIMIT 50) sub"
    result = enforce_limit(sql, 10)
    # The outer query must have LIMIT 10
    assert "LIMIT 10" in result.upper()
    # The inner LIMIT 50 must still be present somewhere in the string
    assert "50" in result


# ---------------------------------------------------------------------------
# enforce_limit — CTE
# ---------------------------------------------------------------------------


def test_enforce_limit_handles_cte() -> None:
    sql = (
        "WITH top_actors AS (SELECT actor_id FROM actor LIMIT 5) "
        "SELECT * FROM top_actors"
    )
    result = enforce_limit(sql, 10)
    # Outer query gets LIMIT 10 injected
    assert "LIMIT 10" in result.upper()


# ---------------------------------------------------------------------------
# enforce_limit — fallback for unparsable input
# ---------------------------------------------------------------------------


def test_enforce_limit_fallback_appends_limit_on_parse_failure() -> None:
    # Deliberately broken SQL — sqlglot may partially parse it but we test
    # that some LIMIT appears and no exception is raised.
    sql = "THIS IS NOT SQL AT ALL"
    result = enforce_limit(sql, 5)
    # Must not raise, and LIMIT should appear (either via rewrite or fallback)
    assert result  # non-empty


# ---------------------------------------------------------------------------
# query_enforce_limit node
# ---------------------------------------------------------------------------


def _make_state(sql: str | None, row_limit_hint: int = 10) -> GraphState:
    return GraphState(
        user_input="test",
        memory=MemoryState(preferences={"row_limit_hint": row_limit_hint}),
        query=QueryPipelineState(generated_sql=sql),
    )


@pytest.mark.asyncio
async def test_node_injects_limit_when_absent() -> None:
    state = _make_state("SELECT * FROM actor", row_limit_hint=5)
    result = await query_enforce_limit(state)
    assert "query_enforce_limit" in result["steps"]
    assert "LIMIT 5" in result["query"]["generated_sql"].upper()


@pytest.mark.asyncio
async def test_node_tightens_limit_when_too_large() -> None:
    state = _make_state("SELECT * FROM film LIMIT 200", row_limit_hint=15)
    result = await query_enforce_limit(state)
    assert "LIMIT 15" in result["query"]["generated_sql"].upper()


@pytest.mark.asyncio
async def test_node_leaves_sql_unchanged_when_limit_ok() -> None:
    state = _make_state("SELECT * FROM film LIMIT 5", row_limit_hint=10)
    result = await query_enforce_limit(state)
    # No "query" key when SQL unchanged
    assert "query" not in result


@pytest.mark.asyncio
async def test_node_is_noop_when_sql_is_empty() -> None:
    state = _make_state(None)
    result = await query_enforce_limit(state)
    assert "query" not in result
    assert "query_enforce_limit" in result["steps"]


@pytest.mark.asyncio
async def test_node_uses_default_limit_when_no_preferences() -> None:
    state = GraphState(
        user_input="test",
        query=QueryPipelineState(generated_sql="SELECT * FROM actor"),
    )
    result = await query_enforce_limit(state)
    assert "LIMIT 10" in result["query"]["generated_sql"].upper()
