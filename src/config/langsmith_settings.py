"""LangSmith tracing configuration (LANGSMITH_* env vars only)."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LangSmithSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices("LANGSMITH_TRACING"),
        description=(
            "When true, export traces to LangSmith "
            "(requires API key for useful output)."
        ),
    )
    langsmith_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGSMITH_API_KEY"),
    )
    langsmith_project: str = Field(
        default="default",
        validation_alias=AliasChoices("LANGSMITH_PROJECT"),
    )
    langsmith_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGSMITH_ENDPOINT"),
        description="Override API URL (e.g. EU region or self-hosted).",
    )
