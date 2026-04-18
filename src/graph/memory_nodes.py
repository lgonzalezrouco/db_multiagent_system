"""LangGraph nodes for persistent + session memory."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from config.memory_settings import AppMemorySettings
from graph.state import GraphState
from memory.preferences import UserPreferencesStore, default_preferences
from memory.schema_docs import SchemaDocsStore
from memory.session import seed_session_fields, snapshot_session_fields

logger = logging.getLogger(__name__)


async def memory_load_user(state: GraphState) -> dict[str, Any]:
    """Load user preferences and approved schema docs from app_memory into state."""
    steps = list(state.get("steps", []))
    steps.append("memory_load_user")
    settings = AppMemorySettings()
    user_id = state.get("user_id") or settings.default_user_id

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "memory_load_user",
            "graph_phase": "enter",
            "user_id": user_id,
            "steps_count": len(steps),
        },
    )

    out: dict[str, Any] = {
        "steps": steps,
        "user_id": user_id,
        "memory_warning": None,
        "schema_docs_warning": None,
        "schema_docs_context": None,
        "preferences": None,
    }
    out.update(seed_session_fields(state))

    try:
        pref_store = UserPreferencesStore(settings)
        out["preferences"] = pref_store.get(user_id)
    except psycopg.OperationalError as exc:
        warn = f"app_memory unreachable: {type(exc).__name__}"
        out["memory_warning"] = warn
        out["preferences"] = default_preferences()
        logger.warning(
            "memory_load_user_db_error",
            extra={
                "graph_node": "memory_load_user",
                "phase": "preferences",
                "warning": warn,
            },
        )

    try:
        docs_store = SchemaDocsStore(settings)
        payload = docs_store.get_payload()
        if payload is not None:
            out["schema_docs_context"] = payload
        else:
            out["schema_docs_warning"] = "No approved schema docs in app_memory"
    except psycopg.OperationalError as exc:
        warn = f"app_memory unreachable: {type(exc).__name__}"
        out["schema_docs_warning"] = warn
        if out.get("memory_warning") is None:
            out["memory_warning"] = warn
        logger.warning(
            "memory_load_user_db_error",
            extra={
                "graph_node": "memory_load_user",
                "phase": "schema_docs",
                "warning": warn,
            },
        )

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "memory_load_user",
            "graph_phase": "exit",
            "user_id": user_id,
            "has_prefs": out["preferences"] is not None,
            "has_schema_docs": out["schema_docs_context"] is not None,
            "memory_warning": out["memory_warning"],
            "steps_count": len(steps),
        },
    )
    return out


async def memory_update_session(state: GraphState) -> dict[str, Any]:
    """Snapshot session fields and persist dirty preferences to app_memory."""
    steps = list(state.get("steps", []))
    steps.append("memory_update_session")
    settings = AppMemorySettings()
    user_id = state.get("user_id") or settings.default_user_id

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "memory_update_session",
            "graph_phase": "enter",
            "user_id": user_id,
            "steps_count": len(steps),
        },
    )

    session_delta = snapshot_session_fields(state)
    out: dict[str, Any] = {"steps": steps, **session_delta}

    if state.get("preferences_dirty"):
        prefs = state.get("preferences") or {}
        try:
            store = UserPreferencesStore(settings)
            store.upsert(user_id, prefs)
        except psycopg.OperationalError as exc:
            warn = f"could not persist preferences: {type(exc).__name__}"
            out["memory_warning"] = warn
            logger.warning(
                "memory_update_session_db_error",
                extra={"graph_node": "memory_update_session", "warning": warn},
            )

    logger.info(
        "graph_node_transition",
        extra={
            "graph_node": "memory_update_session",
            "graph_phase": "exit",
            "user_id": user_id,
            "preferences_dirty": bool(state.get("preferences_dirty")),
            "steps_count": len(steps),
        },
    )
    return out
