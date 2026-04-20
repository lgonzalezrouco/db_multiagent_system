"""Structured output for the preferences-inference agent."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from memory.preferences import _DEFAULTS

_ALLOWED_PREF_KEYS: frozenset[str] = frozenset(_DEFAULTS.keys())


def _sanitize_delta_dict(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = {k: v for k, v in raw.items() if k in _ALLOWED_PREF_KEYS}
    return cleaned if cleaned else None


class PreferencesInferenceOutput(BaseModel):
    """Structured preferences inference; use JSON null per key for “no change”."""

    model_config = ConfigDict(extra="ignore")

    @classmethod
    def no_change(cls, rationale: str) -> PreferencesInferenceOutput:
        return cls(
            preferred_language=None,
            output_format=None,
            date_format=None,
            safety_strictness=None,
            row_limit_hint=None,
            rationale=rationale,
        )

    preferred_language: str | None = Field(
        description=(
            'New IETF language tag (e.g. "es", "en") when the user signals a '
            "persistent assistant language preference; null if unchanged."
        ),
    )
    output_format: Literal["table", "json"] | None = Field(
        description='Persistent output format: "table" or "json"; null if unchanged.',
    )
    date_format: Literal["ISO8601", "US", "EU"] | None = Field(
        description="Persistent date display format; null if unchanged.",
    )
    safety_strictness: Literal["strict", "normal", "lenient"] | None = Field(
        description="Persistent safety strictness; null if unchanged.",
    )
    row_limit_hint: int | None = Field(
        ge=1,
        le=500,
        description=(
            "Only when the user asks for a standing/default max rows for all "
            "future answers (not a one-off LIMIT for a single question). "
            "Null if unchanged or if the number only applies to this query."
        ),
    )
    rationale: str = Field(
        description=(
            "One or two sentences: what preference signals were detected and "
            "which fields you set, or why everything stays null."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _unwrap_legacy_proposed_delta(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "proposed_delta" not in data:
            return data
        nested = data.get("proposed_delta")
        flat = {k: v for k, v in data.items() if k != "proposed_delta"}
        if nested is None or nested == {}:
            return flat
        if isinstance(nested, dict):
            for key in _ALLOWED_PREF_KEYS:
                if key in nested:
                    flat[key] = nested[key]
        return flat

    @model_validator(mode="before")
    @classmethod
    def _ensure_all_properties_present(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in _ALLOWED_PREF_KEYS:
            if key not in data:
                data[key] = None
        if "rationale" not in data:
            data["rationale"] = ""
        return data

    @model_validator(mode="after")
    def _nonempty_rationale(self) -> Self:
        if not (self.rationale or "").strip():
            object.__setattr__(
                self,
                "rationale",
                "No explanation provided by the model.",
            )
        return self

    def _collect_raw_delta(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.preferred_language is not None and str(self.preferred_language).strip():
            d["preferred_language"] = str(self.preferred_language).strip()
        if self.output_format is not None:
            d["output_format"] = self.output_format
        if self.date_format is not None:
            d["date_format"] = self.date_format
        if self.safety_strictness is not None:
            d["safety_strictness"] = self.safety_strictness
        if self.row_limit_hint is not None:
            d["row_limit_hint"] = self.row_limit_hint
        return d

    @computed_field
    def proposed_delta(self) -> dict[str, Any] | None:
        return _sanitize_delta_dict(self._collect_raw_delta())

    @classmethod
    def from_delta(
        cls,
        delta: dict[str, Any] | None,
        *,
        rationale: str,
    ) -> PreferencesInferenceOutput:
        d = delta or {}
        return cls(
            preferred_language=d.get("preferred_language"),
            output_format=d.get("output_format"),
            date_format=d.get("date_format"),
            safety_strictness=d.get("safety_strictness"),
            row_limit_hint=d.get("row_limit_hint"),
            rationale=rationale,
        )
