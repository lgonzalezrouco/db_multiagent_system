"""Tests for session memory helpers: seed, snapshot, ConversationTurn (Spec 11 §10)."""

from __future__ import annotations

import pytest

from graph.state import ConversationTurn, GraphState, MemoryState, QueryPipelineState
from memory.session import (
    HISTORY_MAX_TURNS,
    HISTORY_ROW_VALUE_MAX_CHARS,
    HISTORY_ROWS_PREVIEW,
    _trim_rows,
    seed_session_fields,
    snapshot_session_fields,
)

# ---------------------------------------------------------------------------
# seed_session_fields
# ---------------------------------------------------------------------------


def test_seed_preserves_existing_conversation_history() -> None:
    """seed_session_fields passes through existing conversation history unchanged."""
    turns = [
        ConversationTurn(user_input="q1", sql="SELECT 1 LIMIT 1"),
        ConversationTurn(user_input="q2", sql="SELECT 2 LIMIT 1"),
    ]
    state = GraphState(memory=MemoryState(conversation_history=turns))

    delta = seed_session_fields(state)

    assert delta["memory"]["conversation_history"] == turns


def test_seed_returns_empty_list_for_fresh_state() -> None:
    """seed_session_fields returns an empty history for a new state."""
    state = GraphState()

    delta = seed_session_fields(state)

    assert delta["memory"]["conversation_history"] == []


# ---------------------------------------------------------------------------
# snapshot_session_fields
# ---------------------------------------------------------------------------


def test_snapshot_appends_turn_after_successful_query() -> None:
    """snapshot_session_fields appends a ConversationTurn when SQL was generated."""
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
    turn = history[0]
    assert isinstance(turn, ConversationTurn)
    assert turn.user_input == "list films"
    assert turn.sql == "SELECT * FROM film LIMIT 10"
    assert turn.explanation == "Found 2 films."


def test_snapshot_does_not_append_for_schema_turns() -> None:
    """snapshot_session_fields skips appending when no SQL was generated."""
    state = GraphState(user_input="describe schema")

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_does_not_append_for_error_turns() -> None:
    """snapshot_session_fields skips appending when SQL is None (error turn)."""
    state = GraphState(
        user_input="bad query",
        query=QueryPipelineState(generated_sql=None),
    )

    delta = snapshot_session_fields(state)

    assert delta == {}


def test_snapshot_caps_history_at_max_turns() -> None:
    """snapshot_session_fields enforces HISTORY_MAX_TURNS (oldest entry dropped)."""
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
    assert history[-1].user_input == "new question"
    assert history[0].user_input == "q1"  # q0 was dropped (oldest)


def test_snapshot_6th_entry_drops_oldest() -> None:
    """Adding a 6th turn (with HISTORY_MAX_TURNS=5) drops the first."""
    assert HISTORY_MAX_TURNS == 5, "this test assumes HISTORY_MAX_TURNS=5"

    existing = [
        ConversationTurn(user_input=f"old{i}", sql=f"SELECT {i} LIMIT 1")
        for i in range(5)
    ]
    state = GraphState(
        user_input="newest",
        memory=MemoryState(conversation_history=existing),
        query=QueryPipelineState(
            generated_sql="SELECT 100 LIMIT 1",
            execution_result={"success": True, "rows_returned": 0},
        ),
    )

    delta = snapshot_session_fields(state)
    history = delta["memory"]["conversation_history"]

    assert len(history) == 5
    assert history[0].user_input == "old1"  # old0 is gone
    assert history[-1].user_input == "newest"


# ---------------------------------------------------------------------------
# _trim_rows
# ---------------------------------------------------------------------------


def test_trim_rows_keeps_up_to_preview_limit() -> None:
    """_trim_rows returns at most HISTORY_ROWS_PREVIEW rows."""
    execution_result = {
        "rows": [{"n": i} for i in range(10)],
    }

    rows = _trim_rows(execution_result)

    assert len(rows) <= HISTORY_ROWS_PREVIEW


def test_trim_rows_truncates_long_string_values() -> None:
    """_trim_rows truncates string values longer than HISTORY_ROW_VALUE_MAX_CHARS."""
    long_val = "x" * (HISTORY_ROW_VALUE_MAX_CHARS + 50)
    execution_result = {
        "rows": [{"title": long_val}],
    }

    rows = _trim_rows(execution_result)

    assert len(rows) == 1
    assert len(rows[0]["title"]) == HISTORY_ROW_VALUE_MAX_CHARS


def test_trim_rows_preserves_non_string_values() -> None:
    """_trim_rows does not truncate numeric or None values."""
    execution_result = {
        "rows": [{"count": 42, "ratio": 0.5, "null_col": None}],
    }

    rows = _trim_rows(execution_result)

    assert len(rows) == 1
    assert rows[0]["count"] == 42
    assert rows[0]["ratio"] == 0.5
    assert rows[0]["null_col"] is None


def test_trim_rows_returns_empty_for_none_input() -> None:
    """_trim_rows handles None execution_result gracefully."""
    assert _trim_rows(None) == []


def test_trim_rows_returns_empty_for_missing_rows() -> None:
    """_trim_rows handles execution_result with no rows key."""
    assert _trim_rows({}) == []


@pytest.mark.parametrize("rows_val", [None, []])
def test_trim_rows_returns_empty_for_empty_rows(rows_val: object) -> None:
    """_trim_rows handles empty or None rows."""
    assert _trim_rows({"rows": rows_val}) == []


# ---------------------------------------------------------------------------
# ConversationTurn row preview in snapshot
# ---------------------------------------------------------------------------


def test_snapshot_includes_row_preview() -> None:
    """snapshot_session_fields populates rows_preview with trimmed rows."""
    state = GraphState(
        user_input="actors",
        query=QueryPipelineState(
            generated_sql="SELECT * FROM actor LIMIT 5",
            execution_result={
                "success": True,
                "rows_returned": 3,
                "rows": [
                    {"first_name": "Nick", "last_name": "Wahlberg"},
                    {"first_name": "Ed", "last_name": "Chase"},
                    {"first_name": "Jennifer", "last_name": "Davis"},
                ],
                "columns": ["first_name", "last_name"],
            },
        ),
    )

    delta = snapshot_session_fields(state)
    turn = delta["memory"]["conversation_history"][0]

    assert len(turn.rows_preview) == min(3, HISTORY_ROWS_PREVIEW)
    assert turn.rows_preview[0]["first_name"] == "Nick"


def test_snapshot_extracts_row_count_from_rows_returned() -> None:
    """snapshot_session_fields sets row_count from execution_result.rows_returned."""
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
    turn = delta["memory"]["conversation_history"][0]

    assert turn.row_count == 42
