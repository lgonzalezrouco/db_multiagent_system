"""Injectable ``SchemaPresence`` doubles for graph unit tests."""

from __future__ import annotations

from graph.presence import SchemaPresenceResult


class ReadySchemaPresence:
    """Treat persisted schema documentation as present (query path)."""

    def check(self) -> SchemaPresenceResult:
        return SchemaPresenceResult(True, None)


class NotReadySchemaPresence:
    """Treat schema documentation as absent (schema stub path)."""

    def check(self) -> SchemaPresenceResult:
        return SchemaPresenceResult(False, "unit tests: schema docs absent")
