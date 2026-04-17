"""Injectable ``SchemaPresence`` doubles for graph unit tests."""

from __future__ import annotations


class ReadySchemaPresence:
    """Treat persisted schema documentation as present (query path)."""

    def is_ready(self) -> bool:
        return True

    def reason(self) -> str | None:
        return None


class NotReadySchemaPresence:
    """Treat schema documentation as absent (schema stub path)."""

    def is_ready(self) -> bool:
        return False

    def reason(self) -> str | None:
        return "unit tests: schema docs absent"
