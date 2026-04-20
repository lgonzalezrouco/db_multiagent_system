"""Query-pipeline node: enforce row_limit_hint via sqlglot LIMIT rewriting."""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlglot
import sqlglot.expressions as exp

from graph.state import QueryGraphState

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


def enforce_limit(sql: str, default_limit: int) -> str:
    """Ensure a safe outer SELECT LIMIT.

    - If there is **no** outer ``LIMIT``, append ``LIMIT default_limit``
      (from user ``row_limit_hint`` preferences).
    - If there **is** an outer ``LIMIT``, only **raise** it when it exceeds
      ``_MAX_ALLOWED_LIMIT`` (safety cap). Otherwise leave it unchanged so
      explicit counts (e.g. “100 actors”) are not overwritten by the default
      hint (often 10).
    """
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError:
        logger.warning("sqlglot_parse_failed_appending_raw_limit", exc_info=True)
        return _append_raw_limit(sql, default_limit)

    if not statements or statements[0] is None:
        return _append_raw_limit(sql, default_limit)

    stmt = statements[0]

    existing_limit_node = stmt.args.get("limit")

    if existing_limit_node is not None:
        try:
            count_node = existing_limit_node.args.get("expression")
            current = int(count_node.name) if count_node is not None else None
        except (AttributeError, TypeError, ValueError):
            current = None

        if current is None:
            pass
        elif current > _MAX_ALLOWED_LIMIT:
            try:
                stmt.set(
                    "limit",
                    exp.Limit(expression=exp.Literal.number(_MAX_ALLOWED_LIMIT)),
                )
                rewritten = stmt.sql(dialect="postgres")
                logger.info(
                    "limit_capped_at_max",
                    extra={"original": current, "new_limit": _MAX_ALLOWED_LIMIT},
                )
                return rewritten
            except Exception:
                logger.warning(
                    "sqlglot_limit_cap_failed_appending_raw_limit", exc_info=True
                )
                return _append_raw_limit(sql, _MAX_ALLOWED_LIMIT)
        else:
            return sql

    try:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(default_limit)))
        rewritten = stmt.sql(dialect="postgres")
    except Exception:
        logger.warning(
            "sqlglot_limit_rewrite_failed_appending_raw_limit", exc_info=True
        )
        return _append_raw_limit(sql, default_limit)

    logger.info(
        "limit_injected",
        extra={"new_limit": default_limit},
    )
    return rewritten


def _append_raw_limit(sql: str, limit: int) -> str:
    base = sql.rstrip().rstrip(";").rstrip()

    if re.search(r"\bLIMIT\b", base, flags=re.IGNORECASE):
        return sql
    return f"{base} LIMIT {limit}"


async def query_enforce_limit(state: QueryGraphState) -> dict[str, Any]:
    """Add a default LIMIT if missing; cap only above ``_MAX_ALLOWED_LIMIT``."""
    sql = state.query.generated_sql
    if not sql or not sql.strip():
        return {"steps": ["query_enforce_limit"]}

    default_limit = _get_row_limit_hint(state.memory.preferences)

    rewritten = enforce_limit(sql, default_limit)

    if rewritten == sql:
        return {"steps": ["query_enforce_limit"]}

    logger.info(
        "query_limit_rewritten",
        extra={
            "default_limit": default_limit,
            "original": sql[:120],
            "rewritten": rewritten[:120],
        },
    )
    return {
        "steps": ["query_enforce_limit"],
        "query": {"generated_sql": rewritten},
    }
