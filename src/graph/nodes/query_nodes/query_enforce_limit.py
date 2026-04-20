"""Query-pipeline node: enforce row_limit_hint via sqlglot LIMIT rewriting."""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlglot
import sqlglot.expressions as exp

from graph.state import GraphState

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 10
_MAX_ALLOWED_LIMIT = 500


def _get_row_limit_hint(preferences: dict | None) -> int:
    if not isinstance(preferences, dict):
        return _DEFAULT_LIMIT
    raw = preferences.get("row_limit_hint", _DEFAULT_LIMIT)
    try:
        val = int(raw)
        return max(1, min(val, _MAX_ALLOWED_LIMIT))
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT


def enforce_limit(sql: str, limit: int) -> str:
    """Set or tighten the outer SELECT LIMIT; on parse failure append ``LIMIT n``."""
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError:
        logger.warning("sqlglot_parse_failed_appending_raw_limit", exc_info=True)
        return _append_raw_limit(sql, limit)

    if not statements or statements[0] is None:
        return _append_raw_limit(sql, limit)

    stmt = statements[0]

    existing_limit_node = stmt.args.get("limit")

    if existing_limit_node is not None:
        try:
            count_node = existing_limit_node.args.get("expression")
            current = int(count_node.name) if count_node is not None else None
        except (AttributeError, TypeError, ValueError):
            current = None

        if current is not None and current <= limit:
            return sql

    try:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
        rewritten = stmt.sql(dialect="postgres")
    except Exception:
        logger.warning(
            "sqlglot_limit_rewrite_failed_appending_raw_limit", exc_info=True
        )
        return _append_raw_limit(sql, limit)

    logger.info(
        "limit_enforced",
        extra={
            "original_limit": getattr(existing_limit_node, "this", None),
            "new_limit": limit,
        },
    )
    return rewritten


def _append_raw_limit(sql: str, limit: int) -> str:
    base = sql.rstrip().rstrip(";").rstrip()

    if re.search(r"\bLIMIT\b", base, flags=re.IGNORECASE):
        return sql
    return f"{base} LIMIT {limit}"


async def query_enforce_limit(state: GraphState) -> dict[str, Any]:
    """Apply ``row_limit_hint`` to ``generated_sql`` (best effort)."""
    sql = state.query.generated_sql
    if not sql or not sql.strip():
        return {"steps": ["query_enforce_limit"]}

    limit = _get_row_limit_hint(state.memory.preferences)

    rewritten = enforce_limit(sql, limit)

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
