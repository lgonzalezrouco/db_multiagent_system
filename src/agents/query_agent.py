"""Natural-language → plan → SQL via LiteLLM + structured outputs.

Also contains the preferences-inference builder, which shares the same LLM
factory and message-construction patterns as the query builders.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

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

# Keys the LLM is allowed to propose changes for.
ALLOWED_PREF_KEYS: frozenset[str] = frozenset(_CANONICAL_KEYS)


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


def _history_summary(conversation_history: list[dict] | None) -> str | None:
    """Condensed history block (user_input only) to reduce token cost."""
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
    """Strip any keys not in the canonical set; return None if nothing remains."""
    if not raw_delta:
        return None
    cleaned = {k: v for k, v in raw_delta.items() if k in ALLOWED_PREF_KEYS}
    return cleaned if cleaned else None


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
    """Detect whether the user intends to change a persistent preference.

    Returns a :class:`PreferencesInferenceOutput` with:
    - ``proposed_delta``: a dict of only the keys to change, or ``None``
    - ``rationale``: explanation of the decision

    The delta is sanitized to remove any keys outside the canonical set, so
    callers can trust the result is safe to pass to ``UserPreferencesStore.patch``.
    Never raises: any LLM error produces a null delta (soft-fail).
    """
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
        return PreferencesInferenceOutput(
            proposed_delta=None,
            rationale="Inference call failed; no preference change proposed.",
        )

    sanitized = _sanitize_delta(result.proposed_delta)
    if sanitized != result.proposed_delta:
        logger.warning(
            "preferences_delta_contained_unknown_keys",
            extra={
                "raw_keys": sorted(result.proposed_delta.keys())
                if result.proposed_delta
                else [],
                "sanitized_keys": sorted(sanitized.keys()) if sanitized else [],
            },
        )

    return PreferencesInferenceOutput(
        proposed_delta=sanitized,
        rationale=result.rationale,
    )
