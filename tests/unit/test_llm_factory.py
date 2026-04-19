"""Unit tests for LLM client factory (no network)."""

from __future__ import annotations

from unittest.mock import patch

from config.llm_settings import LLMSettings
from llm.factory import create_chat_llm


def test_create_chat_llm_passes_url_without_appending_path() -> None:
    """LLM factory uses service URL as-is without appending paths."""
    # Given: settings with clean service URL
    settings = LLMSettings(
        llm_service_url="https://sa-llmproxy.it.itba.edu.ar",
        llm_model="m",
        llm_api_key="k",
    )

    # When: creating chat LLM
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings)

    # Then: api_base matches service URL exactly
    call_kw = mocked.call_args.kwargs
    assert call_kw["api_base"] == "https://sa-llmproxy.it.itba.edu.ar"


def test_create_chat_llm_strips_trailing_slash_and_uses_dummy_key() -> None:
    """LLM factory strips trailing slash and uses dummy key for empty api_key."""
    # Given: settings with trailing slash and empty api_key
    settings = LLMSettings(
        llm_service_url="https://proxy.example/openai/v1/",
        llm_model="m",
        llm_api_key="",
    )

    # When: creating chat LLM
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings)

    # Then: trailing slash is stripped and dummy key is used
    assert mocked.call_args.kwargs["api_base"] == "https://proxy.example/openai/v1"
    assert mocked.call_args.kwargs["api_key"] == "dummy-key"


def test_create_chat_llm_applies_temperature_override() -> None:
    """LLM factory applies explicit temperature override."""
    # Given: settings with default temperature
    settings = LLMSettings(
        llm_service_url="http://x/v1",
        llm_model="m",
        llm_api_key="x",
        llm_temperature=0.0,
    )

    # When: creating chat LLM with temperature override
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings, temperature=0.7)

    # Then: override temperature is used
    assert mocked.call_args.kwargs["temperature"] == 0.7


def test_create_chat_llm_coerces_gpt5_zero_temperature_to_one() -> None:
    """LLM factory coerces GPT-5 zero temperature to 1.0."""
    # Given: GPT-5 model with zero temperature
    settings = LLMSettings(
        llm_service_url="http://x/v1",
        llm_model="gpt-5-nano",
        llm_api_key="x",
        llm_temperature=0.0,
    )

    # When: creating chat LLM
    with patch("llm.factory.ChatLiteLLM") as mocked:
        create_chat_llm(settings)

    # Then: temperature is coerced to 1.0
    assert mocked.call_args.kwargs["temperature"] == 1.0
