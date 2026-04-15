# Spec 01 — Bootstrap

**Sources of truth:** [TASK.md](../TASK.md), [AGENTS.md](../AGENTS.md). This spec does not replace them; it covers **project bootstrap** (layout, tooling, DB connectivity proof) only.

---

## 1. Purpose

Establish a **reproducible baseline**: Python project layout, dependency management with **uv**, configuration via **pydantic-settings** (or equivalent), Docker Compose **PostgreSQL** with the **DVD Rental** dataset loaded into database **`dvdrental`**, and a **minimal entrypoint** that proves a successful read-only connection from the host to that database.

**Functional outcome:** `docker compose up -d` yields a healthy Postgres with `dvdrental` populated; `uv run …` runs the entrypoint and exits `0` after a trivial `SELECT` succeeds.

---

## 2. Scope

| In scope                                                                                   | Out of scope (future work / other specs)                            |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| Target repo layout (folders, package name)                                                 | LangGraph graphs, state, routing                                    |
| `uv` runtime + dev deps via `uv add` / `uv add --dev`                                      | MCP server, `langchain-mcp-adapters`, tools                         |
| `pydantic-settings` (or equivalent) for DB URL / host, port, user, password, database name | Schema Agent, Query Agent, prompts                                  |
| `.env.example` (no real secrets)                                                           | Persistent / short-term memory implementation                       |
| Document Docker Compose + `db/dvdrental.tar` + restore script                              | Streamlit or HTTP API                                               |
| Minimal CLI/module entrypoint: connect + `SELECT 1` (or equivalent) + structured logging   | Read-only SQL validator (full guardrails → `specs/02-tools-mcp.md`) |

---

## 3. Target repository layout

Keep the bootstrap change set small. Suggested structure (adjust names if needed, but document in this spec when implementing):

```text
db_multiagent_system/
  pyproject.toml          # uv-managed; do not hand-edit dependency tables
  uv.lock
  .env.example            # documented defaults; no secrets
  main.py                 # thin entrypoint or re-export to package
  docker-compose.yml
  db/
    dvdrental.tar
    restore-dvdrental.sh
  src/
    config/               # pydantic-settings (top-level package `config`)
      __init__.py
      settings.py         # BaseSettings for POSTGRES_*
    db_multiagent_system/
      __init__.py
      bootstrap.py        # connect + read-only SELECT
      # later: agents/, graph/, tools/, memory/ — not required for bootstrap
  tests/
    conftest.py             # optional: load `.env` for tests (see §10)
    test_bootstrap_smoke.py   # optional but recommended: smoke + `@pytest.mark.integration`
  specs/
    01-bootstrap.md         # this file
```

**Rule:** Future work may add `agents/`, `graph/`, `tools/`, `memory/` without breaking the bootstrap entrypoint.

---

## 4. Python toolchain

