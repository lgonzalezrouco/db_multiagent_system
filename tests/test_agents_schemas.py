"""Pydantic schema contracts for structured LLM outputs."""

from __future__ import annotations

from agents.schemas.query_outputs import QueryPlanOutput, SqlGenerationOutput
from agents.schemas.schema_outputs import ColumnDraft, SchemaDraftOutput, TableDraft


def test_query_plan_output_roundtrip() -> None:
    raw = {
        "intent": "aggregate",
        "summary": "Count rows",
        "relevant_tables": ["public.actor"],
        "notes": ["n1"],
        "assumptions": [],
    }
    m = QueryPlanOutput.model_validate(raw)
    assert m.model_dump(mode="json") == raw


def test_sql_generation_output_requires_sql() -> None:
    m = SqlGenerationOutput.model_validate(
        {"sql": "SELECT 1 AS x LIMIT 1", "rationale": "smoke"},
    )
    assert m.sql.startswith("SELECT")


def test_schema_draft_dump_uses_schema_alias() -> None:
    out = SchemaDraftOutput(
        tables=[
            TableDraft(
                schema="public",
                name="actor",
                description="Actors",
                columns=[
                    ColumnDraft(name="actor_id", description="PK"),
                ],
            ),
        ],
    )
    d = out.model_dump(by_alias=True, mode="json")
    assert d["tables"][0]["schema"] == "public"
    assert d["tables"][0]["columns"][0]["name"] == "actor_id"
