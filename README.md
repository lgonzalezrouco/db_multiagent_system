# db-multiagent-system

Individual course project: a **natural-language query system** over PostgreSQL using **LangGraph**, two agents (schema + query), MCP tools, and memory—evaluated on the **DVD Rental** dataset.

| Doc | Role |
| --- | --- |
| [TASK.md](TASK.md) | Full assignment: agents, memory, MCP, deliverables, rubric |
| [AGENTS.md](AGENTS.md) | Repo workflow: `uv`, safety rules, Git conventions, verification |
| [specs/01-bootstrap.md](specs/01-bootstrap.md) | Bootstrap spec (layout, DB proof, tooling) |

---

## Prerequisites

- **Python** `>=3.12` (see `.python-version` / `pyproject.toml`)
- **[uv](https://github.com/astral-sh/uv)** for environments and dependencies
- **Docker** (or another Postgres 18 instance) for local database work

---

## 1. Clone and install dependencies

From the repository root:

```bash
uv sync
```

This creates/updates the virtualenv and installs the project (editable) plus dev tools from the lockfile.

---

## 2. Database (Docker Compose + DVD Rental)

The app talks to database **`dvdrental`** (DVD Rental sample data). [docker-compose.yml](docker-compose.yml) sets `POSTGRES_DB` to **`dvdrental`** and the healthcheck uses that same database (`pg_isready` on `dvdrental`).

1. Ensure `db/dvdrental.tar` is present (used on first container init).
2. Start Postgres (and the MCP server in this branch):

   ```bash
   docker compose up -d
   ```

3. Wait until the container is healthy:

   ```bash
   docker ps --filter name=multiagent-postgres
   ```

4. Optional sanity check (Postgres):

   ```bash
   docker exec -it multiagent-postgres psql -U postgres -d dvdrental -c '\dt'
   ```

---

## 3. Configuration (environment)

`POSTGRES_*` are **required**; there are **no defaults in Python**. Values come from the process environment and from a **`.env`** file next to the project (loaded by `pydantic-settings`).

| Step | What to do |
| --- | --- |
| Template | Copy [`.env.example`](.env.example) → `.env` (`cp -n .env.example .env`) |
| Edit | Adjust only if your host, port, or credentials differ from local Compose |
| Git | **Never commit** `.env` (it is gitignored); `.env.example` stays safe to commit |

Variables:

| Variable | Purpose |
| --- | --- |
| `POSTGRES_HOST` | Host (e.g. `localhost`) |
| `POSTGRES_PORT` | Port (e.g. `5432`) |
| `POSTGRES_USER` | Role (matches Compose: `postgres`) |
| `POSTGRES_PASSWORD` | Password (matches Compose in dev) |
| `POSTGRES_DB` | Must be **`dvdrental`** for this dataset |

MCP (streamable HTTP) variables (used when running the MCP server):

| Variable | Purpose |
| --- | --- |
| `MCP_HOST` | Host/interface to bind (e.g. `127.0.0.1` locally, `0.0.0.0` in Docker) |
| `MCP_PORT` | Port to bind (default in this repo: `8000`) |
| `MCP_SERVER_URL` | Full client URL (e.g. `http://127.0.0.1:8000/mcp`) |

---

## 4. Run the end-to-end demo (bootstrap + MCP tools)

This repo’s `main.py` runs:

- a **bootstrap** check (read-only DB connection + a trivial `SELECT`)
- then a small **MCP demo** that calls the running MCP server over streamable HTTP

Prereq: `docker compose up -d` (brings up Postgres + `mcp-server`).

```bash
uv run python main.py
```

Expect exit code **0** and output that includes:

- `bootstrap_ok database=dvdrental ...`
- `MCP endpoint: http://127.0.0.1:8000/mcp`
- tool names including `inspect_schema` and `execute_readonly_sql`

The MCP server exposes streamable HTTP on **`/mcp`** (default `http://127.0.0.1:8000/mcp`).

---

## 5. Tests

| Command | What it does |
| --- | --- |
| `uv run pytest tests/ -q` | All tests. Integration tests **skip** if Postgres is not reachable. |
| `uv run pytest tests/test_bootstrap_smoke.py -q` | Bootstrap tests only |
| `uv run pytest -m integration -q` | Only tests marked `@pytest.mark.integration` (needs Postgres + valid `.env`) |

`tests/conftest.py` loads **`.env`** from the repo root (via pytest’s `config.rootpath`) so `Settings()` can be built for unit and integration tests (`override=False` preserves env vars already set in the shell).

MCP-specific tests live alongside the rest:

- `tests/test_mcp_streamable_http_client.py`: starts an in-process streamable HTTP server and asserts the tool list includes `inspect_schema` and `execute_readonly_sql`
- `tests/test_mcp_readonly_unit.py`: read-only SQL validator unit tests (no DB)
- `tests/test_mcp_db_integration.py`: integration tests against live `dvdrental`

---

## 6. Lint and format

```bash
uv run ruff check .
uv run ruff format .
```

Fix issues before merging; see [AGENTS.md](AGENTS.md) for project expectations.

---

## 7. Git hooks (optional)

Pre-commit is configured for Ruff and small hygiene checks:

```bash
uv run pre-commit install
```

---

## 8. Project layout (high level)

```text
main.py                      # CLI entry (logging + bootstrap)
src/config/
  settings.py                # pydantic-settings (package `config`)
src/db_multiagent_system/
  bootstrap.py               # connect + read-only SELECT
  mcp_demo.py                # demo client for MCP server/tools
src/mcp_server/
  main.py                    # MCP server entrypoint (streamable HTTP)
  readonly_sql.py            # read-only SQL validation + safety checks
  schema_metadata.py         # schema introspection helpers
  tools.py                   # MCP tool registration (inspect_schema, execute_readonly_sql)
src/utils/                   # small shared helpers
tests/
  conftest.py                # load .env for tests
  test_bootstrap_smoke.py
db/                          # dvdrental.tar + restore script
docker-compose.yml
Dockerfile                   # container image for MCP server
specs/                       # feature specs (e.g. 01-bootstrap)
```

---

## 9. Adding dependencies

Use **uv** only—do not edit dependency tables by hand:

```bash
uv add <package>
uv add --dev <package>
```

See [AGENTS.md](AGENTS.md).
