# Copilot instructions — db_multiagent_system

## Project (see TASK.md)

**Goal:** A **LangGraph** multi-agent **natural language query system** over **PostgreSQL**, mandatory baseline dataset **DVD Rental** (`dvdrental`) for development, evaluation, and demo.

**Two specialized agents (required):**

1. **Schema Agent** — Inspect schema metadata; draft natural language **table/column** descriptions; **human-in-the-loop** for approval/edits; **persist** approved descriptions for reuse.
2. **Query Agent** — Accept NL questions; use schema descriptions + metadata to generate SQL; **execute read-only**; return **SQL + sample rows + explanation**; support **iterative refinement**.

**Mandatory technical themes:** LangGraph graph with explicit **nodes, edges, state, routing**; agents separated by **prompts, responsibilities, tools, and graph nodes**; **MCP tools** at minimum for schema inspection and **read-only** SQL execution, **traceable in logs** and wired into the graph; **persistent memory** (e.g. language, output format, date format, safety strictness) and **short-term memory** (conversation context — prior questions, last SQL, assumptions); document what each memory stores and why; **patterns**: planner/executor (or equivalent), **HITL** before committing schema descriptions, **critic/validator** before final SQL execution.

**Functional:** Schema flow = discover structure → draft descriptions → user review → store → reuse in querying. Query flow = NL → intent/tables → SQL → safe execution → results package → refinement.

**Non-functional:** Modular layout, reproducible setup, robust errors (bad SQL, empty results, ambiguity), **observability**: log node transitions, tool calls, retries, HITL.

---

## PR review — check first (highest priority)

1. **SQL execution safety (non-negotiable)**  
   Only **read-only** SQL on the execution path. Reject or guard against: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, `VACUUM`, `ANALYZE`, `COPY`, `DO`, `CALL`, `EXECUTE`. Prefer **`LIMIT`** on exploratory/preview queries.

2. **Two-agent separation**  
   Changes should not blur Schema vs Query responsibilities (distinct prompts, tools, nodes).

3. **Human-in-the-loop**  
   Schema descriptions must not be persisted without an explicit **approval** path.

4. **Traceability**  
   New behavior should log **graph transitions**, **tool calls**, **validation/critic** steps, and **HITL** where relevant.

5. **Dataset**  
   Core flows and demos must stay on **DVD Rental**; do not replace it as the primary dataset.

---

## Repo conventions (AGENTS.md)

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — e.g. `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`; optional scope e.g. `feat(schema-agent): …`.
- **Branches:** GitHub Flow — short-lived branches; names like `feat/…`, `fix/…`, `chore/…`, `docs/…`; keep `main` working.
- **Secrets:** No real tokens or committed `.env` secrets; use environment variables and documented defaults.

**Suggested layout:** `agents/` (schema_agent, query_agent), `graph/`, `memory/` (persistent + session), `tools/` (MCP schema + SQL), `prompts/`, `tests/`.

**Verification hint:** Substantial changes should remain verifiable with Postgres via `docker compose` and a minimal flow: schema metadata → one safe SQL with `LIMIT` → execute against `dvdrental`.

---

## Style of review

- Prefer **focused** PRs (one logical feature). Flag unrelated drive-by refactors.
- Flag missing tests or docs when behavior or public API changes materially.
- For grading alignment, call out gaps vs TASK.md deliverables (README, demo script, memory documentation) when the PR touches those areas.
