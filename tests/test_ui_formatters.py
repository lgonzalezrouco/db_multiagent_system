"""Unit tests for ``ui.formatters`` pure helpers."""

from __future__ import annotations

import pytest

from ui.formatters import (
    default_schema_edit_json,
    format_query_answer_markdown,
    format_schema_persist_markdown,
    format_turn_state,
    schema_resume_from_inputs,
)


def test_format_query_answer_markdown_basic() -> None:
    md = format_query_answer_markdown(
        {
            "kind": "query_answer",
            "sql": "SELECT 1 AS n",
            "columns": ["n"],
            "rows": [{"n": 1}],
            "explanation": "One row.",
        },
    )
    assert "SELECT 1" in md
    assert "| n |" in md
    assert "One row." in md


def test_format_query_answer_max_rows() -> None:
    rows = [{"i": i} for i in range(5)]
    md = format_query_answer_markdown(
        {"sql": "SELECT i", "columns": ["i"], "rows": rows},
        max_rows=2,
    )
    assert "Showing first 2 of 5" in md


def test_format_turn_state_error_only() -> None:
    text = format_turn_state({"last_error": "MCP down", "last_result": None})
    assert "MCP down" in text


def test_format_turn_state_query_answer() -> None:
    text = format_turn_state(
        {
            "last_error": None,
            "last_result": {
                "kind": "query_answer",
                "sql": "SELECT 1",
                "columns": [],
                "rows": [],
            },
        },
    )
    assert "SELECT 1" in text


def test_format_schema_persist_markdown_success_plural() -> None:
    md = format_schema_persist_markdown(
        {"kind": "schema_persist", "success": True, "table_count": 22},
    )
    assert "22" in md
    assert "tables" in md
    assert "```json" not in md


def test_format_schema_persist_markdown_success_singular() -> None:
    md = format_schema_persist_markdown(
        {"kind": "schema_persist", "success": True, "table_count": 1},
    )
    assert "1" in md
    assert "table." in md or "table " in md


def test_format_turn_state_schema_persist() -> None:
    text = format_turn_state(
        {
            "last_error": None,
            "last_result": {
                "kind": "schema_persist",
                "success": True,
                "table_count": 3,
            },
        },
    )
    assert "3" in text
    assert "```json" not in text


def test_schema_resume_approve() -> None:
    resume, err = schema_resume_from_inputs(
        mode="approve",
        draft={"tables": [{"table_name": "actor"}]},
        edited_json="{}",
    )
    assert err is None
    assert resume == {"tables": [{"table_name": "actor"}]}


def test_schema_resume_edit_valid() -> None:
    raw = '{"tables": [{"table_name": "film"}]}'
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json=raw,
    )
    assert err is None
    assert resume == {"tables": [{"table_name": "film"}]}


def test_schema_resume_edit_bad_json() -> None:
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json="not json",
    )
    assert resume is None
    assert err is not None
    assert "Invalid JSON" in (err or "")


def test_schema_resume_edit_missing_tables_key() -> None:
    resume, err = schema_resume_from_inputs(
        mode="edit JSON",
        draft=None,
        edited_json='{"foo": []}',
    )
    assert resume is None
    assert "tables" in (err or "")


def test_default_schema_edit_json_from_draft() -> None:
    s = default_schema_edit_json({"tables": [{"x": 1}]})
    assert "actor" not in s
    assert "x" in s


@pytest.mark.parametrize(
    ("draft", "needle"),
    [
        (None, '"tables"'),
        ({}, '"tables"'),
    ],
)
def test_default_schema_edit_json_empty(draft: object, needle: str) -> None:
    assert needle in default_schema_edit_json(draft)
