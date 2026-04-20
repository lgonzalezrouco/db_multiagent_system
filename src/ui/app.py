"""Streamlit entry: Schema agent tab + Query agent tab (separate graphs)."""

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

from graph import (
    DbSchemaPresence,
    get_compiled_query_graph,
    get_compiled_schema_graph,
    graph_run_config,
)
from graph.invoke_v2 import unwrap_query_graph_v2, unwrap_schema_graph_v2
from ui.formatters import (
    default_schema_edit_json,
    format_schema_turn_state,
    format_turn_state,
    schema_resume_from_inputs,
)

_PENDING_QUERY_INPUT = "_pending_graph_input"
_PENDING_SCHEMA_RUN = "_pending_schema_run"

_SCHEMA_HITL_KEY = "_schema_hitl"
_NAV_AGENT_KEY = "agent_tab"


def _query_graph_app() -> Any:
    key = "_compiled_query_graph_app"
    if key not in st.session_state:
        st.session_state[key] = get_compiled_query_graph()
    return st.session_state[key]


def _schema_graph_app() -> Any:
    key = "_compiled_schema_graph_app"
    if key not in st.session_state:
        st.session_state[key] = get_compiled_schema_graph()
    return st.session_state[key]


def _init_query_thread_id() -> None:
    if "thread_id" not in st.session_state:
        env_tid = os.getenv("DEFAULT_THREAD_ID")
        st.session_state.thread_id = env_tid if env_tid else str(uuid.uuid4())


def _init_schema_thread_id() -> None:
    if "schema_thread_id" not in st.session_state:
        st.session_state.schema_thread_id = str(uuid.uuid4())


def _init_messages() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _init_schema_messages() -> None:
    if "schema_messages" not in st.session_state:
        st.session_state.schema_messages = []


def _close_run(
    run_tree: Any, *, outputs: dict | None = None, error: str | None = None
) -> None:
    try:
        run_tree.end(outputs=outputs or {}, error=error)
        run_tree.patch()
    except Exception:
        pass


async def _run_until_interrupt_or_done_query(
    app: Any,
    initial: dict[str, Any],
    config: RunnableConfig,
) -> tuple[Any, dict[str, Any] | None]:
    async with ls_trace(
        "agent_turn_query",
        run_type="chain",
        inputs={"user_input": initial.get("user_input", "")},
        tags=config.get("tags") or [],
        metadata=config.get("metadata") or {},
        _end_on_exit=False,
    ) as run_tree:
        out = await app.ainvoke(initial, config=config, version="v2")
        while True:
            state, interrupts = unwrap_query_graph_v2(out)
            if not interrupts:
                _close_run(run_tree, outputs={"steps": state.steps})
                return state, None
            intr = interrupts[0]
            payload = getattr(intr, "value", intr)
            if not isinstance(payload, dict):
                msg = f"unexpected interrupt payload: {type(payload).__name__}"
                _close_run(run_tree, error=msg)
                raise TypeError(msg)
            msg = f"unexpected query interrupt: {payload!r}"
            _close_run(run_tree, error=msg)
            raise RuntimeError(msg)


async def _run_until_interrupt_or_done_schema(
    app: Any,
    initial: dict[str, Any],
    config: RunnableConfig,
) -> tuple[Any, dict[str, Any] | None]:
    async with ls_trace(
        "agent_turn_schema",
        run_type="chain",
        inputs={"schema_run": True},
        tags=config.get("tags") or [],
        metadata=config.get("metadata") or {},
        _end_on_exit=False,
    ) as run_tree:
        out = await app.ainvoke(initial, config=config, version="v2")
        while True:
            state, interrupts = unwrap_schema_graph_v2(out)
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
            if kind != "schema_review":
                msg = f"unhandled interrupt: {payload!r}"
                _close_run(run_tree, error=msg)
                raise RuntimeError(msg)
            pending = {"config": config, "payload": payload, "run_tree": run_tree}
            return state, pending


async def _consume_resume_schema(
    app: Any,
    config: RunnableConfig,
    resume: dict[str, Any] | str,
    *,
    run_tree: Any = None,
) -> Any:
    ctx = (
        tracing_context(parent=run_tree)
        if run_tree is not None
        else contextlib.nullcontext()
    )
    with ctx:
        out = await app.ainvoke(Command(resume=resume), config=config, version="v2")
    while True:
        state, interrupts = unwrap_schema_graph_v2(out)
        if not interrupts:
            st.session_state.pop(_SCHEMA_HITL_KEY, None)
            _close_run(run_tree, outputs={"steps": state.steps})
            return state
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            msg = f"unexpected interrupt payload: {type(payload).__name__}"
            raise TypeError(msg)
        if payload.get("kind") == "schema_review":
            st.session_state[_SCHEMA_HITL_KEY] = {
                "config": config,
                "payload": payload,
                "run_tree": run_tree,
            }
            st.session_state.pop("hitl_json", None)
            return state
        raise RuntimeError(f"unhandled interrupt: {payload!r}")


async def _run_user_turn_query(app: Any, user_text: str, thread_id: str) -> Any:
    config, state_seed = graph_run_config(
        thread_id=thread_id,
        run_kind="streamlit",
    )
    initial: dict[str, Any] = {
        "user_input": user_text,
        "steps": [],
        **state_seed,
    }
    state, pending = await _run_until_interrupt_or_done_query(app, initial, config)
    if pending is not None:
        raise RuntimeError("query graph returned unexpected pending interrupt")
    return state


