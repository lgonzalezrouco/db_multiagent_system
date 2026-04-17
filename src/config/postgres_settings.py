from pydantic_settings import SettingsConfigDict

from config.mcp_settings import MCPSettings


class PostgresSettings(MCPSettings):
    """Settings required to access Postgres (and run the MCP server)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
