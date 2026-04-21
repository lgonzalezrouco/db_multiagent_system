"""Natural-language → plan → SQL via LiteLLM + structured outputs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage

from agents.prompts.guardrail import GUARDRAIL_INSTRUCTIONS, GUARDRAIL_SYSTEM_MESSAGE
from agents.prompts.preferences import (
    PREFERENCES_INFERENCE_INSTRUCTIONS,
    PREFERENCES_SYSTEM_MESSAGE,
)
from agents.prompts.query import (
    QUERY_CRITIC_INSTRUCTIONS,
    QUERY_EXPLAIN_INSTRUCTIONS,
    QUERY_PLAN_INSTRUCTIONS,
    QUERY_SQL_INSTRUCTIONS,
    QUERY_SYSTEM_MESSAGE,
)
from agents.schemas.guardrail_outputs import GuardrailOutput
from agents.schemas.preferences_outputs import PreferencesInferenceOutput
from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryExplanationOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)
from llm.factory import create_chat_llm
from memory.preferences import _DEFAULTS as _CANONICAL_KEYS

logger = logging.getLogger(__name__)

ALLOWED_PREF_KEYS: frozenset[str] = frozenset(_CANONICAL_KEYS)
_GUARDRAIL_KEYWORDS: tuple[str, ...] = (
    "actor",
    "film",
    "customer",
    "rental",
    "payment",
    "store",
    "staff",
    "inventory",
    "category",
    "language",
    "country",
    "city",
    "address",
)


def _compact_json(data: Any, *, max_chars: int = 12000) -> str:
    raw = json.dumps(data, default=str, ensure_ascii=False)
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "\n... (truncated)"


def _history_block(conversation_history: list[dict] | None) -> str | None:
    """JSON block for the human message, or None if there is no history."""
    if not conversation_history:
        return None
    return "Conversation history (JSON, oldest-first):\n" + _compact_json(
        conversation_history
    )


def _history_summary(conversation_history: list[Any] | None) -> str | None:
    """Last few user messages only (for the preferences prompt)."""
    if not conversation_history:
        return None
    lines = [
        f"- {turn.get('user_input', '')}"
        for turn in conversation_history[-3:]
        if isinstance(turn, dict)
    ]
    if not lines:
        return None
    return "Recent conversation context (user messages only):\n" + "\n".join(lines)


def _sanitize_delta(raw_delta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw_delta:
        return None
    cleaned = {k: v for k, v in raw_delta.items() if k in ALLOWED_PREF_KEYS}
    return cleaned if cleaned else None


def _extract_schema_terms(schema_docs_context: dict[str, Any] | None) -> set[str]:
    terms: set[str] = set()
    if not isinstance(schema_docs_context, dict):
        return terms

    tables = schema_docs_context.get("tables")
    if not isinstance(tables, list):
        return terms

    for table in tables:
        if not isinstance(table, dict):
            continue
        for key in ("name", "table_name", "qualified_name"):
            raw = table.get(key)
            if isinstance(raw, str) and raw.strip():
                parts = [p.strip().lower() for p in raw.split(".") if p.strip()]
                terms.update(parts)
        cols = table.get("columns")
        if isinstance(cols, list):
            for col in cols:
                if not isinstance(col, dict):
                    continue
                for col_key in ("name", "column_name"):
                    raw_col = col.get(col_key)
                    if isinstance(raw_col, str) and raw_col.strip():
                        terms.add(raw_col.strip().lower())
    return terms


def _has_keyword_match(user_input: str, *, schema_terms: set[str]) -> bool:
    text = (user_input or "").lower()
    if not text:
        return False
    tokens = set(re.findall(r"[a-z_][a-z0-9_]*", text))
    if tokens.intersection(_GUARDRAIL_KEYWORDS):
        return True
    return bool(schema_terms and tokens.intersection(schema_terms))


async def classify_topic(
    user_input: str,
    *,
    schema_docs_context: dict[str, Any] | None,
    preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    schema_terms = _extract_schema_terms(schema_docs_context)
    if _has_keyword_match(user_input, schema_terms=schema_terms):
        return {
            "in_scope": True,
            "reason": "",
            "canned_response": "",
            "used_llm": False,
        }

    llm = create_chat_llm()
    structured = llm.with_structured_output(GuardrailOutput)
    preferred_language = "en"
    if isinstance(preferences, dict) and preferences.get("preferred_language"):
        preferred_language = str(preferences.get("preferred_language")).strip() or "en"

    human_parts = [
        GUARDRAIL_INSTRUCTIONS,
        f"User message:\n{(user_input or '').strip() or '(empty)'}",
        f"Preferred language: {preferred_language}",
        "Known table keywords: " + ", ".join(_GUARDRAIL_KEYWORDS),
    ]
    if schema_docs_context is not None:
        human_parts.append(
            "Schema documentation context (JSON):\n"
            + _compact_json(schema_docs_context),
        )
    history_str = _history_block(conversation_history)
    if history_str:
        human_parts.append(history_str)

    messages = [
        SystemMessage(content=GUARDRAIL_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]

    try:
        raw = await structured.ainvoke(messages)
        result = GuardrailOutput.model_validate(raw)
    except Exception:
        logger.exception("guardrail_classification_failed")
        return {
            "in_scope": True,
            "reason": "",
            "canned_response": "",
            "used_llm": True,
        }

    return {
        "in_scope": bool(result.in_scope),
        "reason": str(result.reason or "").strip(),
        "canned_response": str(result.canned_response or "").strip(),
        "used_llm": True,
    }


async def build_query_plan(
    user_input: str,
    *,
    schema_docs_context: dict[str, Any] | None,
    preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    llm = create_chat_llm()
    structured = llm.with_structured_output(QueryPlanOutput)
    human_parts = [
        QUERY_PLAN_INSTRUCTIONS,
        f"User question:\n{(user_input or '').strip() or '(empty)'}",
    ]
    if schema_docs_context is not None:
        human_parts.append(
            "Schema documentation context (JSON):\n"
            + _compact_json(schema_docs_context),
        )
    if preferences is not None:
        human_parts.append(
            "User preferences (JSON):\n" + _compact_json(preferences),
        )
    history_str = _history_block(conversation_history)
    if history_str:
        human_parts.append(history_str)
    messages = [
        SystemMessage(content=QUERY_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]
    raw = await structured.ainvoke(messages)
    result = QueryPlanOutput.model_validate(raw)
    return result.model_dump(mode="json")


async def build_sql(
    user_input: str,
    query_plan: dict[str, Any] | None,
    schema_docs_context: dict[str, Any] | None,
    refinement_count: int,
    *,
    critic_feedback: str | None = None,
    preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> str:
    llm = create_chat_llm()
    structured = llm.with_structured_output(SqlGenerationOutput)
    ctx: dict[str, Any] = {
        "refinement_count": refinement_count,
        "critic_feedback": critic_feedback,
        "query_plan": query_plan,
    }
    human_parts = [
        QUERY_SQL_INSTRUCTIONS,
        f"User question:\n{(user_input or '').strip() or '(empty)'}",
        "Planner context (JSON):\n" + _compact_json(ctx),
    ]
    if schema_docs_context is not None:
        human_parts.append(
            "Schema documentation context (JSON):\n"
            + _compact_json(schema_docs_context),
        )
    if preferences is not None:
        human_parts.append(
            "User preferences (JSON):\n" + _compact_json(preferences),
        )
    if critic_feedback:
        human_parts.append(
            "Address this critic feedback in your revised SQL:\n" + critic_feedback,
        )
    history_str = _history_block(conversation_history)
    if history_str:
        human_parts.append(history_str)
    messages = [
        SystemMessage(content=QUERY_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]
    raw = await structured.ainvoke(messages)
    out = SqlGenerationOutput.model_validate(raw)
    sql = (out.sql or "").strip()
    if not sql:
        logger.warning("sql_generation_empty_structured_output")
    return sql


async def build_query_critique(
    user_input: str,
    sql: str,
    *,
    query_plan: dict[str, Any] | None,
    schema_docs_context: dict[str, Any] | None,
    preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    llm = create_chat_llm()
    structured = llm.with_structured_output(QueryCritiqueOutput)
    ctx: dict[str, Any] = {
        "query_plan": query_plan,
        "sql": sql,
    }
    human_parts = [
        QUERY_CRITIC_INSTRUCTIONS,
        f"User question:\n{(user_input or '').strip() or '(empty)'}",
        "Critique context (JSON):\n" + _compact_json(ctx),
    ]
    if schema_docs_context is not None:
        human_parts.append(
            "Schema documentation context (JSON):\n"
            + _compact_json(schema_docs_context),
        )
    if preferences is not None:
        human_parts.append(
            "User preferences (JSON):\n" + _compact_json(preferences),
        )
    history_str = _history_block(conversation_history)
    if history_str:
        human_parts.append(history_str)
    messages = [
        SystemMessage(content=QUERY_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]
    raw = await structured.ainvoke(messages)
    result = QueryCritiqueOutput.model_validate(raw)
    return result.model_dump(mode="json")


async def build_query_explanation(
    user_input: str,
    sql: str,
    *,
    query_execution_result: dict[str, Any],
    outcome: str | None = None,
    schema_docs_warning: str | None = None,
    query_plan: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    llm = create_chat_llm()
    structured = llm.with_structured_output(QueryExplanationOutput)
    ctx: dict[str, Any] = {
        "query_plan": query_plan,
        "sql": sql,
        "query_execution_result": query_execution_result,
        "outcome": outcome,
        "schema_docs_warning": schema_docs_warning,
    }
    human_parts = [
        QUERY_EXPLAIN_INSTRUCTIONS,
        f"User question:\n{(user_input or '').strip() or '(empty)'}",
        "Explanation context (JSON):\n" + _compact_json(ctx),
    ]
    if preferences is not None:
        human_parts.append(
            "User preferences (JSON):\n" + _compact_json(preferences),
        )
    messages = [
        SystemMessage(content=QUERY_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]
    raw = await structured.ainvoke(messages)
    result = QueryExplanationOutput.model_validate(raw)
    return result.model_dump(mode="json")


async def infer_preferences_delta(
    user_input: str,
    *,
    current_preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> PreferencesInferenceOutput:
    """Infer preference changes; on LLM failure returns a no-op via ``no_change``."""
    llm = create_chat_llm()
    structured = llm.with_structured_output(PreferencesInferenceOutput)

    human_parts = [
        PREFERENCES_INFERENCE_INSTRUCTIONS,
        f"User message:\n{(user_input or '').strip() or '(empty)'}",
    ]
    if current_preferences is not None:
        human_parts.append(
            "Current preferences (JSON):\n"
            + _compact_json(current_preferences, max_chars=4000),
        )
    history_str = _history_summary(conversation_history)
    if history_str:
        human_parts.append(history_str)

    messages = [
        SystemMessage(content=PREFERENCES_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]

    try:
        raw = await structured.ainvoke(messages)
        result = PreferencesInferenceOutput.model_validate(raw)
    except Exception:
        logger.exception("preferences_inference_failed")
        return PreferencesInferenceOutput.no_change(
            "Inference call failed; no preference change proposed.",
        )

    return result


async def build_plan_and_preferences_delta(
    user_input: str,
    *,
    schema_docs_context: dict[str, Any] | None,
    preferences: dict[str, Any] | None = None,
    conversation_history: list[dict] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    async def _plan() -> dict[str, Any]:
        return await build_query_plan(
            user_input,
            schema_docs_context=schema_docs_context,
            preferences=preferences,
            conversation_history=conversation_history,
        )

    async def _prefs() -> PreferencesInferenceOutput:
        return await infer_preferences_delta(
            user_input,
            current_preferences=preferences,
            conversation_history=conversation_history,
        )

    plan_task = asyncio.create_task(_plan())
    prefs_task = asyncio.create_task(_prefs())
    plan_res, prefs_res = await asyncio.gather(
        plan_task,
        prefs_task,
        return_exceptions=True,
    )

    plan_out: dict[str, Any] = {}
    if isinstance(plan_res, Exception):
        logger.warning("planner_failed", exc_info=plan_res)
    elif isinstance(plan_res, dict):
        plan_out = plan_res

    prefs_delta: dict[str, Any] | None = None
    prefs_rationale: str | None = None
    if isinstance(prefs_res, Exception):
        logger.warning(
            "preferences_inference_failed_inside_planner", exc_info=prefs_res
        )
    elif isinstance(prefs_res, PreferencesInferenceOutput):
        prefs_delta = cast(dict[str, Any] | None, prefs_res.proposed_delta)
        prefs_rationale = prefs_res.rationale

    return plan_out, prefs_delta, prefs_rationale
