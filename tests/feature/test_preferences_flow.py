"""Feature tests for the end-to-end preferences flow.

Covers:
- preferences_infer detects no change → skips HITL, query continues normally
- preferences_infer proposes a delta → HITL interrupt fires
- HITL approval resumes → preferences_persist patches the store → turn completes
- HITL rejection resumes with None → prefs unchanged, turn completes
- Approved pref (row_limit_hint) is used as default LIMIT when SQL has no LIMIT
- Approved pref (output_format) is reflected in last_result["output_format"]
- Approved pref (safety_strictness=lenient) allows critic to pass despite risks
- preferences_dirty write-back through memory_update_session persists changes
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.types import Command

from agents.schemas.preferences_outputs import PreferencesInferenceOutput
from graph import get_compiled_query_graph, graph_run_config
from graph.invoke_v2 import unwrap_query_graph_v2
from memory.preferences import default_preferences


class _FakeTool:
    name = "execute_readonly_sql"

    async def ainvoke(self, _input: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "rows_returned": 1,
            "rows": [{"n": 42}],
            "columns": ["n"],
        }


class _FakeClient:
    async def get_tools(self) -> list[_FakeTool]:
        return [_FakeTool()]


async def _fake_mcp_client(_settings: Any) -> _FakeClient:
    return _FakeClient()


class _FakePrefsStore:
    """In-memory substitute for UserPreferencesStore."""

    def __init__(self, settings=None) -> None:
        self._data: dict[str, dict] = {}

    def get(self, user_id: str) -> dict:
        stored = self._data.get(user_id, {})
        return {**default_preferences(), **stored}

    def upsert(self, user_id: str, prefs: dict) -> None:
        self._data[user_id] = prefs

    def patch(self, user_id: str, delta: dict) -> dict:
        existing = self._data.get(user_id, {})
        merged = {**existing, **delta}
        self._data[user_id] = merged
        return {**default_preferences(), **merged}


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused-for-unit")
    monkeypatch.setenv("POSTGRES_DB", "dvdrental")


async def _no_delta_infer(*_a: Any, **_kw: Any) -> PreferencesInferenceOutput:
    return PreferencesInferenceOutput.no_change("stub: no change detected")


def _delta_infer(delta: dict) -> Any:
    """Return an async callable that always proposes *delta*."""

    async def _inner(*_a: Any, **_kw: Any) -> PreferencesInferenceOutput:
        return PreferencesInferenceOutput.from_delta(
            delta,
            rationale="stub: user requested change",
        )

    return _inner


@pytest.mark.asyncio
async def test_no_delta_skips_hitl_and_completes(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When infer returns no delta the graph goes straight through query_plan."""
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _no_delta_infer,
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-no-delta-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "list films", "steps": [], **seed}, config=cfg
    )
    state, interrupts = unwrap_query_graph_v2(out)

    assert not interrupts, "expected no interrupts when delta is None"
    assert "preferences_infer" in state.steps
    assert "preferences_hitl" not in state.steps
    assert "preferences_persist" not in state.steps
    assert state.last_error is None


@pytest.mark.asyncio
async def test_delta_proposed_triggers_hitl_interrupt(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When infer proposes a delta the graph pauses at preferences_hitl."""
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"output_format": "json"}),
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-hitl-1", run_kind="pytest")
    out = await app.ainvoke(
        {"user_input": "always show results as JSON", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    state, interrupts = unwrap_query_graph_v2(out)

    assert interrupts, "expected interrupt at preferences_hitl"
    payload = getattr(interrupts[0], "value", interrupts[0])
    assert payload["kind"] == "preferences_review"
    assert payload["proposed_delta"] == {"output_format": "json"}
    assert payload.get("rationale")
    # Query pipeline must NOT have run yet
    assert "query_plan" not in state.steps


@pytest.mark.asyncio
async def test_hitl_approval_persists_and_completes_turn(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving the HITL delta stores it and the query turn finishes."""
    fake_store = _FakePrefsStore()
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")
    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"output_format": "json"}),
    )
    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-approve-1", run_kind="pytest")

    # Turn 1: triggers HITL
    await app.ainvoke(
        {"user_input": "always show JSON", "steps": [], **seed},
        config=cfg,
        version="v2",
    )

    # Resume: approve the delta
    out2 = await app.ainvoke(
        Command(resume={"output_format": "json"}), config=cfg, version="v2"
    )
    state, interrupts = unwrap_query_graph_v2(out2)

    assert not interrupts, "expected no further interrupts after approval"
    assert "preferences_persist" in state.steps
    assert "query_plan" in state.steps
    assert state.last_error is None

    # Pref was written to the store
    stored = fake_store.get("default")
    assert stored["output_format"] == "json"


