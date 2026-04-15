# AGENTS.md

This file defines **project-specific instructions** for AI coding agents working in this repository.

**Requirements source of truth:** `TASK.md` (two LangGraph agents, memory, MCP tools, patterns, observability, dataset, suggested layout, deliverables, rubric). This file only adds **repo workflow**, **how to run things here**, and **rules agents must not miss**.

## Project goal (summary)

Build a **Schema Agent** and a **Query Agent** over PostgreSQL using the **DVD Rental** dataset, with safe read-only execution and human approval before persisting schema docs. Details: `TASK.md`.

## Repo state (current)

- `README.md` is currently empty.
- `main.py` is a placeholder.
- `docker-compose.yml` starts Postgres and loads the DVD Rental dataset into a `dvdrental` database via `db/restore-dvdrental.sh`.
- **Python tooling:** this repo uses **[uv](https://github.com/astral-sh/uv)** for environments and dependencies (`pyproject.toml`, `uv.lock`).

### Dependencies (must follow)

- **Do not add or edit dependency entries by hand** in `pyproject.toml` or `uv.lock`. Use **`uv add <package>`** for runtime deps and **`uv add --dev <package>`** for dev-only tools so versions and the lockfile stay consistent.
- If you cannot run `uv` in this environment (e.g. no network or `uv` missing), **tell the user exactly which `uv add` command to run** instead of pasting manual dependency lines.

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

## Spec-driven design

Work is **spec-driven**: requirements live in specs (e.g. `TASK.md`, feature specs, or `SPEC-*.md`), and implementation follows them deliberately.

- **One spec ≈ one feature ≈ one branch** (see [Git conventions](#git-conventions-must-follow)). The branch should land in a **functional** state: the app builds, core flows run, and nothing is left half-wired in a way that breaks `main`-level expectations after merge.
- **Never rewrite a previous spec**: when adding or updating requirements, do not replace or overwrite an existing spec file in full. Add a new spec for new scope, or make **targeted, additive** edits to the spec you are working on; preserve prior spec documents as the historical record unless the user explicitly asks to revise or replace a specific file.
- **Multi-step specs**: after every step, the codebase should still be **functional**—no “big bang” integration at the end. Prefer small, verifiable increments.
- **Testing**: treat automated tests as a first-class deliverable; run the project’s test suite (plus [smoke checks](#how-to-verify-changes-minimum)) before considering work complete.

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
- A **spec/feature** usually maps to one branch (often `feat/...`); keep that branch shippable until merge.

Branch naming:

- `feat/<short-description>`
- `fix/<short-description>`
- `chore/<short-description>`
- `docs/<short-description>`

## How to verify changes (minimum)

When you change anything substantial:

- **Tests**: run the project’s automated tests (`pytest` or whatever the repo standardizes on) and fix failures before merging. New features and bugfixes should include tests where practical.
- **Formatting/lint**: `uv run ruff check .` and `uv run ruff format .` (Ruff is configured in `pyproject.toml`).
- **Smoke run**: start Postgres (`docker compose up -d`) and run a minimal agent flow that:
  - reads schema metadata
  - generates one safe SQL query with `LIMIT`
  - executes it against `dvdrental`

## What not to do

- Don’t introduce alternate datasets for core flows; grading expects **DVD Rental**.
- Don’t rely on manual psql steps inside the agent flows; the system should operate via tools/graph.
- Don’t add secrets (tokens, `.env` with real creds). Use environment variables and document defaults.
