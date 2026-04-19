# Spec 10 — Streamlit UI (chat + schema HITL)

**Sources of truth:** [TASK.md](../TASK.md), [AGENTS.md](../AGENTS.md). Build on [specs/01-bootstrap.md](01-bootstrap.md), [specs/02-tools-mcp.md](02-tools-mcp.md), [specs/03-graph-shell.md](03-graph-shell.md), [specs/04-schema-gate.md](04-schema-gate.md), [specs/05-schema-agent-hitl.md](05-schema-agent-hitl.md), [specs/06-query-agent-critic.md](06-query-agent-critic.md), [specs/07-memory.md](07-memory.md), [specs/08-litellm.md](08-litellm.md), and [specs/09-observability.md](09-observability.md).

**Scope of this document:** Define a **Streamlit** front end that invokes the **same compiled LangGraph** as the CLI ([`main.py`](../main.py)): shared checkpointing (`MemorySaver`), **`graph_run_config`** (thread, user, session, **`run_kind`** for LangSmith), **`ainvoke(..., version="v2")`**, and **`Command(resume=...)`** for schema human-in-the-loop (HITL). Implementation lands in a follow-on coding PR; this file is the **behavioral contract**.

**Greenfield:** Nothing is deployed yet. **Do not** preserve alternate UIs, feature flags for undeployed clients, or compatibility shims. After this, **only** the Streamlit app is the supported interactive experience; the CLI (`main.py`) is development/testing only.

---

## 1. Purpose

Deliver the **UI slice** for [TASK.md](../TASK.md) and course demos:

1. **Primary UX:** Natural-language questions against the **DVD Rental** database via the **query agent** path, with results shown as **SQL + sample rows + short explanation** when `last_result` is a structured query answer ([`GraphState`](../src/graph/state.py)).
2. **Schema path + HITL:** When the schema-presence gate routes to the schema pipeline, the UI must allow the user to **approve** or **edit** the draft and **resume** the graph—same payload rules as the CLI and [`tests/test_schema_hitl.py`](../tests/test_schema_hitl.py).
3. **Observability:** Every graph invocation from the UI uses **`run_kind="streamlit"`** so LangSmith traces are filterable alongside CLI runs ([specs/09-observability.md](09-observability.md)).
4. **Safety:** No new execution path; **read-only SQL** and MCP contracts remain unchanged ([AGENTS.md](../AGENTS.md)).

**Functional outcomes:**

