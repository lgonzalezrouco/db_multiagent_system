from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from memory.db import get_app_memory_connection

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "preferred_language": "en",
    "output_format": "table",
    "date_format": "ISO8601",
    "safety_strictness": "strict",
    "row_limit_hint": 10,
}


def default_preferences() -> dict[str, Any]:
    """Canonical defaults (same base dict as merge in UserPreferencesStore.get)."""
    return dict(_DEFAULTS)


class UserPreferencesStore:
    """PostgreSQL-backed user preferences."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._ensure_table()

    def _ensure_table(self) -> None:
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id     TEXT        PRIMARY KEY,
                    prefs       JSONB       NOT NULL DEFAULT '{}'::jsonb,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.commit()

    def get(self, user_id: str) -> dict[str, Any]:
        """Return stored prefs merged with defaults. Does not insert on miss."""
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT prefs FROM user_preferences WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if row is None:
            return dict(_DEFAULTS)
        stored = row["prefs"] if isinstance(row, dict) else {}
        return {**_DEFAULTS, **stored}

    def upsert(self, user_id: str, prefs: dict[str, Any]) -> None:
        """Insert or replace all prefs for user_id (full overwrite)."""
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_preferences (user_id, prefs, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET prefs      = EXCLUDED.prefs,
                        updated_at = EXCLUDED.updated_at
                """,
                (user_id, Jsonb(prefs), datetime.now(UTC)),
            )
            conn.commit()
        logger.info(
            "preferences_upserted",
            extra={"user_id": user_id, "prefs_keys": sorted(prefs.keys())},
        )

    def patch(self, user_id: str, delta: dict[str, Any]) -> dict[str, Any]:
        """Merge *delta* into the stored prefs using JSONB || operator.

        - If the user has no row yet, inserts *delta* as the initial value.
        - Only keys present in *delta* are touched; other stored keys are preserved.
        - Returns the full merged prefs dict (defaults applied on top of stored).
        """
        now = datetime.now(UTC)
        with get_app_memory_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_preferences (user_id, prefs, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET prefs      = user_preferences.prefs || EXCLUDED.prefs,
                        updated_at = EXCLUDED.updated_at
                RETURNING prefs
                """,
                (user_id, Jsonb(delta), now),
            )
            row = cur.fetchone()
            conn.commit()
        stored: dict[str, Any] = (
            row["prefs"] if row and isinstance(row, dict) else delta
        )
        merged = {**_DEFAULTS, **stored}
        logger.info(
            "preferences_patched",
            extra={"user_id": user_id, "delta_keys": sorted(delta.keys())},
        )
        return merged
