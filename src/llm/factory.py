"""Build ``ChatLiteLLM`` from ``LLMSettings``."""

from __future__ import annotations

from langchain_litellm import ChatLiteLLM

from config.llm_settings import LLMSettings


def create_chat_llm(
    settings: LLMSettings | None = None,
    *,
    temperature: float | None = None,
) -> ChatLiteLLM:
    cfg = settings or LLMSettings()
    api_base = cfg.llm_service_url.rstrip("/")
    temp = cfg.llm_temperature if temperature is None else temperature
    return ChatLiteLLM(
        model=cfg.llm_model,
        api_base=api_base,
        api_key=cfg.llm_api_key or "dummy-key",
        temperature=temp,
        timeout=cfg.llm_timeout_seconds,
        max_retries=cfg.llm_max_retries,
    )
