"""Streamable HTTP MCP server on /mcp (default port 8000)."""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from config.postgres_settings import PostgresSettings
from mcp_server.tools import register_tools


def build_app(settings: PostgresSettings | None = None) -> FastMCP:
    """Construct FastMCP with streamable-http defaults and registered tools."""
    cfg = settings or PostgresSettings()
    transport_security = None
    if cfg.mcp_host in ("127.0.0.1", "localhost", "::1"):
        from mcp.server.transport_security import TransportSecuritySettings

        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        )

    app = FastMCP(
        name="dvdrental-mcp",
        host=cfg.mcp_host,
        port=cfg.mcp_port,
        streamable_http_path="/mcp",
        transport_security=transport_security,
    )
    register_tools(app, cfg)
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    settings = PostgresSettings()
    app = build_app(settings)
    app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
