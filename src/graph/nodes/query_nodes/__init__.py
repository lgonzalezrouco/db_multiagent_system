__all__ = [
    "guardrail_node",
    "off_topic_node",
    "persist_prefs_node",
    "route_after_guardrail",
    "query_load_context",
    "query_plan",
    "query_generate_sql",
    "query_enforce_limit",
    "query_critic",
    "route_after_critic",
    "route_after_execute",
    "validate_sql_for_execution",
    "query_execute",
    "query_explain",
]

from typing import Literal

from .guardrail import guardrail_node
from .off_topic import off_topic_node
from .persist_prefs import persist_prefs_node
from .query_critic import (
    query_critic,
    query_max_refinements,
    route_after_critic,
    validate_sql_for_execution,
)
from .query_enforce_limit import query_enforce_limit
from .query_execute import query_execute
from .query_explain import query_explain
from .query_generate_sql import query_generate_sql
from .query_load_context import query_load_context
from .query_plan import query_plan


def route_after_guardrail(state) -> Literal["planner", "off_topic"]:
    in_scope = state.query.topic_in_scope
    return "off_topic" if in_scope is False else "planner"


def route_after_execute(state) -> Literal["explain", "retry"]:
    payload = state.query.execution_result
    success = isinstance(payload, dict) and payload.get("success") is True
    if success:
        return "explain"
    if int(state.query.refinement_count or 0) < query_max_refinements():
        return "retry"
    return "explain"
