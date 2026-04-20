"""Unit tests for the three preferences query-pipeline nodes.

Covers:
- preferences_infer  — calls LLM builder, writes proposed_delta to state
- preferences_hitl   — interrupt payload shape, approve/reject semantics
- preferences_persist — patch call, merged prefs written to state, soft-fail
- route_after_preferences_infer / route_after_preferences_hitl — routing logic
"""

from __future__ import annotations

import importlib
from typing import Any

import psycopg
import pytest

from agents.schemas.preferences_outputs import PreferencesInferenceOutput
from graph.nodes.query_nodes import (
    route_after_preferences_hitl,
    route_after_preferences_infer,
)
from graph.nodes.query_nodes.preferences_hitl import preferences_hitl
from graph.nodes.query_nodes.preferences_infer import preferences_infer
from graph.nodes.query_nodes.preferences_persist import preferences_persist
from graph.state import MemoryState, QueryGraphState

# Import the actual module objects so monkeypatch.setattr can replace
# module-level names (not the re-exported function references in __init__).
_hitl_mod = importlib.import_module("graph.nodes.query_nodes.preferences_hitl")
_infer_mod = importlib.import_module("graph.nodes.query_nodes.preferences_infer")
_persist_mod = importlib.import_module("graph.nodes.query_nodes.preferences_persist")


def _async_const(value: Any):
    """Return an async function that always returns *value*."""

    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner


def _state(
    user_input: str = "",
    preferences: dict | None = None,
    proposed_delta: dict | None = None,
    rationale: str | None = None,
    user_id: str = "alice",
) -> QueryGraphState:
    return QueryGraphState(
        user_input=user_input,
        user_id=user_id,
        memory=MemoryState(
            preferences=preferences or {"output_format": "table", "row_limit_hint": 10},
            preferences_proposed_delta=proposed_delta,
            preferences_rationale=rationale,
        ),
    )


_NO_DELTA = PreferencesInferenceOutput.no_change(
    "No persistent change detected.",
)
_WITH_DELTA = PreferencesInferenceOutput(
    preferred_language=None,
    output_format="json",
    date_format=None,
    safety_strictness=None,
    row_limit_hint=None,
    rationale="User asked to always use JSON.",
)


@pytest.mark.asyncio
async def test_preferences_infer_sets_none_delta_when_no_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_infer_mod, "infer_preferences_delta", _async_const(_NO_DELTA))
    result = await preferences_infer(_state("list all films"))

    assert result["memory"]["preferences_proposed_delta"] is None
    assert result["memory"]["preferences_rationale"]
    assert "preferences_infer" in result["steps"]


@pytest.mark.asyncio
async def test_preferences_infer_sets_delta_when_intent_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _infer_mod, "infer_preferences_delta", _async_const(_WITH_DELTA)
    )
    result = await preferences_infer(_state("always show me JSON"))

    assert result["memory"]["preferences_proposed_delta"] == {"output_format": "json"}
    assert result["memory"]["preferences_rationale"]


@pytest.mark.asyncio
async def test_preferences_infer_passes_history_as_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conversation history is serialised to dicts before being passed to agent."""
    from graph.state import ConversationTurn

    captured: list[Any] = []

    async def _fake_infer(
        user_input, *, current_preferences=None, conversation_history=None
    ):
        captured.append(conversation_history)
        return _NO_DELTA

    monkeypatch.setattr(_infer_mod, "infer_preferences_delta", _fake_infer)

    state = QueryGraphState(
        user_input="test",
        memory=MemoryState(
            preferences={},
            conversation_history=[
                ConversationTurn(user_input="prior", sql="SELECT 1 LIMIT 1")
            ],
        ),
    )
    await preferences_infer(state)

    assert captured[0] is not None
    assert isinstance(captured[0][0], dict)  # serialised to dict, not ConversationTurn
    assert captured[0][0]["user_input"] == "prior"


@pytest.mark.asyncio
async def test_preferences_infer_passes_none_history_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    async def _fake_infer(
        user_input, *, current_preferences=None, conversation_history=None
    ):
        captured.append(conversation_history)
        return _NO_DELTA

    monkeypatch.setattr(_infer_mod, "infer_preferences_delta", _fake_infer)

    state = QueryGraphState(user_input="test", memory=MemoryState(preferences={}))
    await preferences_infer(state)

    assert captured[0] is None


def test_preferences_hitl_interrupt_payload_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """interrupt() is called with the correct kind and current prefs."""
    payloads: list[dict] = []

    def _fake_interrupt(payload: dict) -> dict:
        payloads.append(payload)
        return {"output_format": "json"}  # simulate user approval

    monkeypatch.setattr(_hitl_mod, "interrupt", _fake_interrupt)

    state = _state(
        proposed_delta={"output_format": "json"},
        rationale="User asked for JSON.",
        preferences={"output_format": "table", "row_limit_hint": 10},
    )
    preferences_hitl(state)

    assert len(payloads) == 1
    p = payloads[0]
    assert p["kind"] == "preferences_review"
    assert p["proposed_delta"] == {"output_format": "json"}
    assert p["rationale"] == "User asked for JSON."
    assert p["current"] == {"output_format": "table", "row_limit_hint": 10}


def test_preferences_hitl_approved_delta_stored_in_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user approves, proposed_delta is updated with approved value."""
    monkeypatch.setattr(_hitl_mod, "interrupt", lambda _: {"output_format": "json"})
    state = _state(proposed_delta={"output_format": "json"})
    result = preferences_hitl(state)

    assert result["memory"]["preferences_proposed_delta"] == {"output_format": "json"}
    assert result["memory"]["preferences_rationale"] is None


