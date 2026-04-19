# Spec 11 — Pydantic GraphState + Iterative Refinement via Conversation History

**Sources of truth:** [TASK.md](../TASK.md), [AGENTS.md](../AGENTS.md). Builds on all prior specs (01–10). This spec introduces two tightly coupled changes that ship together:

1. **Refactor `GraphState`** from a flat `TypedDict` to nested Pydantic `BaseModel`s so field ownership by pipeline concern is explicit and validated.
2. **Add iterative refinement support** via a bounded `conversation_history` list, so the Query Agent can resolve pronouns and reuse entities/filters from prior turns in the same session.

**Prior specs are preserved as historical record** per [AGENTS.md](../AGENTS.md). This spec supersedes no prior spec — it replaces only the `GraphState` shape (previously defined incrementally across Specs 03–07) with a single authoritative definition in `src/graph/state.py`.

---

## 1. Purpose

### 1.1 Problem

The Query Agent has no conversational memory between turns. Every invocation is treated as an isolated question:

- `previous_user_input`, `previous_sql`, `assumptions`, and `recent_filters` are captured in `GraphState` by `memory_update_session` (Spec 07) and survive the checkpointer, but **none of them are ever fed into any LLM prompt**.
- Each LLM call (`build_query_plan`, `build_sql`, `build_query_critique`) constructs a fresh two-message list with no prior turn context.
- Follow-up questions that use pronouns ("his movies", "those same actors", "now filter by rental date") silently fail to resolve — the model has no referent.

### 1.2 Goals

- A user who asks _"Which actors worked with Nick Wahlberg?"_ followed by _"Now show me his movies"_ receives a SQL query that correctly filters by the actor identified in the previous turn, without re-stating the name.
- The `GraphState` type hierarchy reflects the three distinct concerns of the system: **schema pipeline**, **query pipeline**, and **memory/session**, reducing the cognitive overhead of working in any individual node.

### 1.3 Non-goals

- Vector / semantic search over history (separate spec).
- History spanning multiple `thread_id` values (each thread keeps its own `conversation_history`).
- Persisting `conversation_history` to the `app_memory` Postgres DB. The `MemorySaver` checkpointer (thread-scoped, in-process) is sufficient for the session refinement use case.

---

## 2. Scope

| In scope                                                                                                  | Out of scope                                         |
| --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Replace `GraphState TypedDict` with `GraphState(BaseModel)`                                               | Changing the LangGraph graph topology                |
| Three nested sub-`BaseModel`s: `schema`, `query`, `memory`                                                | Changing LLM providers or agent logic beyond prompts |
| `append_steps` reducer + `merge_submodel` reducer                                                         | Adding vector/semantic memory                        |
| `ConversationTurn` model, capped at 5 entries                                                             | Persisting history to `app_memory` DB                |
| Remove legacy single-turn fields (`previous_user_input`, `previous_sql`, `assumptions`, `recent_filters`) | Changing the HITL flow for schema approval           |
| Inject history into plan / SQL / critic prompts                                                           | Changing MCP tool contracts                          |
| Extend system prompt for pronoun / anaphora resolution                                                    | Rewriting prior specs                                |
| Migrate all nodes, memory helpers, UI, and CLI to the new shape                                           | —                                                    |
| New tests: reducers, session snapshot, agent history injection, two-turn integration                      | —                                                    |

---

## 3. Target `GraphState` shape

### 3.1 Top-level model

```python
class GraphState(BaseModel):
    user_input: str = ""
    steps: Annotated[list[str], append_steps]   # reducer: extends on each node update
    gate_decision: str | None = None
    user_id: str = "default"
    session_id: str | None = None
    last_result: str | dict | None = None       # shared output channel (UI/CLI)
    last_error: str | None = None               # shared error channel  (UI/CLI)

    schema: Annotated[SchemaPipelineState, merge_submodel] = Field(default_factory=SchemaPipelineState)
    query:  Annotated[QueryPipelineState,  merge_submodel] = Field(default_factory=QueryPipelineState)
    memory: Annotated[MemoryState,         merge_submodel] = Field(default_factory=MemoryState)
```

### 3.2 Sub-models

