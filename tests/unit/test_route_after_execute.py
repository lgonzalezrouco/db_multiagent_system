from __future__ import annotations

from graph.nodes.query_nodes import route_after_execute
from graph.state import QueryGraphState, QueryPipelineState


def test_route_success_goes_to_explain() -> None:
    state = QueryGraphState(
        query=QueryPipelineState(execution_result={"success": True})
    )
    assert route_after_execute(state) == "explain"


def test_route_db_error_under_cap_retries_sql_gen(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_REFINEMENTS", "3")
    state = QueryGraphState(
        query=QueryPipelineState(
            execution_result={"success": False}, refinement_count=2
        )
    )
    assert route_after_execute(state) == "retry"


def test_route_db_error_at_cap_sets_db_failure_outcome_and_goes_to_explain(
    monkeypatch,
) -> None:
    monkeypatch.setenv("QUERY_MAX_REFINEMENTS", "3")
    state = QueryGraphState(
        query=QueryPipelineState(
            execution_result={"success": False}, refinement_count=3
        )
    )
    assert route_after_execute(state) == "explain"
