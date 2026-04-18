"""Unit tests for LiteLLM client factory (no network)."""

from __future__ import annotations

from unittest.mock import patch

from config.llm_settings import LLMSettings
from llm.factory import create_chat_llm


def test_create_chat_llm_passes_url_without_appending_path() -> None:
    settings = LLMSettings(
        llm_service_url="https://sa-llmproxy.it.itba.edu.ar",
        llm_model="m",
        llm_api_key="k",
    )
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings)
    call_kw = mocked.call_args.kwargs
    assert call_kw["api_base"] == "https://sa-llmproxy.it.itba.edu.ar"


def test_create_chat_llm_strips_trailing_slash_only() -> None:
    settings = LLMSettings(
        llm_service_url="https://proxy.example/openai/v1/",
        llm_model="m",
        llm_api_key="",
    )
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings)
    assert mocked.call_args.kwargs["api_base"] == "https://proxy.example/openai/v1"
    assert mocked.call_args.kwargs["api_key"] == "dummy-key"


def test_create_chat_llm_uses_temperature_override() -> None:
    settings = LLMSettings(
        llm_service_url="http://x/v1",
        llm_model="m",
        llm_api_key="x",
        llm_temperature=0.0,
    )
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings, temperature=0.7)
    assert mocked.call_args.kwargs["temperature"] == 0.7
