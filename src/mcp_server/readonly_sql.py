"""Read-only SQL validation and safe execution helpers."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import sqlglot
import sqlglot.expressions as exp
from psycopg.rows import dict_row

from config.postgres_settings import PostgresSettings
from utils.postgres import connect_async

# Kept as a public constant for documentation / external reference.
FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "DROP",
        "ALTER",
        "CREATE",
        "GRANT",
        "REVOKE",
        "VACUUM",
        "ANALYZE",
        "COPY",
        "DO",
        "CALL",
        "EXECUTE",
    }
)

_MAX_PREVIEW_CHARS = 200
_ROW_CAP = 1000

# Expression types that are safe as the top-level statement.
# Everything else (Insert, Update, Delete, Drop, Create, Alter, Grant, Revoke,
# Copy, Analyze, TruncateTable, Command) is forbidden.
_SAFE_STMT_TYPES: tuple[type, ...] = (exp.Select,)

# Map sqlglot expression type → canonical forbidden-token name for error messages.
# exp.Command covers VACUUM, DO, CALL, EXECUTE (sqlglot falls back to Command
# for statements it can parse but doesn't have a dedicated node for).
_STMT_TYPE_TO_TOKEN: dict[type, str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.TruncateTable: "TRUNCATE",
    exp.Drop: "DROP",
    exp.Alter: "ALTER",
    exp.Create: "CREATE",
    exp.Grant: "GRANT",
    exp.Revoke: "REVOKE",
    exp.Copy: "COPY",
    exp.Analyze: "ANALYZE",
    exp.Command: "non-SELECT command",
}


def truncate_sql_preview(sql: str, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
    """Truncate SQL for logs (no secrets — caller must not pass passwords)."""
    s = sql.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "[... truncated]"


def sql_has_limit(sql: str) -> bool:
    """Return True when the outermost SELECT in *sql* has an AST-level LIMIT clause.

    Tokens inside string literals and comments are invisible to the parser, so
    ``SELECT 'LIMIT 999' FROM film`` correctly returns False.
    """
    try:
        stmts = sqlglot.parse(
            sql, dialect="postgres", error_level=sqlglot.ErrorLevel.WARN
        )
    except Exception:
        return False
    if not stmts or stmts[0] is None:
        return False
    return stmts[0].args.get("limit") is not None


def validate_readonly_sql(sql: str) -> tuple[bool, dict[str, Any]]:
    """Return ``(ok, error_payload)`` using sqlglot AST analysis.

    Replaces the former hand-rolled comment/literal masking + regex approach.
    sqlglot parses the SQL into an AST, so tokens that appear only inside string
    literals, identifiers, or comments are never visible to the safety check.

    Rules enforced:
    - Non-empty input required.
    - Exactly one statement (multi-statement → reject).
    - Top-level statement must be SELECT or a WITH…SELECT (CTE).
    - Any other statement type (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER,
      GRANT, REVOKE, COPY, ANALYZE, TRUNCATE, VACUUM, DO, CALL, EXECUTE) → reject.

    On success returns ``(True, {})``.
    On failure returns ``(False, {success: False, error: {type, message}})``.
    """
    if not sql or not sql.strip():
        return False, {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": "SQL must be a non-empty string.",
            },
        }

    try:
        stmts = sqlglot.parse(
            sql, dialect="postgres", error_level=sqlglot.ErrorLevel.WARN
        )
    except sqlglot.errors.ParseError as exc:
        return False, {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": f"SQL could not be parsed: {exc}",
            },
        }

    # Filter out None entries (sqlglot may produce them for trailing semicolons).
    stmts = [s for s in stmts if s is not None]

    if not stmts:
        return False, {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": "SQL must be a non-empty string.",
            },
        }

    if len(stmts) > 1:
        return False, {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": (
                    "Multi-statement SQL not supported. "
                    "Please submit one statement at a time."
                ),
            },
        }

    stmt = stmts[0]

    # A CTE (WITH … SELECT) is parsed as a Select with a "with" arg.
    if isinstance(stmt, exp.Select):
        return True, {}

    # Identify the forbidden token for a helpful error message.
    token_name = _STMT_TYPE_TO_TOKEN.get(type(stmt), type(stmt).__name__)
    # For Command nodes, try to extract the first word for a cleaner message.
    if isinstance(stmt, exp.Command):
        first_word = str(stmt.this or "").split()[0].upper() if stmt.this else ""
        if first_word in FORBIDDEN_TOKENS:
            token_name = first_word

    return False, {
        "success": False,
        "error": {
            "type": "validation_error",
            "message": f"Query contains forbidden token: {token_name}",
        },
    }


async def execute_readonly_sql(
    settings: PostgresSettings,
    sql: str,
) -> dict[str, Any]:
    """Validate and run a single read-only statement; return spec-shaped dict."""
    ok, err = validate_readonly_sql(sql)
    if not ok:
        return err

    stmt = sql.strip()
    if stmt.endswith(";"):
        stmt = stmt[:-1].rstrip()

    try:
        async with (
            await connect_async(settings) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(stmt)  # type: ignore[arg-type]
            if cur.description is None:
                return {
                    "success": True,
                    "columns": [],
                    "rows": [],
                    "rows_returned": 0,
                    "rows_truncated": False,
                    "limit_enforced": _ROW_CAP,
                }
            colnames = [d.name for d in cur.description]
            rows = await cur.fetchmany(_ROW_CAP + 1)
    except psycopg.OperationalError as e:
        return {
            "success": False,
            "error": {
                "type": "connection_error",
                "message": (
                    "Cannot connect to dvdrental database. "
                    "Verify database is running and accessible."
                ),
                "details": str(e)[:500],
            },
        }
    except psycopg.Error as e:
        return {
            "success": False,
            "error": {
                "type": "database_error",
                "message": str(e).split("\n", 1)[0],
                "details": getattr(e, "pgcode", None) or str(e)[:500],
            },
        }

    truncated = len(rows) > _ROW_CAP
    out_rows = rows[:_ROW_CAP]
    serialized: list[dict[str, Any]] = []
    for row in out_rows:
        clean: dict[str, Any] = {}
        for k, v in row.items():
            try:
                json.dumps(v)
                clean[k] = v
            except TypeError:
                clean[k] = str(v)
        serialized.append(clean)

    return {
        "success": True,
        "columns": colnames,
        "rows": serialized,
        "rows_returned": len(serialized),
        "rows_truncated": truncated,
        "limit_enforced": _ROW_CAP,
    }
