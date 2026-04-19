"""Structured output for the preferences-inference agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PreferencesInferenceOutput(BaseModel):
    """LLM response for a single preferences-inference call.

    ``proposed_delta`` is ``None`` when the user's input contains no clear
    signal to change a preference.  When non-None it must only include keys
    that belong to the canonical set; callers validate this.
    """

    proposed_delta: dict[str, Any] | None = Field(
        ...,
        description=(
            "Subset of user-preference keys to update, or null if no change "
            "is needed.  Only include keys where the user expressed clear "
            "intent.  Valid keys: preferred_language, output_format, "
            "date_format, safety_strictness, row_limit_hint."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "One or two sentences explaining what signal was detected and why "
            "the delta was chosen, or why no change was proposed."
        ),
    )
