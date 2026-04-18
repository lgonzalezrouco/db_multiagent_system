"""Schema documentation draft from catalog metadata via structured LLM output."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.prompts.schema import (
    INSPECT_METADATA_SENTINEL,
    SCHEMA_DRAFT_HUMAN_LEAD,
    SCHEMA_SYSTEM_MESSAGE,
)
from agents.schemas.schema_outputs import ColumnDraft, SchemaDraftOutput, TableDraft
from llm.factory import create_chat_llm

logger = logging.getLogger(__name__)


async def build_schema_draft(
    metadata: dict[str, Any] | None,
    *,
    user_input: str = "",
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not metadata or not metadata.get("success"):
        return {"tables": []}

    raw_tables = metadata.get("tables") or []
    if not raw_tables:
        return {"tables": []}

    llm = create_chat_llm()
    structured = llm.with_structured_output(SchemaDraftOutput)
    human_parts = [
        *SCHEMA_DRAFT_HUMAN_LEAD,
        f"{INSPECT_METADATA_SENTINEL}\n"
        + json.dumps(metadata, default=str, ensure_ascii=False),
    ]
    preview = (user_input or "").strip()
    if preview:
        human_parts.append(f"User request context (optional):\n{preview[:2000]}")
    if preferences is not None:
        human_parts.append(
            "User preferences (JSON):\n"
            + json.dumps(preferences, default=str, ensure_ascii=False)[:4000],
        )
    messages = [
        SystemMessage(content=SCHEMA_SYSTEM_MESSAGE),
        HumanMessage(content="\n\n".join(human_parts)),
    ]
    try:
        raw = await structured.ainvoke(messages)
        result = SchemaDraftOutput.model_validate(raw)
    except Exception:
        logger.exception("schema_draft_structured_invoke_failed")
        raise

    dumped = result.model_dump(by_alias=True, mode="json")
    if not _draft_covers_metadata(dumped, metadata):
        logger.info("schema_draft_merging_missing_tables_from_metadata")
        dumped = _merge_draft_with_metadata(result, metadata)
    return dumped


def _draft_covers_metadata(draft: dict[str, Any], metadata: dict[str, Any]) -> bool:
    tables_meta = metadata.get("tables") or []
    if not tables_meta:
        return True
    keys = {
        (str(t.get("schema_name") or "public"), str(t.get("table_name") or ""))
        for t in tables_meta
        if isinstance(t, dict) and t.get("table_name")
    }
    keys.discard(("", ""))
    if not keys:
        return True
    present = set()
    for t in draft.get("tables") or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        schema = t.get("schema", "public")
        if name:
            present.add((str(schema), str(name)))
    return keys <= present


def _merge_draft_with_metadata(
    result: SchemaDraftOutput,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Ensure every inspected table appears (LLM may omit); fill gaps from metadata."""
    by_key: dict[tuple[str, str], TableDraft] = {}
    for t in result.tables:
        by_key[(t.schema_name, t.name)] = t

    out_tables: list[TableDraft] = []
    for raw in metadata.get("tables") or []:
        if not isinstance(raw, dict):
            continue
        schema = str(raw.get("schema_name") or "public")
        name = str(raw.get("table_name") or "")
        if not name:
            continue
        key = (schema, name)
        if key in by_key:
            out_tables.append(by_key[key])
            continue
        cols_out: list[ColumnDraft] = []
        for c in raw.get("columns") or []:
            if not isinstance(c, dict):
                continue
            cname = c.get("name")
            if not cname:
                continue
            cols_out.append(
                ColumnDraft(
                    name=str(cname),
                    description=f"Column {cname} ({c.get('data_type', 'unknown')}).",
                ),
            )
        out_tables.append(
            TableDraft(
                schema=schema,
                name=name,
                description=f"Table {schema}.{name} (auto-filled from catalog).",
                columns=cols_out,
            ),
        )
    merged = SchemaDraftOutput(tables=out_tables)
    return merged.model_dump(by_alias=True, mode="json")
