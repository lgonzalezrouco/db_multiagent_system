"""Unit tests for the preferences-inference builder in query_agent."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agents.query_agent import (
    ALLOWED_PREF_KEYS,
    _history_summary,
    _sanitize_delta,
    infer_preferences_delta,
)
from agents.schemas.preferences_outputs import PreferencesInferenceOutput


def _human_content(captured: list[Any]) -> str:
    for m in captured:
        if isinstance(m, HumanMessage):
            content = m.content
            if isinstance(content, str):
                return content
            raise AssertionError(
                f"Expected HumanMessage content to be str, got {type(content)}"
            )
    raise AssertionError("No HumanMessage found in captured messages")


def _make_capturing_llm(stub_output: Any) -> tuple[MagicMock, list[Any]]:
    captured: list[Any] = []
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def _ainvoke(messages: list[Any]) -> Any:
        captured.extend(messages)
        return stub_output

    mock_structured.ainvoke = _ainvoke
    return mock_llm, captured


def test_sanitize_delta_returns_none_for_none() -> None:
    assert _sanitize_delta(None) is None


def test_sanitize_delta_returns_none_for_empty_dict() -> None:
    assert _sanitize_delta({}) is None


def test_sanitize_delta_keeps_canonical_keys() -> None:
    delta = {"row_limit_hint": 5, "output_format": "json"}
    result = _sanitize_delta(delta)
    assert result == delta


def test_sanitize_delta_strips_unknown_keys() -> None:
    delta = {"row_limit_hint": 5, "malicious_key": "drop table"}
    result = _sanitize_delta(delta)
    assert result == {"row_limit_hint": 5}
    assert "malicious_key" not in (result or {})


def test_sanitize_delta_returns_none_when_only_unknown_keys() -> None:
    result = _sanitize_delta({"unknown": "value", "also_bad": 42})
    assert result is None


def test_sanitize_delta_all_canonical_keys_are_allowed() -> None:
    """Every key in the canonical set passes sanitization."""
    delta = {
        "preferred_language": "fr",
        "output_format": "json",
        "date_format": "EU",
        "safety_strictness": "lenient",
        "row_limit_hint": 3,
    }
    result = _sanitize_delta(delta)
    assert result == delta


def test_history_summary_returns_none_for_none() -> None:
    assert _history_summary(None) is None


def test_history_summary_returns_none_for_empty_list() -> None:
    assert _history_summary([]) is None


def test_history_summary_includes_user_input() -> None:
    history = [{"user_input": "list actors", "sql": "SELECT * FROM actor LIMIT 10"}]
    result = _history_summary(history)
    assert result is not None
    assert "list actors" in result


def test_history_summary_caps_at_three_turns() -> None:
    history = [{"user_input": f"turn {i}"} for i in range(6)]
    result = _history_summary(history)
    assert result is not None
    # Only last 3 should appear; earlier ones omitted
    assert "turn 5" in result
    assert "turn 4" in result
    assert "turn 3" in result
    assert "turn 0" not in result
    assert "turn 1" not in result
    assert "turn 2" not in result


def test_history_summary_skips_non_dict_entries() -> None:
    history: list[Any] = [{"user_input": "valid"}, "not a dict", None]
    result = _history_summary(history)
    assert result is not None
    assert "valid" in result


def test_allowed_pref_keys_contains_all_canonical_keys() -> None:
    expected = {
        "preferred_language",
        "output_format",
        "date_format",
        "safety_strictness",
        "row_limit_hint",
    }
    assert expected <= ALLOWED_PREF_KEYS


_NO_CHANGE_STUB = PreferencesInferenceOutput.no_change(
    "No persistent preference change detected.",
)

_CHANGE_STUB = PreferencesInferenceOutput(
    preferred_language=None,
    output_format="json",
    date_format=None,
    safety_strictness=None,
    row_limit_hint=None,
    rationale="User asked to always show results as JSON.",
)


@pytest.mark.asyncio
async def test_infer_sends_user_message_in_human_block() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("always show me JSON")
    assert "always show me JSON" in _human_content(captured)


@pytest.mark.asyncio
async def test_infer_sends_system_message() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("anything")
    system_msgs = [m for m in captured if isinstance(m, SystemMessage)]
    assert len(system_msgs) == 1
    sys_content = system_msgs[0].content
    sys_text = sys_content if isinstance(sys_content, str) else str(sys_content)
    assert "preferences" in sys_text.lower()


@pytest.mark.asyncio
async def test_infer_includes_current_preferences_in_human_block() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    prefs = {"output_format": "table", "row_limit_hint": 10}
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("test", current_preferences=prefs)
    content = _human_content(captured)
    assert "output_format" in content
    assert "row_limit_hint" in content


@pytest.mark.asyncio
async def test_infer_omits_prefs_block_when_none() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("test", current_preferences=None)
    content = _human_content(captured)
    assert "Current preferences" not in content


@pytest.mark.asyncio
async def test_infer_includes_history_summary_when_provided() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    history = [{"user_input": "show me actors"}]
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("test", conversation_history=history)
    assert "show me actors" in _human_content(captured)


@pytest.mark.asyncio
async def test_infer_omits_history_block_when_none() -> None:
    mock_llm, captured = _make_capturing_llm(_NO_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await infer_preferences_delta("test", conversation_history=None)
    assert "Recent conversation" not in _human_content(captured)


@pytest.mark.asyncio
async def test_infer_returns_none_delta_when_llm_returns_none() -> None:
    mock_llm, _ = _make_capturing_llm(_NO_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        result = await infer_preferences_delta("list all films")
    assert result.proposed_delta is None
    assert result.rationale


@pytest.mark.asyncio
async def test_infer_returns_sanitized_delta_for_canonical_key() -> None:
    mock_llm, _ = _make_capturing_llm(_CHANGE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        result = await infer_preferences_delta("always show me JSON")
    assert result.proposed_delta == {"output_format": "json"}


@pytest.mark.asyncio
async def test_infer_legacy_nested_proposed_delta_unwraps_to_fields() -> None:
    """Providers may still return legacy JSON with a nested proposed_delta object."""
    stub = {
        "proposed_delta": {"preferred_language": "es"},
        "rationale": "User wants Spanish.",
    }
    mock_llm, _ = _make_capturing_llm(stub)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        result = await infer_preferences_delta("Hablame siempre en español")
    assert result.proposed_delta == {"preferred_language": "es"}


@pytest.mark.asyncio
async def test_infer_soft_fails_when_llm_raises() -> None:
    """An LLM exception must not propagate; returns no-op result instead."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def _failing_ainvoke(messages: list[Any]) -> Any:
        raise RuntimeError("LLM connection timeout")

    mock_structured.ainvoke = _failing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        result = await infer_preferences_delta("test")

    assert result.proposed_delta is None
    assert result.rationale  # some explanation present


@pytest.mark.asyncio
async def test_infer_soft_fails_returns_no_op_output_type() -> None:
    """Soft-fail result is a valid PreferencesInferenceOutput instance."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def _failing_ainvoke(messages: list[Any]) -> Any:
        raise ValueError("Structured output schema mismatch")

    mock_structured.ainvoke = _failing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        result = await infer_preferences_delta("anything")

    assert isinstance(result, PreferencesInferenceOutput)
