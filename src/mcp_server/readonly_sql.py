"""Read-only SQL validation and safe execution helpers."""

from __future__ import annotations

import json
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row

from config.settings import Settings
from utils.postgres import connect_async

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

_TOKEN_RE = {
    tok: re.compile(rf"\b{re.escape(tok)}\b", re.IGNORECASE) for tok in FORBIDDEN_TOKENS
}


def truncate_sql_preview(sql: str, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
    """Truncate SQL for logs (no secrets — caller must not pass passwords)."""
    s = sql.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "[... truncated]"


def _mask_sql(sql: str) -> str:
    """Mask comments and literals so ';' and tokens are analyzed safely."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if i + 1 < n and sql[i : i + 2] == "--":
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if i + 1 < n and sql[i : i + 2] == "/*":
            out.append(" ")
            i += 2
            while i + 1 < n and sql[i : i + 2] != "*/":
                out.append(" ")
                i += 1
            i = min(i + 2, n)
            continue

        c = sql[i]
        if c == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue

        if c == '"':
            out.append(" ")
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            i += 1
            continue

        if c == "$" and i + 1 < n:
            j = i + 1
            while j < n and sql[j] != "$":
                j += 1
            if j < n:
                tag = sql[i : j + 1]
                end = sql.find(tag, j + 1)
                if end != -1:
                    out.append(" " * (end + len(tag) - i))
                    i = end + len(tag)
                    continue

        out.append(c)
        i += 1

    return "".join(out)


def validate_readonly_sql(sql: str) -> tuple[bool, dict[str, Any] | None]:
    """
    Return (ok, error_payload) where error_payload matches spec error JSON
    (top-level success=false).
    """
    if not sql or not sql.strip():
        return False, {
            "success": False,
            "error": {
                "type": "validation_error",
                "message": "SQL must be a non-empty string.",
            },
        }

    masked = _mask_sql(sql)
    stripped_parts = [p.strip() for p in masked.split(";")]
    non_empty = [p for p in stripped_parts if p]
    if len(non_empty) > 1:
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

    check_text = non_empty[0] if non_empty else ""
    for token in FORBIDDEN_TOKENS:
        if _TOKEN_RE[token].search(check_text):
            return False, {
                "success": False,
                "error": {
                    "type": "validation_error",
                    "message": f"Query contains forbidden token: {token}",
                },
            }

    return True, None


async def execute_readonly_sql(
    settings: Settings,
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
            await cur.execute(stmt)
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
