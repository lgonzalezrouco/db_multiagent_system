from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import psycopg

from config.memory_settings import AppMemorySettings
from graph.state import GraphState
from memory.schema_docs import SchemaDocsStore

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    ts = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return ts.replace("+00:00", "Z")


def _normalize_approved(approved: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Return ``(tables, error_message)`` from HITL resume payload."""
    if not isinstance(approved, dict):
        return [], "resume payload must be a JSON object"
    tables = approved.get("tables")
    if not isinstance(tables, list) or not tables:
        return [], "resume payload must include a non-empty ``tables`` list"
    out: list[dict[str, Any]] = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        schema = t.get("schema", "public")
        name = t.get("name")
        if not name:
            continue
        desc = t.get("description", "")
        cols_in = t.get("columns") if isinstance(t.get("columns"), list) else []
        cols_out: list[dict[str, str]] = []
        for c in cols_in:
            if isinstance(c, dict) and c.get("name"):
                cols_out.append(
                    {
                        "name": str(c["name"]),
                        "description": str(c.get("description", "")),
                    },
                )
        out.append(
            {
                "schema": str(schema),
                "name": str(name),
                "description": str(desc),
                "columns": cols_out,
            },
        )
    if not out:
        return [], "``tables`` did not contain any valid table entries"
    return out, None


def schema_persist(state: GraphState) -> dict[str, Any]:
    """Persist approved schema docs to app_memory via SchemaDocsStore."""
    out: dict[str, Any] = {
        "steps": ["schema_persist"],
        "schema": {"persist_error": None},
    }

    approved = state.schema.approved
    tables, err = _normalize_approved(approved)
    if err:
        out["schema"] = {"persist_error": err}
        out["last_error"] = err
        out["last_result"] = None
        logger.error("schema_persist validation failed: %s", err)
        return out

    updated = _utc_now_iso()
    payload_doc: dict[str, Any] = {
        "version": 1,
        "updated_at": updated,
        "source": "schema_agent_hitl",
        "tables": tables,
    }
    meta = state.schema.metadata
    fingerprint: str | None = None
    if isinstance(meta, dict):
        fingerprint = hashlib.sha256(
            json.dumps(meta, sort_keys=True, default=str).encode("utf-8"),
        ).hexdigest()

    try:
        store = SchemaDocsStore(AppMemorySettings())
        store.upsert_approved(payload_doc, metadata_fingerprint=fingerprint)
        out["schema"] = {"persist_error": None, "ready": True}
        out["last_result"] = {
            "kind": "schema_persist",
            "success": True,
            "table_count": len(tables),
        }
        out["last_error"] = None
    except psycopg.OperationalError as exc:
        msg = f"could not persist schema docs: {type(exc).__name__}"
        out["schema"] = {"persist_error": msg}
        out["last_error"] = msg
        out["last_result"] = None
        logger.error("could not persist schema docs: %s", exc)

    return out
