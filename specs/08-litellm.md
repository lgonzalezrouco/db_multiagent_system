# Spec 08 — LiteLLM proxy inference + Pydantic structured output

**Sources of truth:** [TASK.md](../TASK.md), [AGENTS.md](../AGENTS.md). Build on [specs/01-bootstrap.md](01-bootstrap.md), [specs/02-tools-mcp.md](02-tools-mcp.md), [specs/03-graph-shell.md](03-graph-shell.md), [specs/04-schema-gate.md](04-schema-gate.md), [specs/05-schema-agent-hitl.md](05-schema-agent-hitl.md), [specs/06-query-agent-critic.md](06-query-agent-critic.md), and [specs/07-memory.md](07-memory.md).

**Scope of this document:** Define how the application obtains **natural-language inference** through a **LiteLLM-compatible HTTP gateway** (e.g. a university-hosted proxy) using **`langchain-litellm`** (`ChatLiteLLM`), and how **Pydantic** models drive **`with_structured_output`** for the **Query Agent** and **Schema Agent** drafts. Implementation lands in a follow-on coding PR; this file is the **behavioral and layout contract**.

**Greenfield:** There is **no** requirement to preserve deterministic stub code paths once this spec is implemented. **Remove** stub-only implementations in [`agents/query_agent.py`](../src/agents/query_agent.py) and [`agents/schema_agent.py`](../src/agents/schema_agent.py); tests use **mocks / fake chat models**, not parallel “offline modes” in production modules.

---

## 1. Purpose

Deliver the **LLM inference** slice for [TASK.md](../TASK.md):

1. **Single chat abstraction:** Instantiate **`ChatLiteLLM`** from **`langchain_litellm`** so **`LLM_MODEL`** can target **different backends** routed by the same LiteLLM proxy **without** adding extra LangChain provider packages per vendor.
2. **Typed outputs:** Use **Pydantic `BaseModel`** schemas with LangChain **`with_structured_output(...)`** so plans, SQL candidates, and schema drafts are **validated objects**, not ad-hoc JSON parsing.
3. **Safety alignment:** Prompts and output schemas **must** reinforce read-only querying, **`LIMIT`**, and DVD Rental grounding; execution remains behind the existing critic + MCP path ([AGENTS.md](../AGENTS.md), Spec 06).

**Functional outcomes:**

- **`LLMSettings`** (`pydantic-settings`) + **one factory** (e.g. `create_chat_llm()` → `ChatLiteLLM`).
- **Query path:** `build_query_plan` / `build_sql` become **async** (or delegate to async helpers) and return data derived from **structured LLM** calls; [`graph/query_pipeline.py`](../src/graph/query_pipeline.py) continues to populate **`query_plan`** (`dict`) and **`generated_sql`** (`str`).
- **Schema path:** `build_schema_draft` produces a **`schema_draft`**-compatible **`dict`** from a **structured** schema-draft model (converted with **`.model_dump(mode="json")`** or equivalent).
- **Proof:** Unit tests pass with injected fakes; optional integration test hits the real proxy when env is set.

---

## 2. Scope

| In scope | Out of scope (later / other specs) |
| --- | --- |
| **`uv add langchain-litellm`** ([AGENTS.md](../AGENTS.md)); single factory module under `src/llm/` | **`langchain-openai`** unless §15 escape hatch applies |
| **`LLMSettings`** in `src/config/llm_settings.py`; export from [`config/__init__.py`](../src/config/__init__.py) | Observability dashboards / tracing policy (later observability spec) |
| **Pydantic output models** (query plan, SQL generation, schema draft) under `src/agents/schemas/` or `src/llm/schemas.py` | Streamlit UI |
| Replace stub logic in **`agents/query_agent.py`** and **`agents/schema_agent.py`** | Vector DB / semantic memory |
| **`with_structured_output`** + **`ainvoke`** / **`invoke`** in agents | Changing critic rules or MCP SQL validator (Spec 02) |
| **`.env.example`** entries for LLM env vars | Multi-user auth |

