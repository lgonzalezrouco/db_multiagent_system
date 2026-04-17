"""Natural-language → plan → SQL helpers (stub implementation; no graph imports)."""

from __future__ import annotations

from typing import Any


def build_query_plan(
    user_input: str,
    *,
    schema_docs_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a lightweight structured plan for downstream SQL generation."""
    preview = (user_input or "").strip()
    return {
        "intent": "explore",
        "summary": preview[:200] if preview else "(empty)",
        "schema_grounded": bool(schema_docs_context),
    }


def build_sql(
    _user_input: str,
    _query_plan: dict[str, Any] | None,
    _schema_docs_context: dict[str, Any] | None,
    _refinement_count: int,
) -> str:
    """Produce read-only SQL with LIMIT (deterministic stub until LLM wiring)."""
    return "SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10"
