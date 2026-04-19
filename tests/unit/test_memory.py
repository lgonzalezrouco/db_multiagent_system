"""Unit tests for memory: stores, session helpers, memory nodes."""

from __future__ import annotations

import psycopg
import pytest

from graph.memory_nodes import memory_load_user, memory_update_session
from graph.presence import DbSchemaPresence, SchemaPresenceResult
from graph.state import GraphState, MemoryState, QueryPipelineState
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


def test_seed_session_fields_preserves_existing_history() -> None:
    """Existing conversation_history is preserved when seeding."""
    from graph.state import ConversationTurn

    turn = ConversationTurn(user_input="hello", sql="SELECT 1 LIMIT 1")
    state = GraphState(memory=MemoryState(conversation_history=[turn]))

    # When: seeding session fields
    delta = seed_session_fields(state)

    # Then: history is preserved
    assert "memory" in delta
    assert len(delta["memory"]["conversation_history"]) == 1
    assert delta["memory"]["conversation_history"][0].user_input == "hello"


def test_seed_session_fields_returns_empty_list_for_no_history() -> None:
    """Seed returns empty history when no prior turns exist."""
    state = GraphState()

    delta = seed_session_fields(state)

    assert "memory" in delta
    assert delta["memory"]["conversation_history"] == []


def test_snapshot_session_fields_appends_turn_when_sql_executed() -> None:
    """Snapshot appends a ConversationTurn when SQL was generated."""
    state = GraphState(
        user_input="list films",
        query=QueryPipelineState(
            generated_sql="SELECT * FROM film LIMIT 10",
            execution_result={
                "success": True,
                "rows_returned": 2,
                "rows": [{"title": "Academy Dinosaur"}, {"title": "Ace Goldfinger"}],
                "columns": ["title"],
            },
            explanation="Found 2 films.",
        ),
    )

    delta = snapshot_session_fields(state)

    assert "memory" in delta
    history = delta["memory"]["conversation_history"]
    assert len(history) == 1
    assert history[0].user_input == "list films"
    assert history[0].sql == "SELECT * FROM film LIMIT 10"
    assert history[0].explanation == "Found 2 films."


def test_snapshot_session_fields_skips_when_no_sql() -> None:
    """Snapshot does not append a turn when no SQL was generated (schema turns)."""
    state = GraphState(user_input="describe schema")

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_caps_history_at_max_turns() -> None:
    """Snapshot enforces the HISTORY_MAX_TURNS cap (oldest discarded)."""
    from graph.state import ConversationTurn
    from memory.session import HISTORY_MAX_TURNS

    existing = [
        ConversationTurn(user_input=f"q{i}", sql=f"SELECT {i} LIMIT 1")
        for i in range(HISTORY_MAX_TURNS)
    ]
    state = GraphState(
        user_input="new question",
        memory=MemoryState(conversation_history=existing),
        query=QueryPipelineState(
            generated_sql="SELECT 99 LIMIT 1",
            execution_result={"success": True, "rows_returned": 0},
        ),
    )

    delta = snapshot_session_fields(state)

    history = delta["memory"]["conversation_history"]
    assert len(history) == HISTORY_MAX_TURNS
    # The oldest entry should be dropped; newest is "new question"
    assert history[-1].user_input == "new question"
    assert history[0].user_input == "q1"  # q0 was dropped


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
    state = GraphState(user_id="alice")
    result = await memory_load_user(state)

    # Then: preferences and docs are loaded without warnings
    assert result["user_id"] == "alice"
    assert result["memory"]["preferences"] == {**default_preferences(), **fake_prefs}
    assert result["query"]["docs_context"] == sample_payload
    assert result["memory"].get("warning") is None
    assert result["query"].get("docs_warning") is None
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
    state = GraphState()
    result = await memory_load_user(state)

    # Then: schema docs warning is set
    assert result["query"].get("docs_context") is None
    assert result["query"].get("docs_warning") is not None
    assert result["memory"].get("warning") is None


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
    state = GraphState()
    result = await memory_load_user(state)

    # Then: defaults are used and warning is set
    assert result["memory"]["preferences"] == default_preferences()
    assert result["memory"].get("warning") is not None
    assert "unreachable" in result["memory"]["warning"]


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
    state = GraphState()
    result = await memory_load_user(state)

    # Then: warnings are set for both
    assert result["memory"].get("preferences") is not None
    assert result["query"].get("docs_warning") is not None
    assert result["memory"].get("warning") is not None


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_both_db_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory falls back gracefully when both DBs are unreachable."""
    # Given: both stores that fail
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    # When: loading user memory
    state = GraphState()
    result = await memory_load_user(state)

    # Then: defaults are used and warnings are set
    assert result["memory"]["preferences"] == default_preferences()
    assert result["memory"].get("warning") is not None
    assert result["query"].get("docs_warning") is not None


# ---------------------------------------------------------------------------
# memory_update_session (mocked stores)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_update_session_snapshots_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session update snapshots SQL into conversation history."""
    # Given: completed query state
    state = GraphState(
        user_input="list films",
        query=QueryPipelineState(
            generated_sql="SELECT * FROM film LIMIT 5",
            execution_result={"success": True, "rows_returned": 2},
            explanation="Found films.",
        ),
    )

    # When: updating session
    result = await memory_update_session(state)

    # Then: conversation history has the new turn
    assert "memory_update_session" in result["steps"]
    history = result["memory"]["conversation_history"]
    assert len(history) == 1
    assert history[0].user_input == "list films"
    assert history[0].sql == "SELECT * FROM film LIMIT 5"


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

    state = GraphState(
        user_id="bob",
        memory=MemoryState(
            preferences={"preferred_language": "fr", "row_limit_hint": 20},
            preferences_dirty=True,
        ),
    )

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

    state = GraphState(
        user_id="carol",
        memory=MemoryState(
            preferences={"row_limit_hint": 5},
            preferences_dirty=True,
        ),
    )

    # When: updating session
    result = await memory_update_session(state)

    # Then: warning is set
    assert result.get("memory", {}).get("warning") is not None
    assert "could not persist" in result["memory"]["warning"]
