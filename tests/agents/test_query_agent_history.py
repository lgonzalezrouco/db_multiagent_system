"""Tests verifying conversation history injection into LLM messages (Spec 11 §6.3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from agents.query_agent import (
    _history_block,
    build_query_critique,
    build_query_plan,
    build_sql,
)
from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)


def _human_content(captured: list[Any]) -> str:
    """Return the content of the first HumanMessage in *captured*."""
    for m in captured:
        if isinstance(m, HumanMessage):
            return m.content
    raise AssertionError("No HumanMessage found in captured messages")


# ---------------------------------------------------------------------------
# _history_block helper
# ---------------------------------------------------------------------------


class TestHistoryBlock:
    def test_returns_none_for_none_input(self):
        assert _history_block(None) is None

    def test_returns_none_for_empty_list(self):
        assert _history_block([]) is None

    def test_includes_json_serialisation(self):
        turns = [{"user_input": "hello", "sql": "SELECT 1 LIMIT 1"}]
        result = _history_block(turns)
        assert result is not None
        assert "Conversation history" in result
        assert "hello" in result
        assert "oldest-first" in result

    def test_multiple_turns_all_included(self):
        turns = [
            {"user_input": "q1", "sql": "SELECT 1 LIMIT 1"},
            {"user_input": "q2", "sql": "SELECT 2 LIMIT 1"},
        ]
        result = _history_block(turns)
        assert result is not None
        assert "q1" in result
        assert "q2" in result


# ---------------------------------------------------------------------------
# build_query_plan — history injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_query_plan_includes_history_in_human_message() -> None:
    """build_query_plan includes Conversation history block in the human message."""
    captured_messages: list[Any] = []

    stub_output = QueryPlanOutput(
        intent="explore",
        summary="List actors",
        relevant_tables=["public.actor"],
        notes=[],
        assumptions=[],
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "Which actors worked with Nick?",
            schema_docs_context=None,
            conversation_history=[
                {"user_input": "hello", "sql": "SELECT 1 LIMIT 1", "explanation": "hi"}
            ],
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" in content
    assert "hello" in content


@pytest.mark.asyncio
async def test_build_query_plan_omits_history_when_none() -> None:
    """build_query_plan omits history block when conversation_history is None."""
    captured_messages: list[Any] = []

    stub_output = QueryPlanOutput(
        intent="explore",
        summary="List actors",
        relevant_tables=["public.actor"],
        notes=[],
        assumptions=[],
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "List actors",
            schema_docs_context=None,
            conversation_history=None,
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" not in content


@pytest.mark.asyncio
async def test_build_query_plan_omits_history_when_empty_list() -> None:
    """build_query_plan omits history block when conversation_history is []."""
    captured_messages: list[Any] = []

    stub_output = QueryPlanOutput(
        intent="explore",
        summary="List actors",
        relevant_tables=["public.actor"],
        notes=[],
        assumptions=[],
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "List actors",
            schema_docs_context=None,
            conversation_history=[],
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" not in content


# ---------------------------------------------------------------------------
# build_sql — history injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_sql_includes_history_in_human_message() -> None:
    """build_sql includes Conversation history block when history provided."""
    captured_messages: list[Any] = []

    stub_output = SqlGenerationOutput(
        sql="SELECT * FROM actor LIMIT 10", rationale="returns actors"
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_sql(
            "His movies",
            None,
            None,
            1,
            conversation_history=[
                {"user_input": "Nick Wahlberg films", "sql": "SELECT 1 LIMIT 1"}
            ],
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" in content
    assert "Nick Wahlberg films" in content


@pytest.mark.asyncio
async def test_build_sql_omits_history_when_none() -> None:
    """build_sql omits history block when conversation_history is None."""
    captured_messages: list[Any] = []

    stub_output = SqlGenerationOutput(
        sql="SELECT * FROM actor LIMIT 10", rationale="returns actors"
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_sql("List actors", None, None, 0, conversation_history=None)

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" not in content


# ---------------------------------------------------------------------------
# build_query_critique — history injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_query_critique_includes_history_in_human_message() -> None:
    """build_query_critique includes Conversation history block when provided."""
    captured_messages: list[Any] = []

    stub_output = QueryCritiqueOutput(
        verdict="accept",
        feedback="Looks good.",
        risks=[],
        assumptions=[],
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_critique(
            "His movies",
            "SELECT * FROM film LIMIT 10",
            query_plan=None,
            schema_docs_context=None,
            conversation_history=[
                {"user_input": "Nick Wahlberg actors", "sql": "SELECT 1 LIMIT 1"}
            ],
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" in content
    assert "Nick Wahlberg actors" in content


@pytest.mark.asyncio
async def test_build_query_critique_omits_history_when_none() -> None:
    """build_query_critique omits history block when conversation_history is None."""
    captured_messages: list[Any] = []

    stub_output = QueryCritiqueOutput(
        verdict="accept",
        feedback="OK",
        risks=[],
        assumptions=[],
    )

    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def capturing_ainvoke(messages: list[Any]) -> Any:
        captured_messages.extend(messages)
        return stub_output

    mock_structured.ainvoke = capturing_ainvoke

    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_critique(
            "List actors",
            "SELECT * FROM actor LIMIT 10",
            query_plan=None,
            schema_docs_context=None,
            conversation_history=None,
        )

    content = _human_content(captured_messages)
    assert "Conversation history (JSON" not in content