```python
class SchemaPipelineState(BaseModel):
    ready: bool = False             # formerly schema_ready
    metadata: dict | None = None    # formerly schema_metadata
    draft: dict | None = None       # formerly schema_draft
    approved: dict | None = None    # formerly schema_approved
    hitl_prompt: dict | None = None
    persist_error: str | None = None


class QueryPipelineState(BaseModel):
    docs_context: dict | None = None    # formerly schema_docs_context
    docs_warning: str | None = None     # formerly schema_docs_warning
    plan: dict | None = None            # formerly query_plan
    generated_sql: str | None = None
    critic_status: str | None = None
    critic_feedback: str | None = None
    refinement_count: int = 0
    execution_result: dict | None = None    # formerly query_execution_result
    explanation: str | None = None          # formerly query_explanation


class ConversationTurn(BaseModel):
    user_input: str
    sql: str | None = None
    row_count: int | None = None
    rows_preview: list[dict] = Field(default_factory=list)   # up to 3 rows, values trimmed
    explanation: str | None = None


class MemoryState(BaseModel):
    preferences: dict | None = None
    preferences_dirty: bool = False
    conversation_history: list[ConversationTurn] = Field(default_factory=list)   # capped at 5
    warning: str | None = None    # formerly memory_warning
```

### 3.3 Legacy fields removed

The following flat `GraphState` fields from Specs 03–07 are **deleted**:

| Removed field            | Replaced by                                               |
| ------------------------ | --------------------------------------------------------- |
| `schema_ready`           | `schema.ready`                                            |
| `schema_metadata`        | `schema.metadata`                                         |
| `schema_draft`           | `schema.draft`                                            |
| `schema_approved`        | `schema.approved`                                         |
| `hitl_prompt`            | `schema.hitl_prompt`                                      |
| `persist_error`          | `schema.persist_error`                                    |
| `schema_docs_context`    | `query.docs_context`                                      |
| `schema_docs_warning`    | `query.docs_warning`                                      |
| `query_plan`             | `query.plan`                                              |
| `generated_sql`          | `query.generated_sql`                                     |
| `critic_status`          | `query.critic_status`                                     |
| `critic_feedback`        | `query.critic_feedback`                                   |
| `refinement_count`       | `query.refinement_count`                                  |
| `query_execution_result` | `query.execution_result`                                  |
| `query_explanation`      | `query.explanation`                                       |
| `preferences`            | `memory.preferences`                                      |
| `preferences_dirty`      | `memory.preferences_dirty`                                |
| `memory_warning`         | `memory.warning`                                          |
| `previous_user_input`    | derived from `memory.conversation_history[-1].user_input` |
| `previous_sql`           | derived from `memory.conversation_history[-1].sql`        |
| `assumptions`            | removed (no consumer; was never written to)               |
| `recent_filters`         | removed (no consumer; was never written to)               |

---

## 4. Reducers

Two custom LangGraph reducers are defined in `src/graph/state.py` and used via `Annotated`:

### 4.1 `append_steps`

```python
def append_steps(current: list[str], update: list[str]) -> list[str]:
    return current + (update or [])
```

- Nodes return `{"steps": ["query_plan"]}` (only the new step names) instead of cloning the entire list.
- Eliminates the `steps = list(state.get("steps", [])); steps.append(...)` boilerplate from every node.

### 4.2 `merge_submodel`

```python
def merge_submodel(current: BaseModel, update: BaseModel | dict | None) -> BaseModel:
    if update is None:
        return current
    if isinstance(update, dict):
        return current.model_copy(update=update)
    return current.model_copy(update=update.model_dump(exclude_unset=True))
```

- Nodes return partial dicts: `{"query": {"generated_sql": sql}}`.
- Only the explicitly provided keys are overwritten; all other sub-fields retain their current values.
- This means `query_load_context` can reset `{"query": {"refinement_count": 0, "plan": None, ...}}` without affecting `query.docs_context` if it doesn't include that key.

---

## 5. Node write / read conventions

### 5.1 Reading state

Nodes receive a `GraphState` instance and use attribute access:

```python
# Before (Spec 03–07 style)
ctx = state.get("schema_docs_context")
plan = state.get("query_plan")

# After (Spec 11)
ctx = state.query.docs_context
plan = state.query.plan
```

### 5.2 Writing updates

Nodes return a plain `dict` that LangGraph merges via the reducers:

```python
# Before (Spec 03–07 style)
steps = list(state.get("steps", []))
steps.append("query_generate_sql")
return {"steps": steps, "generated_sql": sql}

# After (Spec 11)
return {"steps": ["query_generate_sql"], "query": {"generated_sql": sql}}
```

