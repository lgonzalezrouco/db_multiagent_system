"""Demo script for TASK.md demo requirements (schema HITL + NL queries).

Runs:
- one schema documentation session with a human correction (HITL resume),
- three natural language query examples,
- one follow-up refinement example,
all against the DVD Rental dataset (Postgres `dvdrental`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import uuid
from typing import Any

import psycopg
from langgraph.types import Command
from pydantic import ValidationError

from config import MCPSettings, PostgresSettings
from graph import (
    DbSchemaPresence,
    get_compiled_query_graph,
    get_compiled_schema_graph,
    graph_run_config,
)
from graph.invoke_v2 import unwrap_query_graph_v2, unwrap_schema_graph_v2
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


def _bootstrap_postgres() -> int:
    """Check Postgres connectivity with a SELECT 1."""
    try:
        settings = PostgresSettings()
    except ValidationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    conninfo = psycopg.conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
        connect_timeout=5,
    )
    try:
        with (
            psycopg.connect(conninfo) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT 1 AS one, current_database() AS db, current_user AS role"
            )
            row = cur.fetchone()
        logger.info(
            "bootstrap_ok database=%s user=%s select_one=%s", row[1], row[2], row[0]
        )
    except psycopg.OperationalError as exc:
        logger.error("Postgres connection failed: %s", exc)
        return 1
    return 0


def _print_rule(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _compact_json(data: Any, *, max_chars: int = 9000) -> str:
    raw = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "\n... (truncated)"


def _pick_human_correction(
    draft: dict[str, Any],
) -> tuple[dict[str, Any], str] | tuple[None, str]:
    """Return (edited_schema, rationale) for a minimal human correction demo.

    We keep it deterministic and small: tweak one table description if present,
    otherwise tweak the first table we find.
    """
    tables = draft.get("tables") if isinstance(draft, dict) else None
    if not isinstance(tables, list) or not tables:
        return None, "Draft did not contain tables."

    edited = json.loads(json.dumps({"tables": tables}))  # deep copy via JSON
    out_tables = edited.get("tables") or []

    def _edit_table(table: dict[str, Any], *, note: str) -> None:
        prev = str(table.get("description") or "").strip()
        base = (
            prev
            if prev
            else f"Table {table.get('schema', 'public')}.{table.get('name', '')}."
        )
        table["description"] = base + f" {note}"

    # Prefer editing "film" (high-signal table in DVD Rental).
    for t in out_tables:
        if isinstance(t, dict) and str(t.get("name") or "").lower() == "film":
            _edit_table(
                t,
                note=(
                    "(Human correction: clarify this table stores film metadata used "
                    "for rentals.)"
                ),
            )
            return edited, "Adjusted `film` table description to be more explicit."

    # Fallback: edit first table.
    for t in out_tables:
        if isinstance(t, dict) and t.get("name"):
            _edit_table(t, note="(Human correction: tighten wording for clarity.)")
            return (
                edited,
                f"Adjusted `{t.get('name')}` table description to show a human edit.",
            )

    return None, "Could not locate a table object to edit."


def _schema_resume_interactive(payload: dict[str, Any]) -> dict[str, Any] | str:
    """Collect a HITL decision and return a schema resume object or 'reject'."""
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        print("Unexpected schema review payload; rejecting.")
        return "reject"

    edited, rationale = _pick_human_correction(draft)
    print("\nSchema agent produced a draft. A human review is required to continue.\n")
    print("Draft preview (truncated):")
    print(_compact_json({"tables": (draft.get("tables") or [])}, max_chars=5000))

    if edited is not None:
        print("\nProposed human correction:")
        print(f"- {rationale}")

    print(
        "\nChoose one option:\n"
        "  [1] Approve as-is\n"
        "  [2] Apply the proposed human correction (edit)\n"
        "  [3] Paste full edited JSON (advanced)\n"
        "  [4] Reject (end without saving)\n"
    )
    choice = input("Selection (default 2): ").strip() or "2"
    if choice == "1":
        return {"tables": list(draft.get("tables") or [])}
    if choice == "2":
        if edited is None:
            print("No auto-edit available; approving as-is.")
            return {"tables": list(draft.get("tables") or [])}
        return edited
    if choice == "3":
        print(
            '\nPaste JSON like: {"tables":[...]} then press Ctrl-D (Unix) to finish.\n'
        )
        raw = sys.stdin.read()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}. Rejecting.")
            return "reject"
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("tables"), list)
            or not obj["tables"]
        ):
            print('JSON must be an object with a non-empty "tables" list. Rejecting.')
            return "reject"
        return obj
    return "reject"


def _schema_resume_auto(payload: dict[str, Any]) -> dict[str, Any] | str:
    """Non-interactive: always apply a deterministic human correction if possible."""
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        return "reject"
    edited, _rationale = _pick_human_correction(draft)
    if edited is not None:
        return edited
    tables = draft.get("tables")
    if isinstance(tables, list) and tables:
        return {"tables": tables}
    return "reject"


def _print_query_answer(payload: dict[str, Any]) -> None:
    print(f"\nSQL:\n{payload.get('sql', '')}\n")
    cols = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not cols:
        print("(no columns)\n")
        return
    print(" | ".join(cols))
    print("-" * min(120, len(" | ".join(cols)) or 3))
    for row in rows:
        cells = [str(row.get(c, "")) for c in cols]
        print(" | ".join(cells))
    expl = payload.get("explanation")
    lim = payload.get("limitations")
    if expl:
        print(f"\n{expl}")
    if lim:
        print(f"\n{lim}\n")


def _print_query_outcome(state: QueryGraphState) -> None:
    if state.last_error:
        print(f"\nError: {state.last_error}\n", file=sys.stderr)
    lr = state.last_result
    if not lr:
        return
    if isinstance(lr, dict) and lr.get("kind") == "query_answer":
        _print_query_answer(lr)
        return
    print(_compact_json(lr))


async def _ensure_mcp_server(*, auto_start: bool) -> asyncio.subprocess.Process | None:
    """Ensure the local MCP server is reachable; optionally start it."""
    from graph.mcp_helpers import get_mcp_tools

    settings = MCPSettings()
    try:
        await get_mcp_tools(settings)
        return None
    except Exception as exc:
        if not auto_start:
            raise RuntimeError(
                "MCP server is not reachable. Start it with: "
                "`uv run python -m mcp_server` (default http://127.0.0.1:8000/mcp)."
            ) from exc

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_server",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=os.environ.copy(),
    )

    # Retry tool discovery for a short window while the server boots.
    last_exc: Exception | None = None
    for _ in range(20):
        await asyncio.sleep(0.25)
        try:
            await get_mcp_tools(settings)
            return proc
        except Exception as exc:  # noqa: PERF203 - small bounded retry loop
            last_exc = exc
            continue

    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    raise RuntimeError(
        "Started MCP server but it never became reachable."
    ) from last_exc


async def _shutdown_process(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    if proc.returncode is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
        return
    except TimeoutError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=3)


async def _run_schema_demo(*, thread_id: str, auto: bool) -> int:
    app = get_compiled_schema_graph()
    cfg, state_seed = graph_run_config(thread_id=thread_id, run_kind="demo")

    initial: dict[str, Any] = {"steps": [], **state_seed}
    out = await app.ainvoke(initial, config=cfg, version="v2")
    state, interrupts = unwrap_schema_graph_v2(out)

    if state.last_error:
        print("Schema graph failed before review.")
        print(f"Error: {state.last_error}")
        return 1

    if not interrupts:
        # This can happen if schema graph ends without HITL (unexpected).
        print("Schema graph completed without a review interrupt.")
        return 0

    intr = interrupts[0]
    payload = getattr(intr, "value", intr)
    if not isinstance(payload, dict) or payload.get("kind") != "schema_review":
        print(f"Unexpected interrupt payload: {payload!r}")
        return 1

    draft = payload.get("draft")
    if (
        not isinstance(draft, dict)
        or not isinstance(draft.get("tables"), list)
        or not draft.get("tables")
    ):
        print("Schema draft was empty; cannot run the HITL correction demo.")
        return 1

    resume = (
        _schema_resume_auto(payload) if auto else _schema_resume_interactive(payload)
    )
    out2 = await app.ainvoke(Command(resume=resume), config=cfg, version="v2")
    final_state, interrupts2 = unwrap_schema_graph_v2(out2)
    if interrupts2:
        print(
            "Schema graph requested another review interrupt; run interactively "
            "to proceed."
        )
        return 1

    lr = final_state.last_result
    if isinstance(lr, dict) and lr.get("kind") == "schema_persist":
        ok = bool(lr.get("success") is True)
        print("\nSchema persist outcome:")
        print(_compact_json(lr, max_chars=4000))
        return 0 if ok else 1

    if final_state.last_error:
        print(f"Schema error: {final_state.last_error}")
        return 1
    print("Schema flow completed.")
    return 0


async def _run_one_query_turn(
    *, app: Any, cfg: dict[str, Any], state_seed: dict[str, Any], user_input: str
) -> QueryGraphState:
    initial: dict[str, Any] = {
        "user_input": user_input,
        "steps": [],
        **state_seed,
    }
    out = await app.ainvoke(initial, config=cfg, version="v2")
    state, interrupts = unwrap_query_graph_v2(out)
    if interrupts:
        raise RuntimeError(f"Unexpected query interrupt: {interrupts!r}")
    return state


async def _run_query_demo(*, thread_id: str) -> int:
    presence = DbSchemaPresence.from_settings()
    pr = presence.check()
    if not pr.ready:
        print(
            "Schema docs are not ready. Run schema demo first "
            "(or use the Streamlit Schema agent tab)."
        )
        if pr.reason:
            print(f"Reason: {pr.reason}")
        return 1

    app = get_compiled_query_graph()
    cfg, state_seed = graph_run_config(thread_id=thread_id, run_kind="demo")

    examples: list[tuple[str, str]] = [
        (
            "Query 1",
            "Show the top 5 customers by total payments, including customer name "
            "and total amount.",
        ),
        (
            "Query 2",
            "Which films have been rented the most? Show the top 10 films and "
            "rental counts.",
        ),
        (
            "Follow-up refinement",
            "Now only include films in the Comedy category and keep it to the top 10.",
        ),
    ]

    for label, nl in examples:
        _print_rule(label)
        print(f"NL: {nl}")
        state = await _run_one_query_turn(
            app=app, cfg=cfg, state_seed=state_seed, user_input=nl
        )
        _print_query_outcome(state)
        if state.last_error:
            return 1
    return 0


async def _main_async(args: argparse.Namespace) -> int:
    code = _bootstrap_postgres()
    if code != 0:
        return code

    schema_tid = args.schema_thread_id or str(uuid.uuid4())
    query_tid = args.thread_id or schema_tid

    # Avoid noisy LangSmith network retries during a local demo run.
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGSMITH_RUNS_ENDPOINTS", "")

    mcp_proc: asyncio.subprocess.Process | None = None
    try:
        mcp_proc = await _ensure_mcp_server(auto_start=True)

        if not args.skip_schema:
            _print_rule("Schema documentation demo (HITL)")
            code = await _run_schema_demo(thread_id=schema_tid, auto=False)
            if code != 0:
                return code

        _print_rule("Query agent demo (3 NL queries + refinement)")
        return await _run_query_demo(thread_id=query_tid)
    finally:
        # Only stop if we started it.
        await _shutdown_process(mcp_proc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the TASK.md demo script.")
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip the schema HITL session and run the query demo only.",
    )
    parser.add_argument(
        "--schema-thread-id",
        default=None,
        help="Thread id for the schema graph (HITL session). Defaults to a new UUID.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help=(
            "Thread id for the query graph (conversation). "
            "Defaults to schema thread id."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
