from __future__ import annotations

import pytest

from graph.nodes.query_nodes.persist_prefs import persist_prefs_node
from graph.state import MemoryState, QueryGraphState, QueryPipelineState


class _FakeStore:
    def __init__(self, settings=None) -> None:
        self.calls: list[tuple[str, dict]] = []

    def patch(self, user_id: str, delta: dict) -> dict:
        self.calls.append((user_id, delta))
        return {"output_format": "json", "row_limit_hint": 10}


@pytest.mark.asyncio
async def test_persist_prefs_patches_delta_and_snapshots_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(
        "graph.nodes.query_nodes.persist_prefs.UserPreferencesStore",
        lambda settings=None: store,
    )
    state = QueryGraphState(
        user_id="alice",
        user_input="show actors",
        memory=MemoryState(preferences_proposed_delta={"output_format": "json"}),
        query=QueryPipelineState(
            generated_sql="SELECT * FROM actor LIMIT 1",
            execution_result={"success": True, "rows_returned": 1, "rows": [{"n": 1}]},
            explanation="done",
        ),
    )
    result = await persist_prefs_node(state)
    assert store.calls == [("alice", {"output_format": "json"})]
    assert result["memory"]["preferences"]["output_format"] == "json"
    assert len(result["memory"]["conversation_history"]) == 1


@pytest.mark.asyncio
async def test_persist_prefs_snapshots_session_on_success_only_when_sql_ran() -> None:
    state = QueryGraphState(
        user_input="off topic",
        query=QueryPipelineState(outcome="off_topic", guardrail_reason="outside scope"),
    )
    result = await persist_prefs_node(state)
    history = result["memory"]["conversation_history"]
    assert len(history) == 1
    assert history[0].row_count is None


@pytest.mark.asyncio
async def test_persist_prefs_timeout_schedules_background_and_sets_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SlowStore(_FakeStore):
        def patch(self, user_id: str, delta: dict) -> dict:
            import time

            time.sleep(0.05)
            return super().patch(user_id, delta)

    monkeypatch.setattr(
        "graph.nodes.query_nodes.persist_prefs.UserPreferencesStore",
        lambda settings=None: _SlowStore(),
    )
    monkeypatch.setenv("PERSIST_PREFS_TIMEOUT_MS", "1")
    state = QueryGraphState(
        memory=MemoryState(preferences_proposed_delta={"output_format": "json"}),
        query=QueryPipelineState(),
    )
    result = await persist_prefs_node(state)
    assert result["memory"]["warning"] in {
        "persist scheduled in background",
        "could not persist preferences",
    }


@pytest.mark.asyncio
async def test_persist_prefs_exception_does_not_set_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomStore:
        def __init__(self, settings=None) -> None:
            pass

        def patch(self, user_id: str, delta: dict) -> dict:
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "graph.nodes.query_nodes.persist_prefs.UserPreferencesStore",
        _BoomStore,
    )
    state = QueryGraphState(
        last_error=None,
        memory=MemoryState(preferences_proposed_delta={"output_format": "json"}),
    )
    result = await persist_prefs_node(state)
    assert "last_error" not in result or result.get("last_error") is None
    assert result["memory"]["warning"] == "could not persist preferences"
