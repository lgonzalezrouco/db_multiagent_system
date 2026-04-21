"""Unit tests for memory: stores, session helpers, memory nodes, state models."""

from __future__ import annotations

import psycopg
import pytest

from graph.memory_nodes import memory_load_user
from graph.presence import DbSchemaPresence, SchemaPresenceResult
from graph.state import (
    ConversationTurn,
    MemoryState,
    QueryGraphState,
    QueryPipelineState,
    SchemaPipelineState,
    append_steps,
    merge_submodel,
)
from memory.preferences import UserPreferencesStore, default_preferences
from memory.session import (
    HISTORY_MAX_TURNS,
    HISTORY_ROW_VALUE_MAX_CHARS,
    HISTORY_ROWS_PREVIEW,
    _trim_rows,
    seed_session_fields,
    snapshot_session_fields,
)


def test_default_preferences_returns_all_canonical_keys() -> None:
    """Default preferences contain all required keys with expected defaults."""

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


class _InMemoryPrefsDB:
    """Minimal in-memory stand-in for the app_memory Postgres table.

    Simulates the JSONB || merge semantics of the real patch() SQL without
    requiring a live DB connection.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def get_row(self, user_id: str) -> dict | None:
        return self._rows.get(user_id)

    def upsert_merge(self, user_id: str, delta: dict) -> dict:
        existing = self._rows.get(user_id, {})
        merged = {**existing, **delta}
        self._rows[user_id] = merged
        return merged

    def upsert_replace(self, user_id: str, prefs: dict) -> None:
        self._rows[user_id] = prefs


class _PatchablePrefsStore(UserPreferencesStore):
    """UserPreferencesStore subclass that overrides DB calls with in-memory backend."""

    def __init__(self, db: _InMemoryPrefsDB) -> None:
        self._db = db
        # Skip _ensure_table (no real DB)

    def _ensure_table(self) -> None:
        pass

    def get(self, user_id: str) -> dict:
        row = self._db.get_row(user_id)
        if row is None:
            return default_preferences()
        return {**default_preferences(), **row}

    def upsert(self, user_id: str, prefs: dict) -> None:
        self._db.upsert_replace(user_id, prefs)

    def patch(self, user_id: str, delta: dict) -> dict:
        merged_stored = self._db.upsert_merge(user_id, delta)
        return {**default_preferences(), **merged_stored}


def _make_store() -> tuple[_PatchablePrefsStore, _InMemoryPrefsDB]:
    db = _InMemoryPrefsDB()
    return _PatchablePrefsStore(db), db


def test_patch_new_user_inserts_delta_merged_with_defaults() -> None:
    """patch() on a new user inserts the delta and returns defaults + delta."""
    store, _ = _make_store()
    result = store.patch("alice", {"row_limit_hint": 5})

    assert result["row_limit_hint"] == 5
    # Unspecified keys should be defaults
    assert result["output_format"] == "table"
    assert result["preferred_language"] == "en"


def test_patch_existing_user_merges_without_wiping_other_keys() -> None:
    """patch() preserves keys not present in the delta."""
    store, _ = _make_store()
    # Seed with two non-default values
    store.upsert(
        "bob", {**default_preferences(), "row_limit_hint": 25, "output_format": "json"}
    )
    result = store.patch("bob", {"preferred_language": "fr"})

    # Patched key updated
    assert result["preferred_language"] == "fr"
    # Non-patched stored keys preserved
    assert result["row_limit_hint"] == 25
    assert result["output_format"] == "json"


def test_patch_overwrites_targeted_key_only() -> None:
    """patch() changes only the key(s) in delta; others unchanged."""
    store, db = _make_store()
    store.upsert("carol", {**default_preferences(), "safety_strictness": "lenient"})
    result = store.patch("carol", {"safety_strictness": "strict"})

    assert result["safety_strictness"] == "strict"
    # Other defaults still present
    assert result["row_limit_hint"] == default_preferences()["row_limit_hint"]


def test_patch_returns_full_prefs_with_defaults_for_missing_keys() -> None:
    """patch() return value always contains all canonical default keys."""
    store, _ = _make_store()
    result = store.patch("dave", {"date_format": "US"})

    for key in default_preferences():
        assert key in result, f"Missing key: {key}"


def test_patch_multiple_keys_in_single_call() -> None:
    """patch() accepts a delta with multiple keys and applies all of them."""
    store, _ = _make_store()
    result = store.patch(
        "eve", {"row_limit_hint": 3, "output_format": "json", "date_format": "EU"}
    )

    assert result["row_limit_hint"] == 3
    assert result["output_format"] == "json"
    assert result["date_format"] == "EU"
    # Untouched key stays at default
    assert result["safety_strictness"] == default_preferences()["safety_strictness"]


def test_patch_successive_calls_accumulate_correctly() -> None:
    """Two successive patch() calls accumulate without losing earlier changes."""
    store, _ = _make_store()
    store.patch("frank", {"row_limit_hint": 7})
    result = store.patch("frank", {"output_format": "json"})

    # First patch persisted
    assert result["row_limit_hint"] == 7
    # Second patch applied
    assert result["output_format"] == "json"


def test_upsert_full_replace_does_not_preserve_old_keys() -> None:
    """upsert() (full replace) wipes keys not present in the new prefs dict."""
    store, db = _make_store()
    store.upsert("grace", {**default_preferences(), "row_limit_hint": 99})
    # Full replace with a smaller dict (no row_limit_hint key)
    store.upsert("grace", {"preferred_language": "de"})
    row = db.get_row("grace")

    # row_limit_hint was wiped (not in new dict)
    assert "row_limit_hint" not in row
    assert row["preferred_language"] == "de"


def test_seed_session_fields_preserves_existing_history() -> None:
    """Existing conversation_history is preserved when seeding."""
    from graph.state import ConversationTurn

    turn = ConversationTurn(user_input="hello", sql="SELECT 1 LIMIT 1")
    state = QueryGraphState(memory=MemoryState(conversation_history=[turn]))

    delta = seed_session_fields(state)

    assert "memory" in delta
    assert len(delta["memory"]["conversation_history"]) == 1
    assert delta["memory"]["conversation_history"][0].user_input == "hello"


def test_seed_session_fields_returns_empty_list_for_no_history() -> None:
    """Seed returns empty history when no prior turns exist."""
    state = QueryGraphState()

    delta = seed_session_fields(state)

    assert "memory" in delta
    assert delta["memory"]["conversation_history"] == []


def test_snapshot_session_fields_appends_turn_when_sql_executed() -> None:
    """Snapshot appends a ConversationTurn when SQL was generated."""
    state = QueryGraphState(
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
    state = QueryGraphState(user_input="describe schema")

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_session_fields_skips_on_last_error() -> None:
    """Snapshot does not append when the turn ended with last_error set."""
    state = QueryGraphState(
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
    state = QueryGraphState(
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
    state = QueryGraphState(
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
    assert history[-1].user_input == "new question"
    assert history[0].user_input == "q1"


def test_db_schema_presence_returns_ready_when_store_ready() -> None:
    """Schema presence returns ready when store is ready."""

    class _ReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return True

    presence = DbSchemaPresence(store=_ReadyStore())
    result = presence.check()

    assert result == SchemaPresenceResult(True, None)


def test_db_schema_presence_returns_not_ready_when_store_not_ready() -> None:
    """Schema presence returns not ready when store is not ready."""

    class _NotReadyStore:
        def __init__(self, settings=None):
            pass

        def is_ready(self) -> bool:
            return False

    presence = DbSchemaPresence(store=_NotReadyStore())
    result = presence.check()

    assert result.ready is False
    assert result.reason is not None


def test_db_schema_presence_soft_fails_on_operational_error() -> None:
    """Schema presence handles DB connection errors gracefully."""

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

    state = QueryGraphState(user_id="alice")
    result = await memory_load_user(state)

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
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    state = QueryGraphState()
    result = await memory_load_user(state)

    assert result["query"].get("docs_context") is None
    assert result["query"].get("docs_warning") is not None
    assert result["memory"].get("warning") is None


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_prefs_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory falls back to defaults when prefs DB is unreachable."""
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr(
        "graph.memory_nodes.SchemaDocsStore",
        lambda settings=None: _FakeSchemaDocsStore(payload=None),
    )

    state = QueryGraphState()
    result = await memory_load_user(state)

    assert result["memory"]["preferences"] == default_preferences()
    assert result["memory"].get("warning") is not None
    assert "unreachable" in result["memory"]["warning"]


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_docs_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory warns when docs DB is unreachable."""
    monkeypatch.setattr(
        "graph.memory_nodes.UserPreferencesStore",
        lambda settings=None: _FakePrefsStore(),
    )
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    state = QueryGraphState()
    result = await memory_load_user(state)

    assert result["memory"].get("preferences") is not None
    assert result["query"].get("docs_warning") is not None
    assert result["memory"].get("warning") is not None


@pytest.mark.asyncio
async def test_memory_load_user_soft_fails_on_both_db_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User memory falls back gracefully when both DBs are unreachable."""
    monkeypatch.setattr("graph.memory_nodes.UserPreferencesStore", _ErrorStore)
    monkeypatch.setattr("graph.memory_nodes.SchemaDocsStore", _ErrorStore)

    state = QueryGraphState()
    result = await memory_load_user(state)

    assert result["memory"]["preferences"] == default_preferences()
    assert result["memory"].get("warning") is not None
    assert result["query"].get("docs_warning") is not None


