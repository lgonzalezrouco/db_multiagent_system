"""Query branch: load docs → plan → SQL → critic loop → MCP execute → explanation."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

from pydantic import ValidationError

from agents.query_agent import build_query_plan, build_sql
from config import MCPSettings
from graph import mcp_helpers
from graph.state import GraphState
from mcp_server.readonly_sql import mask_sql_for_analysis, validate_readonly_sql

logger = logging.getLogger(__name__)


def query_max_refinements() -> int:
    raw = os.environ.get("QUERY_MAX_REFINEMENTS", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def validate_sql_for_execution(sql: str | None) -> tuple[bool, str]:
    """Return (ok, user-safe feedback). Uses server validation + LIMIT presence."""
    if not sql or not str(sql).strip():
        return False, "SQL must be non-empty."

    ok, err_payload = validate_readonly_sql(sql)
    if not ok:
        msg = "SQL validation failed."
        err = (
            (err_payload or {}).get("error") if isinstance(err_payload, dict) else None
        )
        if isinstance(err, dict) and err.get("message"):
            msg = str(err["message"])
        return False, msg

    masked = mask_sql_for_analysis(sql.strip())
    if not re.search(r"\bLIMIT\b", masked, flags=re.IGNORECASE):
        return False, "SQL must include a LIMIT clause."

    return True, ""


def route_after_critic(state: GraphState) -> Literal["execute", "retry", "cap"]:
    if state.get("critic_status") == "accept":
        return "execute"
    max_r = query_max_refinements()
    if int(state.get("refinement_count") or 0) < max_r:
        return "retry"
    return "cap"


async def query_load_context(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    gate_decision = "query_path"
    steps.append(f"gate:{gate_decision}")
    steps.append("query_load_context")

    schema_docs_context: dict[str, Any] | None = state.get("schema_docs_context")
    schema_docs_warning: str | None = state.get("schema_docs_warning")

    return {
        "steps": steps,
        "gate_decision": gate_decision,
        "schema_ready": True,
        "schema_docs_context": schema_docs_context,
        "schema_docs_warning": schema_docs_warning,
        "refinement_count": 0,
        "critic_status": None,
        "critic_feedback": None,
        "generated_sql": None,
        "query_plan": None,
        "query_execution_result": None,
        "query_explanation": None,
        "last_error": None,
        "last_result": None,
    }


async def query_plan(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_plan")
    ctx = state.get("schema_docs_context")

    raw_prefs = state.get("preferences")
    prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    try:
        plan = await build_query_plan(
            state.get("user_input", "") or "",
            schema_docs_context=ctx if isinstance(ctx, dict) else None,
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"Query plan LLM error: {type(exc).__name__}: {exc}"
        logger.exception("Query plan LLM call failed: %s", msg)
        return {"steps": steps, "query_plan": {}, "last_error": msg}

    return {"steps": steps, "query_plan": plan}


async def query_generate_sql(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_generate_sql")

    ctx = state.get("schema_docs_context")
    raw_prefs = state.get("preferences")
    prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    cf = (
        state.get("critic_feedback")
        if isinstance(state.get("critic_feedback"), str)
        else None
    )
    try:
        qp = (
            state.get("query_plan")
            if isinstance(state.get("query_plan"), dict)
            else None
        )
        sql = await build_sql(
            state.get("user_input", "") or "",
            qp,
            ctx if isinstance(ctx, dict) else None,
            int(state.get("refinement_count") or 0),
            critic_feedback=cf,
            preferences=prefs,
        )
    except Exception as exc:
        msg = f"SQL generation LLM error: {type(exc).__name__}: {exc}"
        logger.exception("SQL generation LLM call failed: %s", msg)
        return {"steps": steps, "generated_sql": "", "last_error": msg}

    return {"steps": steps, "generated_sql": sql}


async def query_critic(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_critic")

    sql = state.get("generated_sql")

    ok, feedback = validate_sql_for_execution(sql if isinstance(sql, str) else None)

    if ok:
        return {
            "steps": steps,
            "critic_status": "accept",
            "critic_feedback": None,
        }

    logger.warning("SQL validation failed (critic reject): %s", feedback)
    rc = int(state.get("refinement_count") or 0) + 1
    return {
        "steps": steps,
        "critic_status": "reject",
        "critic_feedback": feedback,
        "refinement_count": rc,
    }


async def query_execute(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_execute")

    sql = state.get("generated_sql") or ""

    out: dict[str, Any] = {
        "steps": steps,
        "query_execution_result": None,
        "last_error": None,
    }

    try:
        settings = MCPSettings()
        client = await mcp_helpers.get_mcp_client(settings)
        tools = await client.get_tools()
        exec_tool = next((t for t in tools if t.name == "execute_readonly_sql"), None)
        if exec_tool is None:
            out["last_error"] = "MCP tool execute_readonly_sql not found"
            logger.error("%s", out["last_error"])
            return out

        raw = await exec_tool.ainvoke({"sql": sql})
        payload = mcp_helpers.tool_result_to_dict(raw)
        out["query_execution_result"] = payload

        if isinstance(payload, dict) and payload.get("success"):
            pass
        else:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            err_type = (
                err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            )
            if not isinstance(payload, dict):
                out["last_error"] = "could not parse MCP tool result"
                logger.error(
                    "could not parse MCP tool result from execute_readonly_sql",
                )
            else:
                logger.warning(
                    "MCP execute_readonly_sql returned failure: error_type=%s",
                    err_type,
                )

    except ValidationError as exc:
        out["last_error"] = mcp_helpers.format_settings_validation_error(exc)
        logger.error("MCP settings validation failed: %s", out["last_error"])
    except OSError as exc:
        out["last_error"] = f"MCP connection error: {type(exc).__name__}"
        logger.error("MCP connection error: %s", exc)
    except Exception as exc:
        exc_name = type(exc).__name__
        out["last_error"] = f"Unexpected error: {exc_name}"
        logger.exception("Unexpected error during query_execute")

    return out


def _rows_to_dicts(columns: list[str], rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({columns[i]: row[i] for i in range(min(len(columns), len(row)))})
    return out


async def query_explain(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_explain")

    err_early = state.get("last_error")
    payload = state.get("query_execution_result")
    sql = state.get("generated_sql") or ""

    if err_early:
        return {
            "steps": steps,
            "last_error": err_early,
            "last_result": None,
            "query_explanation": None,
        }

    if not isinstance(payload, dict) or not payload.get("success"):
        msg = "Query execution did not return a successful result."
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict) and err.get("message"):
                msg = str(err["message"])
        logger.warning("%s", msg)
        return {
            "steps": steps,
            "last_error": msg,
            "last_result": None,
            "query_explanation": None,
        }

    columns = [str(c) for c in (payload.get("columns") or [])]
    rows_raw = payload.get("rows") or []
    rows_out = _rows_to_dicts(columns, rows_raw)

    warn = state.get("schema_docs_warning")
    lim_parts = [
        "Read-only SELECT with LIMIT; MCP may truncate rows (server row cap).",
    ]
    if warn:
        lim_parts.append(str(warn))

    limitations = " ".join(lim_parts)
    preview_in = (state.get("user_input", "") or "").strip()
    expl = (
        f"Answer for: {preview_in[:120]!r}. "
        f"Returned {payload.get('rows_returned', len(rows_out))} row(s)."
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
        "steps": steps,
        "last_result": last_result,
        "last_error": None,
        "query_explanation": expl,
    }


async def query_refine_cap(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_refine_cap")

    msg = "Critic rejected SQL after max refinement attempts."
    logger.warning("%s", msg)

    return {
        "steps": steps,
        "last_error": msg,
        "last_result": None,
    }
