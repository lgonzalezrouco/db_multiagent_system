"""Streamlit entry: session state, chat, schema HITL, preferences HITL."""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from typing import Any

import streamlit as st
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langsmith.run_helpers import trace as ls_trace
from langsmith.run_helpers import tracing_context

from graph import DbSchemaPresence, get_compiled_graph, graph_run_config
from graph.invoke_v2 import unwrap_graph_v2
from ui.formatters import (
    default_schema_edit_json,
    format_turn_state,
    schema_resume_from_inputs,
)

_PENDING_GRAPH_INPUT = "_pending_graph_input"

_SCHEMA_HITL_KEY = "_schema_hitl"
_PREFS_HITL_KEY = "_prefs_hitl"

_PREF_LABELS: dict[str, str] = {
    "preferred_language": "Preferred language (IETF tag, e.g. en, es, fr)",
    "output_format": "Output format (table | json)",
    "date_format": "Date format (ISO8601 | US | EU)",
    "safety_strictness": "Safety strictness (strict | normal | lenient)",
    "row_limit_hint": "Row limit hint (1–500)",
}


def _graph_app() -> Any:
    """Lazy-init compiled graph once per Streamlit session (``st.session_state``)."""
    key = "_compiled_graph_app"
    if key not in st.session_state:
        st.session_state[key] = get_compiled_graph()
    return st.session_state[key]


def _init_thread_id() -> None:
    if "thread_id" not in st.session_state:
        env_tid = os.getenv("DEFAULT_THREAD_ID")
        st.session_state.thread_id = env_tid if env_tid else str(uuid.uuid4())


def _init_messages() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _close_run(
    run_tree: Any, *, outputs: dict | None = None, error: str | None = None
) -> None:
    """End and patch a LangSmith RunTree; silently no-ops on any error."""
    try:
        run_tree.end(outputs=outputs or {}, error=error)
        run_tree.patch()
    except Exception:
        pass


