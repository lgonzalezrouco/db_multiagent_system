"""Unit tests for query_agent helpers: _history_block and history injection."""

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


def _make_capturing_llm(stub_output: Any) -> tuple[MagicMock, list[Any]]:
    """Return (mock_llm, captured_messages) where invoking the LLM captures msgs."""
    captured: list[Any] = []
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    async def _ainvoke(messages: list[Any]) -> Any:
        captured.extend(messages)
        return stub_output

    mock_structured.ainvoke = _ainvoke
    return mock_llm, captured


# ---------------------------------------------------------------------------
# _history_block
# ---------------------------------------------------------------------------


def test_history_block_returns_none_for_none() -> None:
    assert _history_block(None) is None


def test_history_block_returns_none_for_empty_list() -> None:
    assert _history_block([]) is None


def test_history_block_serialises_turns() -> None:
    turns = [{"user_input": "hello", "sql": "SELECT 1 LIMIT 1"}]
    result = _history_block(turns)
    assert result is not None
    assert "Conversation history" in result
    assert "oldest-first" in result
    assert "hello" in result


def test_history_block_includes_all_turns() -> None:
    turns = [{"user_input": "q1"}, {"user_input": "q2"}]
    result = _history_block(turns)
    assert result is not None
    assert "q1" in result and "q2" in result


# ---------------------------------------------------------------------------
# build_query_plan — history injection
# ---------------------------------------------------------------------------

_PLAN_STUB = QueryPlanOutput(
    intent="explore",
    summary="stub",
    relevant_tables=["public.actor"],
    notes=[],
    assumptions=[],
)


@pytest.mark.asyncio
async def test_build_query_plan_includes_history() -> None:
    mock_llm, captured = _make_capturing_llm(_PLAN_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "follow-up question",
            schema_docs_context=None,
            conversation_history=[
                {"user_input": "prior turn", "sql": "SELECT 1 LIMIT 1"}
            ],
        )
    assert "Conversation history (JSON" in _human_content(captured)
    assert "prior turn" in _human_content(captured)


@pytest.mark.asyncio
async def test_build_query_plan_omits_history_when_none() -> None:
    mock_llm, captured = _make_capturing_llm(_PLAN_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "question", schema_docs_context=None, conversation_history=None
        )
    assert "Conversation history (JSON" not in _human_content(captured)


@pytest.mark.asyncio
async def test_build_query_plan_omits_history_when_empty() -> None:
    mock_llm, captured = _make_capturing_llm(_PLAN_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_plan(
            "question", schema_docs_context=None, conversation_history=[]
        )
    assert "Conversation history (JSON" not in _human_content(captured)


# ---------------------------------------------------------------------------
# build_sql — history injection
# ---------------------------------------------------------------------------

_SQL_STUB = SqlGenerationOutput(sql="SELECT * FROM actor LIMIT 10", rationale="stub")


@pytest.mark.asyncio
async def test_build_sql_includes_history() -> None:
    mock_llm, captured = _make_capturing_llm(_SQL_STUB)
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
    content = _human_content(captured)
    assert "Conversation history (JSON" in content
    assert "Nick Wahlberg films" in content


@pytest.mark.asyncio
async def test_build_sql_omits_history_when_none() -> None:
    mock_llm, captured = _make_capturing_llm(_SQL_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_sql("List actors", None, None, 0, conversation_history=None)
    assert "Conversation history (JSON" not in _human_content(captured)


# ---------------------------------------------------------------------------
# build_query_critique — history injection
# ---------------------------------------------------------------------------

_CRITIQUE_STUB = QueryCritiqueOutput(
    verdict="accept", feedback="ok", risks=[], assumptions=[]
)


@pytest.mark.asyncio
async def test_build_query_critique_includes_history() -> None:
    mock_llm, captured = _make_capturing_llm(_CRITIQUE_STUB)
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
    content = _human_content(captured)
    assert "Conversation history (JSON" in content
    assert "Nick Wahlberg actors" in content


@pytest.mark.asyncio
async def test_build_query_critique_omits_history_when_none() -> None:
    mock_llm, captured = _make_capturing_llm(_CRITIQUE_STUB)
    with patch("agents.query_agent.create_chat_llm", return_value=mock_llm):
        await build_query_critique(
            "List actors",
            "SELECT * FROM actor LIMIT 10",
            query_plan=None,
            schema_docs_context=None,
            conversation_history=None,
        )
    assert "Conversation history (JSON" not in _human_content(captured)
