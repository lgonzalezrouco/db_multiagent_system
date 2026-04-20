"""Structured output for the query-topic guardrail."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GuardrailOutput(BaseModel):
    """Topic classification for DVD Rental scope checks."""

    in_scope: bool = Field(
        ...,
        description="True when the user message is about the DVD Rental dataset.",
    )
    reason: str = Field(
        ...,
        description="Short rationale for the in-scope / out-of-scope decision.",
    )
    canned_response: str = Field(
        ...,
        description="Short user-facing response for out-of-scope messages.",
    )
