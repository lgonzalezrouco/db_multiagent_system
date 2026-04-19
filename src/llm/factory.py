"""Build ``ChatLiteLLM`` from ``LLMSettings``."""

from __future__ import annotations

import math

from langchain_litellm import ChatLiteLLM

from config.llm_settings import LLMSettings


def _temperature_for_litellm_model(model: str, temp: float) -> float:
    """Map ``temperature=0`` to ``1`` for GPT-5 (LiteLLM/OpenAI rejects 0)."""
    m = model.lower()
    if "gpt-5" in m and math.isclose(temp, 0.0):
        return 1.0
    return temp


def create_chat_llm(
    settings: LLMSettings | None = None,
    *,
    temperature: float | None = None,
) -> ChatLiteLLM:
    cfg = settings or LLMSettings()
    api_base = cfg.llm_service_url.rstrip("/")
    temp = cfg.llm_temperature if temperature is None else temperature
    temp = _temperature_for_litellm_model(cfg.llm_model, temp)
    return ChatLiteLLM(
        model=cfg.llm_model,
        api_base=api_base,
        api_key=cfg.llm_api_key or "dummy-key",
        temperature=temp,
        timeout=cfg.llm_timeout_seconds,
        max_retries=cfg.llm_max_retries,
    )
