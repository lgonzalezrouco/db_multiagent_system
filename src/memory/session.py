"""Session memory helpers: seed and snapshot session fields across turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graph.state import GraphState

HISTORY_MAX_TURNS: int = 5
HISTORY_ROWS_PREVIEW: int = 3
HISTORY_ROW_VALUE_MAX_CHARS: int = 200


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
            out.append(
                {
                    str(i): v[:HISTORY_ROW_VALUE_MAX_CHARS] if isinstance(v, str) else v
                    for i, v in enumerate(row)
                }
            )
    return out


def seed_session_fields(state: GraphState) -> dict[str, Any]:
    """Preserve ``conversation_history`` when memory_load_user merges other fields."""
    return {
        "memory": {
            "conversation_history": list(state.memory.conversation_history),
        }
    }


def snapshot_session_fields(state: GraphState) -> dict[str, Any]:
    """Append a ConversationTurn after successful SQL execution; cap history FIFO."""
    from graph.state import ConversationTurn

    sql = state.query.generated_sql
    if not sql:
        return {}

    if state.last_error:
        return {}

    execution_result = state.query.execution_result
    if not isinstance(execution_result, dict) or not execution_result.get("success"):
        return {}

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
