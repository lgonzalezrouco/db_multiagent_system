"""Session memory helpers: seed and snapshot session fields across turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graph.state import GraphState

# ---------------------------------------------------------------------------
# Constants (Spec 11 §11)
# ---------------------------------------------------------------------------

HISTORY_MAX_TURNS: int = 5
HISTORY_ROWS_PREVIEW: int = 3
HISTORY_ROW_VALUE_MAX_CHARS: int = 200


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _trim_rows(execution_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return up to HISTORY_ROWS_PREVIEW rows with string values truncated."""
    if not isinstance(execution_result, dict):
        return []
    rows = execution_result.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows[:HISTORY_ROWS_PREVIEW]:
        if isinstance(row, dict):
            trimmed = {
                k: v[:HISTORY_ROW_VALUE_MAX_CHARS] if isinstance(v, str) else v
                for k, v in row.items()
            }
            out.append(trimmed)
        elif isinstance(row, (list, tuple)):
            # Columns not available here; store as a list
            out.append(
                {
                    str(i): v[:HISTORY_ROW_VALUE_MAX_CHARS] if isinstance(v, str) else v
                    for i, v in enumerate(row)
                }
            )
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_session_fields(state: GraphState) -> dict[str, Any]:
    """Return state delta that preserves ``conversation_history`` across turns.

    Called at the start of each turn (inside ``memory_load_user``) so that
    loading preferences / schema docs does not inadvertently clear the history
    that was checkpointed from the previous turn.
    """
    return {
        "memory": {
            "conversation_history": list(state.memory.conversation_history),
        }
    }


def snapshot_session_fields(state: GraphState) -> dict[str, Any]:
    """Append a ConversationTurn for the completed turn (if SQL was executed).

    Called at the end of each turn (inside ``memory_update_session``).
    Schema-path turns and error turns (no generated SQL) do not append.
    History is capped at HISTORY_MAX_TURNS (FIFO).
    """
    from graph.state import ConversationTurn  # avoid circular at module level

    sql = state.query.generated_sql
    if not sql:
        # Schema turns / error turns — do not pollute history
        return {}

    execution_result = state.query.execution_result
    row_count: int | None = None
    if isinstance(execution_result, dict):
        row_count = execution_result.get("row_count") or execution_result.get(
            "rows_returned"
        )

    turn = ConversationTurn(
        user_input=state.user_input or "",
        sql=sql,
        row_count=row_count,
        rows_preview=_trim_rows(execution_result),
        explanation=state.query.explanation,
    )

    current: list[ConversationTurn] = list(state.memory.conversation_history)
    updated = current + [turn]
    if len(updated) > HISTORY_MAX_TURNS:
        updated = updated[-HISTORY_MAX_TURNS:]

    return {"memory": {"conversation_history": updated}}