async def _run_schema_start(app: Any, thread_id: str) -> Any:
    config, state_seed = graph_run_config(
        thread_id=thread_id,
        run_kind="streamlit",
    )
    initial: dict[str, Any] = {
        "steps": [],
        **state_seed,
    }
    state, pending = await _run_until_interrupt_or_done_schema(app, initial, config)
    if pending is not None:
        st.session_state[_SCHEMA_HITL_KEY] = pending
        st.session_state.pop("hitl_json", None)
    return state


async def main() -> None:
    st.set_page_config(page_title="DVD Rental agents", layout="wide")
    st.title("DVD Rental agents")

    _init_query_thread_id()
    _init_schema_thread_id()
    _init_messages()
    _init_schema_messages()

    presence = DbSchemaPresence.from_settings()
    pr = presence.check()
    st.sidebar.caption("Schema docs (read-only)")
    st.sidebar.write("**Ready**" if pr.ready else "**Not ready**")
    if pr.reason and not pr.ready:
        st.sidebar.caption(pr.reason)

    if st.sidebar.button("New query chat"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.pop(_PENDING_QUERY_INPUT, None)

    if st.sidebar.button("New schema session"):
        st.session_state.schema_thread_id = str(uuid.uuid4())
        st.session_state.schema_messages = []
        st.session_state.pop(_SCHEMA_HITL_KEY, None)
        st.session_state.pop("hitl_json", None)
        st.session_state.pop(_PENDING_SCHEMA_RUN, None)

    st.session_state.setdefault(_NAV_AGENT_KEY, "Query agent")
    nav = st.radio(
        "Agent",
        ["Schema agent", "Query agent"],
        horizontal=True,
        label_visibility="collapsed",
        key=_NAV_AGENT_KEY,
    )

    if nav == "Schema agent":
        await _render_schema_tab()
    else:
        await _render_query_tab(presence)


async def _render_schema_tab() -> None:
    app = _schema_graph_app()
    schema_hitl = st.session_state.get(_SCHEMA_HITL_KEY)

    for role, content in st.session_state.schema_messages:
        with st.chat_message(role):
            st.markdown(content)

    if schema_hitl:
        st.warning("Schema review required — submit a decision below to continue.")
        payload = schema_hitl["payload"]
        config = schema_hitl["config"]
        draft = payload.get("draft")
        with st.expander("Schema review (approval required)", expanded=True):
            st.json(payload)
            mode = st.radio(
                "Decision",
                ["approve", "edit JSON", "reject"],
                horizontal=True,
            )
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
                        state = await _consume_resume_schema(
                            app, config, resume, run_tree=_run_tree
                        )
                        if not st.session_state.get(_SCHEMA_HITL_KEY):
                            st.session_state.schema_messages.append(
                                ("assistant", format_schema_turn_state(state)),
                            )
                            st.session_state.pop("hitl_json", None)
                        st.rerun()
                    except (RuntimeError, TypeError) as exc:
                        _close_run(_run_tree, error=str(exc))
                        st.error(str(exc))

    pending_schema = st.session_state.get(_PENDING_SCHEMA_RUN)
    if pending_schema and not schema_hitl:
        try:
            with st.spinner("Running schema agent…"):
                final_state = await _run_schema_start(
                    app,
                    st.session_state.schema_thread_id,
                )
            if not st.session_state.get(_SCHEMA_HITL_KEY):
                st.session_state.schema_messages.append(
                    ("assistant", format_schema_turn_state(final_state)),
                )
        except (RuntimeError, TypeError) as exc:
            st.session_state.schema_messages.append(
                ("assistant", f"**Error:** {exc}"),
            )
        finally:
            st.session_state.pop(_PENDING_SCHEMA_RUN, None)
        st.rerun()

    if not schema_hitl and st.button(
        "Start schema review", type="primary", key="schema_start_btn"
    ):
        st.session_state[_PENDING_SCHEMA_RUN] = True
        st.rerun()


async def _render_query_tab(presence: Any) -> None:
    pr = presence.check()
    if not pr.ready:
        st.warning(
            "Schema docs are not ready yet. Please run the **Schema agent** tab first."
        )
        if st.button("Go to Schema tab", key="goto_schema_tab"):
            st.session_state[_NAV_AGENT_KEY] = "Schema agent"
            st.rerun()
        return

    app = _query_graph_app()

    for role, content in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(content)

    pending_text = st.session_state.get(_PENDING_QUERY_INPUT)
    if pending_text is not None:
        try:
            with st.spinner("Running agents…"):
                final_state = await _run_user_turn_query(
                    app,
                    pending_text,
                    st.session_state.thread_id,
                )
            st.session_state.messages.append(
                ("assistant", format_turn_state(final_state)),
            )
        except (RuntimeError, TypeError) as exc:
            st.session_state.messages.append(("assistant", f"**Error:** {exc}"))
        finally:
            st.session_state.pop(_PENDING_QUERY_INPUT, None)
        st.rerun()

    prompt = st.chat_input("Ask about the DVD Rental database")

    if prompt:
        st.session_state.messages.append(("user", prompt))
        st.session_state[_PENDING_QUERY_INPUT] = prompt
        st.rerun()


if __name__ == "__main__":
    asyncio.run(main())
