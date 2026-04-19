from __future__ import annotations

import logging
from typing import Any

from agents.query_agent import build_query_explanation
from graph.state import GraphState

logger = logging.getLogger(__name__)


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


async def query_explain(state: GraphState) -> dict[str, Any]:
    err_early = state.last_error
    payload = state.query.execution_result
    sql = state.query.generated_sql or ""

    if err_early:
        return {
            "steps": ["query_explain"],
            "last_error": err_early,
            "last_result": None,
            "query": {"explanation": None},
        }

    if not isinstance(payload, dict) or not payload.get("success"):
        msg = "Query execution did not return a successful result."
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict) and err.get("message"):
                msg = str(err["message"])
        logger.warning("%s", msg)
        return {
            "steps": ["query_explain"],
            "last_error": msg,
            "last_result": None,
            "query": {"explanation": None},
        }

    columns = [str(c) for c in (payload.get("columns") or [])]
    rows_raw = payload.get("rows") or []
    rows_out = _rows_to_dicts(columns, rows_raw)

    warn = state.query.docs_warning
    limitations = _default_limitations(warn)
    expl = _fallback_explanation(state.user_input or "", payload, rows_out)

    prefs = state.memory.preferences
    qp = state.query.plan if isinstance(state.query.plan, dict) else None

    try:
        llm_out = await build_query_explanation(
            state.user_input or "",
            sql,
            query_execution_result=payload,
            schema_docs_warning=str(warn) if warn else None,
            query_plan=qp,
            preferences=prefs if isinstance(prefs, dict) else None,
        )
        if isinstance(llm_out, dict):
            llm_expl = llm_out.get("explanation")
            llm_limits = llm_out.get("limitations")
            if isinstance(llm_expl, str) and llm_expl.strip():
                expl = llm_expl.strip()
            if isinstance(llm_limits, str) and llm_limits.strip():
                merged_limitations = [limitations, llm_limits.strip()]
                limitations = " ".join(
                    part for part in merged_limitations if part.strip()
                )
    except Exception as exc:
        logger.exception(
            "Query explanation LLM call failed; using deterministic fallback: %s",
            exc,
        )

    last_result: dict[str, Any] = {
        "kind": "query_answer",
        "sql": sql,
        "columns": columns,
        "rows": rows_out,
        "explanation": expl,
        "limitations": limitations,
    }

    return {
        "steps": ["query_explain"],
        "last_result": last_result,
        "last_error": None,
        "query": {"explanation": expl},
    }