- **Python:** `>=3.12` (match [pyproject.toml](../pyproject.toml) `requires-python`).
- **Package manager:** **[uv](https://github.com/astral-sh/uv)** only for adding dependencies.

**Runtime dependencies (implementation):** add via CLI, for example:

- `uv add pydantic-settings`
- `uv add psycopg[binary]` (or `psycopg` without binary if policy requires; document choice in PR)

**Dev dependencies (implementation):** e.g. `uv add --dev pytest` if smoke tests are added.

**Hard rule (per [AGENTS.md](../AGENTS.md)):** Do **not** hand-edit dependency lists in `pyproject.toml` or `uv.lock`. The spec names _what_ to add; implementation uses `uv add <package>`.

---

## 5. Configuration

- Use **`pydantic-settings`** `BaseSettings` (or equivalent) for a single settings object.
- **Minimum fields:** database host, port, user, password, database name (or a single `DATABASE_URL` — pick one style and document it).
- **Local development** assumes the process runs on the **host** and Postgres is reachable at `localhost:5432` (published by Compose). Document that shape in **`.env.example`**; do **not** duplicate those values as default assignments in Python unless the team explicitly chooses to—this repo uses **required** settings with **no in-code defaults**, so `POSTGRES_*` must come from the environment and/or a copied **`.env`** file.
- **Optional:** SSL mode for future non-local deployments; omit if unused.

Settings must be loadable from environment variables (see section 6) and optionally from `.env` in dev (`.env` remains gitignored; `.env.example` documents keys and sample values).

---

## 6. Environment variables

Document all variables in **`.env.example`**. Example naming (adjust to match implementation):

| Variable            | Meaning                       | Example / default (local) | Required |
| ------------------- | ----------------------------- | ------------------------- | -------- |
| `POSTGRES_HOST`     | TCP host                      | `localhost`               | Yes      |
| `POSTGRES_PORT`     | TCP port                      | `5432`                    | Yes      |
| `POSTGRES_USER`     | DB role                       | `postgres`                | Yes      |
| `POSTGRES_PASSWORD` | DB password                   | `mysecretpassword`        | Yes      |
| `POSTGRES_DB`       | **Application database name** | `dvdrental`               | Yes      |

**Compose default database:**

- [docker-compose.yml](../docker-compose.yml) sets `POSTGRES_DB` to **`dvdrental`** (same DB the app and healthcheck use).
- [db/restore-dvdrental.sh](../db/restore-dvdrental.sh) runs `pg_restore` into **`dvdrental`** from `db/dvdrental.tar` on first init.

**The application must use `POSTGRES_DB=dvdrental`** in settings and `.env.example` so queries hit the restored DVD Rental schema.

---

## 7. Docker and dataset

### 7.1 Compose

- Service: `postgres`, image `postgres:18`, container name **`multiagent-postgres`**.
- Port **5432** published to host.
- Init: `./db/restore-dvdrental.sh` and `./db/dvdrental.tar` mounted into `/docker-entrypoint-initdb.d/`.

### 7.2 Restore behavior

- On first init, the restore script creates **`dvdrental`** if missing and runs `pg_restore` from the tar.
- If `dvdrental.tar` is missing at init, the script logs and **exits 0** (no `dvdrental` DB). For bootstrap acceptance, **`db/dvdrental.tar` must be present** on the host before first `docker compose up`.

### 7.3 Verification (manual)

- Wait until healthcheck passes (`pg_isready` on `dvdrental` — see Compose — or check `docker ps`).
- Confirm dataset: `docker exec -it multiagent-postgres psql -U postgres -d dvdrental -c '\dt'` (expect tables).

**Dataset rule:** [TASK.md](../TASK.md) requires **DVD Rental**; do not substitute another dataset for core flows.

---

## 8. Minimal entrypoint contract

Replace the placeholder [main.py](../main.py) behavior with something equivalent to:

1. Load settings from env / `.env` (dev).
2. Open a connection to **`dvdrental`** using configured credentials.
3. Execute a trivial **read-only** statement, e.g. `SELECT 1` or `SELECT current_database(), current_user`.
4. Log success (structured or clear text) including database name; on failure log error and **exit non-zero**.

**Explicit non-requirements:** no LLM, no LangGraph, no MCP, no HTTP server.

**Note:** Full destructive-token blocking for arbitrary SQL belongs in `specs/02-tools-mcp.md`. [AGENTS.md](../AGENTS.md) read-only rules apply to the full system; bootstrap only needs a safe hardcoded `SELECT`.

---

## 9. Acceptance criteria

1. With `db/dvdrental.tar` present, `docker compose up -d` starts **`multiagent-postgres`** and healthcheck succeeds.
2. Database **`dvdrental`** exists and contains DVD Rental data (at least one user-visible table).
3. `.env.example` lists all required variables; no committed file contains real secrets.
4. `uv sync` (or documented equivalent) installs declared dependencies from lockfile.
5. Entrypoint runs via `uv run python main.py` (or `uv run python -m <package>` as documented) and exits `0` when DB is reachable and credentials in `.env` (typically copied from `.env.example`) match a working local Compose setup.
6. Wrong password or wrong host produces a **clear error** and non-zero exit (no silent success).
7. `uv run ruff check .` and `uv run ruff format .` pass on changed code (per [AGENTS.md](../AGENTS.md)).

---

## 10. Verification commands

Run from repository root after implementation:

```bash
docker compose up -d
docker ps --filter name=multiagent-postgres
# optional: wait for healthy
uv sync
cp -n .env.example .env   # if using .env locally; do not commit .env
uv run python main.py     # or documented module path
```

Optional smoke tests:

```bash
uv run pytest tests/test_bootstrap_smoke.py -q
# Integration test (Postgres up; register `integration` in pyproject if using the marker):
uv run pytest -m integration -q
```

**Tests note:** `tests/conftest.py` loads `.env` so `Settings()` can be constructed in unit tests and integration tests without hardcoding `POSTGRES_*` in Python.

---

## 11. Implementation checklist

1. Add `pydantic-settings` and PostgreSQL driver via `uv add` (see section 4).
2. Add `src/config/settings.py` (or equivalent) with documented env vars, plus `bootstrap.py` under `db_multiagent_system/` for shared connect/`SELECT` logic.
3. Add `.env.example` matching section 6; ensure `.gitignore` ignores `.env`.
4. Implement connection + `SELECT` in `main.py` (thin) or `python -m` package entry.
5. Optional: `tests/conftest.py` + `@pytest.mark.integration` for live DB smoke; register the marker under `[tool.pytest.ini_options]` in `pyproject.toml`.
6. Document exact commands in README snippet or keep this spec as reference for the bootstrap PR.
7. Run Ruff and optional pytest; fix failures.

---

## 12. Prompt for coding agent (optional)

Implement **only** `specs/01-bootstrap.md`: uv + pydantic-settings, `.env.example`, minimal `main.py` (or module) that connects to Postgres database **`dvdrental`** on `localhost:5432` using env vars, runs `SELECT 1`, logs success, exits non-zero on failure. Use `uv add` for dependencies; do not hand-edit `pyproject.toml` dependency arrays. Do not add LangGraph, MCP, agents, or memory. Follow [AGENTS.md](../AGENTS.md) for Ruff and Conventional Commits.
