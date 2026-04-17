"""Read-only schema documentation readiness (marker file or injectable backend)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NamedTuple, Protocol, runtime_checkable


class SchemaPresenceResult(NamedTuple):
    """Snapshot from a single presence evaluation (thread-safe: no shared state)."""

    ready: bool
    reason: str | None


@runtime_checkable
class SchemaPresence(Protocol):
    """True if persisted schema documentation exists for the query agent."""

    def check(self) -> SchemaPresenceResult:
        """Return readiness and an optional debug reason in one call."""


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

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def from_env(cls) -> FileSchemaPresence:
        raw = os.environ.get("SCHEMA_PRESENCE_PATH", "").strip()
        path = Path(raw).expanduser() if raw else default_schema_presence_path()
        return cls(path)

    def check(self) -> SchemaPresenceResult:
        if not self._path.is_file():
            return SchemaPresenceResult(False, "missing file")
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            return SchemaPresenceResult(False, f"read error: {type(exc).__name__}")
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            return SchemaPresenceResult(False, "invalid json")
        if not isinstance(data, dict):
            return SchemaPresenceResult(False, "not a json object")
        version = data.get("version")
        if version != 1:
            return SchemaPresenceResult(False, f"unsupported version: {version!r}")
        if data.get("ready") is not True:
            return SchemaPresenceResult(False, "ready is not true")
        return SchemaPresenceResult(True, None)
