"""Observability (Spec 09): LangSmith settings and RunnableConfig trace fields."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig

from config import LangSmithSettings
from graph import build_traceable_config, graph_run_config


def test_langsmith_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "proj-x")
    s = LangSmithSettings()
    assert s.langsmith_tracing is True
    assert s.langsmith_api_key == "sk-test"
    assert s.langsmith_project == "proj-x"


def test_graph_run_config_trace_fields() -> None:
    cfg, seed = graph_run_config(
        thread_id="thr-1",
        user_id="usr-1",
        session_id="sess-1",
        run_kind="cli",
    )
    assert cfg["configurable"]["thread_id"] == "thr-1"
    assert cfg["metadata"]["thread_id"] == "thr-1"
    assert cfg["metadata"]["user_id"] == "usr-1"
    assert cfg["metadata"]["session_id"] == "sess-1"
    assert cfg["metadata"]["run_kind"] == "cli"
    assert cfg["tags"] == ["dvdrental-agent", "langgraph", "cli"]
    assert seed["user_id"] == "usr-1"
    assert seed["session_id"] == "sess-1"


def test_build_traceable_config_merges_without_dropping_thread() -> None:
    base: RunnableConfig = {
        "configurable": {"thread_id": "a", "other": 1},
        "metadata": {"upstream": "x", "session_id": "old"},
        "tags": ["pytest", "custom"],
    }
    merged = build_traceable_config(
        base=base,
        user_id="u",
        session_id="s",
        thread_id="a",
        run_kind="pytest",
    )
    assert merged["configurable"]["thread_id"] == "a"
    assert merged["configurable"]["other"] == 1
    assert merged["metadata"]["upstream"] == "x"
    assert merged["metadata"]["user_id"] == "u"
    assert merged["metadata"]["session_id"] == "s"
    assert merged["metadata"]["run_kind"] == "pytest"
    assert merged["tags"] == ["pytest", "custom", "dvdrental-agent", "langgraph"]
