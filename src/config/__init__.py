from config.llm_settings import LLMSettings
from config.mcp_settings import MCPSettings
from config.memory_settings import AppMemorySettings
from config.postgres_settings import PostgresSettings

# Backwards-compatible aliases (prefer MCPSettings / PostgresSettings).
ClientSettings = MCPSettings
ServerSettings = PostgresSettings
Settings = PostgresSettings

__all__ = [
    "AppMemorySettings",
    "LLMSettings",
    "MCPSettings",
    "PostgresSettings",
    "Settings",
    "ClientSettings",
    "ServerSettings",
]
