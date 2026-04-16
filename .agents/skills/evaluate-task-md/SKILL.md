---
name: evaluate-task-md
description: Audits implementation and docs against TASK.md (LangGraph two-agent DVD Rental NL query system)—mandatory requirements, rubric weights, minimum acceptance checklist—with file-level evidence. Use when reviewing assignment completeness, pre-submission gaps, rubric alignment, or when the user asks if the repo meets TASK.md.
---

# Evaluate against TASK.md

## Source of truth

- Read **`TASK.md`** at the repository root before judging. Do not assume requirements from memory; quote or paraphrase sections only after re-reading if the file may have changed.

## What to evaluate

Map the repo to these **TASK.md** areas (use them as section headings in the report):

| Area                 | TASK.md anchor                                         | Weight (rubric) |
| -------------------- | ------------------------------------------------------ | --------------- |
| Architecture         | LangGraph graph, two agents, separation                | 25%             |
| Schema agent + HITL  | Schema flow, approval, persistence                     | 20%             |
| Query agent          | NL→SQL, safe execution, explanation, refinement        | 25%             |
| Memory               | Persistent preferences + short-term session context    | 15%             |
| MCP tools + patterns | MCP in graph, planner/executor, HITL, critic/validator | 10%             |
| Code quality & docs  | Structure, README, reproducibility                     | 5%              |

## Process

1. **Inventory**: List agents, graph entrypoints, memory modules, MCP tool modules, and test/demo paths (from `README`, `pyproject.toml`, `src/`, `tests/`).
2. **Trace requirements**: For each mandatory item below, mark **Met / Partial / Missing** and cite **evidence** (file path + short quote or symbol name). If unknown without running code, say what command would verify (e.g. `uv run pytest`).
3. **Rubric view**: For each rubric row, summarize strengths and gaps in one or two sentences with evidence.
4. **Checklist**: Walk the **Minimum Acceptance Checklist** in TASK.md line by line; output a checkbox line per item with status and proof.
5. **Risks**: Call out contradictions (e.g. destructive SQL path, missing DVD Rental in demo) explicitly.

## Mandatory items to verify (from TASK.md)

Use this as an internal checklist; expand in the report only where status is not obvious:

- **LangGraph**: Explicit graph with nodes/edges, state, routing.
- **Two agents**: Schema Agent vs Query Agent—separate prompts/tools/nodes.
- **Memory**: Persistent (cross-session preferences) and short-term (conversation context); documented **what** and **why**.
- **MCP tools**: At least schema inspection + read-only SQL execution; traceable in logs; invoked from the graph.
- **Patterns**: Planner/executor (or equivalent), HITL before persisting schema docs or risky execution, critic/validator before final SQL execution.
- **Schema flow**: Connect, discover, draft descriptions, human review, persist, reuse in queries.
- **Query flow**: NL in → intent → SQL → read-only execution → SQL + sample + explanation; iterative refinement.
- **Non-functional**: Modularity, reproducible setup, error handling, observability (nodes, tools, retries, HITL).
- **Constraints**: Read-only execution path; DVD Rental required for test/eval/demo; README setup steps.

## Output format

Produce a short report (markdown) with:

1. **Executive summary**: 2–4 sentences: likely submission readiness and top blockers.
2. **Requirement matrix**: Table or bullet list—requirement, status (Met/Partial/Missing), evidence.
3. **Rubric alignment**: One subsection per rubric category (25/20/25/15/10/5) with gaps and next actions.
4. **Minimum acceptance checklist**: Copy TASK.md checklist items; append `✓ / ⚠ / ✗` and evidence for each.
5. **Suggested next steps**: Ordered list of the smallest changes that close the largest gaps.

Keep claims **evidence-based**. Prefer `path/to/file` and concrete symbols over vague praise.

## Scope notes

- **DVD Rental**: Confirm dataset is the default for tests/demo per README or scripts; note if only mentioned but not wired.
- **Safety**: If evaluating SQL generation, confirm the execution path rejects or blocks non-read-only statements (align with project rules in `AGENTS.md` if present).
- Do not rewrite `TASK.md`; this skill is for **evaluation**, not editing the spec.

## Optional deep dive

If the user needs detail on one area, read implementation files for that area only and add a focused addendum (still with paths and evidence).
