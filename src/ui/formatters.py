"""Pure formatters and HITL resume builders for the Streamlit UI (unit-testable)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graph.state import QueryGraphState, SchemaGraphState

_QUERY_ANSWER_DISPLAY_ROW_CAP = 500


def _render_rows_table(
    cols: list[str],
    rows: list[Any],
    *,
    max_rows: int,
) -> str:
    """Render columns + rows as a markdown table string."""
    if not cols:
        return "_No columns returned._"
    display_rows = rows[:max_rows]
    header = " | ".join(c.replace("|", "\\|") for c in cols)
    sep = " | ".join("---" for _ in cols)
    lines = [f"| {header} |", f"| {sep} |"]
    for row in display_rows:
        cells = []
        for c in cols:
            v = row.get(c, "") if isinstance(row, dict) else ""
            s = str(v).replace("|", "\\|").replace("\n", " ")
            cells.append(s)
        lines.append(f"| {' | '.join(cells)} |")
    result = "\n".join(lines)
    if len(rows) > max_rows:
        result += f"\n\n_Showing first {max_rows} of {len(rows)} rows._"
    return result


def _render_rows_json(cols: list[str], rows: list[Any], *, max_rows: int) -> str:
    """Render columns + rows as a fenced JSON code block."""
    display_rows = rows[:max_rows]
    blob = json.dumps(display_rows, indent=2, ensure_ascii=False, default=str)
    result = f"```json\n{blob}\n```"
    if len(rows) > max_rows:
        result += f"\n\n_Showing first {max_rows} of {len(rows)} rows._"
    return result


def format_query_answer_markdown(
    payload: dict[str, Any],
    *,
    max_rows: int = _QUERY_ANSWER_DISPLAY_ROW_CAP,
) -> str:
    """Render a structured ``query_answer`` payload as markdown.

    Respects ``payload["output_format"]``:
    - ``"table"`` (default) — markdown table
    - ``"json"`` — fenced JSON code block
    """
    parts: list[str] = []
    sql = payload.get("sql") or ""
    parts.append("**SQL**")
    parts.append(f"```sql\n{sql}\n```")

    cols = list(payload.get("columns") or [])
    rows = list(payload.get("rows") or [])
    output_format = str(payload.get("output_format") or "table").strip().lower()

    if output_format == "json":
        parts.append(_render_rows_json(cols, rows, max_rows=max_rows))
    else:
        parts.append(_render_rows_table(cols, rows, max_rows=max_rows))

    expl = payload.get("explanation")
    if expl:
        parts.append(f"**Explanation**\n\n{expl}")
    lim = payload.get("limitations")
    if lim:
        parts.append(f"**Limitations**\n\n{lim}")
    return "\n\n".join(parts)


def format_schema_persist_markdown(payload: dict[str, Any]) -> str:
    """Render a ``schema_persist`` result for chat (success or structured failure)."""
    if payload.get("success") is True:
        n = payload.get("table_count")
        try:
            count = int(n) if n is not None else 0
        except (TypeError, ValueError):
            count = 0
        word = "table" if count == 1 else "tables"
        follow = (
            "You can ask questions about the DVD Rental database; "
            "new requests will use this schema."
        )
        return (
            "**Schema documentation saved.**\n\n"
            f"Stored descriptions for **{count}** {word}. {follow}"
        )
    detail = payload.get("message") or payload.get("error")
    if detail == "rejected by user":
        return (
            "**Schema review rejected — nothing was saved.** "
            "Previously persisted schema documentation (if any) is unchanged."
        )
    if detail:
        return f"**Schema was not saved.** {detail}"
    return (
        "**Schema was not saved.** Something went wrong while persisting; "
        "check the error above if shown."
    )


def _format_last_outputs(
    *,
    last_error: str | None,
    last_result: str | dict | None,
) -> str:
    err = last_error
    lr = last_result
    parts: list[str] = []
    if err:
        parts.append(f"**Error:** {err}")
    if lr is None:
        if not err:
            parts.append("_No result in state._")
        return "\n\n".join(parts) if parts else "_No result in state._"
    if isinstance(lr, dict) and lr.get("kind") == "query_answer":
        parts.append(format_query_answer_markdown(lr))
        return "\n\n".join(parts)
    if isinstance(lr, dict) and lr.get("kind") == "off_topic":
        message = str(lr.get("message") or "")
        reason = str(lr.get("reason") or "")
        if message:
            parts.append(message)
        if reason:
            parts.append(f"_Reason: {reason}_")
        return "\n\n".join(parts)
    if isinstance(lr, dict) and lr.get("kind") == "query_failure":
        expl = str(lr.get("explanation") or "")
        reason = str(lr.get("reason") or "")
        subtype = str(lr.get("subtype") or "")
        if expl:
            parts.append(f"**Could not complete the query**\n\n{expl}")
        else:
            parts.append("**Could not complete the query.**")
        if reason:
            parts.append(f"**Reason**\n\n{reason}")
        if subtype:
            parts.append(f"_Failure type: `{subtype}`_")
        return "\n\n".join(parts)
    if isinstance(lr, dict) and lr.get("kind") == "schema_persist":
        parts.append(format_schema_persist_markdown(lr))
        return "\n\n".join(parts)
    blob = json.dumps(lr, indent=2, ensure_ascii=False, default=str)
    parts.append(f"```json\n{blob}\n```")
    return "\n\n".join(parts)


def format_turn_state(state: QueryGraphState) -> str:
    """Format query graph ``last_error`` / ``last_result`` for chat display."""
    return _format_last_outputs(
        last_error=state.last_error,
        last_result=state.last_result,
    )


def format_schema_turn_state(state: SchemaGraphState) -> str:
    """Format schema graph ``last_error`` / ``last_result`` for the Schema tab."""
    return _format_last_outputs(
        last_error=state.last_error,
        last_result=state.last_result,
    )


def schema_resume_from_inputs(
    *,
    mode: str,
    draft: object,
    edited_json: str,
) -> tuple[dict[str, Any] | str | None, str | None]:
    """Build schema HITL resume from form inputs.

    Returns ``(resume, error_message)``. On reject, ``resume`` is the
    string ``reject``; otherwise a dict with a ``tables`` list.
    """
    if mode == "reject":
        return "reject", None
    if mode == "approve":
        tables = (draft or {}).get("tables") if isinstance(draft, dict) else None
        if not isinstance(tables, list) or not tables:
            return None, 'Draft must contain a non-empty "tables" list.'
        return {"tables": tables}, None
    try:
        result = json.loads(edited_json)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    if not isinstance(result, dict) or "tables" not in result:
        return None, 'JSON must contain a "tables" key.'
    tables = result.get("tables")
    if not isinstance(tables, list) or not tables:
        return None, 'JSON "tables" must be a non-empty list.'
    return result, None


def default_schema_edit_json(draft: object) -> str:
    """Default JSON text for the schema review text area."""
    if isinstance(draft, dict) and "tables" in draft:
        return json.dumps(
            {"tables": draft.get("tables", [])}, indent=2, ensure_ascii=False
        )
    return json.dumps({"tables": []}, indent=2)