**Explicit deletes / non-goals:** Do **not** keep a deterministic “stub mode” alongside LLM mode in production agent modules. Do **not** import **`langchain_anthropic`**, **`langchain_google_genai`**, or other vendor packages in **`src/`** application code — the LiteLLM proxy owns provider diversity.

---

## 3. Target repository layout

```text
src/
  llm/
    __init__.py
    factory.py              # create_chat_llm() -> ChatLiteLLM; normalize api_base URL
  agents/
    schemas/
      __init__.py
      query_outputs.py      # QueryPlanOutput, SqlGenerationOutput (Pydantic)
      schema_outputs.py     # SchemaDraftOutput, nested table/column models
    query_agent.py          # REFACTOR: async structured LLM calls
    schema_agent.py         # REFACTOR: async/sync structured LLM calls
  config/
    llm_settings.py         # NEW: LLMSettings
    __init__.py             # export LLMSettings
```

**Packaging:** Add **`src/llm`** to `[tool.hatch.build.targets.wheel] packages` in [pyproject.toml](../pyproject.toml) (the **`agents/schemas/`** package is included under the existing **`agents`** tree). Extend **`known-first-party`** with **`llm`** (manual `pyproject.toml` edits allowed for packaging/ruff — same rule as Spec 07).

---

## 4. Dependencies

- **Python:** `>=3.12` ([pyproject.toml](../pyproject.toml)).
- **New runtime dependency:** add via CLI only:

```bash
uv add langchain-litellm
```

This pulls **`litellm`** and LangChain-compatible glue as transitive deps. **Do not** hand-edit **`[project.dependencies]`** or **`uv.lock`** ([AGENTS.md](../AGENTS.md)).

- **Already present / transitive:** **`pydantic`** (via LangChain stacks) — output models should use **Pydantic v2** (`model_validate`, `model_dump`).
- **Forbidden in `src/` imports:** direct use of **`langchain_openai.ChatOpenAI`** unless §15 applies; **`langchain_anthropic`**, **`langchain_google_genai`**, etc.

---

## 5. Configuration

### 5.1 `LLMSettings`

Add `src/config/llm_settings.py`:

```python
# Illustrative — implement in config/llm_settings.py.
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_service_url: str = Field(
        ...,
        validation_alias=AliasChoices("LLM_SERVICE_URL"),
        description="LiteLLM (OpenAI-compatible) HTTP root, normally ending with /v1",
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY"),
    )
    llm_model: str = Field(
        ...,
        validation_alias=AliasChoices("LLM_MODEL"),
        description="Model id as understood by the LiteLLM proxy router",
    )
    llm_temperature: float = Field(default=0.0, validation_alias=AliasChoices("LLM_TEMPERATURE"))
    llm_timeout_seconds: float = Field(default=120.0, validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS"))
    llm_max_retries: int = Field(default=3, validation_alias=AliasChoices("LLM_MAX_RETRIES"))
```

### 5.2 Environment variable table

| Variable | Required | Purpose |
| --- | --- | --- |
| `LLM_SERVICE_URL` | yes | HTTP(S) base for OpenAI-compatible chat completions — **must** include the **`/v1`** suffix expected by your proxy (factory normalizes trailing slashes). |
| `LLM_API_KEY` | yes in deployed environments | API key issued for the proxy (may be non-empty dummy in local-only tests — document policy in README when implementing). |
| `LLM_MODEL` | yes | Logical model name routed by LiteLLM (swap to test different providers/models **without code changes**). |
| `LLM_TEMPERATURE` | no | Default **`0.0`** for deterministic SQL-ish behavior. |
| `LLM_TIMEOUT_SECONDS` | no | Client timeout for each LLM HTTP call. |
| `LLM_MAX_RETRIES` | no | Retries for transient proxy/network failures. |

