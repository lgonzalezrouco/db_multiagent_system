"""Query-pipeline node: enforce row_limit_hint via sqlglot LIMIT rewriting."""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
import sqlglot.expressions as exp

from graph.state import GraphState

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 10
_MAX_ALLOWED_LIMIT = 500


def _get_row_limit_hint(preferences: dict | None) -> int:
    """Return the validated row_limit_hint from preferences, or the default."""
    if not isinstance(preferences, dict):
        return _DEFAULT_LIMIT
    raw = preferences.get("row_limit_hint", _DEFAULT_LIMIT)
    try:
        val = int(raw)
        return max(1, min(val, _MAX_ALLOWED_LIMIT))
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT


def enforce_limit(sql: str, limit: int) -> str:
    """Parse *sql* with sqlglot and set/tighten its LIMIT to *limit*.

    Rules:
    - If the SQL has no LIMIT, inject one.
    - If the SQL already has a LIMIT that exceeds *limit*, tighten it.
    - If the SQL already has a LIMIT ≤ *limit*, leave it unchanged.
    - If parsing fails, append a raw ``LIMIT <n>`` as a last resort.

    Only the outermost SELECT statement's LIMIT is touched; sub-query
    LIMITs are left alone.
    """
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except Exception:
        logger.warning("sqlglot_parse_failed_appending_raw_limit", exc_info=True)
        return _append_raw_limit(sql, limit)

    if not statements or statements[0] is None:
        return _append_raw_limit(sql, limit)

    stmt = statements[0]

    # Find the outermost LIMIT node (direct child of the top SELECT).
    # In sqlglot ≥ 20, Limit uses 'expression' (not 'this') for the row count.
    existing_limit_node = stmt.args.get("limit")

    if existing_limit_node is not None:
        try:
            count_node = existing_limit_node.args.get("expression")
            current = int(count_node.name) if count_node is not None else None
        except (AttributeError, TypeError, ValueError):
            current = None

        if current is not None and current <= limit:
            # Already within budget — do nothing.
            return sql

    # Set (or replace) the outermost LIMIT.
    stmt.set("limit", exp.Limit(expression=exp.Literal.number(limit)))

    rewritten = stmt.sql(dialect="postgres")
    logger.info(
        "limit_enforced",
        extra={
            "original_limit": getattr(existing_limit_node, "this", None),
            "new_limit": limit,
        },
    )
    return rewritten


def _append_raw_limit(sql: str, limit: int) -> str:
    """Fallback: strip any trailing semicolon and append LIMIT clause as text."""
    base = sql.rstrip().rstrip(";").rstrip()
    import re

    if re.search(r"\bLIMIT\b", base, flags=re.IGNORECASE):
        # A LIMIT is already present but unparsable — leave it alone.
        return sql
    return f"{base} LIMIT {limit}"


async def query_enforce_limit(state: GraphState) -> dict[str, Any]:
    """Rewrite the generated SQL to respect the user's ``row_limit_hint`` preference.

    Inserted after ``query_generate_sql`` and before ``query_critic``.
    Never raises: if rewriting fails the original SQL is kept and a warning is
    logged so the critic can still evaluate it.
    """
    sql = state.query.generated_sql
    if not sql or not sql.strip():
        return {"steps": ["query_enforce_limit"]}

    limit = _get_row_limit_hint(state.memory.preferences)

    try:
        rewritten = enforce_limit(sql, limit)
    except Exception:
        logger.warning(
            "query_enforce_limit_unexpected_error; keeping original sql", exc_info=True
        )
        return {"steps": ["query_enforce_limit"]}

    if rewritten == sql:
        return {"steps": ["query_enforce_limit"]}

    logger.info(
        "query_limit_rewritten",
        extra={"limit": limit, "original": sql[:120], "rewritten": rewritten[:120]},
    )
    return {
        "steps": ["query_enforce_limit"],
        "query": {"generated_sql": rewritten},
    }
