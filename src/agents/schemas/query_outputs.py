"""Structured outputs for the query agent (plan + SQL + critique + explanation)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryPlanOutput(BaseModel):
    """High-level plan before SQL generation."""

    intent: str = Field(
        ...,
        description="Short intent label (e.g. aggregate, lookup, explore).",
    )
    summary: str = Field(
        ...,
        description="User question restated in natural language for grounding.",
    )
    relevant_tables: list[str] = Field(
        description='Fully qualified table names such as "public.actor".',
    )
    notes: list[str] = Field(
        description="Optional planner notes (risks, ambiguity).",
    )
    assumptions: list[str] = Field(
        description="Optional assumptions when the question is underspecified.",
    )


class SqlGenerationOutput(BaseModel):
    """Single SELECT candidate for PostgreSQL dvdrental — critic enforces policy."""

    sql: str = Field(
        ...,
        description="Exactly one PostgreSQL SELECT statement including a LIMIT clause.",
    )
    rationale: str = Field(
        description="One or two sentences on why this SQL answers the user.",
    )


class QueryCritiqueOutput(BaseModel):
    """Semantic critique of generated SQL before execution."""

    verdict: str = Field(
        ...,
        description='Semantic review result, typically "accept" or "reject".',
    )
    feedback: str = Field(
        ...,
        description="Concise critique explaining what is wrong or confirming fit.",
    )
    risks: list[str] = Field(
        ...,
        description="Potential ambiguity, mismatch, or interpretation risks.",
    )
    assumptions: list[str] = Field(
        ...,
        description="Assumptions the SQL makes about the user intent or schema.",
    )


class QueryExplanationOutput(BaseModel):
    """Human-facing explanation of the executed SQL result."""

    explanation: str = Field(
        ...,
        description=(
            "Concise natural-language explanation of what the query did and returned."
        ),
    )
    limitations: str = Field(
        ...,
        description=(
            "Limitations, assumptions, truncation notes, or caveats for the result."
        ),
    )
    follow_up_suggestions: list[str] = Field(
        ...,
        description="Optional next questions or refinements the user may want to try.",
    )
