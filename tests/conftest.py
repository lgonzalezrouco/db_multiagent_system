"""Pytest hooks for test env.

Loads `.env` so `Settings()` works in unit tests and integration tests
without in-code defaults.
Uses `override=False` so existing shell/CI env wins.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from pytest import Config

from agents.prompts.schema import INSPECT_METADATA_SENTINEL
from agents.schemas.preferences_outputs import PreferencesInferenceOutput
from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryExplanationOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)
from agents.schemas.schema_outputs import ColumnDraft, SchemaDraftOutput, TableDraft


def pytest_configure(config: Config) -> None:
    load_dotenv(config.rootpath / ".env", override=False)


def _message_blob(messages: list[BaseMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        c = getattr(m, "content", "")
        if isinstance(c, str):
            parts.append(c)
        else:
            parts.append(str(c))
    return "\n".join(parts)


def _draft_from_inspect_metadata(blob: str) -> SchemaDraftOutput:
    idx = blob.find(INSPECT_METADATA_SENTINEL)
    if idx == -1:
        return SchemaDraftOutput(tables=[])
    sub = blob[idx + len(INSPECT_METADATA_SENTINEL) :].strip()
    brace = sub.find("{")
    if brace == -1:
        return SchemaDraftOutput(tables=[])
    try:
        meta, _ = json.JSONDecoder().raw_decode(sub[brace:])
    except json.JSONDecodeError:
        return SchemaDraftOutput(tables=[])
    if not isinstance(meta, dict):
        return SchemaDraftOutput(tables=[])
    out_tables: list[TableDraft] = []
    for t in meta.get("tables") or []:
        if not isinstance(t, dict):
            continue
        schema = str(t.get("schema_name") or "public")
        name = str(t.get("table_name") or "")
        if not name:
            continue
        desc = f"Placeholder description for {schema}.{name}"
        cols_out: list[ColumnDraft] = []
        for c in t.get("columns") or []:
            if not isinstance(c, dict):
                continue
            cname = c.get("name")
            if not cname:
                continue
            cols_out.append(
                ColumnDraft(
                    name=str(cname),
                    description=f"Placeholder description for column {cname}",
                ),
            )
        out_tables.append(
            TableDraft(
                schema=schema,
                name=name,
                description=desc,
                columns=cols_out,
            ),
        )
    return SchemaDraftOutput(tables=out_tables)


def _stub_create_chat_llm(
    settings: Any = None,
    *,
    temperature: float | None = None,
) -> Any:
    class _StructuredRunnable:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        async def ainvoke(self, messages: list[BaseMessage]) -> Any:
            blob = _message_blob(messages)
            if self.kind == "plan":
                summary = blob.strip()[-400:].strip()[:200] or "(empty)"
                return QueryPlanOutput(
                    intent="explore",
                    summary=summary,
                    relevant_tables=["public.actor"],
                    notes=[],
                    assumptions=[],
                )
            if self.kind == "sql":
                return SqlGenerationOutput(
                    sql="SELECT COUNT(*)::bigint AS n FROM public.actor LIMIT 10",
                    rationale="unit-test stub LLM",
                )
            if self.kind == "schema":
                return _draft_from_inspect_metadata(blob)
            if self.kind == "critique":
                return QueryCritiqueOutput(
                    verdict="accept",
                    feedback="unit-test stub semantic critic accepts the SQL",
                    risks=[],
                    assumptions=[],
                )
            if self.kind == "explain":
                return QueryExplanationOutput(
                    explanation=(
                        "unit-test stub explanation for the executed query result"
                    ),
                    limitations=(
                        "Read-only SELECT with LIMIT; "
                        "MCP may truncate rows (server row cap)."
                    ),
                    follow_up_suggestions=[],
                )
            if self.kind == "preferences_infer":
                # Stub always returns no-op so tests are unaffected by the
                # preferences inference node running in the query pipeline.
                return PreferencesInferenceOutput.no_change(
                    "stub: no preference change detected",
                )
            raise NotImplementedError(self.kind)

    class _FakeChatLiteLLM:
        def with_structured_output(self, schema: type[Any]) -> _StructuredRunnable:
            name = getattr(schema, "__name__", "")
            mapping = {
                "QueryPlanOutput": "plan",
                "SqlGenerationOutput": "sql",
                "SchemaDraftOutput": "schema",
                "QueryCritiqueOutput": "critique",
                "QueryExplanationOutput": "explain",
                "PreferencesInferenceOutput": "preferences_infer",
            }
            if name not in mapping:
                raise NotImplementedError(name)
            return _StructuredRunnable(mapping[name])

    return _FakeChatLiteLLM()


@pytest.fixture(autouse=True)
def _llm_test_defaults(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("litellm_integration"):
        return
    monkeypatch.setenv("LLM_SERVICE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setattr("agents.query_agent.create_chat_llm", _stub_create_chat_llm)
    monkeypatch.setattr("agents.schema_agent.create_chat_llm", _stub_create_chat_llm)
