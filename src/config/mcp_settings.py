from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    """Settings required by MCP clients (and as a base for the MCP server)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mcp_host: str = Field(
        default="127.0.0.1",
        description="Bind address for MCP HTTP server",
    )
    mcp_port: int = Field(default=8000, description="Port for MCP streamable HTTP")
    mcp_server_url: str | None = Field(
        default=None,
        description="Full URL for MCP clients (e.g. http://localhost:8000/mcp)",
    )
