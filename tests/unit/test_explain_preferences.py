"""Unit tests for output_format, date_format, and preferred_language wiring
in query_explain node and formatters."""

from __future__ import annotations

import importlib
import json
from datetime import date, datetime
from typing import Any

import pytest

from graph.nodes.query_nodes.query_explain import (
    _apply_date_format,
    _format_date_value,
    _get_pref,
    query_explain,
)
from graph.state import GraphState, MemoryState, QueryPipelineState
from ui.formatters import format_query_answer_markdown

_explain_mod = importlib.import_module("graph.nodes.query_nodes.query_explain")


def test_get_pref_returns_default_for_none() -> None:
    assert _get_pref(None, "output_format", "table") == "table"


def test_get_pref_returns_stored_value() -> None:
    assert _get_pref({"output_format": "json"}, "output_format", "table") == "json"


def test_get_pref_falls_back_to_default_for_missing_key() -> None:
    result = _get_pref({"safety_strictness": "strict"}, "output_format", "table")
    assert result == "table"


def test_get_pref_falls_back_when_value_is_empty_string() -> None:
    assert _get_pref({"output_format": ""}, "output_format", "table") == "table"


def test_format_date_value_formats_date_object_us() -> None:
    assert _format_date_value(date(2024, 3, 15), "%m/%d/%Y") == "03/15/2024"


def test_format_date_value_formats_date_object_eu() -> None:
    assert _format_date_value(date(2024, 3, 15), "%d/%m/%Y") == "15/03/2024"


def test_format_date_value_formats_datetime_object() -> None:
    dt = datetime(2024, 6, 1, 12, 30, 0)
    assert _format_date_value(dt, "%m/%d/%Y") == "06/01/2024"


def test_format_date_value_parses_iso_date_string() -> None:
    assert _format_date_value("2024-03-15", "%m/%d/%Y") == "03/15/2024"


def test_format_date_value_parses_iso_datetime_string() -> None:
    result = _format_date_value("2024-03-15T10:30:00", "%d/%m/%Y")
    assert result == "15/03/2024"


def test_format_date_value_leaves_non_date_string_unchanged() -> None:
    assert _format_date_value("Nick Wahlberg", "%m/%d/%Y") == "Nick Wahlberg"


def test_format_date_value_leaves_integer_unchanged() -> None:
    assert _format_date_value(42, "%m/%d/%Y") == 42


def test_format_date_value_leaves_none_unchanged() -> None:
    assert _format_date_value(None, "%m/%d/%Y") is None


def test_apply_date_format_iso_is_noop() -> None:
    rows = [{"d": "2024-01-01"}]
    assert _apply_date_format(rows, "ISO8601") is rows  # same object returned


def test_apply_date_format_us_converts_date_strings() -> None:
    rows = [{"last_update": "2024-06-15", "name": "Alice"}]
    result = _apply_date_format(rows, "US")
    assert result[0]["last_update"] == "06/15/2024"
    assert result[0]["name"] == "Alice"


def test_apply_date_format_eu_converts_date_strings() -> None:
    rows = [{"d": "2024-06-15"}]
    result = _apply_date_format(rows, "EU")
    assert result[0]["d"] == "15/06/2024"


def test_apply_date_format_returns_new_list() -> None:
    rows = [{"d": "2024-01-01"}]
    result = _apply_date_format(rows, "US")
    assert result is not rows


def test_apply_date_format_preserves_non_date_values() -> None:
    rows = [{"n": 99, "s": "hello", "d": "2024-01-01"}]
    result = _apply_date_format(rows, "US")
    assert result[0]["n"] == 99
    assert result[0]["s"] == "hello"


def _make_payload(
    *,
    output_format: str = "table",
    rows: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "output_format": output_format,
        "sql": "SELECT * FROM actor LIMIT 2",
        "columns": ["actor_id", "first_name"],
        "rows": rows
        or [
            {"actor_id": 1, "first_name": "Nick"},
            {"actor_id": 2, "first_name": "Ed"},
        ],
        "explanation": "Found 2 actors.",
        "limitations": "Result may be truncated.",
    }


def test_formatter_table_format_renders_markdown_table() -> None:
    md = format_query_answer_markdown(_make_payload(output_format="table"))
    assert "| actor_id |" in md
    assert "| Nick |" in md


def test_formatter_json_format_renders_json_block() -> None:
    md = format_query_answer_markdown(_make_payload(output_format="json"))
    assert "```json" in md
    # Verify valid JSON is embedded
    json_start = md.index("```json\n") + len("```json\n")
    json_end = md.index("\n```", json_start)
    parsed = json.loads(md[json_start:json_end])
    assert isinstance(parsed, list)
    assert parsed[0]["first_name"] == "Nick"


