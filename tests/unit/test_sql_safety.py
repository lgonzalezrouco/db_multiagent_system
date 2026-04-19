"""Unit tests for read-only SQL validation (SQL injection prevention)."""

import pytest

from mcp_server.readonly_sql import truncate_sql_preview, validate_readonly_sql


def test_truncate_sql_preview_caps_output_at_max_length() -> None:
    """SQL preview is truncated when exceeding max length."""
    # Given: a SQL string exceeding the max preview length
    long_sql = "x" * 300
    max_chars = 200

    # When: truncating the preview
    result = truncate_sql_preview(long_sql, max_chars=max_chars)

    # Then: output is capped at max_chars plus truncation marker
    assert len(result) == max_chars + len("[... truncated]")
    assert result.endswith("[... truncated]")


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
def test_validate_readonly_sql_allows_safe_queries_rejects_mutations(
    sql: str, expect_ok: bool
) -> None:
    """Readonly validation accepts SELECT/WITH and rejects mutations."""
    # Given: a SQL statement to validate

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: result matches expected safety classification
    assert ok is expect_ok
    if not expect_ok:
        assert err is not None
        assert err["success"] is False
        assert "error" in err


def test_validate_ignores_forbidden_token_inside_single_quoted_string() -> None:
    """Forbidden keywords inside single-quoted strings are ignored."""
    # Given: a SELECT with DELETE keyword inside a string literal
    sql = "SELECT 'DELETE' AS x"

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation passes (string content is not executed)
    assert ok is True
    assert err == {}


def test_validate_ignores_forbidden_token_inside_double_quoted_literal() -> None:
    """Forbidden keywords inside double-quoted identifiers are ignored."""
    # Given: a SELECT with INSERT keyword as a double-quoted identifier
    sql = 'SELECT "INSERT" AS x'

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation passes (identifier content is not executed)
    assert ok is True
    assert err == {}


def test_validate_ignores_forbidden_token_inside_dollar_quoted_string() -> None:
    """Forbidden keywords inside dollar-quoted strings are ignored."""
    # Given: a SELECT with DROP keyword in a dollar-quoted string
    sql = "SELECT $$DROP$$::text"

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation passes (string content is not executed)
    assert ok is True
    assert err == {}


def test_validate_allows_forbidden_token_in_line_comment() -> None:
    """Forbidden keywords in SQL comments are ignored."""
    # Given: a SELECT with DELETE in a line comment
    sql = "SELECT 1 -- DELETE not executed"

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation passes (comments are not executed)
    assert ok is True
    assert err == {}


def test_validate_rejects_multi_statement_with_clear_error() -> None:
    """Multi-statement SQL is rejected with descriptive error."""
    # Given: a SQL string with multiple statements
    sql = "SELECT 1; SELECT 2"

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation fails with multi-statement error message
    assert ok is False
    assert err is not None
    assert "Multi-statement" in err["error"]["message"]


def test_validate_rejects_forbidden_token_naming_it_in_error() -> None:
    """Forbidden tokens are named in the error message."""
    # Given: a SQL statement with ALTER keyword
    sql = "ALTER TABLE film ADD COLUMN x int"

    # When: validating the SQL
    ok, err = validate_readonly_sql(sql)

    # Then: validation fails and error mentions the forbidden token
    assert ok is False
    assert err is not None
    assert "ALTER" in err["error"]["message"]
