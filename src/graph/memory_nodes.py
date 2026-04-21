"""LangGraph nodes for persistent + session memory."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from config.memory_settings import AppMemorySettings
from graph.state import QueryGraphState
from memory.preferences import UserPreferencesStore, default_preferences
from memory.schema_docs import SchemaDocsStore
from memory.session import seed_session_fields

logger = logging.getLogger(__name__)


async def memory_load_user(state: QueryGraphState) -> dict[str, Any]:
    """Load user preferences and approved schema docs from app_memory into state."""
    settings = AppMemorySettings()
    user_id = state.user_id or settings.default_user_id

    out: dict[str, Any] = {
        "steps": ["memory_load_user"],
        "user_id": user_id,
        "memory": {"warning": None},
        "query": {"docs_context": None, "docs_warning": None},
    }
    session_seed = seed_session_fields(state)
    out.update({k: v for k, v in session_seed.items() if k != "memory"})
    if "memory" in session_seed:
        out["memory"] = {**out.get("memory", {}), **session_seed["memory"]}

    try:
        pref_store = UserPreferencesStore(settings)
        out["memory"] = {
            **out.get("memory", {}),
            "preferences": pref_store.get(user_id),
        }
    except psycopg.OperationalError:
        warn = "app_memory unreachable while loading preferences"
        out["memory"] = {
            **out.get("memory", {}),
            "warning": warn,
            "preferences": default_preferences(),
        }
        logger.warning(warn)

    try:
        docs_store = SchemaDocsStore(settings)
        payload = docs_store.get_payload()
        if payload is not None:
            out["query"] = {**out.get("query", {}), "docs_context": payload}
        else:
            out["query"] = {
                **out.get("query", {}),
                "docs_warning": "No approved schema docs in app_memory",
            }
    except psycopg.OperationalError:
        warn = "app_memory unreachable while loading schema docs"
        out["query"] = {**out.get("query", {}), "docs_warning": warn}
        current_memory = out.get("memory", {})
        if current_memory.get("warning") is None:
            out["memory"] = {**current_memory, "warning": warn}
        logger.warning(warn)

    return out
