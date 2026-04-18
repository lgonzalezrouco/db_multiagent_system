"""Unit tests for src/memory/: stores, session helpers, memory nodes."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from graph.memory_nodes import memory_load_user, memory_update_session
from graph.presence import DbSchemaPresence, SchemaPresenceResult
from memory.preferences import default_preferences
from memory.session import seed_session_fields, snapshot_session_fields

# ---------------------------------------------------------------------------
# default_preferences
# ---------------------------------------------------------------------------


def test_default_preferences_returns_all_canonical_keys() -> None:
    prefs = default_preferences()
    assert set(prefs) >= {
        "preferred_language",
        "output_format",
        "date_format",
        "safety_strictness",
        "row_limit_hint",
    }
    assert prefs["preferred_language"] == "en"
    assert prefs["row_limit_hint"] == 10


# ---------------------------------------------------------------------------
# session helpers
# ---------------------------------------------------------------------------


def test_seed_session_fields_preserves_existing() -> None:
    state: dict[str, Any] = {
        "previous_user_input": "hello",
        "previous_sql": "SELECT 1",
        "assumptions": ["a"],
        "recent_filters": {"table": "actor"},
    }
    delta = seed_session_fields(state)
    assert delta["previous_user_input"] == "hello"
    assert delta["previous_sql"] == "SELECT 1"
    assert delta["assumptions"] == ["a"]
    assert delta["recent_filters"] == {"table": "actor"}


def test_seed_session_fields_returns_none_for_missing_keys() -> None:
    delta = seed_session_fields({})
    assert delta["previous_user_input"] is None
    assert delta["previous_sql"] is None
    assert delta["assumptions"] is None
    assert delta["recent_filters"] is None


def test_snapshot_session_fields_extracts_from_completed_run() -> None:
    state: dict[str, Any] = {
        "user_input": "list films",
        "last_result": {"sql": "SELECT * FROM film LIMIT 10", "kind": "query_answer"},
        "assumptions": ["public schema"],
        "recent_filters": {"limit": 10},
    }
    delta = snapshot_session_fields(state)
    assert delta["previous_user_input"] == "list films"
    assert delta["previous_sql"] == "SELECT * FROM film LIMIT 10"
    assert delta["assumptions"] == ["public schema"]
    assert delta["recent_filters"] == {"limit": 10}


def test_snapshot_session_fields_handles_missing_last_result() -> None:
    state: dict[str, Any] = {"user_input": "q"}
    delta = snapshot_session_fields(state)
    assert delta["previous_sql"] is None


# ---------------------------------------------------------------------------
# DbSchemaPresence with mocked store
# ---------------------------------------------------------------------------


def test_db_schema_presence_ready_store() -> None:
    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()
    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_not_ready_store() -> None:
    class _NotReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return False

    presence = DbSchemaPresence(store=_NotReadyStore())
    result = presence.check()
    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_operational_error_soft_fails() -> None:
    class _ErrorStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            raise psycopg.OperationalError("connection refused")

    presence = DbSchemaPresence(store=_ErrorStore())
    result = presence.check()
    assert result.ready is False
    assert result.reason is not None
    assert "unreachable" in result.reason


# ---------------------------------------------------------------------------
# memory_load_user (mocked stores)
# ---------------------------------------------------------------------------


class _FakePrefsStore:
    """In-memory UserPreferencesStore substitute. Merges stored prefs with defaults."""

    def __init__(self, settings=None, *, prefs: dict | None = None) -> None:
        self._stored = prefs or {}

    def get(self, user_id: str) -> dict:
        return {**default_preferences(), **self._stored}

    def upsert(self, user_id: str, prefs: dict) -> None:
        self._stored = prefs


class _FakeSchemaDocsStore:
    """In-memory SchemaDocsStore substitute."""

    def __init__(self, settings=None, *, payload: dict | None = None) -> None:
        self._payload = payload

    def get_payload(self) -> dict | None:
        return self._payload

    def is_ready(self) -> bool:
        return self._payload is not None

    def upsert_approved(
        self,
        payload: dict,
        metadata_fingerprint: str | None = None,
    ) -> None:
        self._payload = payload


class _ErrorStore:
    """Store that always raises OperationalError on construction."""

    def __init__(self, settings=None) -> None:
        raise psycopg.OperationalError("simulated DB down")


@pytest.mark.asyncio
async def test_memory_load_user_loads_prefs_and_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_payload = {"version": 1, "tables": [{"name": "actor"}]}
    fake_prefs = {"preferred_language": "es", "row_limit_hint": 5}
    store_instance_prefs = _FakePrefsStore(prefs=fake_prefs)
    store_instance_docs = _FakeSchemaDocsStore(payload=sample_payload)

    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: store_instance_prefs,
    )
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: store_instance_docs,
    )

    result = await memory_load_user({"user_id": "alice", "steps": []})

    assert result["user_id"] == "alice"
    assert result["preferences"] == {**default_preferences(), **fake_prefs}
    assert result["schema_docs_context"] == sample_payload
    assert result["memory_warning"] is None
    assert result["schema_docs_warning"] is None
    assert "memory_load_user" in result["steps"]


@pytest.mark.asyncio
async def test_memory_load_user_no_docs_sets_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    result = await memory_load_user({"steps": []})

    assert result["schema_docs_context"] is None
    assert result["schema_docs_warning"] is not None
    assert result["memory_warning"] is None


@pytest.mark.asyncio
async def test_memory_load_user_prefs_db_error_soft_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    result = await memory_load_user({"steps": []})

    assert result["preferences"] == default_preferences()
    assert result["memory_warning"] is not None
    assert "unreachable" in result["memory_warning"]


@pytest.mark.asyncio
async def test_memory_load_user_docs_db_error_soft_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    result = await memory_load_user({"steps": []})

    assert result["preferences"] is not None
    assert result["schema_docs_warning"] is not None
    assert result["memory_warning"] is not None


@pytest.mark.asyncio
async def test_memory_load_user_both_db_errors_soft_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    result = await memory_load_user({"steps": []})

    assert result["preferences"] == default_preferences()
    assert result["memory_warning"] is not None
    assert result["schema_docs_warning"] is not None


# ---------------------------------------------------------------------------
# memory_update_session (mocked stores)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_update_session_snapshots_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "steps": [],
        "user_input": "list films",
        "last_result": {"sql": "SELECT * FROM film LIMIT 5", "kind": "query_answer"},
        "assumptions": ["public"],
        "recent_filters": {},
        "preferences_dirty": False,
    }

    result = await memory_update_session(state)

    assert result["previous_user_input"] == "list films"
    assert result["previous_sql"] == "SELECT * FROM film LIMIT 5"
    assert "memory_update_session" in result["steps"]


@pytest.mark.asyncio
async def test_memory_update_session_upserts_when_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upserted: list[dict] = []

    class _CapturingPrefsStore(_FakePrefsStore):
        def upsert(self, user_id: str, prefs: dict) -> None:  # type: ignore[override]
            upserted.append({"user_id": user_id, "prefs": prefs})
            super().upsert(user_id, prefs)

    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _CapturingPrefsStore(),
    )

    state = {
        "steps": [],
        "user_id": "bob",
        "preferences": {"preferred_language": "fr", "row_limit_hint": 20},
        "preferences_dirty": True,
    }

    await memory_update_session(state)

    assert len(upserted) == 1
    assert upserted[0]["user_id"] == "bob"
    assert upserted[0]["prefs"]["preferred_language"] == "fr"


@pytest.mark.asyncio
async def test_memory_update_session_db_error_skips_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)

    state = {
        "steps": [],
        "user_id": "carol",
        "preferences": {"row_limit_hint": 5},
        "preferences_dirty": True,
    }

    result = await memory_update_session(state)

    assert result.get("memory_warning") is not None
    assert "could not persist" in result["memory_warning"]