**Single-model policy:** One trio (`LLM_SERVICE_URL`, `LLM_API_KEY`, `LLM_MODEL`) drives **both** schema drafting and query generation unless a future spec introduces optional `QUERY_LLM_MODEL` / `SCHEMA_LLM_MODEL` — **out of scope** for Spec 08 unless course staff require split routing.

---

## 6. LiteLLM client factory (`ChatLiteLLM`)

### 6.1 URL normalization

The factory **must**:

1. Strip trailing slashes from `LLM_SERVICE_URL`.
2. **Always** append **`/v1`** if the path does not already end with it (misconfigured env should fail fast in CI).

### 6.2 Factory snippet (normative shape)

```python
# src/llm/factory.py
from langchain_litellm import ChatLiteLLM

from src.config.llm_settings import LLMSettings


def create_chat_llm(settings: LLMSettings | None = None, *, temperature: float | None = None) -> ChatLiteLLM:
    cfg = settings or LLMSettings()
    base = cfg.llm_service_url.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    temp = cfg.llm_temperature if temperature is None else temperature
    return ChatLiteLLM(
        model=cfg.llm_model,
        api_base=base,
        api_key=cfg.llm_api_key or "dummy-key",
        temperature=temp,
        timeout=cfg.llm_timeout_seconds,
        max_retries=cfg.llm_max_retries,
    )
```

**Note:** Using a placeholder API key when empty is acceptable **only** if local tests never hit the network; production docs should require a real key. Adjust import paths if running from a different root context.

---

## 7. Pydantic structured output models

### 7.1 Query agent outputs

Define **two** models — one for planning, one for SQL — so prompts can stay focused and validation can differ per step.

**Query plan (`QueryPlanOutput`):** fields should include at least **`intent`** (short string), **`summary`** (user NL restatement), **`relevant_tables`** (list of `"schema.table"` strings or similar), and **`notes`** / **`assumptions`** optional lists. Use **`Field(description=...)`** on every field LangChain should bind tightly.

**SQL generation (`SqlGenerationOutput`):**

```python
# Illustrative — implement in agents/schemas/query_outputs.py.
from pydantic import BaseModel, Field


class SqlGenerationOutput(BaseModel):
    """Single SELECT candidate for PostgreSQL dvdrental — critic enforces policy."""

    sql: str = Field(
        ...,
        description="Exactly one PostgreSQL SELECT statement including a LIMIT clause.",
    )
    rationale: str = Field(
        default="",
        description="One or two sentences on why this SQL answers the user.",
    )
```

### 7.2 Schema agent draft output

Mirror the **`schema_draft`** JSON shape consumed by Spec 05 / graph: **`tables`** array with **`schema`**, **`name`**, **`description`**, **`columns`** (`name` + `description`). Implement as nested **`BaseModel`** lists and dump to **`dict`** for **`GraphState.schema_draft`**.

```python
# Illustrative — implement in agents/schemas/schema_outputs.py.
from pydantic import BaseModel, Field


class ColumnDraft(BaseModel):
    name: str = Field(description="Column name as in inspect_schema metadata.")
    description: str = Field(description="Short NL description for query grounding.")


class TableDraft(BaseModel):
    schema_name: str = Field(
        ...,
        alias="schema",
        description='PostgreSQL schema name (often "public").',
    )
    name: str = Field(description="Table name.")
    description: str = Field(description="Short NL description of the table.")
    columns: list[ColumnDraft] = Field(default_factory=list)


class SchemaDraftOutput(BaseModel):
    tables: list[TableDraft] = Field(default_factory=list)
```

Use **`.model_dump(by_alias=True, mode="json")`** to ensure the serialized output uses the aliased key names (e.g., `"schema"` instead of `"schema_name"`) for compatibility with graph state and persistence layers.

### 7.3 Structured LLM binding

