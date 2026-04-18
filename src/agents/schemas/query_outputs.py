"""Structured outputs for the query agent (plan + SQL)."""

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
