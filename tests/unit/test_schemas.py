"""Pydantic schema contracts for structured LLM outputs."""

from __future__ import annotations

from agents.schemas.query_outputs import (
    QueryCritiqueOutput,
    QueryExplanationOutput,
    QueryPlanOutput,
    SqlGenerationOutput,
)
from agents.schemas.schema_outputs import ColumnDraft, SchemaDraftOutput, TableDraft


def test_query_plan_output_roundtrips_all_fields() -> None:
    """QueryPlanOutput serializes and deserializes without data loss."""
    # Given: raw query plan data
    raw = {
        "intent": "aggregate",
        "summary": "Count rows",
        "relevant_tables": ["public.actor"],
        "notes": ["n1"],
        "assumptions": [],
    }

    # When: validating and dumping
    model = QueryPlanOutput.model_validate(raw)

    # Then: output matches input
    assert model.model_dump(mode="json") == raw


def test_sql_generation_output_requires_sql_field() -> None:
    """SqlGenerationOutput validates presence of sql field."""
    # Given: raw SQL generation data with required fields
    raw = {"sql": "SELECT 1 AS x LIMIT 1", "rationale": "smoke"}

    # When: validating
    model = SqlGenerationOutput.model_validate(raw)

    # Then: sql field is accessible
    assert model.sql.startswith("SELECT")


def test_query_critique_output_roundtrips_all_fields() -> None:
    """QueryCritiqueOutput serializes and deserializes without data loss."""
    # Given: raw critique data
    raw = {
        "verdict": "accept",
        "feedback": "SQL matches the user request.",
        "risks": ["May omit null handling"],
        "assumptions": ["public.actor is the intended source"],
    }

    # When: validating and dumping
    model = QueryCritiqueOutput.model_validate(raw)

    # Then: output matches input
    assert model.model_dump(mode="json") == raw


def test_query_explanation_output_roundtrips_all_fields() -> None:
    """QueryExplanationOutput serializes and deserializes without data loss."""
    # Given: raw explanation data
    raw = {
        "explanation": "The query counts actors and returns a small preview.",
        "limitations": "Result is limited and may be truncated.",
        "follow_up_suggestions": [
            "Break the count down by first letter",
            "Show sample actor names",
        ],
    }

    # When: validating and dumping
    model = QueryExplanationOutput.model_validate(raw)

    # Then: output matches input
    assert model.model_dump(mode="json") == raw


def test_query_explanation_schema_marks_all_properties_required() -> None:
    """QueryExplanationOutput JSON schema requires all properties."""
    # Given: the model class

    # When: getting JSON schema
    schema = QueryExplanationOutput.model_json_schema()

    # Then: all fields are required
    assert schema["required"] == [
        "explanation",
        "limitations",
        "follow_up_suggestions",
    ]


def test_schema_draft_output_uses_schema_alias() -> None:
    """SchemaDraftOutput serializes with correct field aliases."""
    # Given: a schema draft with tables and columns
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

    # When: dumping with aliases
    dumped = out.model_dump(by_alias=True, mode="json")

    # Then: schema alias is used correctly
    assert dumped["tables"][0]["schema"] == "public"
    assert dumped["tables"][0]["columns"][0]["name"] == "actor_id"
