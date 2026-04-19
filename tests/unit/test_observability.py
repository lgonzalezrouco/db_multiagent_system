"""Observability: LangSmith settings and RunnableConfig trace fields."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig

from config import LangSmithSettings
from graph import build_traceable_config, graph_run_config


def test_langsmith_settings_reads_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangSmith settings load from environment variables."""
    # Given: environment variables set
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "proj-x")

    # When: loading settings
    settings = LangSmithSettings()

    # Then: values are read from environment
    assert settings.langsmith_tracing is True
    assert settings.langsmith_api_key == "sk-test"
    assert settings.langsmith_project == "proj-x"


def test_graph_run_config_includes_all_trace_fields() -> None:
    """Graph run config includes thread, user, session, and run_kind metadata."""
    # Given: trace parameters
    thread_id = "thr-1"
    user_id = "usr-1"
    session_id = "sess-1"
    run_kind = "cli"

    # When: building run config
    cfg, seed = graph_run_config(
        thread_id=thread_id,
        user_id=user_id,
        session_id=session_id,
        run_kind=run_kind,
    )

    # Then: config contains all trace fields
    assert cfg["configurable"]["thread_id"] == thread_id
    assert cfg["metadata"]["thread_id"] == thread_id
    assert cfg["metadata"]["user_id"] == user_id
    assert cfg["metadata"]["session_id"] == session_id
    assert cfg["metadata"]["run_kind"] == run_kind
    assert cfg["tags"] == ["dvdrental-agent", "langgraph", "cli"]
    assert seed["user_id"] == user_id
    assert seed["session_id"] == session_id


def test_build_traceable_config_merges_without_overwriting_existing() -> None:
    """Traceable config merges new fields while preserving existing values."""
    # Given: base config with existing fields
    base: RunnableConfig = {
        "configurable": {"thread_id": "a", "other": 1},
        "metadata": {"upstream": "x", "session_id": "old"},
        "tags": ["pytest", "custom"],
    }

    # When: building traceable config
    merged = build_traceable_config(
        base=base,
        user_id="u",
        session_id="s",
        thread_id="a",
        run_kind="pytest",
    )

    # Then: existing fields are preserved and new fields are added
    assert merged["configurable"]["thread_id"] == "a"
    assert merged["configurable"]["other"] == 1
    assert merged["metadata"]["upstream"] == "x"
    assert merged["metadata"]["user_id"] == "u"
    assert merged["metadata"]["session_id"] == "s"
    assert merged["metadata"]["run_kind"] == "pytest"
    assert merged["tags"] == ["pytest", "custom", "dvdrental-agent", "langgraph"]
