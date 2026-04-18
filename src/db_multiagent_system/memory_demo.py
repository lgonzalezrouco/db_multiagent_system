"""Interactive demo for Spec 07 — persistent + short-term memory.

Run via ``python main.py --memory-demo`` (or directly).

What it exercises
-----------------
1. Preference round-trip  — upsert prefs for a demo user, reload in a fresh store.
2. Schema docs gate       — DbSchemaPresence.check() (soft-fails gracefully if
                            app_memory is down).
3. Two-turn conversation  — same thread_id, two ainvoke calls; second result shows
                            previous_user_input / previous_sql from the first turn.
4. Soft-fail visibility   — memory_warning / schema_docs_warning surfaced in state.

Requirements
------------
* MCP server running (docker compose up) for the live SQL calls.
* app_memory DB (port 5433) is optional — soft-fail is part of the demo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import psycopg
from pydantic import ValidationError

from config import MCPSettings
from config.memory_settings import AppMemorySettings
from graph import get_compiled_graph, graph_run_config
from graph.presence import DbSchemaPresence, SchemaPresenceResult
from memory.preferences import UserPreferencesStore, default_preferences

logger = logging.getLogger(__name__)

_DEMO_USER = "demo-user"
_DEMO_THREAD_1 = "memory-demo-turn-1"
_DEMO_THREAD_2 = "memory-demo-turn-2"

_CUSTOM_PREFS = {
    "preferred_language": "es",
    "output_format": "table",
    "date_format": "ISO8601",
    "safety_strictness": "strict",
    "row_limit_hint": 5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sep(title: str = "") -> None:
    bar = "─" * 72
    if title:
        print(f"\n{bar}\n  {title}\n{bar}")
    else:
        print(f"\n{bar}")


def _show(label: str, value: Any) -> None:
    if isinstance(value, (dict, list)):
        print(f"  {label}:")
        for line in json.dumps(value, indent=4, default=str).splitlines():
            print(f"    {line}")
    else:
        print(f"  {label}: {value!r}")


def _app_memory_available() -> bool:
    try:
        s = AppMemorySettings()
        with psycopg.connect(
            f"host={s.app_memory_host} port={s.app_memory_port} "
            f"dbname={s.app_memory_db} user={s.app_memory_user} "
            f"password={s.app_memory_password} connect_timeout=2"
        ):
            return True
    except psycopg.OperationalError:
        return False


# ---------------------------------------------------------------------------
# Step 1 — Preference round-trip
# ---------------------------------------------------------------------------


def _demo_prefs_round_trip(available: bool) -> None:
    _sep("STEP 1 — User preference round-trip (app_memory DB)")

    if not available:
        print("  ⚠  app_memory DB not reachable (port 5433). Skipping write.")
        print("     Defaults that would be used:")
        _show("default_preferences()", default_preferences())
        return

    settings = AppMemorySettings()
    store = UserPreferencesStore(settings)

    print(f"  Writing custom prefs for user={_DEMO_USER!r} ...")
    store.upsert(_DEMO_USER, _CUSTOM_PREFS)
    print("  Upsert OK.")

    # Reload in a brand-new store instance (simulates process restart)
    fresh_store = UserPreferencesStore(settings)
    reloaded = fresh_store.get(_DEMO_USER)
    print("  Reloaded in fresh store instance:")
    _show("prefs", reloaded)

    assert reloaded["preferred_language"] == "es", "round-trip failed!"
    assert reloaded["row_limit_hint"] == 5, "round-trip failed!"
    print("  ✓ Preferences survived the store re-instantiation.")


# ---------------------------------------------------------------------------
# Step 2 — Schema presence gate
# ---------------------------------------------------------------------------


def _demo_schema_presence(available: bool) -> None:
    _sep("STEP 2 — DbSchemaPresence.check()")

    presence = DbSchemaPresence.from_settings()
    result: SchemaPresenceResult = presence.check()
    _show("ready", result.ready)
    _show("reason", result.reason)

    if available and result.ready:
        print("  ✓ Schema docs are approved — query path will be used.")
    elif available and not result.ready:
        print("  ℹ  app_memory is reachable but schema_docs.ready = false.")
        print("     Run the schema pipeline first to approve docs.")
    else:
        print("  ⚠  app_memory unreachable — gate will route to schema path.")


# ---------------------------------------------------------------------------
# Step 3 — Two-turn conversation (session continuity)
# ---------------------------------------------------------------------------


class _ForceQueryPath:
    def check(self) -> SchemaPresenceResult:
        return SchemaPresenceResult(True, "memory-demo: force query path")


async def _demo_two_turns(mcp_ok: bool) -> None:
    _sep("STEP 3 — Two-turn conversation (session continuity)")

    if not mcp_ok:
        print("  ⚠  MCP settings invalid — skipping live query turns.")
        return

    app = get_compiled_graph(presence=_ForceQueryPath())

    # --- Turn 1 ---
    print("  Turn 1: 'How many actors are there?'")
    cfg1, seed1 = graph_run_config(thread_id=_DEMO_THREAD_1, user_id=_DEMO_USER)
    state1 = {
        "user_input": "How many actors are there?",
        "steps": [],
        **seed1,
    }
    result1 = await app.ainvoke(state1, config=cfg1)

    _show("steps", result1.get("steps"))
    _show("memory_warning", result1.get("memory_warning"))
    _show("schema_docs_warning", result1.get("schema_docs_warning"))
    _show("preferences loaded", result1.get("preferences"))
    _show("previous_sql (after turn 1)", result1.get("previous_sql"))

    # --- Turn 2 (same thread) ---
    print()
    print("  Turn 2: 'Show me the first 5 films.'  (same thread)")
    cfg2, seed2 = graph_run_config(thread_id=_DEMO_THREAD_1, user_id=_DEMO_USER)
    state2 = {
        "user_input": "Show me the first 5 films.",
        "steps": [],
        **seed2,
    }
    result2 = await app.ainvoke(state2, config=cfg2)

    _show("previous_user_input (from turn 1)", result2.get("previous_user_input"))
    _show("previous_sql      (from turn 1)", result2.get("previous_sql"))
    _show("memory_warning", result2.get("memory_warning"))

    if result2.get("previous_user_input") == "How many actors are there?":
        print("  ✓ Session continuity confirmed — previous turn visible in state.")
    else:
        print("  ℹ  previous_user_input not propagated (memory DB may be down).")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_async() -> int:
    try:
        MCPSettings()
        mcp_ok = True
    except ValidationError:
        logger.warning("MCP settings missing — live query turns will be skipped.")
        mcp_ok = False

    available = _app_memory_available()
    _sep("Spec 07 Memory Demo")
    db_status = "AVAILABLE" if available else "UNREACHABLE (soft-fail mode)"
    mcp_status = "CONFIGURED" if mcp_ok else "NOT CONFIGURED"
    print(f"  app_memory DB (port 5433): {db_status}")
    print(f"  MCP server:               {mcp_status}")

    _demo_prefs_round_trip(available)
    _demo_schema_presence(available)
    await _demo_two_turns(mcp_ok)

    _sep("Done")
    return 0


def run() -> int:
    return asyncio.run(run_async())
