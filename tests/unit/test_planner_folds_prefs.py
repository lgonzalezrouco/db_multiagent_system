from __future__ import annotations

import importlib
from typing import Any

import pytest

from graph.nodes.query_nodes.query_plan import query_plan
from graph.state import QueryGraphState

_plan_mod = importlib.import_module("graph.nodes.query_nodes.query_plan")


@pytest.mark.asyncio
async def test_planner_returns_plan_and_preferences_delta_in_single_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(*args: Any, **kwargs: Any):
        return ({"intent": "lookup"}, {"output_format": "json"}, "user asked")

    monkeypatch.setattr(_plan_mod, "build_plan_and_preferences_delta", _fake)
    result = await query_plan(QueryGraphState(user_input="always json and list actors"))
    assert result["query"]["plan"]["intent"] == "lookup"
    assert result["memory"]["preferences_proposed_delta"] == {"output_format": "json"}


@pytest.mark.asyncio
async def test_planner_tolerates_preference_inference_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(*args: Any, **kwargs: Any):
        return ({"intent": "lookup"}, None, None)

    monkeypatch.setattr(_plan_mod, "build_plan_and_preferences_delta", _fake)
    result = await query_plan(QueryGraphState(user_input="list actors"))
    assert result["query"]["plan"]["intent"] == "lookup"
    assert result["memory"]["preferences_proposed_delta"] is None


@pytest.mark.asyncio
async def test_planner_tolerates_plan_builder_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("planner down")

    monkeypatch.setattr(_plan_mod, "build_plan_and_preferences_delta", _boom)
    result = await query_plan(QueryGraphState(user_input="list actors"))
    assert result["query"]["plan"] == {}
    assert result["memory"]["preferences_proposed_delta"] is None