```python
# Illustrative pattern — inside agents after messages are built.
structured_plan = plan_llm.with_structured_output(QueryPlanOutput)
result: QueryPlanOutput = await structured_plan.ainvoke(messages)
plan_dict = result.model_dump(mode="json")
```

Apply the same pattern for **`SqlGenerationOutput`** and **`SchemaDraftOutput`**.

**Method choice:** Prefer the default structured-output strategy supported by **`langchain-litellm`** for your proxy; if a specific deployment only supports tool-calling structured output, document **`method=`** override in implementation notes (keep proxy compatibility as the acceptance test).

---

## 8. Mapping structured outputs to graph state

| Agent step | Pydantic model | GraphState field | Conversion |
| --- | --- | --- | --- |
| `query_plan` node | `QueryPlanOutput` | **`query_plan: dict \| None`** | **`model_dump(mode="json")`** |
| `query_generate_sql` node | `SqlGenerationOutput` | **`generated_sql: str \| None`** | Use **`.sql`**, strip whitespace |
| `schema_draft` node | `SchemaDraftOutput` | **`schema_draft: dict \| None`** | **`model_dump(by_alias=True, mode="json")`** compatible with persistence |

If structured invocation **raises** (timeouts, validation errors after retries):

- **`query_generate_sql`:** set **`generated_sql`** to an empty string **`""`**; the **`query_critic`** node must reject empty SQL and set an appropriate error message in **`last_error`** before surfacing to the user. This ensures a consistent, predictable error path.
- **`schema_draft`:** set **`schema_draft`** to **`None`** and surface the error message to **`last_error`** before HITL approval step. This prevents invalid schema data from reaching persistence.

---

## 9. Prompting and safety rules (normative minimum)

System prompts **must** state:

1. Database is **PostgreSQL**, **`dvdrental`** — read-only (**SELECT** only); **no** DDL/DML.
2. Generated SQL **must** include **`LIMIT`** (Spec 06 critic reinforces).
3. Prefer **`public`** schema naming when consistent with MCP **`inspect_schema`** metadata.
4. Respect **`schema_docs_context`** and user **`preferences`** from memory when present ([specs/07-memory.md](07-memory.md)).

**Grounding:** Pass **`schema_docs_context`** (and compact **`inspect_schema`** snippets if already in state) inside the **human** or **system** message — avoid sending secrets.

**Error handling:** The **`GraphState`** must include a **`last_error: str | None`** field (added or assumed from prior specs) to capture LLM invocation failures. Schema and query pipeline nodes must populate this field when structured outputs fail, allowing the HITL step to surface diagnostic information to users.

---

## 10. Async contract with LangGraph nodes

[`graph/query_pipeline.py`](../src/graph/query_pipeline.py) nodes are **`async`**. [`graph/schema_pipeline.py`](../src/graph/schema_pipeline.py) nodes **should be refactored to `async`** for consistency. Therefore:

- Prefer **`await structured_llm.ainvoke(...)`** inside **`async def`** helpers called from **`query_plan`** / **`query_generate_sql`**.
- Refactor **`schema_draft`** node to **`async`** when implementing this spec; if technical constraints prevent this, document them clearly in the implementation PR.