The `append_steps` reducer extends the list; `merge_submodel` deep-merges the dict into `QueryPipelineState`.

---

## 6. Conversation history

### 6.1 Accumulation (`memory_update_session`)

At the end of every successful query turn, `memory_update_session` builds a `ConversationTurn` and appends it to `state.memory.conversation_history`:

```
ConversationTurn(
    user_input  = state.user_input,
    sql         = state.query.generated_sql,
    row_count   = state.query.execution_result.get("row_count"),
    rows_preview = first_three_rows_trimmed(state.query.execution_result),
    explanation = state.query.explanation,
)
```

- `first_three_rows_trimmed`: takes the first 3 rows from `execution_result["rows"]` (if present); each string value is truncated to 200 characters.
- History is capped at **5 entries** (FIFO: when 6 entries would exist, the oldest is dropped).

Schema-path turns (where `state.query.generated_sql` is `None`) do **not** append a turn.

### 6.2 Propagation (`memory_load_user` / `seed_session_fields`)

`seed_session_fields` passes `conversation_history` through from the checkpointed state at the start of each turn, so `memory_load_user` does not inadvertently clear it:

```python
def seed_session_fields(state: GraphState) -> dict:
    return {"memory": {"conversation_history": state.memory.conversation_history}}
```

### 6.3 Injection into prompts

`query_plan.py`, `query_generate_sql.py`, and `query_critic.py` extract `state.memory.conversation_history` and forward it to the corresponding agent builder.

`build_query_plan`, `build_sql`, and `build_query_critique` each accept a new keyword argument:

```python
conversation_history: list[dict] | None = None
```

When `conversation_history` is non-empty, the human message is extended with:

```
Conversation history (JSON, oldest-first):
[...]
```

serialised via `_compact_json` (same truncation mechanism already used for schema docs).

### 6.4 System prompt additions

`QUERY_SYSTEM_MESSAGE` in `src/agents/prompts/query.py` is extended with a section:

> **Conversation context:**
> When a `Conversation history` block is provided it contains the last few turns of this session, oldest first. Each entry includes the user's question, the SQL that was executed, a sample of rows returned, and a natural-language explanation.
>
> Use this context to:
>
> - Resolve pronouns and vague references in the current question ("his movies", "those actors", "the same genre", "now filter by ...").
> - Reuse entities, joins, and filters from prior SQL when they remain applicable to the follow-up.
> - Recognise when the current question is clearly unrelated to prior turns and ignore history in that case.
>
> Do not invent facts beyond what is present in the schema docs, history, and current question.

`QUERY_PLAN_INSTRUCTIONS` and `QUERY_SQL_INSTRUCTIONS` each gain a one-line reminder:

> "If a Conversation history block is present, resolve any anaphoric references before planning."

---

## 7. Repository layout changes

```text
src/
  graph/
    state.py           # REWRITE: GraphState, sub-models, reducers, ConversationTurn
    graph.py           # minor: route_after_persist → state.schema.persist_error
    memory_nodes.py    # REWRITE: sub-model attribute access; seed/snapshot updated
    nodes/
      query_nodes/
        query_load_context.py    # partial reset via {"query": {...}}
        query_plan.py            # forward conversation_history
        query_generate_sql.py    # forward conversation_history
        query_critic.py          # forward conversation_history; read state.query.*
        query_execute.py         # read state.query.generated_sql
        query_explain.py         # read/write state.query.*
        query_refine_cap.py      # read state.query.refinement_count
      schema_nodes/
        schema_inspect.py        # write {"schema": {...}}
        schema_draft.py          # read state.schema.metadata; write {"schema": {...}}
        schema_hitl.py           # read state.schema.draft; write {"schema": {...}}
        schema_persist.py        # read state.schema.*; write {"schema": {...}}
  memory/
    session.py         # REWRITE: seed_session_fields, snapshot_session_fields,
                       #          ConversationTurn building, _trim_rows helper
  agents/
    query_agent.py     # add conversation_history kwarg to build_query_plan,
                       #   build_sql, build_query_critique
    prompts/
      query.py         # extend QUERY_SYSTEM_MESSAGE; add reminder lines to
                       #   QUERY_PLAN_INSTRUCTIONS and QUERY_SQL_INSTRUCTIONS
  ui/
    app.py             # migrate state field reads to sub-model attributes
    formatters.py      # last_result / last_error remain top-level; no change needed
```

