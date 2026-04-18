from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from memory.db import get_app_memory_connection

logger = logging.getLogger(__name__)


class SchemaDocsStore:
    """Single-row PostgreSQL store for approved schema documentation."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._ensure_table()

    def _ensure_table(self) -> None:
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_docs (
                    id          SMALLINT    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    version     INT         NOT NULL,
                    payload     JSONB       NOT NULL,
                    ready       BOOLEAN     NOT NULL DEFAULT false,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.commit()

    def get_payload(self) -> dict[str, Any] | None:
        """Return stored payload dict or None if no row. Raises if DB unreachable."""
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM schema_docs WHERE id = 1")
            row = cur.fetchone()
        if row is None:
            return None
        return row["payload"] if isinstance(row, dict) else None

    def is_ready(self) -> bool:
        """True if a row exists and ready=true. Raises if DB unreachable."""
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT ready FROM schema_docs WHERE id = 1")
            row = cur.fetchone()
        if row is None:
            return False
        return bool(row["ready"] if isinstance(row, dict) else False)

    def upsert_approved(
        self,
        payload: dict[str, Any],
        metadata_fingerprint: str | None = None,
    ) -> None:
        """Atomically store approved schema docs and flip ready=true."""
        if metadata_fingerprint:
            payload = {**payload, "metadata_fingerprint": metadata_fingerprint}
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO schema_docs (id, version, payload, ready, updated_at)
                VALUES (1, %s, %s::jsonb, true, %s)
                ON CONFLICT (id) DO UPDATE
                    SET version    = EXCLUDED.version,
                        payload    = EXCLUDED.payload,
                        ready      = true,
                        updated_at = EXCLUDED.updated_at
                """,
                (
                    payload.get("version", 1),
                    Jsonb(payload),
                    datetime.now(UTC),
                ),
            )
            conn.commit()
        logger.info(
            "schema_docs_persisted",
            extra={
                "table_count": len(payload.get("tables") or []),
                "has_fingerprint": metadata_fingerprint is not None,
            },
        )
