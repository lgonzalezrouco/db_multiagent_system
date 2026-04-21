"""Session memory helpers: seed and snapshot session fields across turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graph.state import QueryGraphState

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


def seed_session_fields(state: QueryGraphState) -> dict[str, Any]:
    """Preserve ``conversation_history`` when memory_load_user merges other fields."""
    return {
        "memory": {
            "conversation_history": list(state.memory.conversation_history),
        }
    }


def snapshot_session_fields(
    state: QueryGraphState,
    *,
    include_failures: bool = False,
) -> dict[str, Any]:
    """Append a ConversationTurn and cap history FIFO.

    By default only successful SQL execution is recorded. When
    ``include_failures`` is true, failed/off-topic turns are also recorded with
    ``row_count`` set to ``None`` and a fallback explanation.
    """
    from graph.state import ConversationTurn

    sql = state.query.generated_sql
    if not sql and not include_failures:
        return {}

    if state.last_error and not include_failures:
        return {}

    execution_result = state.query.execution_result
    success = isinstance(execution_result, dict) and execution_result.get("success")
    if not success and not include_failures:
        return {}

    row_count = None
    rows_preview: list[dict[str, Any]] = []
    if success and isinstance(execution_result, dict):
        row_count = execution_result.get("row_count") or execution_result.get(
            "rows_returned"
        )
        rows_preview = _trim_rows(execution_result)

    explanation = state.query.explanation
    if include_failures and not explanation:
        explanation = (
            state.query.guardrail_reason
            or state.last_error
            or "Turn ended without a successful query execution."
        )

    turn = ConversationTurn(
        user_input=state.user_input or "",
        sql=sql,
        row_count=row_count,
        rows_preview=rows_preview,
        explanation=explanation,
    )

    current: list[ConversationTurn] = list(state.memory.conversation_history)
    updated = current + [turn]
    if len(updated) > HISTORY_MAX_TURNS:
        updated = updated[-HISTORY_MAX_TURNS:]

    return {"memory": {"conversation_history": updated}}
