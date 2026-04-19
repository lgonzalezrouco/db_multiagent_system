"""Unit tests for memory: stores, session helpers, memory nodes, state models."""

from __future__ import annotations

import psycopg
import pytest

from graph.memory_nodes import memory_load_user, memory_update_session
from graph.presence import DbSchemaPresence, SchemaPresenceResult
from graph.state import (
    ConversationTurn,
    GraphState,
    MemoryState,
    QueryPipelineState,
    SchemaPipelineState,
    append_steps,
    merge_submodel,
)
from memory.preferences import default_preferences
from memory.session import (
    HISTORY_MAX_TURNS,
    HISTORY_ROW_VALUE_MAX_CHARS,
    HISTORY_ROWS_PREVIEW,
    _trim_rows,
    seed_session_fields,
    snapshot_session_fields,
)

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


def test_snapshot_session_fields_skips_on_last_error() -> None:
    """Snapshot does not append when the turn ended with last_error set."""
    state = GraphState(
        user_input="bad query",
        last_error="Critic rejected SQL after max refinement attempts.",
        query=QueryPipelineState(
            generated_sql="SELECT 1",
            execution_result={"success": True, "rows_returned": 0},
        ),
    )

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_session_fields_skips_on_failed_execution() -> None:
    """Snapshot does not append when execution_result.success is False."""
    state = GraphState(
        user_input="broken query",
        query=QueryPipelineState(
            generated_sql="SELECT * FROM missing_table LIMIT 1",
            execution_result={"success": False, "error": {"type": "database_error"}},
        ),
    )

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_caps_history_at_max_turns() -> None:
    """Snapshot enforces the HISTORY_MAX_TURNS cap (oldest discarded)."""
    from graph.state import ConversationTurn

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
    result = await memory_update_session(state)

    # Then: preferences are persisted and dirty flag is cleared
    assert len(upserted) == 1
    assert upserted[0]["user_id"] == "bob"
    assert upserted[0]["prefs"]["preferred_language"] == "fr"
    assert result.get("memory", {}).get("preferences_dirty") is False


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


# ---------------------------------------------------------------------------
# append_steps reducer
# ---------------------------------------------------------------------------


def test_append_steps_extends_list() -> None:
    assert append_steps(["a", "b"], ["c"]) == ["a", "b", "c"]


def test_append_steps_empty_update_returns_current() -> None:
    assert append_steps(["a"], []) == ["a"]


def test_append_steps_none_update_returns_current() -> None:
    assert append_steps(["a"], None) == ["a"]  # type: ignore[arg-type]


def test_append_steps_does_not_mutate_original() -> None:
    original = ["a", "b"]
    append_steps(original, ["c"])
    assert original == ["a", "b"]


# ---------------------------------------------------------------------------
# merge_submodel reducer
# ---------------------------------------------------------------------------


def test_merge_submodel_none_update_returns_current() -> None:
    current = QueryPipelineState(generated_sql="SELECT 1")
    assert merge_submodel(current, None) is current


def test_merge_submodel_dict_overwrites_specified_fields() -> None:
    current = QueryPipelineState(generated_sql="SELECT 1", refinement_count=0)
    result = merge_submodel(
        current, {"generated_sql": "SELECT 2", "refinement_count": 1}
    )
    assert result.generated_sql == "SELECT 2"
    assert result.refinement_count == 1


def test_merge_submodel_preserves_unspecified_fields() -> None:
    current = QueryPipelineState(
        generated_sql="SELECT 1", plan={"intent": "explore"}, refinement_count=2
    )
    result = merge_submodel(current, {"generated_sql": "SELECT 2"})
    assert result.plan == {"intent": "explore"}
    assert result.refinement_count == 2


def test_merge_submodel_basemodel_only_sets_explicitly_set_fields() -> None:
    current = QueryPipelineState(generated_sql="SELECT 1", refinement_count=3)
    update = QueryPipelineState(generated_sql="SELECT 2")
    result = merge_submodel(current, update)
    assert result.generated_sql == "SELECT 2"
    assert result.refinement_count == 3  # not overwritten


def test_merge_submodel_schema_preserves_unset() -> None:
    current = SchemaPipelineState(ready=True, metadata={"tables": []})
    result = merge_submodel(current, {"persist_error": "DB down"})
    assert result.ready is True
    assert result.persist_error == "DB down"


# ---------------------------------------------------------------------------
# GraphState / sub-model defaults
# ---------------------------------------------------------------------------


def test_graph_state_defaults() -> None:
    state = GraphState()
    assert state.user_input == ""
    assert state.steps == []
    assert isinstance(state.schema_pipeline, SchemaPipelineState)
    assert isinstance(state.query, QueryPipelineState)
    assert isinstance(state.memory, MemoryState)


def test_conversation_turn_defaults() -> None:
    t = ConversationTurn(user_input="test")
    assert t.sql is None
    assert t.row_count is None
    assert t.rows_preview == []
    assert t.explanation is None


# ---------------------------------------------------------------------------
# _trim_rows helper
# ---------------------------------------------------------------------------


def test_trim_rows_keeps_up_to_preview_limit() -> None:
    result = {"rows": [{"n": i} for i in range(10)]}
    rows = _trim_rows(result)
    assert len(rows) <= HISTORY_ROWS_PREVIEW


def test_trim_rows_truncates_long_string_values() -> None:
    long_val = "x" * (HISTORY_ROW_VALUE_MAX_CHARS + 50)
    rows = _trim_rows({"rows": [{"title": long_val}]})
    assert len(rows[0]["title"]) == HISTORY_ROW_VALUE_MAX_CHARS


def test_trim_rows_preserves_non_string_values() -> None:
    rows = _trim_rows({"rows": [{"n": 42, "f": 0.5, "x": None}]})
    assert rows[0] == {"n": 42, "f": 0.5, "x": None}


def test_trim_rows_returns_empty_for_none_input() -> None:
    assert _trim_rows(None) == []


# ---------------------------------------------------------------------------
# snapshot row preview and row_count
# ---------------------------------------------------------------------------


def test_snapshot_includes_row_preview() -> None:
    state = GraphState(
        user_input="actors",
        query=QueryPipelineState(
            generated_sql="SELECT * FROM actor LIMIT 5",
            execution_result={
                "success": True,
                "rows_returned": 3,
                "rows": [
                    {"first_name": "Nick"},
                    {"first_name": "Ed"},
                    {"first_name": "Jennifer"},
                ],
            },
        ),
    )
    delta = snapshot_session_fields(state)
    turn = delta["memory"]["conversation_history"][0]
    assert len(turn.rows_preview) == min(3, HISTORY_ROWS_PREVIEW)
    assert turn.rows_preview[0]["first_name"] == "Nick"


def test_snapshot_extracts_row_count() -> None:
    state = GraphState(
        user_input="count",
        query=QueryPipelineState(
            generated_sql="SELECT COUNT(*) LIMIT 1",
            execution_result={
                "success": True,
                "rows_returned": 42,
                "rows": [{"n": 42}],
            },
        ),
    )
    delta = snapshot_session_fields(state)
    assert delta["memory"]["conversation_history"][0].row_count == 42
