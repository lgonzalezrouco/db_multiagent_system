from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from config import MCPSettings
from graph import mcp_helpers
from graph.nodes.query_nodes.query_critic import query_max_refinements
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


async def query_execute(state: QueryGraphState) -> dict[str, Any]:
    sql = state.query.generated_sql or ""
    rc = int(state.query.refinement_count or 0)
    cap = query_max_refinements()

    out: dict[str, Any] = {
        "steps": ["query_execute"],
        "query": {"execution_result": None, "outcome": None},
        "last_error": None,
    }

    def _mark_db_failure(message: str, payload: dict[str, Any] | None = None) -> None:
        nonlocal rc
        rc += 1
        query_update: dict[str, Any] = {
            "execution_result": payload,
            "critic_feedback": message,
            "refinement_count": rc,
            "outcome": None,
        }
        if rc >= cap:
            query_update["outcome"] = "db_failure"
            logger.warning(
                "db_max_attempts",
                extra={"graph_node": "query_execute", "refinement_count": rc},
            )
        out["query"] = query_update
        out["last_error"] = message

    try:
        settings = MCPSettings()
        exec_tool = await mcp_helpers.get_mcp_tool(
            settings, name="execute_readonly_sql"
        )
        if exec_tool is None:
            _mark_db_failure("MCP tool execute_readonly_sql not found")
            logger.error("MCP tool execute_readonly_sql not found")
            return out

        raw = await exec_tool.ainvoke({"sql": sql})
        payload = mcp_helpers.tool_result_to_dict(raw)
        out["query"] = {"execution_result": payload, "outcome": "success"}

        if isinstance(payload, dict) and payload.get("success"):
            out["last_error"] = None
        else:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            err_type = (
                err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            )
            if not isinstance(payload, dict):
                _mark_db_failure("could not parse MCP tool result")
                logger.error(
                    "could not parse MCP tool result from execute_readonly_sql",
                )
            else:
                msg = (
                    str(err.get("message"))
                    if isinstance(err, dict) and err.get("message")
                    else f"database error: {err_type}"
                )
                _mark_db_failure(msg, payload)
                logger.warning(
                    "MCP execute_readonly_sql returned failure: error_type=%s",
                    err_type,
                )

    except ValidationError as exc:
        _mark_db_failure(mcp_helpers.format_settings_validation_error(exc))
        logger.error("MCP settings validation failed: %s", out["last_error"])
    except OSError as exc:
        _mark_db_failure(f"MCP connection error: {type(exc).__name__}")
        logger.error("MCP connection error: %s", exc)
    except Exception as exc:
        exc_name = type(exc).__name__
        _mark_db_failure(f"Unexpected error: {exc_name}")
        logger.exception("Unexpected error during query_execute")

    return out
