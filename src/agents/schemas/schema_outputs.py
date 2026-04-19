"""Structured schema documentation draft for HITL"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ColumnDraft(BaseModel):
    name: str = Field(description="Column name as in inspect_schema metadata.")
    description: str = Field(
        description="Short NL description for query grounding.",
    )


class TableDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(
        ...,
        alias="schema",
        description='PostgreSQL schema name (often "public").',
    )
    name: str = Field(description="Table name.")
    description: str = Field(description="Short NL description of the table.")
    columns: list[ColumnDraft] = Field(
        description="List of column descriptions for this table."
    )


class SchemaDraftOutput(BaseModel):
    tables: list[TableDraft] = Field(
        description="List of table descriptions in this schema.",
    )
