# Spec 09 — Observability (LangSmith)

**Sources of truth:** [TASK.md](../TASK.md), [AGENTS.md](../AGENTS.md). Build on [specs/01-bootstrap.md](01-bootstrap.md), [specs/02-tools-mcp.md](02-tools-mcp.md), [specs/03-graph-shell.md](03-graph-shell.md), [specs/04-schema-gate.md](04-schema-gate.md), [specs/05-schema-agent-hitl.md](05-schema-agent-hitl.md), [specs/06-query-agent-critic.md](06-query-agent-critic.md), [specs/07-memory.md](07-memory.md), and [specs/08-litellm.md](08-litellm.md).

**Scope of this document:** Define **LangSmith** as the **only** first-class observability platform for traces of graph execution, LangChain/LiteLLM calls, MCP tool usage, critic retry loops, and human-in-the-loop (HITL) interrupts. Structured **stderr logging** remains allowed for local development and CI; it does **not** replace LangSmith for assignment-visible observability. Implementation lands in a follow-on coding PR; this file is the **behavioral contract**.

**Greenfield:** Nothing is deployed yet. **Do not** preserve alternate tracing backends, feature flags for "legacy tracing," or compatibility shims for undeployed observability code paths. Standardize on **`LANGSMITH_*`** environment variables for enabling traces (see [§5.2](#52-environment-variable-table)); legacy **`LANGCHAIN_*`** names are **optional** documentation notes for operators who already export them—implementation may read only **`LANGSMITH_*`**.

---

## 1. Purpose

Satisfy [TASK.md](../TASK.md):

1. **MCP tools:** "All tool calls must be traceable in logs **and integrated into the graph execution**" — traces must show **tool runs** nested under the graph run when tools are invoked from LangGraph nodes.
2. **Non-functional observability:** "Basic observability/logging" for:
   - graph node transitions,
   - tool calls,
   - retry/fallback behavior,
   - human-in-the-loop interactions.

**Functional outcomes:**

- With **`LANGSMITH_TRACING=true`** and a valid **`LANGSMITH_API_KEY`**, a single **`ainvoke`/`invoke`** on the compiled graph produces a **root trace** in LangSmith with **child runs** for nodes, LLM calls, and tool calls where applicable.
- **`RunnableConfig`** passed from the CLI entrypoint includes **`tags`** and **`metadata`** (e.g. `thread_id`, `user_id`, `session_id`) so traces are filterable and attributable.
- **HITL** (`interrupt` on schema review + **`Command(resume=...)`** in [`main.py`](../main.py)) appears as a **paused** run and a **continued** trace after resume.
- **Critic retry loop** (route **`retry`** between **`query_generate_sql`** and **`query_critic`**) is visible as repeated node transitions under the same parent trace.
- **`.env.example`** documents LangSmith variables.

---

## 2. TASK.md expectations → LangSmith (conceptual mapping)

| TASK expectation       | LangSmith manifestation                                                                                                                                                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Graph node transitions | LangGraph/LangChain **run tree**: one root run per top-level **`invoke`/`ainvoke`**, nested **node** spans (e.g. `memory_load_user`, `query_critic`).                                                                                                   |
| MCP / tool calls       | **Tool**-typed child runs under the node that invoked the tool (e.g. **`execute_readonly_sql`**, **`inspect_schema`**).                                                                                                                                 |
| Retry / fallback       | **LiteLLM** **`max_retries`** ([specs/08-litellm.md](08-litellm.md)) surfaces as retried or failed LLM child spans; **critic** path **`retry`** shows multiple transitions **`query_generate_sql` ↔ `query_critic`** before **`execute`** or **`cap`**. |
| Human-in-the-loop      | **Interrupt** payload on **`schema_hitl`** pauses the run; **`Command(resume=...)`** continues the same logical thread—visible as interrupt + resume in LangSmith.                                                                                      |

---

## 3. Scope

| In scope                                                                                                                                | Out of scope                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **`LangSmithSettings`** (`pydantic-settings`) under **`src/config/`**                                                                   | Prometheus, Grafana, Datadog, or generic OpenTelemetry pipelines as _primary_ observability   |
| **`uv add langsmith`** — direct runtime dependency ([AGENTS.md](../AGENTS.md))                                                          | Changing SQL safety, MCP contracts, or critic rules (Specs 02, 06)                            |
| Extend **`graph_run_config`** in [`src/graph/graph.py`](../src/graph/graph.py) and/or merge config at **`main.py`** **`ainvoke`** sites | Streamlit / UI (Spec 10)                                                                      |
| **`tags` + `metadata`** on **`RunnableConfig`** for every graph invocation from the CLI                                                 | Multi-tenant LangSmith workspaces beyond documenting **`LANGSMITH_WORKSPACE_ID`** when needed |
| Document **`GRAPH_DEBUG`** (see [§8](#8-relationship-to-stderr-logging-and-graph_debug))                                                | Self-hosted LangSmith **deployment** (only document **`LANGSMITH_ENDPOINT`** override)        |

---

## 4. Target repository layout

```text
src/
  config/
    langsmith_settings.py   # NEW: LangSmithSettings
    __init__.py             # export LangSmithSettings
  graph/
    graph.py                # EXTEND: graph_run_config returns base RunnableConfig; optional helper merge_trace_config(...)
```

**Packaging:** No new top-level package. **`config`** is already a wheel package—add **`langsmith_settings`** module only.

---

## 5. Configuration

### 5.1 `LangSmithSettings`

Add **`src/config/langsmith_settings.py`** with fields aligned to official LangSmith / LangGraph OSS env names:

```python
# Illustrative — implement in config/langsmith_settings.py.
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LangSmithSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices("LANGSMITH_TRACING"),
        description="When true, export traces to LangSmith (requires API key for useful output).",
    )
    langsmith_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGSMITH_API_KEY"),
    )
    langsmith_project: str = Field(
        default="default",
        validation_alias=AliasChoices("LANGSMITH_PROJECT"),
    )
    langsmith_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGSMITH_ENDPOINT"),
        description="Override API URL (e.g. EU region or self-hosted).",
    )
```

**Normative behavior for the coding PR:**

1. **Primary approach:** Rely on **`.env`** being already loaded (via **`BaseSettings`** default **`env_file=".env"`**). **`LangSmithSettings`** validates that the fields are correctly read from environment. **Do not** re-export to **`os.environ`** at startup; let the **`.env`** file be the single source of truth. LangChain and LangGraph clients will pick up **`LANGSMITH_*`** vars from the same **`.env`** file when the process runs.

### 5.2 Environment variable table

| Variable                 | Required when tracing | Purpose                                                                         |
| ------------------------ | --------------------- | ------------------------------------------------------------------------------- |
| **`LANGSMITH_TRACING`**  | no                    | **`true`** / **`false`** — master switch (mirrored in **`LangSmithSettings`**). |
| **`LANGSMITH_API_KEY`**  | yes for cloud export  | API key for LangSmith.                                                          |
| **`LANGSMITH_PROJECT`**  | no                    | Project name in LangSmith UI (default **`default`**).                           |
| **`LANGSMITH_ENDPOINT`** | no                    | Non-default API base (EU, self-hosted).                                         |

**Legacy (informational only):** Some docs still mention **`LANGCHAIN_TRACING_V2`**, **`LANGCHAIN_API_KEY`**, **`LANGCHAIN_PROJECT`**. This repo **standardizes on `LANGSMITH_*`**; the implementation **must not** read legacy names. Do not add **`AliasChoices`** for legacy **`LANGCHAIN_*`** vars—this ensures no ambiguity and a clean migration path forward.

---

## 6. RunnableConfig: tags and metadata

LangGraph OSS observability: pass **`tags`** and **`metadata`** on the **`config`** dict alongside **`configurable`**.

### 6.1 Extend `graph_run_config`

Today [`graph.graph_run_config`](../src/graph/graph.py) returns **`(config, state_seed)`** with **`config = {"configurable": {"thread_id": tid}}`**. The implementation **must**:

1. Keep **`configurable.thread_id`** as the single source of truth for the checkpointer.
2. Add **`metadata`** including at least: **`user_id`**, **`session_id`**, **`thread_id`** (duplicate of configurable is acceptable for UI filtering).
3. Add **`tags`** including at least: **`dvdrental-agent`**, **`cli`** (or **`pytest`** when invoked from tests), and **`query_path` / `schema_path`** is optional at config time (path unknown until gate runs)—if needed, use a generic tag **`langgraph`** and rely on node names inside the trace.

**Illustrative merge shape:**

```python
from langchain_core.runnables import RunnableConfig


def build_traceable_config(
    *,
    base: RunnableConfig,
    user_id: str,
    session_id: str,
    thread_id: str,
    run_kind: str = "cli",
) -> RunnableConfig:
    meta = {
        "user_id": user_id,
        "session_id": session_id,
        "thread_id": thread_id,
    }
    tags = ["dvdrental-agent", run_kind]
    # RunnableConfig is a TypedDict-like dict: merge without dropping configurable.
    out: RunnableConfig = {**base, "metadata": meta, "tags": tags}
    return out
```

**Call site:** [`main.py`](../main.py) **CLI entrypoints** where **`app.ainvoke(...)`** is called—build **`cfg`** once per session, then pass **`build_traceable_config(base=cfg, ...)`** into every **`app.ainvoke(..., config=...)`** call, **including** **`Command(resume=...)`** invocations. **Critical:** pass the **same** base config to **`Command(resume=...)`** so the resumed run inherits the same **`metadata`** and **`tags`**, maintaining logical trace continuity in LangSmith.

---

## 7. MCP tool calls and graph integration

Nodes that call MCP tools via LangChain abstractions (**`StructuredTool.ainvoke`**, LangChain MCP adapters) **must** run **inside** the same LangGraph invocation so tool calls inherit the **parent run** context. **Do not** spawn fire-and-forget HTTP clients outside the runnable tree for the same logical step.

**Patterns to avoid:**

- Do **not** use **`asyncio.create_task(tool_call())`** to invoke MCP tools in the background; always `await` the tool invocation inside the graph node's coroutine.
- Do **not** delegate tool calls to a separate executor/worker without propagating the runnable context.

If a tool call ever runs in a **detached** executor without context propagation, that is a **spec violation**—fix by ensuring invocation occurs from the graph node coroutine with the same **`RunnableConfig`** context.

---

## 8. Relationship to stderr logging and `GRAPH_DEBUG`

Existing code uses **`logging`** with structured **`extra`** (e.g. **`graph_node_transition`**, **`graph_gate_decision`**) in [`src/graph/graph.py`](../src/graph/graph.py) and [`src/graph/query_pipeline.py`](../src/graph/query_pipeline.py).

| Mechanism                  | Role                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| **LangSmith**              | **Primary** evidence for assignment rubric: traces, filters, drill-down into LLM and tools. |
| **Structured stderr logs** | **Secondary**: local debugging, CI artifacts, quick grep. **Keep only high-value logs**.    |

**Logging cleanup (this PR):**

- **Remove** all **`graph_node_transition`** entries (enter/exit phases, step counts, previews)—LangSmith captures node transitions and timing automatically.
- **Remove** all **`graph_debug_snapshot`** entries—LangSmith provides full state inspection.
- **Remove** all **gate_decision** and low-value transitions logged for observability—LangSmith shows these via run tree.
- **Keep** only **errors, warnings, and validation failures** with actionable messages (e.g., "MCP tool not found", "SQL validation failed: ...").
- **Convert** structured log entries (with **`extra`** dicts) to simple **`logger.error(msg)`** or **`logger.exception(exc_msg)`** for clarity.

This cleanup reduces code size significantly (50%+ fewer log statements) and clarifies that **LangSmith, not stderr, is the observability source**.

---

## 9. `.env.example` and README

- **`.env.example`:** add commented rows for **`LANGSMITH_TRACING`**, **`LANGSMITH_API_KEY`**, **`LANGSMITH_PROJECT`**, optional **`LANGSMITH_ENDPOINT`**. Minimal example:

```bash
# LangSmith observability (optional, defaults to false)
# Set LANGSMITH_TRACING=true and provide LANGSMITH_API_KEY to export graph traces to LangSmith.
LANGSMITH_TRACING=false
# LANGSMITH_API_KEY=your_api_key_here
LANGSMITH_PROJECT=dvdrental-local
# LANGSMITH_ENDPOINT=  # Leave empty for cloud; set for self-hosted or EU region.
```

- **`README.md`:** add a short **"Observability (LangSmith)"** subsection (env vars, link to LangSmith project, one command to generate a trace). If you prefer minimal README churn in the same PR, Spec **11-deliverables** may expand this—minimum is **`.env.example` + spec**.

---

## 10. Dependencies

```bash
uv add langsmith
```

**Do not** hand-edit **`[project.dependencies]`** or **`uv.lock`** ([AGENTS.md](../AGENTS.md)).

---

## 11. Implementation checklist (coding PR)

1. **`uv add langsmith`** ([§10](#10-dependencies)).
2. **`src/config/langsmith_settings.py`:** **`LangSmithSettings`** ([§5.1](#51-langsmithsettings)); export from **`config/__init__.py`**.
3. **`main.py`:** apply env from settings at startup; build **traceable** **`RunnableConfig`** for all **`app.ainvoke`** calls ([§6](#6-runnableconfig-tags-and-metadata)).
4. **`src/graph/graph.py`:** refactor **`graph_run_config`** or add **`build_traceable_config`** next to it—single place for **`configurable`** + trace fields.
5. **`.env.example`:** LangSmith vars ([§9](#9-envexample-and-readme)).
6. **`README.md`:** brief LangSmith subsection ([§9](#9-envexample-and-readme)).
7. **`uv run ruff check .`** and **`uv run ruff format .`**.

---

## 12. Manual verification (demo / rubric)

1. Set **`LANGSMITH_TRACING=true`**, valid **`LANGSMITH_API_KEY`**, desired **`LANGSMITH_PROJECT`**.
2. Run **`uv run python main.py -q "…"`** (query path): confirm in LangSmith a trace with **query pipeline nodes** and at least one **tool** run for **`execute_readonly_sql`**.
3. Run schema path (empty **`schema_docs`** or forced schema route if you have a test harness): confirm **interrupt** and post-**`Command(resume=...)`** continuation appear.
4. Force a **critic retry** (e.g. invalid SQL that critic sends back for regeneration): confirm **multiple** **`query_generate_sql`** / **`query_critic`** spans or transitions.

---

## 13. Assignment alignment

[TASK.md](../TASK.md) requires tool calls to be traceable **and** graph-integrated, plus basic observability for transitions, tools, retries, and HITL. **LangSmith** fulfills the **traceability** and **integration** story when graphs and tools run under the same traced **`invoke`**. Stderr logs complement but do not replace LangSmith for grading demos.

**Minimum acceptance hooks:**

- One LangSmith project shows **at least one** end-to-end CLI run with **nodes + tool** visible.
- **HITL** schema flow shows **interrupt + resume** when exercised.
