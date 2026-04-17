"""Shared LangGraph state for the DB multi-agent system."""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """Minimum state for the query stub path; extend in later specs."""

    user_input: str
    steps: list[str]
    last_result: str | dict | None
    last_error: str | None
