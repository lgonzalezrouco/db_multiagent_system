"""Natural-language → plan → SQL via LiteLLM + structured outputs."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.prompts.query import (
    QUERY_CRITIC_INSTRUCTIONS,
    QUERY_EXPLAIN_INSTRUCTIONS,
    QUERY_PLAN_INSTRUCTIONS,
    QUERY_SQL_INSTRUCTIONS,
    QUERY_SYSTEM_MESSAGE,
)
from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryExplanationOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)
from llm.factory import create_chat_llm

logger = logging.getLogger(__name__)


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
