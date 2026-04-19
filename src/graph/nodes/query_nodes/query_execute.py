from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from config import MCPSettings
from graph import mcp_helpers
from graph.state import GraphState

logger = logging.getLogger(__name__)


async def query_execute(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_execute")

    sql = state.get("generated_sql") or ""

    out: dict[str, Any] = {
        "steps": steps,
        "query_execution_result": None,
        "last_error": None,
    }

    try:
        settings = MCPSettings()
        client = await mcp_helpers.get_mcp_client(settings)
        tools = await client.get_tools()
        exec_tool = next((t for t in tools if t.name == "execute_readonly_sql"), None)
        if exec_tool is None:
            out["last_error"] = "MCP tool execute_readonly_sql not found"
            logger.error("%s", out["last_error"])
            return out

        raw = await exec_tool.ainvoke({"sql": sql})
        payload = mcp_helpers.tool_result_to_dict(raw)
        out["query_execution_result"] = payload

        if isinstance(payload, dict) and payload.get("success"):
            pass
        else:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            err_type = (
                err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            )
            if not isinstance(payload, dict):
                out["last_error"] = "could not parse MCP tool result"
                logger.error(
                    "could not parse MCP tool result from execute_readonly_sql",
                )
            else:
                logger.warning(
                    "MCP execute_readonly_sql returned failure: error_type=%s",
                    err_type,
                )

    except ValidationError as exc:
        out["last_error"] = mcp_helpers.format_settings_validation_error(exc)
        logger.error("MCP settings validation failed: %s", out["last_error"])
    except OSError as exc:
        out["last_error"] = f"MCP connection error: {type(exc).__name__}"
        logger.error("MCP connection error: %s", exc)
    except Exception as exc:
        exc_name = type(exc).__name__
        out["last_error"] = f"Unexpected error: {exc_name}"
        logger.exception("Unexpected error during query_execute")

    return out
