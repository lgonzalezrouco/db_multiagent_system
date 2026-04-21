from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.memory_settings import AppMemorySettings
from graph.state import QueryGraphState
from memory.preferences import UserPreferencesStore
from memory.session import snapshot_session_fields

logger = logging.getLogger(__name__)


def _terminal_outcome(state: QueryGraphState) -> str:
    outcome = state.query.outcome
    if isinstance(outcome, str) and outcome.strip():
        return outcome
    payload = state.query.execution_result
    if isinstance(payload, dict) and payload.get("success") is True:
        return "success"
    return "db_failure"


async def persist_prefs_node(state: QueryGraphState) -> dict[str, Any]:
    settings = AppMemorySettings()
    user_id = state.user_id or settings.default_user_id
    timeout_s = max(1, int(settings.persist_prefs_timeout_ms or 1500)) / 1000

    outcome = _terminal_outcome(state)
    warning: str | None = state.memory.warning
    memory_update: dict[str, Any] = {}

    history_delta = snapshot_session_fields(state, include_failures=True)
    if "memory" in history_delta:
        memory_update.update(history_delta["memory"])

    delta = state.memory.preferences_proposed_delta
    if delta:

        def _on_bg_done(task: asyncio.Task) -> None:
            try:
                task.result()
            except Exception:
                logger.warning(
                    "persist_prefs_background_failed",
                    extra={"graph_node": "persist_prefs_node"},
                    exc_info=True,
                )

        store = UserPreferencesStore(settings)
        patch_task = asyncio.create_task(asyncio.to_thread(store.patch, user_id, delta))
        try:
            done, _pending = await asyncio.wait({patch_task}, timeout=timeout_s)
            if patch_task not in done:
                warning = "persist scheduled in background"
                patch_task.add_done_callback(_on_bg_done)
                memory_update["preferences_proposed_delta"] = None
                memory_update["preferences_rationale"] = None
                logger.warning(
                    "persist_prefs_background",
                    extra={"graph_node": "persist_prefs_node"},
                )
            else:
                merged = patch_task.result()
                memory_update["preferences"] = merged
                memory_update["preferences_proposed_delta"] = None
                memory_update["preferences_rationale"] = None
                logger.info(
                    "persist_prefs_ok",
                    extra={
                        "graph_node": "persist_prefs_node",
                        "delta_keys": sorted(delta.keys()),
                    },
                )
        except Exception:
            warning = "could not persist preferences"
            memory_update["preferences_proposed_delta"] = None
            memory_update["preferences_rationale"] = None
            logger.warning(
                "persist_prefs_failed",
                extra={"graph_node": "persist_prefs_node"},
                exc_info=True,
            )

    if warning is not None:
        memory_update["warning"] = warning

    return {
        "steps": ["persist_prefs_node"],
        "query": {"outcome": outcome},
        "memory": memory_update,
    }
