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
    except psycopg.OperationalError:
        warn = "app_memory unreachable while loading preferences"
        out["memory_warning"] = warn
        out["preferences"] = default_preferences()
        logger.warning(warn)

    try:
        docs_store = SchemaDocsStore(settings)
        payload = docs_store.get_payload()
        if payload is not None:
            out["schema_docs_context"] = payload
        else:
            out["schema_docs_warning"] = "No approved schema docs in app_memory"
    except psycopg.OperationalError:
        warn = "app_memory unreachable while loading schema docs"
        out["schema_docs_warning"] = warn
        if out.get("memory_warning") is None:
            out["memory_warning"] = warn
        logger.warning(warn)

    return out


async def memory_update_session(state: GraphState) -> dict[str, Any]:
    """Snapshot session fields and persist dirty preferences to app_memory."""
    steps = list(state.get("steps", []))
    steps.append("memory_update_session")
    settings = AppMemorySettings()
    user_id = state.get("user_id") or settings.default_user_id

    session_delta = snapshot_session_fields(state)
    out: dict[str, Any] = {"steps": steps, **session_delta}

    if state.get("preferences_dirty"):
        prefs = state.get("preferences") or {}
        try:
            store = UserPreferencesStore(settings)
            store.upsert(user_id, prefs)
        except psycopg.OperationalError:
            warn = "could not persist preferences"
            out["memory_warning"] = warn
            logger.warning(warn)

    return out
