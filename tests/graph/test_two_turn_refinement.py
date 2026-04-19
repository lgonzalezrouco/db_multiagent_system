"""Two-turn integration test for iterative refinement via conversation history.

Verifies Spec 11 §10 minimum acceptance criteria:
- After turn 1: conversation_history has 1 entry.
- After turn 2: conversation_history has 2 entries.
- The second turn's LLM human message contains the turn-1 history (anaphora
  resolution is passed through correctly).
"""

from __future__ import annotations

from typing import Any

import pytest

from graph import get_compiled_graph, graph_run_config
from graph.state import GraphState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unwrap_state(out: Any) -> GraphState:
    if isinstance(out, GraphState):
        return out
    if isinstance(out, dict):
        return GraphState(**out)
    value = getattr(out, "value", None)
    if isinstance(value, GraphState):
        return value
    if isinstance(value, dict):
        return GraphState(**value)
    raise TypeError(f"unexpected output: {type(out).__name__}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8000")


# ---------------------------------------------------------------------------
# Two-turn integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_turns_accumulate_conversation_history(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After two query turns, conversation_history has exactly 2 entries."""

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 2,
                "rows": [
                    {"actor_id": 1, "first_name": "Nick", "last_name": "Wahlberg"},
                    {"actor_id": 2, "first_name": "Ed", "last_name": "Chase"},
                ],
                "columns": ["actor_id", "first_name", "last_name"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    from tests.schema_presence_stubs import ReadySchemaPresence

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="two-turn-test-1", run_kind="pytest")

    # --- Turn 1 ---
    out1 = await app.ainvoke(
        {
            "user_input": "Which actors worked with Nick Wahlberg?",
            "steps": [],
            **state_seed,
        },
        config=cfg,
    )
    state1 = _unwrap_state(out1)

    assert state1.last_error is None
    assert state1.last_result is not None
    assert isinstance(state1.last_result, dict)
    assert state1.last_result.get("kind") == "query_answer"
    assert len(state1.memory.conversation_history) == 1, (
        f"expected 1 turn after turn 1, got {len(state1.memory.conversation_history)}"
    )
    turn1 = state1.memory.conversation_history[0]
    assert turn1.user_input == "Which actors worked with Nick Wahlberg?"
    assert turn1.sql is not None

    # --- Turn 2 ---
    out2 = await app.ainvoke(
        {
            "user_input": "Now show me his movies.",
            "steps": [],
            **state_seed,
        },
        config=cfg,
    )
    state2 = _unwrap_state(out2)

    assert state2.last_error is None
    assert state2.last_result is not None
    assert isinstance(state2.last_result, dict)
    assert state2.last_result.get("kind") == "query_answer"
    assert len(state2.memory.conversation_history) == 2, (
        f"expected 2 turns after turn 2, got {len(state2.memory.conversation_history)}"
    )
    turn2 = state2.memory.conversation_history[1]
    assert turn2.user_input == "Now show me his movies."


@pytest.mark.asyncio
async def test_second_turn_human_message_contains_history(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second turn's LLM calls receive the turn-1 history in the human message."""
    from langchain_core.messages import HumanMessage

    from tests.schema_presence_stubs import ReadySchemaPresence

    plan_messages_by_turn: list[list[Any]] = []

    # Build a stub that captures messages and delegates to the autouse stub
    from agents.schemas.query_outputs import (
        QueryCritiqueOutput,
        QueryExplanationOutput,
        QueryPlanOutput,
        SqlGenerationOutput,
    )

    class _CapturingStructuredRunnable:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        async def ainvoke(self, messages: list[Any]) -> Any:
            if self.kind == "plan":
                plan_messages_by_turn.append(messages)
                return QueryPlanOutput(
                    intent="explore",
                    summary="stub",
                    relevant_tables=["public.film"],
                    notes=[],
                    assumptions=[],
                )
            if self.kind == "sql":
                return SqlGenerationOutput(
                    sql="SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10",
                    rationale="stub",
                )
            if self.kind == "critique":
                return QueryCritiqueOutput(
                    verdict="accept", feedback="ok", risks=[], assumptions=[]
                )
            if self.kind == "explain":
                return QueryExplanationOutput(
                    explanation="stub explain",
                    limitations="none",
                    follow_up_suggestions=[],
                )
            raise NotImplementedError(self.kind)

    class _CapturingFakeLLM:
        def with_structured_output(
            self, schema: type[Any]
        ) -> _CapturingStructuredRunnable:
            name = getattr(schema, "__name__", "")
            mapping = {
                "QueryPlanOutput": "plan",
                "SqlGenerationOutput": "sql",
                "QueryCritiqueOutput": "critique",
                "QueryExplanationOutput": "explain",
            }
            if name not in mapping:
                raise NotImplementedError(name)
            return _CapturingStructuredRunnable(mapping[name])

    monkeypatch.setattr(
        "agents.query_agent.create_chat_llm",
        lambda *a, **kw: _CapturingFakeLLM(),
    )

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 5}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    cfg, state_seed = graph_run_config(thread_id="two-turn-test-2", run_kind="pytest")

    # Turn 1 — no prior history
    await app.ainvoke(
        {
            "user_input": "Which actors worked with Nick Wahlberg?",
            "steps": [],
            **state_seed,
        },
        config=cfg,
    )

    assert len(plan_messages_by_turn) == 1
    turn1_human = next(
        (m.content for m in plan_messages_by_turn[0] if isinstance(m, HumanMessage)),
        "",
    )
    # Turn 1 should NOT have history (no prior turns)
    assert "Conversation history (JSON" not in turn1_human

    # Turn 2 — has prior history from turn 1
    await app.ainvoke(
        {"user_input": "Now show me his movies.", "steps": [], **state_seed},
        config=cfg,
    )

    assert len(plan_messages_by_turn) == 2
    turn2_human = next(
        (m.content for m in plan_messages_by_turn[1] if isinstance(m, HumanMessage)),
        "",
    )
    # Turn 2 should have history
    assert "Conversation history (JSON" in turn2_human
    assert "Nick Wahlberg" in turn2_human


@pytest.mark.asyncio
async def test_history_capped_at_max_turns(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History is capped at HISTORY_MAX_TURNS even after more turns."""
    from memory.session import HISTORY_MAX_TURNS
    from tests.schema_presence_stubs import ReadySchemaPresence

    class _FakeTool:
        name = "execute_readonly_sql"

        async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "rows_returned": 1,
                "rows": [{"n": 1}],
                "columns": ["n"],
            }

    class _FakeClient:
        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool()]

    async def _fake_client(_settings: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_client)

    app = get_compiled_graph(presence=ReadySchemaPresence())
    thread_id = "two-turn-cap-test-1"
    cfg, state_seed = graph_run_config(thread_id=thread_id, run_kind="pytest")

    # Run HISTORY_MAX_TURNS + 2 turns
    total_turns = HISTORY_MAX_TURNS + 2
    for i in range(total_turns):
        out = await app.ainvoke(
            {
                "user_input": f"question {i}",
                "steps": [],
                **state_seed,
            },
            config=cfg,
        )

    final_state = _unwrap_state(out)
    assert len(final_state.memory.conversation_history) == HISTORY_MAX_TURNS, (
        f"expected {HISTORY_MAX_TURNS} history entries, "
        f"got {len(final_state.memory.conversation_history)}"
    )
    # The oldest entries should have been dropped
    assert final_state.memory.conversation_history[0].user_input == (
        f"question {total_turns - HISTORY_MAX_TURNS}"
    )