LangGraph OSS pattern reference: async LLM calls inside async nodes ([LangGraph Python docs — use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).

---

## 11. Testing strategy

| Layer | Requirement |
| --- | --- |
| **Unit** | Patch **`create_chat_llm`** or inject a **`GenericFakeChatModel`** / stub that returns pre-built **`BaseMessage`** payloads compatible with **`with_structured_output`** tests — or test schema parsing independently by calling **`QueryPlanOutput.model_validate(...)`**. |
| **Integration** | Optional **`@pytest.mark.integration`**: requires **`LLM_*`** env + reachable proxy; assert one **`ainvoke`** returns a valid **`SqlGenerationOutput`** with **`LIMIT`**. |

**CI default:** Integration tests **skipped** without explicit env — document in **`pytest`** markers.

---

## 12. Relationship to earlier specs

| Spec | Change driven by Spec 08 |
| --- | --- |
| **06** | Supersedes “deterministic stub until LLM spec” — **`agents/query_agent`** uses real structured LLM outputs. |
| **07** | Query/schema agents consume **`schema_docs_context`** / preferences from memory-loaded state — unchanged contract. |
| **05** | **`schema_draft`** content is LLM-produced (Pydantic-validated), not placeholder strings — HITL still mandatory before persist ([AGENTS.md](../AGENTS.md)). |

---

## 13. Risks and escape hatch (`ChatOpenAI`)

If **`ChatLiteLLM`** proves incompatible with a specific proxy deployment (auth headers, streaming quirks):

1. Document the failure mode and proxy requirements.
2. Allow **`uv add langchain-openai`** and a thin **`ChatOpenAI`** factory **only** behind the same **`LLM_*`** env vars (`base_url` = normalized **`LLM_SERVICE_URL`**, **`model`**, **`api_key`**).
3. Keep **Pydantic structured output** — swapping the base chat class does not change Spec 08 output contracts.

This path is **exceptional**; default remains **`langchain-litellm`**.

---

## 14. Implementation checklist (coding PR)

1. **`uv add langchain-litellm`** ([§4](#4-dependencies)).
2. **`src/config/llm_settings.py`:** `LLMSettings` ([§5.1](#51-llmsettings)); export from **`config/__init__.py`**.
3. **`src/llm/factory.py`:** `create_chat_llm()` ([§6](#6-litellm-client-factory-chatlitellm)).
4. **`src/agents/schemas/`:** `QueryPlanOutput`, `SqlGenerationOutput`, `SchemaDraftOutput` ([§7](#7-pydantic-structured-output-models)).
5. **`agents/query_agent.py`:** Replace stubs with structured LLM calls; **`async`** API if graph nodes await ([§8](#8-mapping-structured-outputs-to-graph-state), [§10](#10-async-contract-with-langgraph-nodes)).
6. **`agents/schema_agent.py`:** Replace placeholders with **`SchemaDraftOutput`** pipeline ([§7.2](#72-schema-agent-draft-output)).
7. **`graph/query_pipeline.py` / `graph/schema_pipeline.py`:** Adjust only if signatures become async or error paths need explicit handling.
8. **`pyproject.toml`:** packages + ruff **`known-first-party`** ([§3](#3-target-repository-layout)).
9. **`.env.example`:** document **`LLM_*`** vars ([§5.2](#52-environment-variable-table)).
10. **Tests:** unit fakes + optional integration marker ([§11](#11-testing-strategy)).
11. **`uv run ruff check .`**, **`uv run ruff format .`**, **`uv run pytest`**.

---

## 15. Prompt for coding agent (optional)

Implement **`specs/08-litellm.md`**:

1. Add **`langchain-litellm`** via **`uv add`**.
2. Add **`LLMSettings`** + **`create_chat_llm()`**.
3. Define Pydantic **`QueryPlanOutput`**, **`SqlGenerationOutput`**, **`SchemaDraftOutput`** and wire **`with_structured_output`**.
4. Replace **`agents/query_agent.py`** and **`agents/schema_agent.py`** stub bodies; remove dual stub/LLM modes.
5. Update **`.env.example`**, **`pyproject.toml`** packaging lines, and tests.

---

## 16. Assignment alignment

[TASK.md](../TASK.md) expects NL→SQL and NL schema documentation backed by LLMs in a realistic deployment — this spec connects **university LiteLLM proxy** configuration to **`ChatLiteLLM`** and **typed** outputs without scattering vendor SDKs across **`src/`**.

**Minimum acceptance hooks:**

- Swapping **`LLM_MODEL`** exercises different proxy-routed models ([§5.2](#52-environment-variable-table)).
- Read-only guarantees remain enforced by MCP + critic after LLM generation ([AGENTS.md](../AGENTS.md)).