**No new packages are required.** `pydantic` is already a direct dependency (used by `pydantic-settings` and the existing structured-output models).

---

## 8. Migration guide (node-by-node)

Every node currently follows one of these three patterns. The "After" forms are what every node must look like after Spec 11.

### Pattern A — read one sub-model field, write one sub-model field

```python
# Before
async def query_plan(state: GraphState) -> dict[str, Any]:
    steps = list(state.get("steps", []))
    steps.append("query_plan")
    ctx = state.get("schema_docs_context")
    prefs = state.get("preferences")
    plan = await build_query_plan(state.get("user_input", ""), ...)
    return {"steps": steps, "query_plan": plan}

# After
async def query_plan(state: GraphState) -> dict[str, Any]:
    history = state.memory.conversation_history or []
    plan = await build_query_plan(
        state.user_input,
        schema_docs_context=state.query.docs_context,
        preferences=state.memory.preferences,
        conversation_history=history if history else None,
    )
    return {"steps": ["query_plan"], "query": {"plan": plan}}
```

### Pattern B — reset multiple fields on a sub-model (e.g. `query_load_context`)

```python
# Before
return {
    "steps": steps,
    "refinement_count": 0,
    "critic_status": None,
    "generated_sql": None,
    "query_plan": None,
    ...
}

# After
return {
    "steps": ["query_load_context"],
    "query": {
        "refinement_count": 0,
        "critic_status": None,
        "critic_feedback": None,
        "generated_sql": None,
        "plan": None,
        "execution_result": None,
        "explanation": None,
    },
    "last_error": None,
    "last_result": None,
}
```

### Pattern C — conditional routing reads a sub-model field

```python
# Before
def route_after_persist(state: GraphState) -> str:
    if state.get("persist_error"):
        ...

# After
def route_after_persist(state: GraphState) -> str:
    if state.schema.persist_error:
        ...
```

---

## 9. Behavioral contracts

### 9.1 History does not break isolated questions

If a user starts a new turn with a self-contained question (no pronouns, no references to prior results), the LLM must answer it correctly regardless of what is in `conversation_history`. The prompt instructs the model to ignore history when the question is clearly unrelated.

### 9.2 History cap is enforced at snapshot time

`snapshot_session_fields` **never** stores more than 5 `ConversationTurn` entries. If the current history length is already 5 before the new turn is appended, the oldest entry is discarded.

### 9.3 Schema turns do not pollute history

A turn that ends via the schema path (schema inspect → draft → HITL → persist) does not add a `ConversationTurn` because no SQL is executed. `memory_update_session` checks `state.query.generated_sql is not None` before building a turn.

### 9.4 Error turns do not pollute history

If a query turn terminates with `last_error` set (e.g. refinement cap hit), `memory_update_session` does not append a turn. `state.query.generated_sql` will be empty or `None` in that case, satisfying the same guard in §9.3.

### 9.5 `last_result` / `last_error` remain top-level

Both fields are used by the UI (`src/ui/formatters.py`) and CLI (`main.py`) as the single shared output/error channel. They remain flat on `GraphState` so formatters require no pipeline-awareness.

### 9.6 Reducer idempotency

Calling `merge_submodel(current, {})` or `merge_submodel(current, None)` returns the current sub-model unchanged. Calling `append_steps(current, [])` returns `current` unchanged.

---

## 10. Tests

### New test files

| File                                       | What it covers                                                                                                                                                                |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/graph/test_state_reducers.py`       | `merge_submodel` with dict/BaseModel/None updates; `append_steps`; default construction; unset-field preservation                                                             |
| `tests/memory/test_session.py`             | `snapshot_session_fields` appends a `ConversationTurn`; caps at 5 (6th drops oldest); `_trim_rows` keeps ≤3 rows and truncates long strings; schema/error turns do not append |
| `tests/agents/test_query_agent_history.py` | `build_query_plan`, `build_sql`, `build_query_critique` include the history block in the human message when provided; omit it when `None` or `[]`                             |

### Updated test files

| File                                                 | Why it changes                                                                                                                                                                            |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Any test constructing `GraphState` directly          | Must use the new `BaseModel` with nested sub-models                                                                                                                                       |
| Any test asserting on removed flat fields            | Must assert on the corresponding sub-model field                                                                                                                                          |
| `tests/graph/test_query_pipeline.py` (or equivalent) | Add a two-turn integration test: two turns with shared `thread_id`; second turn's `state.memory.conversation_history` has length 2; the second-turn SQL references the entity from turn 1 |

### Minimum acceptance criteria

- [ ] `uv run ruff check .` passes with zero errors.
- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run pytest` passes — no regressions on existing tests, new tests all green.
- [ ] Manual smoke (requires `docker compose up -d`):
  - Turn 1: _"Which actors worked with Nick Wahlberg?"_ → returns SQL + rows.
  - Turn 2: _"Now show me his movies."_ → returns SQL that filters by `Nick Wahlberg` without re-stating the name explicitly.
