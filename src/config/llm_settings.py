"""LiteLLM / OpenAI-compatible gateway settings."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    llm_service_url: str = Field(
        ...,
        validation_alias=AliasChoices("LLM_SERVICE_URL"),
        description="LiteLLM HTTP root",
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY"),
    )
    llm_model: str = Field(
        ...,
        validation_alias=AliasChoices("LLM_MODEL"),
        description="Model id as understood by the LiteLLM proxy router",
    )
    llm_temperature: float = Field(
        default=0.0,
        validation_alias=AliasChoices("LLM_TEMPERATURE"),
    )
    llm_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS"),
    )
    llm_max_retries: int = Field(
        default=3,
        validation_alias=AliasChoices("LLM_MAX_RETRIES"),
    )
