"""Query-pipeline node: persist an approved preference delta to app_memory."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from config.memory_settings import AppMemorySettings
from graph.state import GraphState
from memory.preferences import UserPreferencesStore

logger = logging.getLogger(__name__)


async def preferences_persist(state: GraphState) -> dict[str, Any]:
    """Patch user preferences in app_memory with the HITL-approved delta.

    - Uses ``UserPreferencesStore.patch`` (JSONB merge) so only the approved
      keys are touched; all other stored prefs are preserved.
    - Updates ``state.memory.preferences`` with the merged result so downstream
      nodes in the same turn see the new values immediately.
    - Clears ``preferences_proposed_delta`` after a successful write.
    - Soft-fails on ``psycopg.OperationalError``: logs a warning and sets
      ``memory.warning``; the query pipeline continues with in-memory prefs.

    If the approved delta is empty or None (user rejected at HITL), this node
    is a no-op — it should not be reached in that case (the router skips it),
    but the guard is here for safety.
    """
    settings = AppMemorySettings()
    user_id = state.user_id or settings.default_user_id
    delta = state.memory.preferences_proposed_delta

    out: dict[str, Any] = {
        "steps": ["preferences_persist"],
        "memory": {"preferences_proposed_delta": None},
    }

    if not delta:
        # Nothing approved — clear and move on
        return out

    try:
        store = UserPreferencesStore(settings)
        merged = store.patch(user_id, delta)
        out["memory"] = {
            "preferences": merged,
            "preferences_proposed_delta": None,
            "preferences_rationale": None,
        }
        logger.info(
            "preferences_persisted",
            extra={"user_id": user_id, "delta_keys": sorted(delta.keys())},
        )
    except psycopg.OperationalError as exc:
        warn = f"could not persist preference update: {type(exc).__name__}"
        out["memory"] = {
            "preferences_proposed_delta": None,
            "warning": warn,
        }
        logger.warning("preferences_persist failed: %s", exc)

    return out
