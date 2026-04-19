__all__ = [
    "preferences_infer",
    "preferences_hitl",
    "preferences_persist",
    "route_after_preferences_infer",
    "route_after_preferences_hitl",
    "query_load_context",
    "query_plan",
    "query_generate_sql",
    "query_critic",
    "route_after_critic",
    "validate_sql_for_execution",
    "query_execute",
    "query_explain",
    "query_refine_cap",
]

from .preferences_hitl import preferences_hitl
from .preferences_infer import preferences_infer
from .preferences_persist import preferences_persist
from .query_critic import query_critic, route_after_critic, validate_sql_for_execution
from .query_execute import query_execute
from .query_explain import query_explain
from .query_generate_sql import query_generate_sql
from .query_load_context import query_load_context
from .query_plan import query_plan
from .query_refine_cap import query_refine_cap


def route_after_preferences_infer(state) -> str:
    """Skip HITL when no delta was proposed; go straight to query_plan."""
    delta = state.memory.preferences_proposed_delta
    return "preferences_hitl" if delta else "query_plan"


def route_after_preferences_hitl(state) -> str:
    """After HITL: persist if user approved a delta, otherwise skip to query_plan."""
    delta = state.memory.preferences_proposed_delta
    return "preferences_persist" if delta else "query_plan"