async def _run_until_interrupt_or_done(
    app: Any,
    initial: dict[str, Any],
    config: RunnableConfig,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Run ``ainvoke`` until completion or first known HITL interrupt."""
    async with ls_trace(
        "agent_turn",
        run_type="chain",
        inputs={"user_input": initial.get("user_input", "")},
        tags=config.get("tags") or [],
        metadata=config.get("metadata") or {},
        _end_on_exit=False,
    ) as run_tree:
        out = await app.ainvoke(initial, config=config, version="v2")
    while True:
        state, interrupts = unwrap_graph_v2(out)
        if not interrupts:
            _close_run(run_tree, outputs={"steps": state.steps})
            return state, None
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            msg = f"unexpected interrupt payload: {type(payload).__name__}"
            _close_run(run_tree, error=msg)
            raise TypeError(msg)
        kind = payload.get("kind")
        if kind not in {"schema_review", "preferences_review"}:
            msg = f"unhandled interrupt: {payload!r}"
            _close_run(run_tree, error=msg)
            raise RuntimeError(msg)
        pending = {"config": config, "payload": payload, "run_tree": run_tree}
        return state, pending


async def _consume_resume(
    app: Any,
    config: RunnableConfig,
    resume: dict[str, Any],
    *,
    run_tree: Any = None,
) -> dict[str, Any]:
    """Resume with ``Command`` (same ``config``); loop until done or next HITL."""
    ctx = (
        tracing_context(parent=run_tree)
        if run_tree is not None
        else contextlib.nullcontext()
    )
    with ctx:
        out = await app.ainvoke(Command(resume=resume), config=config, version="v2")
    while True:
        state, interrupts = unwrap_graph_v2(out)
        if not interrupts:
            st.session_state.pop(_SCHEMA_HITL_KEY, None)
            st.session_state.pop(_PREFS_HITL_KEY, None)
            _close_run(run_tree, outputs={"steps": state.steps})
            return state
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            msg = f"unexpected interrupt payload: {type(payload).__name__}"
            raise TypeError(msg)
        kind = payload.get("kind")
        if kind == "schema_review":
            st.session_state[_SCHEMA_HITL_KEY] = {
                "config": config,
                "payload": payload,
                "run_tree": run_tree,
            }
            st.session_state.pop("hitl_json", None)
            return state
        if kind == "preferences_review":
            st.session_state[_PREFS_HITL_KEY] = {
                "config": config,
                "payload": payload,
                "run_tree": run_tree,
            }
            return state
        raise RuntimeError(f"unhandled interrupt: {payload!r}")


async def _run_user_turn(app: Any, user_text: str, thread_id: str) -> dict[str, Any]:
    config, state_seed = graph_run_config(
        thread_id=thread_id,
        run_kind="streamlit",
    )
    initial: dict[str, Any] = {
        "user_input": user_text,
        "steps": [],
        **state_seed,
    }
    state, pending = await _run_until_interrupt_or_done(app, initial, config)
    if pending is not None:
        kind = pending.get("payload", {}).get("kind")
        if kind == "preferences_review":
            st.session_state[_PREFS_HITL_KEY] = pending
        else:
            st.session_state[_SCHEMA_HITL_KEY] = pending
            st.session_state.pop("hitl_json", None)
    return state


async def main() -> None:
    st.set_page_config(page_title="DVD Rental agents", layout="wide")
    st.title("DVD Rental agents")

    _init_thread_id()
    _init_messages()

    presence = DbSchemaPresence.from_settings()
    pr = presence.check()
    st.sidebar.caption("Schema docs (read-only)")
    st.sidebar.write("**Ready**" if pr.ready else "**Not ready**")
    if pr.reason and not pr.ready:
        st.sidebar.caption(pr.reason)

    if st.sidebar.button("New chat"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.pop("_schema_hitl", None)
        st.session_state.pop("hitl_json", None)
        st.session_state.pop(_PENDING_GRAPH_INPUT, None)

    app = _graph_app()
    schema_hitl = st.session_state.get(_SCHEMA_HITL_KEY)
    prefs_hitl = st.session_state.get(_PREFS_HITL_KEY)
    any_hitl = schema_hitl or prefs_hitl

    for role, content in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(content)

    # ------------------------------------------------------------------ #
    # Schema HITL panel                                                    #
    # ------------------------------------------------------------------ #
    if schema_hitl:
        st.warning("Schema review required — submit a decision below to continue.")
        payload = schema_hitl["payload"]
        config = schema_hitl["config"]
        draft = payload.get("draft")
        with st.expander("Schema review (approval required)", expanded=True):
            st.json(payload)
            mode = st.radio("Decision", ["approve", "edit JSON"], horizontal=True)
            if mode == "edit JSON":
                if "hitl_json" not in st.session_state:
                    st.session_state.hitl_json = default_schema_edit_json(draft)
                st.text_area(
                    "Edited tables JSON",
                    height=220,
                    key="hitl_json",
                )
                raw = str(st.session_state.hitl_json)
            else:
                raw = ""
            if st.button("Submit resume", type="primary", key="schema_hitl_submit"):
                resume, err = schema_resume_from_inputs(
                    mode=mode,
                    draft=draft,
                    edited_json=raw,
                )
                if err:
                    st.error(err)
                else:
                    assert resume is not None
                    _run_tree = schema_hitl.get("run_tree")
                    try:
                        state = await _consume_resume(
                            app, config, resume, run_tree=_run_tree
                        )
                        if not st.session_state.get(_SCHEMA_HITL_KEY):
                            st.session_state.messages.append(
                                ("assistant", format_turn_state(state)),
                            )
                            st.session_state.pop("hitl_json", None)
                        st.rerun()
                    except (RuntimeError, TypeError) as exc:
                        _close_run(_run_tree, error=str(exc))
                        st.error(str(exc))

    # ------------------------------------------------------------------ #
    # Preferences HITL panel                                               #
    # ------------------------------------------------------------------ #
    if prefs_hitl:
        st.info(
            "The assistant detected a preference change — review and approve below."
        )
        payload = prefs_hitl["payload"]
        config = prefs_hitl["config"]
        current: dict = payload.get("current") or {}
        proposed: dict = payload.get("proposed_delta") or {}
        rationale: str = payload.get("rationale") or ""

        with st.expander("Preference change review", expanded=True):
            if rationale:
                st.caption(f"**Why:** {rationale}")

            st.write("**Proposed changes:**")
            for k, v in proposed.items():
                label = _PREF_LABELS.get(k, k)
                old = current.get(k, "_(not set)_")
                st.write(f"- **{label}**: `{old}` → `{v}`")

            st.write("**Edit before approving** (optional):")
            edited: dict[str, Any] = {}
            for k, v in proposed.items():
                label = _PREF_LABELS.get(k, k)
                edited[k] = st.text_input(label, value=str(v), key=f"prefs_edit_{k}")

            col_approve, col_reject = st.columns(2)
            with col_approve:
                approve = st.button("Approve", type="primary", key="prefs_hitl_approve")
            with col_reject:
                reject = st.button("Reject (no change)", key="prefs_hitl_reject")

            if approve or reject:
                # Rejection uses the "reject" sentinel string (not None/{}
                # which LangGraph may misinterpret as an interrupt-id map).
                resume_delta = edited if approve else "reject"
                _run_tree = prefs_hitl.get("run_tree")
                try:
                    state = await _consume_resume(
                        app, config, resume_delta, run_tree=_run_tree
                    )
                    st.session_state.pop(_PREFS_HITL_KEY, None)
                    if not st.session_state.get(_SCHEMA_HITL_KEY):
                        st.session_state.messages.append(
                            ("assistant", format_turn_state(state)),
                        )
                    st.rerun()
                except (RuntimeError, TypeError) as exc:
                    if _run_tree:
                        _close_run(_run_tree, error=str(exc))
                    st.error(str(exc))

    # ------------------------------------------------------------------ #
    # Two-phase turn: append user, rerun, then await                      #
    # ------------------------------------------------------------------ #
    pending_text = st.session_state.get(_PENDING_GRAPH_INPUT)
    if pending_text is not None and not any_hitl:
        try:
            with st.spinner("Running agents…"):
                final_state = await _run_user_turn(
                    app,
                    pending_text,
                    st.session_state.thread_id,
                )
            if not st.session_state.get(_SCHEMA_HITL_KEY) and not st.session_state.get(
                _PREFS_HITL_KEY
            ):
                st.session_state.messages.append(
                    ("assistant", format_turn_state(final_state)),
                )
        except (RuntimeError, TypeError) as exc:
            st.session_state.messages.append(("assistant", f"**Error:** {exc}"))
        finally:
            st.session_state.pop(_PENDING_GRAPH_INPUT, None)
        st.rerun()

    prompt = None if any_hitl else st.chat_input("Ask about the DVD Rental database")

    if prompt:
        st.session_state.messages.append(("user", prompt))
        st.session_state[_PENDING_GRAPH_INPUT] = prompt
        st.rerun()


if __name__ == "__main__":
    asyncio.run(main())
