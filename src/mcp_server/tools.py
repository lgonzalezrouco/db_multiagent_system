"""Register MCP tools with logging and Settings-backed handlers."""

from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from config.postgres_settings import PostgresSettings
from mcp_server.readonly_sql import (
    execute_readonly_sql,
    truncate_sql_preview,
    validate_readonly_sql,
)
from mcp_server.schema_metadata import fetch_schema_metadata

logger = logging.getLogger("mcp_server")


def register_tools(app: FastMCP, settings: PostgresSettings) -> None:
    """Attach `inspect_schema` and `execute_readonly_sql` to the FastMCP app."""

    async def inspect_schema(
        schema_name: str = "public",
        table_name: str | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            result = await fetch_schema_metadata(
                settings,
                schema_name=schema_name,
                table_name=table_name,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            ok = result.get("success", False)
            status = "success" if ok else "error"
            logger.info(
                (
                    "MCP tool_call | name=inspect_schema | schema_name=%s | "
                    "table_name=%s | duration_ms=%s | status=%s"
                ),
                schema_name,
                table_name,
                duration_ms,
                status,
            )
            return result
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                (
                    "MCP tool_call | name=inspect_schema | schema_name=%s | "
                    "table_name=%s | duration_ms=%s | status=exception | error=%s"
                ),
                schema_name,
                table_name,
                duration_ms,
                type(e).__name__,
            )
            return {
                "success": False,
                "error": {
                    "type": "database_error",
                    "message": str(e),
                },
            }

    async def execute_readonly_sql_tool(sql: str) -> dict[str, Any]:
        start = time.perf_counter()
        preview = truncate_sql_preview(sql)
        ok, err = validate_readonly_sql(sql)
        if not ok:
            duration_ms = int((time.perf_counter() - start) * 1000)
            msg = ""
            if err and err.get("error"):
                msg = str(err["error"].get("message", ""))
            et = "forbidden_token" if "forbidden token" in msg else "validation"
            logger.info(
                (
                    "MCP tool_call | name=execute_readonly_sql | "
                    "input_sql_preview=%r | duration_ms=%s | "
                    "status=validation_error | error_type=%s"
                ),
                preview,
                duration_ms,
                et,
            )
            return err

        try:
            result = await execute_readonly_sql(settings, sql)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if result.get("success"):
                rows = result.get("rows_returned", 0)
                logger.info(
                    (
                        "MCP tool_call | name=execute_readonly_sql | "
                        "input_sql_preview=%r | duration_ms=%s | "
                        "status=success | rows_returned=%s"
                    ),
                    preview,
                    duration_ms,
                    rows,
                )
            else:
                err_t = "database_error"
                if result.get("error", {}).get("type") == "connection_error":
                    err_t = "connection_error"
                logger.info(
                    (
                        "MCP tool_call | name=execute_readonly_sql | "
                        "input_sql_preview=%r | duration_ms=%s | status=%s"
                    ),
                    preview,
                    duration_ms,
                    err_t,
                )
            return result
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                (
                    "MCP tool_call | name=execute_readonly_sql | "
                    "input_sql_preview=%r | duration_ms=%s | status=exception | "
                    "error=%s"
                ),
                preview,
                duration_ms,
                type(e).__name__,
            )
            return {
                "success": False,
                "error": {
                    "type": "database_error",
                    "message": str(e),
                },
            }

    app.add_tool(
        inspect_schema,
        name="inspect_schema",
        description=(
            "Return PostgreSQL metadata for dvdrental: tables, columns, types, "
            "nullability, primary keys, and foreign keys (information_schema)."
        ),
    )
    app.add_tool(
        execute_readonly_sql_tool,
        name="execute_readonly_sql",
        description=(
            "Execute a single read-only SQL statement against dvdrental; "
            "returns up to 1000 rows. Forbidden write/admin tokens are rejected."
        ),
    )