- [ ] `state.memory.conversation_history` after turn 2 has exactly 2 entries.

---

## 11. Configuration

No new environment variables are introduced. History depth (5) and row preview size (3 rows, 200-char value truncation) are module-level constants in `src/memory/session.py`:

```python
HISTORY_MAX_TURNS: int = 5
HISTORY_ROWS_PREVIEW: int = 3
HISTORY_ROW_VALUE_MAX_CHARS: int = 200
```

---

## 12. Dependencies

No new packages required.

- `pydantic` — already a direct runtime dependency.
- `langgraph` — `Annotated` reducer support via `langgraph.graph` is already in use (the `MessagesState` pattern; this spec adopts the same mechanism for custom reducers).

---

## 13. Commit sequence

Per [AGENTS.md](../AGENTS.md) (GitHub Flow, Conventional Commits, always-functional increments):

| #   | Commit message                                                                      | What lands                                                                                                                                                  |
| --- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `refactor(state): introduce pydantic GraphState with nested submodels and reducers` | `src/graph/state.py` full rewrite; `tests/graph/test_state_reducers.py`                                                                                     |
| 2   | `refactor(nodes): migrate schema pipeline nodes to pydantic state`                  | All `schema_nodes/`, `graph.py` routing, `graph/__init__.py`                                                                                                |
| 3   | `refactor(nodes): migrate query pipeline nodes to pydantic state`                   | All `query_nodes/`, `memory_nodes.py`, `memory/session.py`                                                                                                  |
| 4   | `refactor(memory): replace previous_* fields with MemoryState and ConversationTurn` | `memory/session.py` snapshot logic, `memory_nodes.py`, `tests/memory/test_session.py`                                                                       |
| 5   | `feat(query-agent): inject conversation history into plan/sql/critic prompts`       | `agents/query_agent.py`, `agents/prompts/query.py`, `query_plan.py`, `query_generate_sql.py`, `query_critic.py`, `tests/agents/test_query_agent_history.py` |
| 6   | `feat(ui): migrate app and formatters to pydantic state`                            | `ui/app.py`, `ui/formatters.py`, `main.py`                                                                                                                  |
| 7   | `test: add two-turn integration test for iterative refinement`                      | `tests/graph/test_query_pipeline.py` updated                                                                                                                |

Each commit leaves the test suite green and the application runnable.

---

## 14. Checklist

Use this as the implementation acceptance gate:

- [ ] `GraphState` is a `BaseModel` (not `TypedDict`) and `StateGraph(GraphState)` compiles without errors.
- [ ] `SchemaPipelineState`, `QueryPipelineState`, `MemoryState`, `ConversationTurn` are defined in `src/graph/state.py`.
- [ ] `append_steps` and `merge_submodel` reducers are defined and unit-tested.
- [ ] All 22 legacy flat fields listed in §3.3 are removed; no node references them.
- [ ] All nodes use `state.<group>.<field>` for reads and `{"<group>": {...}}` partial dicts for writes.
- [ ] `conversation_history` is accumulated (up to 5 turns) in `memory_update_session`.
- [ ] `conversation_history` survives across turns via the `MemorySaver` checkpointer.
- [ ] `build_query_plan`, `build_sql`, `build_query_critique` accept and use `conversation_history`.
- [ ] `QUERY_SYSTEM_MESSAGE` contains the conversation context / anaphora-resolution section.
- [ ] Schema-path turns and error turns do not append to `conversation_history`.
- [ ] All tests pass (`uv run pytest`).
- [ ] Ruff passes (`uv run ruff check . && uv run ruff format --check .`).
- [ ] Two-turn smoke test succeeds (§10 minimum acceptance criteria).