def test_preferences_hitl_none_resume_clears_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When interrupt returns None (non-dict), delta is cleared."""
    monkeypatch.setattr(_hitl_mod, "interrupt", lambda _: None)
    state = _state(proposed_delta={"output_format": "json"})
    result = preferences_hitl(state)

    assert result["memory"]["preferences_proposed_delta"] is None


def test_preferences_hitl_empty_dict_resume_clears_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When interrupt returns {}, it is treated as rejection."""
    monkeypatch.setattr(_hitl_mod, "interrupt", lambda _: {})
    state = _state(proposed_delta={"output_format": "json"})
    result = preferences_hitl(state)

    assert result["memory"]["preferences_proposed_delta"] is None


def test_preferences_hitl_reject_string_clears_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When interrupt returns 'reject' sentinel string, delta is cleared."""
    monkeypatch.setattr(_hitl_mod, "interrupt", lambda _: "reject")
    state = _state(proposed_delta={"output_format": "json"})
    result = preferences_hitl(state)

    assert result["memory"]["preferences_proposed_delta"] is None


def test_preferences_hitl_step_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_hitl_mod, "interrupt", lambda _: {"output_format": "json"})
    result = preferences_hitl(_state(proposed_delta={"output_format": "json"}))
    assert "preferences_hitl" in result["steps"]


class _FakeStore:
    def __init__(self, settings=None):
        self.patched: list[tuple[str, dict]] = []

    def patch(self, user_id: str, delta: dict) -> dict:
        self.patched.append((user_id, delta))
        return {"output_format": "json", "row_limit_hint": 10}

    def _ensure_table(self):
        pass


@pytest.mark.asyncio
async def test_preferences_persist_calls_patch_with_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(
        _persist_mod, "UserPreferencesStore", lambda settings=None: store
    )
    state = _state(user_id="alice", proposed_delta={"output_format": "json"})
    result = await preferences_persist(state)

    assert store.patched == [("alice", {"output_format": "json"})]
    assert result["memory"]["preferences"] == {
        "output_format": "json",
        "row_limit_hint": 10,
    }
    assert result["memory"]["preferences_proposed_delta"] is None


@pytest.mark.asyncio
async def test_preferences_persist_is_noop_when_delta_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(
        _persist_mod, "UserPreferencesStore", lambda settings=None: store
    )
    state = _state(proposed_delta=None)
    result = await preferences_persist(state)

    assert store.patched == []
    assert result["memory"]["preferences_proposed_delta"] is None


@pytest.mark.asyncio
async def test_preferences_persist_step_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _persist_mod, "UserPreferencesStore", lambda settings=None: _FakeStore()
    )
    result = await preferences_persist(_state(proposed_delta={"row_limit_hint": 5}))
    assert "preferences_persist" in result["steps"]


@pytest.mark.asyncio
async def test_preferences_persist_soft_fails_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailStore:
        def __init__(self, settings=None):
            pass

        def patch(self, user_id: str, delta: dict) -> dict:
            raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(_persist_mod, "UserPreferencesStore", _FailStore)
    state = _state(proposed_delta={"output_format": "json"})
    result = await preferences_persist(state)

    # Must not raise; should set warning and clear delta
    assert result["memory"]["preferences_proposed_delta"] is None
    assert "warning" in result["memory"]
    assert result["memory"]["warning"]


def test_route_infer_goes_to_hitl_when_delta_present() -> None:
    state = _state(proposed_delta={"output_format": "json"})
    assert route_after_preferences_infer(state) == "preferences_hitl"


def test_route_infer_skips_hitl_when_delta_is_none() -> None:
    state = _state(proposed_delta=None)
    assert route_after_preferences_infer(state) == "query_plan"


def test_route_infer_skips_hitl_when_delta_is_empty_dict() -> None:
    # Empty dict is falsy — treated as no delta
    state = _state(proposed_delta={})
    assert route_after_preferences_infer(state) == "query_plan"


def test_route_hitl_goes_to_persist_when_delta_approved() -> None:
    state = _state(proposed_delta={"output_format": "json"})
    assert route_after_preferences_hitl(state) == "preferences_persist"


def test_route_hitl_skips_persist_when_delta_rejected() -> None:
    state = _state(proposed_delta=None)
    assert route_after_preferences_hitl(state) == "query_plan"
