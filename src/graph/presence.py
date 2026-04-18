"""Schema documentation readiness (injectable backend)."""

from __future__ import annotations

import logging
from typing import NamedTuple, Protocol, runtime_checkable

import psycopg

logger = logging.getLogger(__name__)


class SchemaPresenceResult(NamedTuple):
    """Snapshot from a single presence evaluation (thread-safe: no shared state)."""

    ready: bool
    reason: str | None


@runtime_checkable
class SchemaPresence(Protocol):
    """True if persisted schema documentation exists for the query agent."""

    def check(self) -> SchemaPresenceResult:
        """Return readiness and an optional debug reason in one call."""


class _SchemaDocsBackend(Protocol):
    """Structural interface for DbSchemaPresence — any object with is_ready()."""

    def is_ready(self) -> bool: ...


class DbSchemaPresence:
    """SchemaPresence implementation backed by app_memory.schema_docs."""

    def __init__(self, store: _SchemaDocsBackend | None = None, settings=None) -> None:
        self._store = store
        self._settings = settings

    @classmethod
    def from_settings(cls, settings=None) -> DbSchemaPresence:
        """Lazy constructor — store is created on first check() call."""
        return cls(settings=settings)

    def check(self) -> SchemaPresenceResult:
        from memory.schema_docs import SchemaDocsStore

        try:
            if self._store is None:
                self._store = SchemaDocsStore(self._settings)
            ready = self._store.is_ready()
        except psycopg.OperationalError as exc:
            reason = f"app_memory unreachable: {type(exc).__name__}"
            logger.warning(
                "schema_presence_db_error",
                extra={"reason": reason},
            )
            return SchemaPresenceResult(ready=False, reason=reason)
        reason = None if ready else "schema_docs.ready is false or row missing"
        logger.info(
            "schema_presence_check",
            extra={"schema_docs_ready": ready, "reason": reason},
        )
        return SchemaPresenceResult(ready=ready, reason=reason)
