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
    settings = AppMemorySettings()
    user_id = state.user_id or settings.default_user_id
    delta = state.memory.preferences_proposed_delta

    out: dict[str, Any] = {
        "steps": ["preferences_persist"],
        "memory": {"preferences_proposed_delta": None},
    }

    if not delta:
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