def test_append_steps_extends_list() -> None:
    assert append_steps(["a", "b"], ["c"]) == ["a", "b", "c"]


def test_append_steps_empty_update_returns_current() -> None:
    assert append_steps(["a"], []) == ["a"]


def test_append_steps_none_update_returns_current() -> None:
    assert append_steps(["a"], None) == ["a"]


def test_append_steps_does_not_mutate_original() -> None:
    original = ["a", "b"]
    append_steps(original, ["c"])
    assert original == ["a", "b"]


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
    assert result.refinement_count == 3


def test_merge_submodel_schema_preserves_unset() -> None:
    current = SchemaPipelineState(ready=True, metadata={"tables": []})
    result = merge_submodel(current, {"persist_error": "DB down"})
    assert result.ready is True
    assert result.persist_error == "DB down"


def test_graph_state_defaults() -> None:
    state = QueryGraphState()
    assert state.user_input == ""
    assert state.steps == []
    assert isinstance(state.query, QueryPipelineState)
    assert isinstance(state.memory, MemoryState)


def test_conversation_turn_defaults() -> None:
    t = ConversationTurn(user_input="test")
    assert t.sql is None
    assert t.row_count is None
    assert t.rows_preview == []
    assert t.explanation is None


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


def test_snapshot_includes_row_preview() -> None:
    state = QueryGraphState(
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
    state = QueryGraphState(
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
