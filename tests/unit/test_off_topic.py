from __future__ import annotations

import pytest

from graph.nodes.query_nodes.off_topic import off_topic_node
from graph.state import MemoryState, QueryGraphState, QueryPipelineState


@pytest.mark.asyncio
async def test_off_topic_builds_canned_response_in_preferred_language() -> None:
    state = QueryGraphState(
        memory=MemoryState(preferences={"preferred_language": "es"}),
        query=QueryPipelineState(guardrail_reason="Fuera de alcance"),
    )
    result = await off_topic_node(state)
    payload = result["last_result"]
    assert payload["kind"] == "off_topic"
    assert "Puedo ayudarte" in payload["message"]


@pytest.mark.asyncio
async def test_off_topic_sets_outcome_and_clears_last_error() -> None:
    state = QueryGraphState(
        last_error="stale",
        query=QueryPipelineState(guardrail_reason="outside scope"),
    )
    result = await off_topic_node(state)
    assert result["query"]["outcome"] == "off_topic"
    assert result["last_error"] is None
