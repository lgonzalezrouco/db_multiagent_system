"""Schema documentation draft from catalog metadata (no graph imports)."""

from __future__ import annotations

from typing import Any


def build_schema_draft(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Build a draft (tables + column descriptions) from ``inspect_schema`` output.

    Deterministic placeholders when metadata is present. A future
    LLM path may use ``SCHEMA_AGENT_MODEL`` after ``uv add`` for a provider SDK.
    """
    if not metadata or not metadata.get("success"):
        return {"tables": []}

    raw_tables = metadata.get("tables") or []
    out_tables: list[dict[str, Any]] = []
    for t in raw_tables:
        if not isinstance(t, dict):
            continue
        schema = str(t.get("schema_name") or "public")
        name = str(t.get("table_name") or "")
        if not name:
            continue
        desc = f"Placeholder description for {schema}.{name}"
        cols_out: list[dict[str, str]] = []
        for c in t.get("columns") or []:
            if not isinstance(c, dict):
                continue
            cname = c.get("name")
            if not cname:
                continue
            cols_out.append(
                {
                    "name": str(cname),
                    "description": f"Placeholder description for column {cname}",
                },
            )
        out_tables.append(
            {
                "schema": schema,
                "name": name,
                "description": desc,
                "columns": cols_out,
            },
        )
    return {"tables": out_tables}
