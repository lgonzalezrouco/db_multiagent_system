"""Streamlit entry: session state, chat, schema HITL (same graph + config as CLI)."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import streamlit as st
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from graph import DbSchemaPresence, get_compiled_graph, graph_run_config
from graph.invoke_v2 import unwrap_graph_v2
from ui.formatters import (
    default_schema_edit_json,
    format_turn_state,
    schema_resume_from_inputs,
)

_PENDING_GRAPH_INPUT = "_pending_graph_input"


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


async def _run_until_interrupt_or_done(
    app: Any,
    initial: dict[str, Any],
    config: RunnableConfig,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Run ``ainvoke`` until completion or first ``schema_review`` interrupt."""
    out = await app.ainvoke(initial, config=config, version="v2")
    while True:
        state, interrupts = unwrap_graph_v2(out)
        if not interrupts:
            return state, None
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            msg = f"unexpected interrupt payload: {type(payload).__name__}"
            raise TypeError(msg)
        if payload.get("kind") != "schema_review":
            raise RuntimeError(f"unhandled interrupt: {payload!r}")
        pending = {"config": config, "payload": payload}
        return state, pending


async def _consume_resume(
    app: Any,
    config: RunnableConfig,
    resume: dict[str, Any],
) -> dict[str, Any]:
    """Resume with ``Command`` (same ``config``); loop until done or next HITL."""
    out = await app.ainvoke(Command(resume=resume), config=config, version="v2")
    while True:
        state, interrupts = unwrap_graph_v2(out)
        if not interrupts:
            st.session_state.pop("_schema_hitl", None)
            return state
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            msg = f"unexpected interrupt payload: {type(payload).__name__}"
            raise TypeError(msg)
        if payload.get("kind") != "schema_review":
            raise RuntimeError(f"unhandled interrupt: {payload!r}")
        st.session_state["_schema_hitl"] = {"config": config, "payload": payload}
        st.session_state.pop("hitl_json", None)
        return state


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
        st.session_state["_schema_hitl"] = pending
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
    hitl = st.session_state.get("_schema_hitl")

    for role, content in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(content)

    if hitl:
        st.warning("Schema review required — submit a decision below to continue.")
        payload = hitl["payload"]
        config = hitl["config"]
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
                    try:
                        state = await _consume_resume(app, config, resume)
                        if not st.session_state.get("_schema_hitl"):
                            st.session_state.messages.append(
                                ("assistant", format_turn_state(state)),
                            )
                            st.session_state.pop("hitl_json", None)
                        st.rerun()
                    except (RuntimeError, TypeError) as exc:
                        st.error(str(exc))

    # Two-phase turn: append user, rerun, then await so the user bubble paints first.
    pending_text = st.session_state.get(_PENDING_GRAPH_INPUT)
    if pending_text is not None and not hitl:
        try:
            with st.spinner("Running agents…"):
                final_state = await _run_user_turn(
                    app,
                    pending_text,
                    st.session_state.thread_id,
                )
            if not st.session_state.get("_schema_hitl"):
                st.session_state.messages.append(
                    ("assistant", format_turn_state(final_state)),
                )
        except (RuntimeError, TypeError) as exc:
            st.session_state.messages.append(("assistant", f"**Error:** {exc}"))
        finally:
            st.session_state.pop(_PENDING_GRAPH_INPUT, None)
        st.rerun()

    prompt = None if hitl else st.chat_input("Ask about the DVD Rental database")

    if prompt:
        st.session_state.messages.append(("user", prompt))
        st.session_state[_PENDING_GRAPH_INPUT] = prompt
        st.rerun()


if __name__ == "__main__":
    asyncio.run(main())
