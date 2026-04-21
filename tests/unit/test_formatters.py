"""Unit tests for UI formatting helpers."""

from __future__ import annotations

import pytest

from graph.state import QueryGraphState
from ui.formatters import (
    default_schema_edit_json,
    format_query_answer_markdown,
    format_query_execute_preview_markdown,
    format_schema_persist_markdown,
    format_turn_state,
    schema_resume_from_inputs,
)


def test_format_query_answer_markdown_includes_sql_table_and_explanation() -> None:
    """Query answer markdown includes SQL, result table, and explanation."""
    # Given: a query answer result
    result = {
        "kind": "query_answer",
        "sql": "SELECT 1 AS n",
        "columns": ["n"],
        "rows": [{"n": 1}],
        "explanation": "One row.",
    }

    # When: formatting as markdown
    md = format_query_answer_markdown(result)

    # Then: all components are present
    assert "SELECT 1" in md
    assert "| n |" in md
    assert "One row." in md


def test_format_query_answer_markdown_truncates_at_max_rows() -> None:
    """Query answer markdown shows truncation message when exceeding max rows."""
    # Given: query result with 5 rows
    rows = [{"i": i} for i in range(5)]
    result = {"sql": "SELECT i", "columns": ["i"], "rows": rows}

    # When: formatting with max_rows=2
    md = format_query_answer_markdown(result, max_rows=2)

    # Then: truncation message is shown
    assert "Showing first 2 of 5" in md


def test_format_query_answer_markdown_default_shows_100_rows_without_note() -> None:
    """Default display cap is high enough for typical LIMIT queries (e.g. 100)."""
    rows = [{"i": i} for i in range(100)]
    result = {"sql": "SELECT i FROM t LIMIT 100", "columns": ["i"], "rows": rows}
    md = format_query_answer_markdown(result)
    assert "Showing first" not in md
    assert "| 99 |" in md


def test_format_query_execute_preview_markdown_shows_partial_header() -> None:
    """Execute preview renders partial header with SQL and rows."""
    md = format_query_execute_preview_markdown(
        sql="SELECT 1 AS n",
        execution_result={
            "success": True,
            "columns": ["n"],
            "rows": [{"n": 1}],
        },
    )
    assert md is not None
    assert "Partial result" in md
    assert "SELECT 1 AS n" in md
    assert "| n |" in md


def test_format_query_execute_preview_markdown_none_for_failure() -> None:
    """Execute preview is omitted when execution result failed."""
    md = format_query_execute_preview_markdown(
        sql="SELECT 1 AS n",
        execution_result={"success": False},
    )
    assert md is None


def test_format_turn_state_displays_error_when_present() -> None:
    """Turn state shows error message when last_error is set."""
    # Given: state with error
    state = QueryGraphState(last_error="MCP down", last_result=None)

    # When: formatting turn state
    text = format_turn_state(state)

    # Then: error is displayed
    assert "MCP down" in text


def test_format_turn_state_displays_query_answer() -> None:
    """Turn state formats query answer result."""
    # Given: state with query answer
    state = QueryGraphState(
        last_error=None,
        last_result={
            "kind": "query_answer",
            "sql": "SELECT 1",
            "columns": [],
            "rows": [],
        },
    )

    # When: formatting turn state
    text = format_turn_state(state)

    # Then: SQL is displayed
    assert "SELECT 1" in text


def test_format_schema_persist_markdown_success_plural_tables() -> None:
    """Schema persist success message uses plural for multiple tables."""
    # Given: successful persist with multiple tables
    result = {"kind": "schema_persist", "success": True, "table_count": 22}

    # When: formatting as markdown
    md = format_schema_persist_markdown(result)

    # Then: plural form and count are shown
    assert "22" in md
    assert "tables" in md
    assert "```json" not in md


def test_format_schema_persist_markdown_success_singular_table() -> None:
    """Schema persist success message uses singular for one table."""
    # Given: successful persist with one table
    result = {"kind": "schema_persist", "success": True, "table_count": 1}

    # When: formatting as markdown
    md = format_schema_persist_markdown(result)

    # Then: singular form and count are shown
    assert "1" in md
    assert "table." in md or "table " in md


def test_format_turn_state_displays_schema_persist() -> None:
    """Turn state formats schema persist result without raw JSON."""
    # Given: state with schema persist result
    state = QueryGraphState(
        last_error=None,
        last_result={
            "kind": "schema_persist",
            "success": True,
            "table_count": 3,
        },
    )

    # When: formatting turn state
    text = format_turn_state(state)

    # Then: count is shown without raw JSON
    assert "3" in text
    assert "```json" not in text


def test_schema_resume_from_inputs_reject_returns_sentinel() -> None:
    """Reject mode returns the string sentinel for LangGraph resume."""
    resume, err = schema_resume_from_inputs(
        mode="reject",
        draft={"tables": []},
        edited_json="",
    )
    assert err is None
    assert resume == "reject"


def test_schema_resume_from_inputs_approve_returns_original_draft() -> None:
    """Approve mode returns original draft unchanged."""
    # Given: approval mode with draft
    draft = {"tables": [{"table_name": "actor"}]}

    # When: resuming from approve
    resume, err = schema_resume_from_inputs(
        mode="approve",
        draft=draft,
        edited_json="{}",
    )

    # Then: original draft is returned
    assert err is None
    assert resume == draft


def test_schema_resume_from_inputs_edit_parses_valid_json() -> None:
    """Edit mode parses valid JSON input."""
    # Given: edit mode with valid JSON
    raw = '{"tables": [{"table_name": "film"}]}'

    # When: resuming from edit
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json=raw,
    )

    # Then: parsed JSON is returned
    assert err is None
    assert resume == {"tables": [{"table_name": "film"}]}


def test_schema_resume_from_inputs_edit_rejects_invalid_json() -> None:
    """Edit mode rejects invalid JSON syntax."""
    # Given: edit mode with invalid JSON
    raw = "not json"

    # When: resuming from edit
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json=raw,
    )

    # Then: error indicates invalid JSON
    assert resume is None
    assert err is not None
    assert "Invalid JSON" in err


def test_schema_resume_from_inputs_edit_requires_tables_key() -> None:
    """Edit mode requires tables key in JSON."""
    # Given: edit mode with JSON missing tables key
    raw = '{"foo": []}'

    # When: resuming from edit
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json=raw,
    )

    # Then: error mentions tables key
    assert resume is None
    assert "tables" in (err or "")


def test_default_schema_edit_json_serializes_draft() -> None:
    """Default edit JSON serializes provided draft."""
    # Given: a draft with custom data
    draft = {"tables": [{"x": 1}]}

    # When: getting default edit JSON
    result = default_schema_edit_json(draft)

    # Then: draft content is serialized
    assert "actor" not in result
    assert "x" in result


@pytest.mark.parametrize(
    ("draft", "expected_key"),
    [
        (None, '"tables"'),
        ({}, '"tables"'),
    ],
)
def test_default_schema_edit_json_returns_template_for_empty_draft(
    draft: object, expected_key: str
) -> None:
    """Default edit JSON returns template with tables key for empty drafts."""
    # Given: empty or None draft

    # When: getting default edit JSON
    result = default_schema_edit_json(draft)

    # Then: template contains tables key
    assert expected_key in result
