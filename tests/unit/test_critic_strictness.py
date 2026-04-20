"""Unit tests for safety_strictness wiring in query_critic."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from graph.nodes.query_nodes.query_critic import (
    _apply_strictness,
    _normalize_safety_strictness,
    query_critic,
)
from graph.state import GraphState, MemoryState, QueryPipelineState


def test_normalize_returns_normal_for_none_prefs() -> None:
    assert _normalize_safety_strictness(None) == "normal"


def test_normalize_returns_normal_for_missing_key() -> None:
    assert _normalize_safety_strictness({"output_format": "json"}) == "normal"


def test_normalize_returns_strict_for_invalid_value() -> None:
    assert _normalize_safety_strictness({"safety_strictness": "extreme"}) == "strict"


def test_normalize_returns_normal() -> None:
    assert _normalize_safety_strictness({"safety_strictness": "normal"}) == "normal"


def test_normalize_returns_lenient() -> None:
    assert _normalize_safety_strictness({"safety_strictness": "lenient"}) == "lenient"


def test_normalize_returns_strict_explicitly() -> None:
    assert _normalize_safety_strictness({"safety_strictness": "strict"}) == "strict"


def test_normalize_is_case_insensitive() -> None:
    assert _normalize_safety_strictness({"safety_strictness": "LENIENT"}) == "lenient"
    assert _normalize_safety_strictness({"safety_strictness": "Normal"}) == "normal"


_ACCEPT_NO_RISKS: dict[str, Any] = {
    "verdict": "accept",
    "feedback": "Looks good.",
    "risks": [],
    "assumptions": [],
}
_ACCEPT_WITH_RISKS: dict[str, Any] = {
    "verdict": "accept",
    "feedback": "Mostly fine.",
    "risks": ["Ambiguous join", "Potential null values"],
    "assumptions": [],
}
_REJECT_NO_RISKS: dict[str, Any] = {
    "verdict": "reject",
    "feedback": "Wrong table used.",
    "risks": [],
    "assumptions": [],
}
_REJECT_WITH_RISKS: dict[str, Any] = {
    "verdict": "reject",
    "feedback": "Wrong table.",
    "risks": ["Unrelated join"],
    "assumptions": [],
}


# --- strict ---


def test_strict_accept_no_risks_passes() -> None:
    r = _apply_strictness("accept", _ACCEPT_NO_RISKS, "strict", 0)
    assert r["critic_status"] == "accept"
    assert r.get("critic_feedback") is None


def test_strict_accept_with_risks_rejects() -> None:
    r = _apply_strictness("accept", _ACCEPT_WITH_RISKS, "strict", 0)
    assert r["critic_status"] == "reject"
    assert "Strict mode" in r["critic_feedback"]
    assert r["refinement_count"] == 1


def test_strict_reject_no_risks_rejects() -> None:
    r = _apply_strictness("reject", _REJECT_NO_RISKS, "strict", 0)
    assert r["critic_status"] == "reject"
    assert r["refinement_count"] == 1


def test_strict_reject_increments_refinement_count() -> None:
    r = _apply_strictness("reject", _REJECT_NO_RISKS, "strict", 2)
    assert r["refinement_count"] == 3


# --- normal ---


def test_normal_accept_no_risks_passes() -> None:
    r = _apply_strictness("accept", _ACCEPT_NO_RISKS, "normal", 0)
    assert r["critic_status"] == "accept"


def test_normal_accept_with_risks_still_passes() -> None:
    """Normal mode ignores risks on an accepted verdict."""
    r = _apply_strictness("accept", _ACCEPT_WITH_RISKS, "normal", 0)
    assert r["critic_status"] == "accept"


def test_normal_reject_rejects() -> None:
    r = _apply_strictness("reject", _REJECT_NO_RISKS, "normal", 0)
    assert r["critic_status"] == "reject"
    assert r["refinement_count"] == 1


def test_normal_reject_includes_feedback() -> None:
    r = _apply_strictness("reject", _REJECT_WITH_RISKS, "normal", 0)
    assert "Wrong table" in r["critic_feedback"]


# --- lenient ---


def test_lenient_accept_passes_with_no_feedback() -> None:
    r = _apply_strictness("accept", _ACCEPT_NO_RISKS, "lenient", 0)
    assert r["critic_status"] == "accept"
    assert r.get("critic_feedback") is None


def test_lenient_accept_with_risks_passes_with_annotation() -> None:
    r = _apply_strictness("accept", _ACCEPT_WITH_RISKS, "lenient", 0)
    assert r["critic_status"] == "accept"
    assert "Lenient mode" in r["critic_feedback"]
    assert "Ambiguous join" in r["critic_feedback"]


def test_lenient_reject_still_passes() -> None:
    """Lenient never blocks, even on an LLM reject verdict."""
    r = _apply_strictness("reject", _REJECT_WITH_RISKS, "lenient", 0)
    assert r["critic_status"] == "accept"
    assert r.get("refinement_count") is None  # no increment


_critic_mod = importlib.import_module("graph.nodes.query_nodes.query_critic")


def _make_state(
    sql: str = "SELECT * FROM film LIMIT 10",
    strictness: str = "normal",
    refinement_count: int = 0,
) -> GraphState:
    return GraphState(
        user_input="test",
        memory=MemoryState(preferences={"safety_strictness": strictness}),
        query=QueryPipelineState(
            generated_sql=sql,
            refinement_count=refinement_count,
        ),
    )


def _mock_critique(verdict: str, risks: list[str]) -> MagicMock:
    """Build a mock that returns a structured critique from build_query_critique."""
    result = {
        "verdict": verdict,
        "feedback": "test feedback",
        "risks": risks,
        "assumptions": [],
    }

    async def _fake(*args: Any, **kwargs: Any) -> dict:
        return result

    return _fake


@pytest.mark.asyncio
async def test_critic_node_normal_accept_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _critic_mod, "build_query_critique", _mock_critique("accept", [])
    )
    result = await query_critic(_make_state(strictness="normal"))
    assert result["query"]["critic_status"] == "accept"


@pytest.mark.asyncio
async def test_critic_node_strict_accept_with_risks_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _critic_mod, "build_query_critique", _mock_critique("accept", ["risk A"])
    )
    result = await query_critic(_make_state(strictness="strict"))
    assert result["query"]["critic_status"] == "reject"


@pytest.mark.asyncio
async def test_critic_node_lenient_reject_still_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _critic_mod, "build_query_critique", _mock_critique("reject", ["risk B"])
    )
    result = await query_critic(_make_state(strictness="lenient"))
    assert result["query"]["critic_status"] == "accept"


@pytest.mark.asyncio
async def test_critic_node_normal_reject_increments_refinement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _critic_mod, "build_query_critique", _mock_critique("reject", [])
    )
    result = await query_critic(_make_state(strictness="normal", refinement_count=1))
    assert result["query"]["critic_status"] == "reject"
    assert result["query"]["refinement_count"] == 2
