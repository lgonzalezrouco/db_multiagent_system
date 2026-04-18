"""Schema branch: inspect → draft → HITL (``interrupt``) → persist"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
from langgraph.types import interrupt
from pydantic import ValidationError

from agents.schema_agent import build_schema_draft
from config import MCPSettings
from config.memory_settings import AppMemorySettings
from graph import nodes as graph_nodes
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


async def schema_inspect(state: GraphState) -> dict[str, Any]:
    """Call MCP ``inspect_schema``; store payload in ``schema_metadata``."""
    steps = list(state.get("steps", []))
    gate_decision = "schema_path"
    steps.append(f"gate:{gate_decision}")
    steps.append("schema_inspect")

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "schema_inspect",
            "graph_phase": "enter",
            "user_input_preview": graph_nodes._user_input_preview(state),
            "steps_count": len(steps),
            "gate_decision": gate_decision,
        },
    )
    if graph_nodes._graph_debug():
        logger.debug(
            "graph_node_debug_snapshot",
            extra={
                "graph_node": "schema_inspect",
                "graph_phase": "enter_debug",
                "state_keys": sorted(state.keys()),
            },
        )

    out: dict[str, Any] = {
        "steps": steps,
        "last_error": None,
        "last_result": None,
        "gate_decision": gate_decision,
        "schema_ready": False,
        "persist_error": None,
    }

    try:
        settings = MCPSettings()
        client = await graph_nodes.get_mcp_client(settings)
        tools = await client.get_tools()
        inspect_tool = next((t for t in tools if t.name == "inspect_schema"), None)
        if inspect_tool is None:
            msg = "MCP tool inspect_schema not found"
            out["last_error"] = msg
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "schema_inspect",
                    "graph_phase": "exit",
                    "mcp_status": "error",
                    "steps_count": len(steps),
                    "result_summary": "tool_missing",
                },
            )
            return out

        raw = await inspect_tool.ainvoke({"schema_name": "public", "table_name": None})
        payload = graph_nodes._tool_result_to_dict(raw)
        if payload and payload.get("success"):
            out["schema_metadata"] = payload
            out["last_result"] = graph_nodes._inspect_schema_summary(payload)
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "schema_inspect",
                    "graph_phase": "exit",
                    "mcp_status": "success",
                    "steps_count": len(steps),
                    "result_summary": "inspect_schema_ok",
                },
            )
        else:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            err_type = (
                err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            )
            out["last_error"] = (
                f"MCP inspect_schema failed ({err_type})"
                if payload
                else "could not parse MCP tool result"
            )
            out["last_result"] = graph_nodes._inspect_schema_summary(payload)
            out["schema_metadata"] = payload if isinstance(payload, dict) else None
            logger.info(
                "graph_node_transition",
                extra={
                    "graph_node": "schema_inspect",
                    "graph_phase": "exit",
                    "mcp_status": "error",
                    "steps_count": len(steps),
                    "result_summary": f"mcp_error_type={err_type}",
                },
            )
    except ValidationError as exc:
        out["last_error"] = graph_nodes._format_settings_validation_error(exc)
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_inspect",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": "settings_validation_error",
            },
        )
    except OSError as exc:
        out["last_error"] = f"MCP connection error: {type(exc).__name__}"
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_inspect",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": "connection_error",
            },
        )
    except Exception as exc:
        exc_name = type(exc).__name__
        out["last_error"] = f"Unexpected error: {exc_name}"
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_inspect",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": exc_name,
            },
        )

    return out


async def schema_draft(state: GraphState) -> dict[str, Any]:
    """Build ``schema_draft`` from ``schema_metadata`` (stub or future LLM)."""
    steps = list(state.get("steps", []))
    steps.append("schema_draft")
    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "schema_draft",
            "graph_phase": "enter",
            "user_input_preview": graph_nodes._user_input_preview(state),
            "steps_count": len(steps),
        },
    )
    meta = state.get("schema_metadata")
    meta_dict = meta if isinstance(meta, dict) else None
    draft = build_schema_draft(meta_dict)

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "schema_draft",
            "graph_phase": "exit",
            "steps_count": len(steps),
            "result_summary": f"table_count={len(draft.get('tables') or [])}",
        },
    )
    return {"schema_draft": draft, "steps": steps}


def schema_hitl(state: GraphState) -> dict[str, Any]:
    """Dynamic HITL: ``interrupt()`` with draft; on resume, set ``schema_approved``.

    Metadata and draft are produced by prior nodes and already in checkpointed
    state, so this node stays safe to re-enter from the top.
    """
    steps = list(state.get("steps", []))
    draft = state.get("schema_draft")
    hitl_payload: dict[str, Any] = {
        "kind": "schema_review",
        "draft": draft,
    }
    table_n = len((draft or {}).get("tables") or []) if isinstance(draft, dict) else 0
    logger.info(
        "hitl_interrupt",
        extra={
            "graph_node": "schema_hitl",
            "graph_phase": "hitl",
            "hitl_kind": hitl_payload.get("kind"),
            "draft_table_count": table_n,
        },
    )
    approved = interrupt(hitl_payload)
    logger.info(
        "hitl_resume",
        extra={
            "graph_node": "schema_hitl",
            "graph_phase": "hitl",
            "resume_type": type(approved).__name__,
        },
    )
    steps.append("schema_hitl")
    return {
        "schema_approved": approved,
        "hitl_prompt": hitl_payload,
        "steps": steps,
    }


def schema_persist(state: GraphState) -> dict[str, Any]:
    """Persist approved schema docs to app_memory via SchemaDocsStore."""
    steps = list(state.get("steps", []))
    steps.append("schema_persist")

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "schema_persist",
            "graph_phase": "enter",
            "steps_count": len(steps),
        },
    )

    out: dict[str, Any] = {"steps": steps, "persist_error": None}

    approved = state.get("schema_approved")
    tables, err = _normalize_approved(approved)
    if err:
        out["persist_error"] = err
        out["last_error"] = err
        out["last_result"] = None
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_persist",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": "validation_error",
            },
        )
        return out

    updated = _utc_now_iso()
    payload_doc: dict[str, Any] = {
        "version": 1,
        "updated_at": updated,
        "source": "schema_agent_hitl",
        "tables": tables,
    }
    meta = state.get("schema_metadata")
    fingerprint: str | None = None
    if isinstance(meta, dict):
        fingerprint = hashlib.sha256(
            json.dumps(meta, sort_keys=True, default=str).encode("utf-8"),
        ).hexdigest()

    try:
        store = SchemaDocsStore(AppMemorySettings())
        store.upsert_approved(payload_doc, metadata_fingerprint=fingerprint)
        out["schema_ready"] = True
        out["last_result"] = {
            "kind": "schema_persist",
            "success": True,
            "table_count": len(tables),
        }
        out["last_error"] = None
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_persist",
                "graph_phase": "exit",
                "mcp_status": "success",
                "steps_count": len(steps),
                "result_summary": f"table_count={len(tables)}",
            },
        )
    except psycopg.OperationalError as exc:
        msg = f"could not persist schema docs: {type(exc).__name__}"
        out["persist_error"] = msg
        out["last_error"] = msg
        out["last_result"] = None
        logger.info(
            "graph_node_transition",
            extra={
                "graph_node": "schema_persist",
                "graph_phase": "exit",
                "mcp_status": "error",
                "steps_count": len(steps),
                "result_summary": "db_error",
            },
        )

    return out
