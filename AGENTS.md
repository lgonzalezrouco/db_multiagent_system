# AGENTS.md

This file defines **project-specific instructions** for AI coding agents working in this repository.

## Project goal (what you’re building)

Build a **two-agent LangGraph** system over PostgreSQL (DVD Rental dataset):

- **Schema Agent**: inspects schema metadata, drafts table/column descriptions, runs a **human-in-the-loop** checkpoint, then persists approved descriptions.
- **Query Agent**: converts natural language questions to **safe, read-only SQL**, executes it, returns **SQL + sample rows + explanation**, and supports iterative refinement.

See `TASK.md` for full grading requirements and deliverables.

## Repo state (current)

- `README.md` is currently empty.
- `main.py` is a placeholder.
- `docker-compose.yml` starts Postgres and loads the DVD Rental dataset into a `dvdrental` database via `db/restore-dvdrental.sh`.

## Local setup (expected workflow)

### Start PostgreSQL + load dataset

1. Ensure `db/dvdrental.tar` exists (already present in this repo).
2. Start Postgres:

```bash
docker compose up -d
```

3. Wait for healthcheck to be green:

```bash
docker ps --filter name=multiagent-postgres
```

### Connect to the database (manual sanity checks)

- Default container is `multiagent-postgres`.
- Credentials from `docker-compose.yml`:
  - user: `postgres`
  - password: `mysecretpassword`
  - database created by restore script: `dvdrental`

Example:

```bash
docker exec -it multiagent-postgres psql -U postgres -d dvdrental
```

## Hard safety rules (must follow)

### SQL execution safety

- **Only read-only SQL is allowed** in the execution path.
- Never execute (or generate for execution) statements containing: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, `VACUUM`, `ANALYZE`, `COPY`, `DO`, `CALL`, `EXECUTE`.
- Prefer `LIMIT` on exploratory queries and previews.
- If a user asks for destructive operations, refuse and offer a safe alternative (e.g., a `SELECT` that previews the rows that would be affected).

### Human-in-the-loop checkpoints

- Before persisting schema descriptions, **require explicit user approval**.
- If descriptions are ambiguous, ask targeted questions and present a short proposed description for approval.

## Implementation conventions (to keep the project gradeable)

- **Keep the two-agent separation crisp**: different prompts, responsibilities, tools, and graph nodes.
- **Traceability/observability is required**:
  - log graph node transitions and tool calls
  - log validation/critic steps
  - log human-in-the-loop interactions
- **Memory**:
  - implement **persistent memory** (user preferences across sessions)
  - implement **short-term memory** (conversation context)
  - document what’s stored and why
- **MCP tools**:
  - implement tools for schema inspection and SQL execution (read-only)
  - ensure tool usage is integrated into the LangGraph workflow and visible in logs

## Git conventions (must follow)

### Commit messages: Conventional Commits

All commits must follow **Conventional Commits** (e.g. `feat: ...`, `fix: ...`, `docs: ...`, `refactor: ...`, `test: ...`, `chore: ...`).

Examples:

- `feat(schema-agent): add HITL approval step for column descriptions`
- `fix(query-agent): block non-readonly SQL tokens`
- `docs: document persistent vs session memory`

### Branching: GitHub Flow (default)

Use **GitHub Flow**:

- `main` stays deployable/working.
- Work happens in short-lived branches and merges back to `main`.
- Prefer small PRs/merges aligned to one logical change.

Branch naming:

- `feat/<short-description>`
- `fix/<short-description>`
- `chore/<short-description>`
- `docs/<short-description>`

## Suggested structure (align with TASK.md)

Prefer keeping code close to the suggested layout:

- `agents/` (schema_agent, query_agent)
- `graph/` (workflow graph, routing)
- `memory/` (persistent + session stores)
- `tools/` (MCP tools for schema + SQL)
- `prompts/`
- `tests/`

## How to verify changes (minimum)

When you change anything substantial:

- **Formatting/lint**: run the project’s formatter/linter once they exist.
- **Smoke run**: start Postgres (`docker compose up -d`) and run a minimal agent flow that:
  - reads schema metadata
  - generates one safe SQL query with `LIMIT`
  - executes it against `dvdrental`

## What not to do

- Don’t introduce alternate datasets for core flows; grading expects **DVD Rental**.
- Don’t rely on manual psql steps inside the agent flows; the system should operate via tools/graph.
- Don’t add secrets (tokens, `.env` with real creds). Use environment variables and document defaults.
