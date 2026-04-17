"""Unit tests for read-only SQL validation (no database)."""

import pytest

from mcp_server.readonly_sql import truncate_sql_preview, validate_readonly_sql


def test_truncate_sql_preview_caps_length() -> None:
    long = "x" * 300
    out = truncate_sql_preview(long, max_chars=200)
    assert len(out) == len("[... truncated]") + 200
    assert out.endswith("[... truncated]")


@pytest.mark.parametrize(
    ("sql", "expect_ok"),
    [
        ("SELECT 1", True),
        ("SELECT 1;", True),
        ("select * from film limit 5", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("DELETE FROM film WHERE film_id = 1", False),
        ("INSERT INTO film VALUES (1)", False),
        ("UPDATE film SET title = 'x'", False),
        ("DROP TABLE film", False),
        ("TRUNCATE film", False),
        ("CREATE TABLE t (id int)", False),
        ("COPY film TO STDOUT", False),
        ("VACUUM film", False),
        ("SELECT 1; SELECT 2", False),
        ("", False),
        ("   ", False),
    ],
)
def test_validate_readonly_sql(sql: str, expect_ok: bool) -> None:
    ok, err = validate_readonly_sql(sql)
    assert ok is expect_ok
    if not expect_ok:
        assert err is not None
        assert err["success"] is False
        assert "error" in err


def test_validate_ignores_forbidden_token_inside_string() -> None:
    ok, err = validate_readonly_sql("SELECT 'DELETE' AS x")
    assert ok is True
    assert err is None


def test_validate_ignores_forbidden_token_inside_double_quoted_literal() -> None:
    ok, err = validate_readonly_sql('SELECT "INSERT" AS x')
    assert ok is True
    assert err is None


def test_validate_ignores_forbidden_token_inside_dollar_quoted_string() -> None:
    ok, err = validate_readonly_sql("SELECT $$DROP$$::text")
    assert ok is True
    assert err is None


def test_validate_allows_delete_in_line_comment() -> None:
    ok, err = validate_readonly_sql("SELECT 1 -- DELETE not executed")
    assert ok is True
    assert err is None


def test_validate_multi_statement_error_message() -> None:
    ok, err = validate_readonly_sql("SELECT 1; SELECT 2")
    assert ok is False
    assert err is not None
    assert "Multi-statement" in err["error"]["message"]


def test_validate_forbidden_token_error_names_token() -> None:
    ok, err = validate_readonly_sql("ALTER TABLE film ADD COLUMN x int")
    assert ok is False
    assert err is not None
    assert "ALTER" in err["error"]["message"]