def test_formatter_json_format_does_not_contain_markdown_table_pipe() -> None:
    md = format_query_answer_markdown(_make_payload(output_format="json"))
    # JSON block should not have a markdown table header
    assert "| actor_id |" not in md


def test_formatter_unknown_format_falls_back_to_table() -> None:
    md = format_query_answer_markdown(_make_payload(output_format="unknown"))
    assert "| actor_id |" in md


def test_formatter_missing_format_falls_back_to_table() -> None:
    payload = _make_payload()
    del payload["output_format"]
    md = format_query_answer_markdown(payload)
    assert "| actor_id |" in md


def test_formatter_max_rows_respected_in_json_mode() -> None:
    rows = [{"n": i} for i in range(10)]
    payload = {
        "output_format": "json",
        "sql": "SELECT n FROM t LIMIT 10",
        "columns": ["n"],
        "rows": rows,
        "explanation": "",
        "limitations": "",
    }
    md = format_query_answer_markdown(payload, max_rows=3)
    json_start = md.index("```json\n") + len("```json\n")
    json_end = md.index("\n```", json_start)
    parsed = json.loads(md[json_start:json_end])
    assert len(parsed) == 3
    assert "Showing first 3" in md


def test_formatter_sql_block_always_present() -> None:
    for fmt in ("table", "json"):
        md = format_query_answer_markdown(_make_payload(output_format=fmt))
        assert "```sql" in md


def test_formatter_explanation_present_in_both_formats() -> None:
    for fmt in ("table", "json"):
        md = format_query_answer_markdown(_make_payload(output_format=fmt))
        assert "Found 2 actors." in md


def _make_state(
    prefs: dict | None = None,
    rows: list[dict] | None = None,
) -> GraphState:
    return GraphState(
        user_input="list actors",
        memory=MemoryState(preferences=prefs or {}),
        query=QueryPipelineState(
            generated_sql="SELECT * FROM actor LIMIT 10",
            execution_result={
                "success": True,
                "rows_returned": 2,
                "columns": ["actor_id", "first_name"],
                "rows": rows or [{"actor_id": 1, "first_name": "Nick"}],
            },
        ),
    )


def _noop_llm_out() -> dict:
    return {
        "explanation": "stub",
        "limitations": "stub",
        "follow_up_suggestions": [],
    }


@pytest.mark.asyncio
async def test_node_forwards_output_format_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _explain_mod,
        "build_query_explanation",
        lambda *a, **k: _async({"explanation": "e", "limitations": "l"}),
    )
    state = _make_state(prefs={"output_format": "table"})
    result = await query_explain(state)
    assert result["last_result"]["output_format"] == "table"


@pytest.mark.asyncio
async def test_node_forwards_output_format_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _explain_mod,
        "build_query_explanation",
        lambda *a, **k: _async({"explanation": "e", "limitations": "l"}),
    )
    state = _make_state(prefs={"output_format": "json"})
    result = await query_explain(state)
    assert result["last_result"]["output_format"] == "json"


@pytest.mark.asyncio
async def test_node_applies_date_format_us_to_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _explain_mod,
        "build_query_explanation",
        lambda *a, **k: _async({"explanation": "e", "limitations": "l"}),
    )
    state = _make_state(
        prefs={"date_format": "US"},
        rows=[{"last_update": "2024-06-15", "name": "Alice"}],
    )
    result = await query_explain(state)
    rows = result["last_result"]["rows"]
    assert rows[0]["last_update"] == "06/15/2024"
    assert rows[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_node_iso_date_format_leaves_rows_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _explain_mod,
        "build_query_explanation",
        lambda *a, **k: _async({"explanation": "e", "limitations": "l"}),
    )
    rows = [{"last_update": "2024-06-15"}]
    state = _make_state(prefs={"date_format": "ISO8601"}, rows=rows)
    result = await query_explain(state)
    assert result["last_result"]["rows"][0]["last_update"] == "2024-06-15"


@pytest.mark.asyncio
async def test_node_defaults_output_format_to_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _explain_mod,
        "build_query_explanation",
        lambda *a, **k: _async({"explanation": "e", "limitations": "l"}),
    )
    state = _make_state(prefs={})
    result = await query_explain(state)
    assert result["last_result"]["output_format"] == "table"


def _async(value: Any):
    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner()
