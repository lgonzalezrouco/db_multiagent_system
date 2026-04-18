"""Export live ``inspect_schema`` MCP output to a JSON file."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import MCPSettings
from graph.mcp_helpers import get_mcp_client, tool_result_to_dict

logger = logging.getLogger(__name__)


async def export_schema_catalog_json(dest: Path) -> None:
    """Call MCP ``inspect_schema`` for ``public`` and write the payload to ``dest``."""
    settings = MCPSettings()
    client = await get_mcp_client(settings)
    tools = await client.get_tools()
    inspect_tool = next((t for t in tools if t.name == "inspect_schema"), None)
    if inspect_tool is None:
        raise RuntimeError("MCP tool inspect_schema not found")
    raw: Any = await inspect_tool.ainvoke(
        {"schema_name": "public", "table_name": None},
    )
    payload = tool_result_to_dict(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("inspect_schema did not return a JSON object")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("wrote schema catalog json path=%s", dest)
