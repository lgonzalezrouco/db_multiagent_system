"""Pydantic schema contracts for structured LLM outputs."""

from __future__ import annotations

from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryExplanationOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)
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


def test_query_critique_output_roundtrip() -> None:
    raw = {
        "verdict": "accept",
        "feedback": "SQL matches the user request.",
        "risks": ["May omit null handling"],
        "assumptions": ["public.actor is the intended source"],
    }
    m = QueryCritiqueOutput.model_validate(raw)
    assert m.model_dump(mode="json") == raw


def test_query_explanation_output_roundtrip() -> None:
    raw = {
        "explanation": "The query counts actors and returns a small preview.",
        "limitations": "Result is limited and may be truncated.",
        "follow_up_suggestions": [
            "Break the count down by first letter",
            "Show sample actor names",
        ],
    }
    m = QueryExplanationOutput.model_validate(raw)
    assert m.model_dump(mode="json") == raw


def test_query_structured_output_json_schema_marks_all_properties_required() -> None:
    schema = QueryExplanationOutput.model_json_schema()
    assert schema["required"] == [
        "explanation",
        "limitations",
        "follow_up_suggestions",
    ]


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
