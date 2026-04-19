"""Unit tests for memory: stores, session helpers, memory nodes."""

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
    """Default preferences contain all required keys with expected defaults."""
    # Given: no existing preferences

    # When: getting default preferences
    prefs = default_preferences()

    # Then: all canonical keys are present with correct defaults
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


def test_seed_session_fields_preserves_existing_values() -> None:
    """Existing session fields are preserved when seeding."""
    # Given: state with existing session fields
    state: dict[str, Any] = {
        "previous_user_input": "hello",
        "previous_sql": "SELECT 1",
        "assumptions": ["a"],
        "recent_filters": {"table": "actor"},
    }

    # When: seeding session fields
    delta = seed_session_fields(state)

    # Then: existing values are preserved
    assert delta["previous_user_input"] == "hello"
    assert delta["previous_sql"] == "SELECT 1"
    assert delta["assumptions"] == ["a"]
    assert delta["recent_filters"] == {"table": "actor"}


def test_seed_session_fields_returns_none_for_missing_keys() -> None:
    """Missing session fields are initialized to None."""
    # Given: empty state
    state: dict[str, Any] = {}

    # When: seeding session fields
    delta = seed_session_fields(state)

    # Then: all keys are None
    assert delta["previous_user_input"] is None
    assert delta["previous_sql"] is None
    assert delta["assumptions"] is None
    assert delta["recent_filters"] is None


def test_snapshot_session_fields_extracts_from_completed_run() -> None:
    """Session fields are extracted from completed run state."""
    # Given: state with completed query results
    state: dict[str, Any] = {
        "user_input": "list films",
        "last_result": {"sql": "SELECT * FROM film LIMIT 10", "kind": "query_answer"},
        "assumptions": ["public schema"],
        "recent_filters": {"limit": 10},
    }

    # When: snapshotting session fields
    delta = snapshot_session_fields(state)

    # Then: values are extracted for next session
    assert delta["previous_user_input"] == "list films"
    assert delta["previous_sql"] == "SELECT * FROM film LIMIT 10"
    assert delta["assumptions"] == ["public schema"]
    assert delta["recent_filters"] == {"limit": 10}


def test_snapshot_session_fields_handles_missing_last_result() -> None:
    """Missing last_result results in None for previous_sql."""
    # Given: state without last_result
    state: dict[str, Any] = {"user_input": "q"}

    # When: snapshotting session fields
    delta = snapshot_session_fields(state)

    # Then: previous_sql is None
    assert delta["previous_sql"] is None


# ---------------------------------------------------------------------------
# DbSchemaPresence with mocked store
# ---------------------------------------------------------------------------


def test_db_schema_presence_returns_ready_when_store_ready() -> None:
    """Schema presence returns ready when store is ready."""

    # Given: a store that reports ready
    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    # When: checking presence
    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()

    # Then: result indicates ready with no reason
    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_returns_not_ready_when_store_not_ready() -> None:
    """Schema presence returns not ready when store is not ready."""

    # Given: a store that reports not ready
    class _NotReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return False

    # When: checking presence
    presence = DbSchemaPresence(store=_NotReadyStore())
    result = presence.check()

    # Then: result indicates not ready with reason
    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_soft_fails_on_operational_error() -> None:
    """Schema presence handles DB connection errors gracefully."""

    # Given: a store that raises OperationalError
    class _ErrorStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            raise psycopg.OperationalError("connection refused")

    # When: checking presence
    presence = DbSchemaPresence(store=_ErrorStore())
    result = presence.check()

    # Then: result indicates not ready with unreachable reason
    assert result.ready is False
    assert result.reason is not None
    assert "unreachable" in result.reason


# ---------------------------------------------------------------------------
# memory_load_user (mocked stores)
# ---------------------------------------------------------------------------


class _FakePrefsStore:
    """In-memory UserPreferencesStore substitute."""

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
    """User memory loads preferences and schema docs successfully."""
    # Given: stores with existing prefs and docs
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

    # When: loading user memory
    result = await memory_load_user({"user_id": "alice", "steps": []})

    # Then: preferences and docs are loaded without warnings
    assert result["user_id"] == "alice"
    assert result["preferences"] == {**default_preferences(), **fake_prefs}
    assert result["schema_docs_context"] == sample_payload
    assert result["memory_warning"] is None
    assert result["schema_docs_warning"] is None
    assert "memory_load_user" in result["steps"]


@pytest.mark.asyncio
async def test_memory_load_user_sets_warning_when_no_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory warns when schema docs are not available."""
    # Given: stores without schema docs
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    # When: loading user memory
    result = await memory_load_user({"steps": []})

    # Then: schema docs warning is set
    assert result["schema_docs_context"] is None
    assert result["schema_docs_warning"] is not None
    assert result["memory_warning"] is None


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_prefs_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory falls back to defaults when prefs DB is unreachable."""
    # Given: prefs store that fails
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    # When: loading user memory
    result = await memory_load_user({"steps": []})

    # Then: defaults are used and warning is set
    assert result["preferences"] == default_preferences()
    assert result["memory_warning"] is not None
    assert "unreachable" in result["memory_warning"]


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_docs_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory warns when docs DB is unreachable."""
    # Given: docs store that fails
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    # When: loading user memory
    result = await memory_load_user({"steps": []})

    # Then: warnings are set for both
    assert result["preferences"] is not None
    assert result["schema_docs_warning"] is not None
    assert result["memory_warning"] is not None


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_both_db_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory falls back gracefully when both DBs are unreachable."""
    # Given: both stores that fail
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    # When: loading user memory
    result = await memory_load_user({"steps": []})

    # Then: defaults are used and warnings are set
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
    """Session update snapshots fields for next session."""
    # Given: completed query state
    state = {
        "steps": [],
        "user_input": "list films",
        "last_result": {"sql": "SELECT * FROM film LIMIT 5", "kind": "query_answer"},
        "assumptions": ["public"],
        "recent_filters": {},
        "preferences_dirty": False,
    }

    # When: updating session
    result = await memory_update_session(state)

    # Then: fields are snapshotted
    assert result["previous_user_input"] == "list films"
    assert result["previous_sql"] == "SELECT * FROM film LIMIT 5"
    assert "memory_update_session" in result["steps"]


@pytest.mark.asyncio
async def test_memory_update_session_upserts_when_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session update persists preferences when dirty flag is set."""
    # Given: dirty preferences
    upserted: list[dict] = []

    class _CapturingPrefsStore(_FakePrefsStore):
        def upsert(self, user_id: str, prefs: dict) -> None:
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

    # When: updating session
    await memory_update_session(state)

    # Then: preferences are persisted
    assert len(upserted) == 1
    assert upserted[0]["user_id"] == "bob"
    assert upserted[0]["prefs"]["preferred_language"] == "fr"


@pytest.mark.asyncio
async def test_memory_update_session_skips_upsert_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session update handles DB errors gracefully."""
    # Given: failing prefs store
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)

    state = {
        "steps": [],
        "user_id": "carol",
        "preferences": {"row_limit_hint": 5},
        "preferences_dirty": True,
    }

    # When: updating session
    result = await memory_update_session(state)

    # Then: warning is set
    assert result.get("memory_warning") is not None
    assert "could not persist" in result["memory_warning"]
