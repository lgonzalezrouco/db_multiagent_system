"""CLI entry: Postgres bootstrap, interactive LangGraph query chat."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

import psycopg
from langgraph.types import Command
from pydantic import ValidationError

from config import PostgresSettings
from graph import get_compiled_query_graph, graph_run_config
from graph.invoke_v2 import unwrap_query_graph_v2
from graph.state import QueryGraphState

logger = logging.getLogger(__name__)


def _bootstrap() -> int:
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
            "bootstrap_ok database=%s user=%s select_one=%s",
            row[1],
            row[2],
            row[0],
        )
    except psycopg.OperationalError as exc:
        logger.error("Postgres connection failed: %s", exc)
        return 1
    return 0


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


def _print_outcome(state: QueryGraphState) -> None:
    err = state.last_error
    if err:
        print(f"\nError: {err}\n", file=sys.stderr)
    lr = state.last_result
    if not lr:
        if not err:
            print("\n(no last_result in state)\n")
        return
    if isinstance(lr, dict) and lr.get("kind") == "query_answer":
        _print_query_answer(lr)
        return
    print(json.dumps(lr, indent=2, ensure_ascii=False, default=str))
    print()


def _stdin_question() -> str | None:
    if sys.stdin.isatty():
        return None
    text = sys.stdin.read().strip()
    return text or None


async def _prompt_preferences_resume(interrupt_payload: dict[str, Any]) -> Any:
    print("\n--- Preference change review ---\n")
    print(json.dumps(interrupt_payload, indent=2, ensure_ascii=False, default=str))
    print("\nType 'approve' to apply proposed edits, or 'reject' to skip.")

    def _read() -> str:
        return input("prefs> ").strip()

    line = await asyncio.to_thread(_read)
    if line.lower() == "reject":
        return "reject"
    if line.lower() == "approve":
        proposed = interrupt_payload.get("proposed_delta") or {}
        return {k: str(v) for k, v in proposed.items()}
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON for resume: {exc}") from exc


async def _run_turn_with_hitl(
    app: Any,
    input_arg: dict[str, Any],
    config: dict[str, Any],
) -> QueryGraphState:
    out = await app.ainvoke(input_arg, config=config, version="v2")
    while True:
        state, interrupts = unwrap_query_graph_v2(out)
        if not interrupts:
            return state
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            raise SystemExit(f"unexpected interrupt payload: {type(payload).__name__}")
        if payload.get("kind") == "preferences_review":
            resume = await _prompt_preferences_resume(payload)
            out = await app.ainvoke(Command(resume=resume), config=config, version="v2")
            continue
        print(
            f"\n(unhandled interrupt kind {payload!r}; stopping.)\n",
            file=sys.stderr,
        )
        return state


async def _interactive_chat(
    *,
    thread_id: str | None,
    initial_question: str | None,
) -> int:
    app = get_compiled_query_graph()
    cfg, state_seed = graph_run_config(thread_id=thread_id)

    print(
        "DVD Rental query agent — ask in natural language (read-only SQL via MCP).\n"
        "Schema documentation must be set up via Streamlit (Schema agent tab) first.\n"
        "Commands: empty line or /quit to exit.\n",
    )

    async def one_turn(user_input: str) -> QueryGraphState:
        initial: dict[str, Any] = {
            "user_input": user_input,
            "steps": [],
            **state_seed,
        }
        return await _run_turn_with_hitl(app, initial, cfg)

    if initial_question:
        state = await one_turn(initial_question)
        _print_outcome(state)
        if state.last_error:
            return 1
        if not sys.stdin.isatty():
            return 0

    while True:
        line = await asyncio.to_thread(input, "you> ")
        text = line.strip()
        if not text or text.lower() in ("/quit", "/exit", "quit", "exit"):
            print("bye.")
            return 0
        state = await one_turn(text)
        _print_outcome(state)


async def _main_async(args: argparse.Namespace) -> int:
    if not args.no_bootstrap:
        code = _bootstrap()
        if code != 0:
            return code

    initial = args.query or _stdin_question()
    try:
        return await _interactive_chat(
            thread_id=args.thread_id,
            initial_question=initial,
        )
    except (KeyboardInterrupt, EOFError):
        print("\nbye.")
        return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Chat with the LangGraph DVD Rental query agent.",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Skip the Postgres SELECT 1 connectivity check.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help=(
            "LangGraph thread id for checkpointing "
            "(default from DEFAULT_THREAD_ID / .env)."
        ),
    )
    parser.add_argument(
        "-q",
        "--query",
        default=None,
        help=(
            "Ask one question first; with a TTY, continue in REPL. "
            "Pass via stdin for a single non-interactive question."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
