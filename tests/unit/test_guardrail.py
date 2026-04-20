from __future__ import annotations

from typing import Any

import pytest

from graph.nodes.query_nodes.guardrail import guardrail_node
from graph.state import QueryGraphState


@pytest.mark.asyncio
async def test_guardrail_accepts_when_schema_table_mentioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "in_scope": True,
            "reason": "mentions actor",
            "canned_response": "",
            "used_llm": False,
        }

    monkeypatch.setattr(
        "graph.nodes.query_nodes.guardrail.classify_topic", _fake_classify
    )
    state = QueryGraphState(user_input="show actor names")
    result = await guardrail_node(state)
    assert result["query"]["topic_in_scope"] is True


@pytest.mark.asyncio
async def test_guardrail_rejects_weather_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "in_scope": False,
            "reason": "weather is unrelated",
            "canned_response": "",
            "used_llm": True,
        }

    monkeypatch.setattr(
        "graph.nodes.query_nodes.guardrail.classify_topic", _fake_classify
    )
    state = QueryGraphState(user_input="what is the weather?")
    result = await guardrail_node(state)
    assert result["query"]["topic_in_scope"] is False
    assert result["query"]["guardrail_reason"] == "weather is unrelated"


@pytest.mark.asyncio
async def test_guardrail_fails_open_on_llm_error_sets_in_scope_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "in_scope": True,
            "reason": "Guardrail unavailable; defaulting to in-scope.",
            "canned_response": "",
            "used_llm": True,
        }

    monkeypatch.setattr("graph.nodes.query_nodes.guardrail.classify_topic", _fallback)
    state = QueryGraphState(user_input="random request")
    result = await guardrail_node(state)
    assert result["query"]["topic_in_scope"] is True


@pytest.mark.asyncio
async def test_guardrail_keyword_shortcut_skips_llm_when_actor_mentioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def _fake_classify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return {
            "in_scope": True,
            "reason": "Matched known DVD Rental schema terms.",
            "canned_response": "",
            "used_llm": False,
        }

    monkeypatch.setattr(
        "graph.nodes.query_nodes.guardrail.classify_topic", _fake_classify
    )
    state = QueryGraphState(user_input="actor with most rentals")
    result = await guardrail_node(state)
    assert result["query"]["topic_in_scope"] is True
    assert calls == [1]