- User runs **`streamlit run …`** (exact module path in [§3](#3-target-repository-layout)); with Postgres + MCP + optional LLM/LangSmith env as documented, they can complete **at least one** query turn and see a coherent answer or a clear error in the UI.
- If the graph **interrupts** with **`kind == "schema_review"`**, the user can complete the flow **without** the terminal—approve or paste JSON, then resume.
- **`thread_id`** (and thus LangGraph checkpoint identity) remains **stable** for the Streamlit session unless the user explicitly starts a “new conversation” (if exposed).

---

## 2. Scope

| In scope                                                                                                   | Out of scope                                                                                                            |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **`uv add streamlit`**; new package under **`src/ui/`** ([§3](#3-target-repository-layout))                | Separate **REST/HTTP** backend for the graph (optional future); this spec is **in-process** `get_compiled_graph()` only |
| **Direct** `await app.ainvoke(..., version="v2")` + HITL loop ([§5](#5-integration-contract-direct-graph)) | Authentication, multi-tenant isolation, hosted Streamlit Cloud specifics                                                |
| Chat-style UI: user message, assistant response, errors                                                    | Replacing or removing [`main.py`](../main.py) CLI (it remains for development/testing)                                  |
| Rendering **`last_result`** query payloads (SQL, columns, rows, explanation) and **`last_error`**          | Vector search, new memory backends                                                                                      |
| Optional **sidebar** “schema readiness” indicator ([§7.3](#73-optional-schema-status-read-only))           | Duplicating the **schema-presence gate** logic outside the graph                                                        |

---

## 3. Target repository layout

```text
src/
  ui/
    __init__.py
    app.py              # Streamlit entry: session state, chat, HITL UI
    formatters.py       # NEW (optional): format query_answer dict -> markdown / table text
```

**Packaging:** Add **`src/ui`** to `[tool.hatch.build.targets.wheel] packages` in [pyproject.toml](../pyproject.toml). Extend **`[tool.ruff.lint.isort] known-first-party`** with **`ui`** if needed.

**Run command (normative for the coding PR):**

```bash
uv run streamlit run src/ui/app.py
```

(Adjust only if the module path is renamed consistently in code and docs.)

---

## 4. Configuration

### 4.1 Environment variables

Reuse existing app memory / graph defaults; **no new required env vars** for a minimal UI beyond what the stack already uses.

| Variable                                                                          | Required           | Purpose                                                                                                                         |
| --------------------------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| **`DEFAULT_THREAD_ID`**                                                           | no                 | Default LangGraph **`thread_id`** when the UI does not override (from [`.env.example`](../.env.example) / `AppMemorySettings`). |
| **`DEFAULT_USER_ID`**                                                             | no                 | Default **`user_id`** seed in state ([`graph_run_config`](../src/graph/graph.py)).                                              |
| **`POSTGRES_*`**, **`MCP_*`**, **`APP_MEMORY_*`**, **`LLM_*`**, **`LANGSMITH_*`** | per existing specs | Same as CLI; UI does not bypass tools or DB.                                                                                    |

**Optional (UI-only, if implementer adds toggles):** e.g. **`STREAMLIT_SERVER_PORT`** — document only if the coding PR reads it; otherwise rely on Streamlit defaults.

### 4.2 Session identity (`thread_id`)

[`graph_run_config`](../src/graph/graph.py) puts **`thread_id`** in **`config["configurable"]`** for **`MemorySaver`**. The Streamlit app **must**:

1. Initialize **`st.session_state["thread_id"]`** once (e.g. to **`DEFAULT_THREAD_ID`** environment variable if set, otherwise a UUID string) and pass it to **`graph_run_config(thread_id=..., run_kind="streamlit")`**. Use `os.getenv("DEFAULT_THREAD_ID")` to check the environment first.
2. Keep that value **stable across reruns** for the same chat session so interrupts/resumes and checkpoints align.
3. If “New chat” is offered, generate a **new** `thread_id` and clear displayed messages only—**do not** silently rotate `thread_id` on every rerun.

---

## 5. Integration contract (direct graph)

Normative rules for the coding PR:

1. **Graph instance:** `app = get_compiled_graph()` (same as [`main.py`](../main.py)). Lazy-init once per process is acceptable; document if cached on `st.session_state`.
2. **Config and state seed:** `config, state_seed = graph_run_config(thread_id=st.session_state["thread_id"], run_kind="streamlit")`. Optionally pass explicit **`user_id`** / **`session_id`** when the UI adds controls; otherwise defaults apply.
3. **User turn:** Build input by spreading **`state_seed`** (which contains graph defaults) and explicitly setting **`user_input`** and **`steps`**; this ensures **`user_input`** is always the user's text and **`steps`** is reset for this turn:

   ```python
   initial = {"user_input": user_text, "steps": [], **state_seed}
   ```

   (**`user_input`** must come after **`state_seed`** in the dict literal or be set explicitly to guarantee it takes precedence.)

4. **Invocation:** `out = await app.ainvoke(initial, config=config, version="v2")` — **always** `version="v2"` for interrupt handling consistent with [`main.py`](../main.py).
5. **Interrupt loop:** Until there are no interrupts, unwrap the result ([§6](#6-illustrative-snippets-non-normative)), and for **`payload.get("kind") == "schema_review"`**, collect the resume value and call `await app.ainvoke(Command(resume=resume), config=config, version="v2")`. Use the **same** `config` dict for resume as for the initial invoke ([specs/09-observability.md](09-observability.md)).
6. **Output:** Read **`last_error`**, **`last_result`** from the final state dict and render; do not print API keys or **`POSTGRES_PASSWORD`** to the UI.

---

## 6. Illustrative snippets (non-normative)

These examples sketch the contract; production code may refactor helpers shared with [`main.py`](../main.py).

### 6.1 Unwrap `v2` graph output and loop on interrupts

```python
from typing import Any

from langgraph.types import Command


def unwrap_graph_v2(result: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    if isinstance(result, dict):
        return result, ()
    value = getattr(result, "value", None)
    interrupts = getattr(result, "interrupts", ()) or ()
    if not isinstance(value, dict):
        msg = f"unexpected graph result type: {type(result).__name__}"
        raise TypeError(msg)
    return value, interrupts


async def run_until_done(app, initial: dict, config: dict) -> dict[str, Any]:
    out = await app.ainvoke(initial, config=config, version="v2")
    while True:
        state, interrupts = unwrap_graph_v2(out)
        if not interrupts:
            return state
        intr = interrupts[0]
        payload = getattr(intr, "value", intr)
        if not isinstance(payload, dict):
            raise TypeError("unexpected interrupt payload")
        if payload.get("kind") != "schema_review":
            # Spec: surface unknown interrupt kinds as errors in UI.
            raise RuntimeError(f"unhandled interrupt: {payload!r}")
        resume = await collect_schema_resume_from_user(payload)  # UI-specific
        out = await app.ainvoke(Command(resume=resume), config=config, version="v2")
```

### 6.2 Session bootstrap (config + state seed)

```python
from graph import get_compiled_graph, graph_run_config

app = get_compiled_graph()
config, state_seed = graph_run_config(
    thread_id=st.session_state["thread_id"],
    run_kind="streamlit",
)
initial = {"user_input": prompt, "steps": [], **state_seed}
```

### 6.3 Streamlit chat skeleton (async)

Use **Streamlit’s async entrypoint** (`async def main()` + `asyncio.run(main())` at the bottom, or the project’s chosen supported pattern for the installed Streamlit version) so **`await app.ainvoke`** is legal. Avoid blocking the event loop with synchronous MCP/LLM work mixed into sync callbacks.

```python
import asyncio
import uuid

import streamlit as st

from graph import get_compiled_graph, graph_run_config


app = get_compiled_graph()


async def run_chat_turn(user_text: str, thread_id: str) -> dict:
    config, state_seed = graph_run_config(
        thread_id=thread_id,
        run_kind="streamlit",
    )
    initial = {"user_input": user_text, "steps": [], **state_seed}
    return await run_until_done(app, initial, config)


async def main() -> None:
    st.title("DVD Rental agents")
    if "thread_id" not in st.session_state:
        st.session_state["thread_id"] = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    for role, content in st.session_state["messages"]:
        with st.chat_message(role):
            st.markdown(content)
    if prompt := st.chat_input("Ask about the DVD Rental database"):
        st.session_state["messages"].append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)
        state = await run_chat_turn(prompt, st.session_state["thread_id"])
        # Format state["last_result"] / state["last_error"] into assistant text
        st.session_state["messages"].append(("assistant", format_turn(state)))


if __name__ == "__main__":
    asyncio.run(main())
```

### 6.4 Schema HITL panel (resume payload)

Align resume values with [`tests/test_schema_hitl.py`](../tests/test_schema_hitl.py) and the CLI: **`approve`** maps to the draft’s **`tables`**, or the user supplies a JSON object **`{"tables": [...]}`**.

```python
import json

import streamlit as st


async def collect_schema_resume_from_user(payload: dict) -> dict:
    draft = payload.get("draft")
    with st.expander("Schema review (approval required)", expanded=True):
        st.json(payload)
        mode = st.radio("Decision", ["approve", "edit JSON"], horizontal=True)
        if mode == "approve":
            tables = (draft or {}).get("tables") if isinstance(draft, dict) else None
            if isinstance(tables, list):
                return {"tables": tables}
            return {"tables": []}
        raw = st.text_area("Edited tables JSON", value=json.dumps({"tables": []}))
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or "tables" not in result:
                st.error('JSON must contain a "tables" key.')
                return None
            return result
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
            return None
```

**Note:** The example above now includes JSON validation with error messaging as required.

---

## 7. UX requirements

### 7.1 Query path

- Show the user’s question and the assistant’s answer in the chat.
- For structured query answers, display at least: **SQL**, a **tabular or readable** representation of **rows**, and **explanation** when present. Limit wide tables visually (e.g. cap rows or horizontal scroll) without changing query semantics.

### 7.2 Errors

- Surface **`last_error`** as user-visible error text; optionally map known messages to short hints (e.g. MCP unreachable).

### 7.3 Optional schema status (read-only)

One of:

- Call the existing **schema presence** abstraction used by the graph ([`SchemaPresence`](../src/graph/presence.py)) in the sidebar **read-only** to show **ready / not ready**, or
- After a turn, show **`schema_ready`** / gate-related fields from state **if** set.

**Do not** implement a second gate in the UI—routing stays inside LangGraph.

---

## 8. Relationship to observability

- **`run_kind="streamlit"`** must appear in trace metadata/tags via [`build_traceable_config`](../src/graph/graph.py) so LangSmith distinguishes UI from CLI ([specs/09-observability.md](09-observability.md)).
- Do **not** fork MCP or tool calls outside the graph for the same logical step.

---

## 9. Dependencies

```bash
uv add streamlit
```

**Do not** hand-edit **`[project.dependencies]`** or **`uv.lock`** ([AGENTS.md](../AGENTS.md)).

---

## 10. Testing strategy (coding PR)

| Level           | Requirement                                                                                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Unit**        | Test **pure** helpers (e.g. `unwrap_graph_v2`, formatting `last_result` → markdown) without Streamlit when extracted to **`formatters.py`** or **`graph`** helpers. |
| **Integration** | Existing graph tests remain the source of truth for HITL payloads; UI PR should not weaken them.                                                                    |
| **UI**          | Optional: smoke test that **`src/ui/app.py`** imports (with env/mocks) or a minimal async call with a stub graph—keep CI fast.                                      |

---

## 11. Acceptance criteria

1. **`uv run streamlit run src/ui/app.py`** (or documented path) starts without import errors when dependencies and env are satisfied.
2. A user can submit a **natural-language** prompt and see **`last_result`** or **`last_error`** rendered without crashing the app.
3. When the graph interrupts with **`schema_review`**, the user can **approve** or supply **JSON** and the run **completes** (persist or error surfaced), using the **same** `config` for **`Command(resume=...)`** as for the initial invoke.
4. **`graph_run_config(..., run_kind="streamlit")`** is used for all **`ainvoke`** calls from the UI.
5. **`thread_id`** is stable for the session per [§4.2](#42-session-identity-thread_id).
6. **Ruff** passes: **`uv run ruff check .`** and **`uv run ruff format .`**.

---

## 12. Implementation checklist (coding PR)

1. **`uv add streamlit`** ([§9](#9-dependencies)).
2. Add **`src/ui/`** package and Streamlit **`app.py`** per [§3](#3-target-repository-layout); wire **`get_compiled_graph`**, **`graph_run_config`**, HITL loop ([§5](#5-integration-contract-direct-graph), [§6](#6-illustrative-snippets-non-normative)).
3. Update **[pyproject.toml](../pyproject.toml)** wheel packages + Ruff first-party for **`ui`**.
4. Optionally refactor shared **`unwrap_graph_v2` / interrupt loop** from [`main.py`](../main.py) into **`src/graph/`** or **`src/utils/`** to avoid duplication—**single behavior**, two entrypoints.
5. **`.env.example`:** only add lines if new optional UI vars are introduced; otherwise no change.
6. **`README.md`:** add a short **Streamlit** subsection with the run command (or defer sentence to **spec 11** if README is intentionally unchanged in this PR—minimum is this spec + run command somewhere discoverable).

---

## 13. Prompt for the coding agent

Implement **Spec 10** exactly: add Streamlit via **`uv add streamlit`**, create **`src/ui/app.py`** that calls **`get_compiled_graph()`** and **`graph_run_config(..., run_kind="streamlit")`**, runs **`await app.ainvoke(..., version="v2")`** in a loop that handles **`schema_review`** interrupts with **`Command(resume=...)`** using the **same** config, renders **`last_result` / `last_error`**, keeps **`thread_id`** in **`st.session_state`**, packages **`ui`** in the wheel, runs Ruff, and adds small unit tests for any extracted pure formatters.

---

## 14. Assignment alignment

[TASK.md](../TASK.md) expects a usable path from natural language to **safe** SQL and **traceable** tool use. This spec adds a **Streamlit** client that **does not** bypass the graph, MCP, or critic—so rubric observability and safety properties remain intact when demos use the UI instead of the CLI.
