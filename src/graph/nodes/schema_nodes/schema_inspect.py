from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from config import MCPSettings
from graph import mcp_helpers
from graph.state import GraphState

logger = logging.getLogger(__name__)


def inspect_schema_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Short summary for ``GraphState.last_result`` after ``inspect_schema``."""
    if not payload:
        return {"kind": "inspect_schema", "success": False, "detail": "no_payload"}
    if payload.get("success"):
        tables = payload.get("tables") or []
        return {
            "kind": "inspect_schema",
            "success": True,
            "table_count": len(tables),
        }
    err = payload.get("error")
    err_type = err.get("type", "unknown") if isinstance(err, dict) else "unknown"
    return {
        "kind": "inspect_schema",
        "success": False,
        "error_type": err_type,
    }


async def schema_inspect(state: GraphState) -> dict[str, Any]:
    """Call MCP ``inspect_schema``; store payload in ``schema.metadata``."""
    gate_decision = "schema_path"

    out: dict[str, Any] = {
        "steps": [f"gate:{gate_decision}", "schema_inspect"],
        "last_error": None,
        "last_result": None,
        "gate_decision": gate_decision,
        "schema": {
            "ready": False,
            "persist_error": None,
        },
    }

    try:
        settings = MCPSettings()
        client = await mcp_helpers.get_mcp_client(settings)
        tools = await client.get_tools()
        inspect_tool = next((t for t in tools if t.name == "inspect_schema"), None)
        if inspect_tool is None:
            msg = "MCP tool inspect_schema not found"
            out["last_error"] = msg
            logger.error("%s", msg)
            return out

        raw = await inspect_tool.ainvoke({"schema_name": "public", "table_name": None})
        payload = mcp_helpers.tool_result_to_dict(raw)
        if payload and payload.get("success"):
            out["schema"] = {"ready": False, "persist_error": None, "metadata": payload}
            out["last_result"] = inspect_schema_summary(payload)
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
            out["last_result"] = inspect_schema_summary(payload)
            out["schema"] = {
                "ready": False,
                "persist_error": None,
                "metadata": payload if isinstance(payload, dict) else None,
            }
            logger.warning("inspect_schema failed: %s", out["last_error"])
    except ValidationError as exc:
        out["last_error"] = mcp_helpers.format_settings_validation_error(exc)
        logger.error("MCP settings validation failed: %s", out["last_error"])
    except OSError as exc:
        out["last_error"] = f"MCP connection error: {type(exc).__name__}"
        logger.error("MCP connection error during inspect_schema: %s", exc)
    except Exception as exc:
        exc_name = type(exc).__name__
        out["last_error"] = f"Unexpected error: {exc_name}"
        logger.exception("Unexpected error during schema_inspect")

    return out
