"""Read-only schema documentation readiness (marker file or injectable backend)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SchemaPresence(Protocol):
    """True if persisted schema documentation exists for the query agent."""

    def is_ready(self) -> bool:
        """Return whether schema docs are available for the query path."""

    def reason(self) -> str | None:
        """Optional short debug string (e.g. missing file, invalid json)."""


def _repo_root() -> Path:
    """``src/graph/presence.py`` → repository root."""
    return Path(__file__).resolve().parents[2]


def default_schema_presence_path() -> Path:
    """Default marker path: ``<repo>/data/schema_presence.json``."""
    return _repo_root() / "data" / "schema_presence.json"


class FileSchemaPresence:
    """JSON marker file: ``{"version": 1, "ready": true, ...}``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_reason: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def from_env(cls) -> FileSchemaPresence:
        raw = os.environ.get("SCHEMA_PRESENCE_PATH", "").strip()
        path = Path(raw).expanduser() if raw else default_schema_presence_path()
        return cls(path)

    def reason(self) -> str | None:
        return self._last_reason

    def is_ready(self) -> bool:
        self._last_reason = None
        if not self._path.is_file():
            self._last_reason = "missing file"
            return False
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            self._last_reason = f"read error: {type(exc).__name__}"
            return False
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            self._last_reason = "invalid json"
            return False
        if not isinstance(data, dict):
            self._last_reason = "not a json object"
            return False
        version = data.get("version")
        if version != 1:
            self._last_reason = f"unsupported version: {version!r}"
            return False
        if data.get("ready") is not True:
            self._last_reason = "ready is not true"
            return False
        return True
