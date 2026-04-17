from config.mcp_settings import MCPSettings
from config.postgres_settings import PostgresSettings

# Backwards-compatible aliases (prefer MCPSettings / PostgresSettings).
ClientSettings = MCPSettings
ServerSettings = PostgresSettings
Settings = PostgresSettings

__all__ = [
    "MCPSettings",
    "PostgresSettings",
    "Settings",
    "ClientSettings",
    "ServerSettings",
]
