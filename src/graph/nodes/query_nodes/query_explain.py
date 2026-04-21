from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


_DATE_FMT_MAP = {
    "ISO8601": "%Y-%m-%d",
    "US": "%m/%d/%Y",
    "EU": "%d/%m/%Y",
}


def _format_date_value(value: Any, fmt_str: str) -> Any:
    if isinstance(value, datetime):
        return value.strftime(fmt_str)
    if isinstance(value, date):
        return value.strftime(fmt_str)
    if isinstance(value, str):
        for pattern, parser in (
            (r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S"),
            (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),
        ):
            if re.match(pattern, value):
                try:
                    return datetime.strptime(value[:19], parser).strftime(fmt_str)
                except ValueError:
                    pass
    return value


def _apply_date_format(
    rows: list[dict[str, Any]],
    date_format: str,
) -> list[dict[str, Any]]:
    fmt_str = _DATE_FMT_MAP.get(date_format)
    if not fmt_str or date_format == "ISO8601":
        return rows
    return [{k: _format_date_value(v, fmt_str) for k, v in row.items()} for row in rows]


def _get_pref(prefs: Any, key: str, default: str) -> str:
    if not isinstance(prefs, dict):
        return default
    return str(prefs.get(key) or default).strip() or default


def _rows_to_dicts(columns: list[str], rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({columns[i]: row[i] for i in range(min(len(columns), len(row)))})
    return out


def _default_limitations(schema_docs_warning: str | None) -> str:
    parts = ["Read-only SELECT with LIMIT; MCP may truncate rows (server row cap)."]
    if schema_docs_warning:
        parts.append(str(schema_docs_warning))
    return " ".join(parts)


def _fallback_explanation(
    user_input: str,
    payload: dict[str, Any],
    rows_out: list[dict[str, Any]],
) -> str:
    preview_in = (user_input or "").strip()
    return (
        f"Answer for: {preview_in[:120]!r}. "
        f"Returned {payload.get('rows_returned', len(rows_out))} row(s)."
    )


def _deterministic_failure_explanation(
    *, subtype: str, reason: str, attempts: int
) -> str:
    prefix = "Sorry, I could not complete that query."
    if subtype == "max_attempts":
        return (
            f"{prefix} I retried {attempts} time(s) but validation/execution "
            f"kept failing. Last issue: {reason}"
        )
    return f"{prefix} Database execution failed with: {reason}"


async def query_explain(state: QueryGraphState) -> dict[str, Any]:
    outcome = state.query.outcome
    if outcome == "off_topic":
        return {"steps": ["query_explain"]}

    payload = state.query.execution_result
    sql = state.query.generated_sql or ""
    if not isinstance(outcome, str) or not outcome:
        if isinstance(payload, dict) and payload.get("success") is True:
            outcome = "success"
        else:
            outcome = "db_failure"

    if outcome in {"max_attempts", "db_failure"}:
        reason = state.last_error or "Query could not be completed."
        subtype = outcome
        attempts = int(state.query.refinement_count or 0)
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            err = payload.get("error") or {}
            if isinstance(err.get("message"), str) and err.get("message"):
                reason = str(err.get("message"))
        expl = _deterministic_failure_explanation(
            subtype=subtype,
            reason=reason,
            attempts=attempts,
        )

        return {
            "steps": ["query_explain"],
            "query": {"explanation": expl, "outcome": outcome},
            "last_result": {
                "kind": "query_failure",
                "subtype": subtype,
                "reason": reason,
                "sql": sql,
                "attempts": attempts,
                "explanation": expl,
            },
            "last_error": state.last_error,
        }

    assert payload is not None
    columns = [str(c) for c in (payload.get("columns") or [])]
    rows_raw = payload.get("rows") or []
    rows_out = _rows_to_dicts(columns, rows_raw)

    prefs = state.memory.preferences
    output_format = _get_pref(prefs, "output_format", "table")
    date_format = _get_pref(prefs, "date_format", "ISO8601")

    rows_formatted = _apply_date_format(rows_out, date_format)

    warn = state.query.docs_warning
    limitations = _default_limitations(warn)
    expl = _fallback_explanation(state.user_input or "", payload, rows_formatted)

    logger.info(
        "query_explain_deterministic",
        extra={"graph_node": "query_explain", "rows": len(rows_formatted)},
    )

    last_result: dict[str, Any] = {
        "kind": "query_answer",
        "output_format": output_format,
        "sql": sql,
        "columns": columns,
        "rows": rows_formatted,
        "explanation": expl,
        "limitations": limitations,
    }

    return {
        "steps": ["query_explain"],
        "last_result": last_result,
        "last_error": None,
        "query": {"explanation": expl, "outcome": "success"},
    }
