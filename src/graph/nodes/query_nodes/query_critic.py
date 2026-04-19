from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

from agents.query_agent import build_query_critique
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


def _normalize_critic_verdict(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"accept", "approved", "ok", "pass", "yes"}:
        return "accept"
    return "reject"


def _semantic_feedback(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "Semantic critic rejected the SQL."
    feedback = str(payload.get("feedback") or "").strip()
    risks = payload.get("risks")
    assumptions = payload.get("assumptions")
    extras: list[str] = []
    if isinstance(risks, list):
        cleaned = [str(x).strip() for x in risks if str(x).strip()]
        if cleaned:
            extras.append("Risks: " + "; ".join(cleaned[:3]))
    if isinstance(assumptions, list):
        cleaned = [str(x).strip() for x in assumptions if str(x).strip()]
        if cleaned:
            extras.append("Assumptions: " + "; ".join(cleaned[:3]))
    parts = [feedback] if feedback else []
    parts.extend(extras)
    return " ".join(parts).strip() or "Semantic critic rejected the SQL."


def route_after_critic(state: GraphState) -> Literal["execute", "retry", "cap"]:
    if state.query.critic_status == "accept":
        return "execute"
    max_r = query_max_refinements()
    if int(state.query.refinement_count or 0) < max_r:
        return "retry"
    return "cap"


async def query_critic(state: GraphState) -> dict[str, Any]:
    sql_text = state.query.generated_sql
    sql_text = sql_text if isinstance(sql_text, str) else None

    ok, feedback = validate_sql_for_execution(sql_text)
    if not ok:
        logger.warning("SQL validation failed (critic reject): %s", feedback)
        rc = int(state.query.refinement_count or 0) + 1
        return {
            "steps": ["query_critic"],
            "query": {
                "critic_status": "reject",
                "critic_feedback": feedback,
                "refinement_count": rc,
            },
        }

    prefs = state.memory.preferences
    query_plan = state.query.plan if isinstance(state.query.plan, dict) else None
    schema_docs_context = (
        state.query.docs_context if isinstance(state.query.docs_context, dict) else None
    )
    history = state.memory.conversation_history or []
    history_dicts = [t.model_dump(mode="json") for t in history] if history else None

    try:
        critique = await build_query_critique(
            state.user_input or "",
            sql_text or "",
            query_plan=query_plan,
            schema_docs_context=schema_docs_context,
            preferences=prefs if isinstance(prefs, dict) else None,
            conversation_history=history_dicts,
        )
    except Exception as exc:
        logger.exception("Semantic SQL critic LLM call failed")
        return {
            "steps": ["query_critic"],
            "query": {
                "critic_status": "accept",
                "critic_feedback": f"Semantic critic unavailable: {type(exc).__name__}",
            },
        }

    verdict = _normalize_critic_verdict(critique.get("verdict"))
    if verdict == "accept":
        return {
            "steps": ["query_critic"],
            "query": {"critic_status": "accept", "critic_feedback": None},
        }

    semantic_feedback = _semantic_feedback(critique)
    logger.warning("Semantic SQL critic rejected generated SQL: %s", semantic_feedback)
    rc = int(state.query.refinement_count or 0) + 1
    return {
        "steps": ["query_critic"],
        "query": {
            "critic_status": "reject",
            "critic_feedback": semantic_feedback,
            "refinement_count": rc,
        },
    }