@pytest.mark.asyncio
async def test_hitl_rejection_leaves_prefs_unchanged(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting the HITL (resume with {}) leaves preferences unchanged."""
    fake_store = _FakePrefsStore()
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")

    # First call proposes a delta; subsequent calls (after resume replay) return no-op.
    call_count = {"n": 0}

    async def _once_then_noop(*_a: Any, **_kw: Any) -> PreferencesInferenceOutput:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return PreferencesInferenceOutput(
                preferred_language=None,
                output_format="json",
                date_format=None,
                safety_strictness=None,
                row_limit_hint=None,
                rationale="stub: user requested change",
            )
        return PreferencesInferenceOutput.no_change("stub: no change")

    monkeypatch.setattr(infer_mod, "infer_preferences_delta", _once_then_noop)
    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-reject-1", run_kind="pytest")

    await app.ainvoke(
        {"user_input": "show JSON", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    # Resume with "reject" sentinel = rejection
    out2 = await app.ainvoke(Command(resume="reject"), config=cfg, version="v2")
    state, interrupts = unwrap_query_graph_v2(out2)

    assert not interrupts, f"unexpected interrupts: {interrupts}"
    # persist must NOT have been called (no data patched into fake_store)
    assert fake_store.get("default")["output_format"] == "table"  # default
    assert state.last_error is None


@pytest.mark.asyncio
async def test_approved_row_limit_hint_enforced_in_sql(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After approving row_limit_hint=3, SQL without LIMIT gets LIMIT 3 injected."""
    fake_store = _FakePrefsStore()
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")
    memory_mod = importlib.import_module("graph.memory_nodes")

    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"row_limit_hint": 3}),
    )
    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr(
        memory_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-limit-1", run_kind="pytest")

    # Turn 1: trigger HITL for row_limit_hint=3
    await app.ainvoke(
        {"user_input": "limit to 3 rows always", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    # Approve
    await app.ainvoke(Command(resume={"row_limit_hint": 3}), config=cfg, version="v2")

    # Turn 2: fresh turn — prefs now say row_limit_hint=3; infer returns no delta
    infer_mod2 = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    monkeypatch.setattr(
        infer_mod2,
        "infer_preferences_delta",
        _no_delta_infer,
    )
    query_gen_mod = importlib.import_module(
        "graph.nodes.query_nodes.query_generate_sql",
    )

    async def _sql_no_limit(
        _ui: str,
        _qp: dict[str, Any] | None,
        _sc: dict[str, Any] | None,
        _rc: int,
        **_kw: Any,
    ) -> str:
        return (
            "SELECT actor_id, first_name, last_name "
            "FROM public.actor ORDER BY actor_id ASC"
        )

    monkeypatch.setattr(query_gen_mod, "build_sql", _sql_no_limit)

    out = await app.ainvoke(
        {"user_input": "list actors", "steps": [], **seed}, config=cfg
    )
    state, _ = unwrap_query_graph_v2(out)

    sql = state.query.generated_sql or ""
    assert "LIMIT 3" in sql.upper(), f"Expected LIMIT 3 in SQL, got: {sql!r}"


@pytest.mark.asyncio
async def test_approved_output_format_json_reflected_in_last_result(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After approving output_format=json the last_result carries that format."""
    fake_store = _FakePrefsStore()
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")
    memory_mod = importlib.import_module("graph.memory_nodes")

    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"output_format": "json"}),
    )
    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr(
        memory_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-fmt-1", run_kind="pytest")

    await app.ainvoke(
        {"user_input": "always JSON please", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    await app.ainvoke(
        Command(resume={"output_format": "json"}), config=cfg, version="v2"
    )

    # Turn 2 — no delta
    infer_mod2 = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    monkeypatch.setattr(
        infer_mod2,
        "infer_preferences_delta",
        _no_delta_infer,
    )
    out = await app.ainvoke(
        {"user_input": "list actors", "steps": [], **seed}, config=cfg
    )
    state, _ = unwrap_query_graph_v2(out)

    lr = state.last_result
    assert isinstance(lr, dict), f"last_result is not a dict: {lr!r}"
    assert lr.get("output_format") == "json", (
        f"expected json, got {lr.get('output_format')!r}"
    )


@pytest.mark.asyncio
async def test_approved_lenient_strictness_passes_critic_with_risks(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """safety_strictness=lenient lets SQL through even when the critic flags risks."""
    from agents.schemas.query_outputs import QueryCritiqueOutput

    fake_store = _FakePrefsStore()
    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")
    memory_mod = importlib.import_module("graph.memory_nodes")
    critic_mod = importlib.import_module("graph.nodes.query_nodes.query_critic")

    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"safety_strictness": "lenient"}),
    )
    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr(
        memory_mod, "UserPreferencesStore", lambda settings=None: fake_store
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    # Critic always returns accept + risks (would block under strict mode)
    async def _risky_critique(*_a: Any, **_kw: Any) -> dict:
        return QueryCritiqueOutput(
            verdict="accept",
            feedback="ok",
            risks=["Potentially slow query", "Join may be ambiguous"],
            assumptions=[],
        ).model_dump()

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-lenient-1", run_kind="pytest")

    await app.ainvoke(
        {"user_input": "be lenient please", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    await app.ainvoke(
        Command(resume={"safety_strictness": "lenient"}), config=cfg, version="v2"
    )

    # Turn 2 — no delta; wire the risky critic
    infer_mod2 = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    monkeypatch.setattr(
        infer_mod2,
        "infer_preferences_delta",
        _no_delta_infer,
    )
    monkeypatch.setattr(critic_mod, "build_query_critique", _risky_critique)

    out = await app.ainvoke(
        {"user_input": "show me films", "steps": [], **seed}, config=cfg
    )
    state, _ = unwrap_query_graph_v2(out)

    # Under lenient mode, critic accepts despite risks → execute must have run
    assert "query_execute" in state.steps
    assert state.last_error is None


@pytest.mark.asyncio
async def test_preferences_dirty_flag_triggers_upsert_in_update_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When preferences_dirty=True, memory_update_session upserts to the store."""
    from graph.memory_nodes import memory_update_session
    from graph.state import MemoryState, QueryGraphState

    upserted: list[dict] = []

    class _CapturingStore:
        def __init__(self, settings=None):
            pass

        def upsert(self, user_id: str, prefs: dict) -> None:
            upserted.append({"user_id": user_id, "prefs": prefs})

    memory_mod = importlib.import_module("graph.memory_nodes")
    monkeypatch.setattr(
        memory_mod, "UserPreferencesStore", lambda settings=None: _CapturingStore()
    )

    state = QueryGraphState(
        user_id="alice",
        memory=MemoryState(
            preferences={"output_format": "json", "row_limit_hint": 5},
            preferences_dirty=True,
        ),
    )

    result = await memory_update_session(state)

    assert len(upserted) == 1
    assert upserted[0]["user_id"] == "alice"
    assert upserted[0]["prefs"]["output_format"] == "json"
    assert result.get("memory", {}).get("preferences_dirty") is False


@pytest.mark.asyncio
async def test_preferences_persist_db_error_does_not_abort_turn(
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB failure in preferences_persist lets the query turn continue."""
    import psycopg

    infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
    persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")

    monkeypatch.setattr(
        infer_mod,
        "infer_preferences_delta",
        _delta_infer({"output_format": "json"}),
    )

    class _FailingStore:
        def __init__(self, settings=None):
            pass

        def patch(self, user_id: str, delta: dict) -> dict:
            raise psycopg.OperationalError("DB down")

    monkeypatch.setattr(
        persist_mod, "UserPreferencesStore", lambda settings=None: _FailingStore()
    )
    monkeypatch.setattr("graph.mcp_helpers.get_mcp_client", _fake_mcp_client)

    app = get_compiled_query_graph()
    cfg, seed = graph_run_config(thread_id="pref-persist-fail-1", run_kind="pytest")

    await app.ainvoke(
        {"user_input": "always JSON", "steps": [], **seed},
        config=cfg,
        version="v2",
    )
    out2 = await app.ainvoke(
        Command(resume={"output_format": "json"}), config=cfg, version="v2"
    )
    state, interrupts = unwrap_query_graph_v2(out2)

    # Graph must still complete — soft-fail
    assert not interrupts
    assert "preferences_persist" in state.steps
    assert "query_plan" in state.steps
    # Warning should be set but last_error should be about the query, not the pref store
    # (the query itself succeeded)
    assert state.last_error is None
