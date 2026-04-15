# Individual Assessment - Multi-Agent Systems (2026 S1)

## Context

This is an **individual assessment**. You must design and implement a production-style prototype of a **Natural Language Query System** over a PostgreSQL database using **LangGraph** and the techniques learned in class.

Your system must be built as **two different agents** that collaborate to solve user requests safely and correctly.

## Goal

Implement a multi-agent solution that supports these two tasks:

1. **Database schema analysis and documentation (with Human-in-the-Loop)**
   - Analyze the PostgreSQL schema (tables, columns, types, constraints, relationships).
   - Ask for human confirmation or edits when descriptions are ambiguous.
   - Produce and persist high-quality natural language descriptions for tables and columns.

2. **Natural language query analysis and SQL execution support**
   - Interpret user requests in natural language.
   - Build SQL queries from those requests.
   - Retrieve data samples and result previews to validate and explain outcomes.
   - Help the user analyze results and refine follow-up queries.

## Mandatory Technical Requirements

### 1) Framework

- Must be implemented using **LangGraph**.
- The workflow must be represented as a graph with explicit nodes/edges, state handling, and routing.

### 2) Two-Agent Architecture (Required)

You must implement at least these two specialized agents:

- **Schema Agent**
  - Inspects DB schema metadata.
  - Generates table/column descriptions.
  - Triggers human-in-the-loop checkpoints for validation and corrections.
  - Stores approved schema descriptions for later reuse.

- **Query Agent**
  - Takes natural language user questions.
  - Uses schema descriptions plus DB metadata to generate SQL.
  - Executes query safely and retrieves sample rows.
  - Explains the result and supports iterative query refinement.

Agents must be clearly separated by responsibilities, prompts, tools, and graph nodes.

### 3) Memory

Your implementation must include both:

- **Persistent Memory**
  - Stores user preferences across sessions.
  - Examples: preferred language, preferred output format (table/json), preferred date format, safety strictness.
- **Short-Term Memory**
  - Maintains conversation/session context.
  - Examples: previous question, last SQL generated, assumptions, clarifications requested, recently used filters.

You must document what is stored in each memory type and why.

### 4) MCP Tools

You must implement and use **MCP tools** in the workflow. At minimum, include tools for:

- Schema inspection (metadata retrieval).
- SQL execution (read-only).
- Optional: sample retrieval, validation, or query explanation support.

All tool calls must be traceable in logs and integrated into the graph execution.

### 5) Agent Patterns

Apply agent patterns studied in class. Your solution must show at least:

- **Planner/Executor separation** or equivalent decomposition.
- **Human-in-the-loop checkpoint** before committing schema descriptions or executing risky queries.
- **Critic/Validator step** (self-check or secondary check) before final SQL execution.

You may include additional patterns if relevant (router, reflection, retries, guardrails).

### Functional Requirements

#### A. Schema Documentation Flow

Your system must:

1. Connect to a PostgreSQL database.
2. Discover schema structure (tables, columns, PK/FK, constraints where available).
3. Draft natural language descriptions per table and per column.
4. Ask user for review/approval/edits.
5. Store approved descriptions in persistent storage.
6. Reuse descriptions in future query-generation steps.

#### B. Querying Flow

Your system must:

1. Receive a natural language question.
2. Determine intent and relevant tables/fields.
3. Generate SQL query (or sequence of queries).
4. Run query in a safe mode (read-only; no destructive commands).
5. Return:
   - SQL produced,
   - sample result rows,
   - concise explanation of interpretation and limitations.

6. Support iterative refinement via follow-up questions

### Non-Functional Requirements

- Clear modular code structure.
- Reproducible setup.
- Robust error handling (bad SQL, empty results, schema mismatch, ambiguous intent).
- Basic observability/logging for:
  - graph node transitions,
  - tool calls,
  - retry/fallback behavior,
  - human-in-the-loop interactions.

### Constraints

- This is an **individual** submission.
- Use only technologies allowed in the course.
- SQL execution must be **safe**:
- read-only operations only,
- no DROP/DELETE/UPDATE/ALTER in execution path.

### Required Test Dataset (for Testing, Evaluation, and Demo)

All students must use the **PostgreSQL DVD Rental sample database** as the mandatory baseline dataset for:

- development testing,
- instructor evaluation,
- final demo.

Reference:

- [PostgreSQL Sample Database (Neon)](https://neon.com/postgresql/postgresql-getting-started/postgresql-sample-database)

Minimum setup expectation:

1. Load the DVD Rental dataset into PostgreSQL.
2. Confirm schema objects are available before running agents.
3. Use this dataset in all demo scenarios submitted for grading.

Notes:

- You may use additional datasets, but the DVD Rental dataset is required.
- Your README must include exact setup steps used in your environment.

### Suggested Project Structure

You can adapt this structure:

- agents/schema_agent.py
- agents/query_agent.py
- graph/workflow.py
- memory/persistent_store.py
- memory/session_store.py
- tools/mcp_schema_tool.py
- tools/mcp_sql_tool.py
- prompts/
- tests/
- README.md

### Deliverables

Submit a repository with:

1. **Source code** (LangGraph-based implementation).
2. **README** including:
   - architecture diagram of the two-agent graph,
   - setup and run instructions,
   - memory design (persistent vs short-term),
   - MCP tools used and their role,
   - agent patterns used.

3. **Demo script** with at least:
   - one schema documentation session (with human correction),
   - three different natural language query examples,
   - one example of follow-up refinement,
   - all examples executed on the required DVD Rental dataset.

4. **Short report** (1-2 pages) explaining design choices and trade-offs.

### Evaluation Rubric

- **Architecture quality (25%)**
  - Correct two-agent decomposition, clear LangGraph orchestration, separation of concerns.
- **Schema agent + human loop (20%)**
  - Quality of schema analysis, interaction design, and persistence of approved descriptions.
- **Query agent quality (25%)**
  - NL understanding, SQL correctness, result analysis, iterative refinement support.
- **Memory implementation (15%)**
  - Correct use of persistent and short-term memory, with clear purpose and impact.
- **MCP tools + patterns (10%)**
  - Effective MCP integration and proper use of agent patterns from class.
- **Code quality and documentation (5%)**
  - Clarity, maintainability, reproducibility, and communication quality.

### Minimum Acceptance Checklist

Your submission is considered complete only if all are true:

- Built with LangGraph.
- Exactly two specialized agents are implemented and used.
- Human-in-the-loop is present in schema documentation flow.
- Persistent memory stores user preferences across sessions.
- Short-term memory supports conversational continuity.
- MCP tools are implemented and called from the graph.
- Natural language queries are converted to SQL and executed safely.
- Results include SQL + data sample + explanation.
- Testing/evaluation/demo are run using the required DVD Rental dataset.
- README and demo scenarios are complete.

### Academic Integrity

This is an individual assessment. You may discuss ideas at a high level, but all design and code must be your own work and must comply with course integrity policies.
