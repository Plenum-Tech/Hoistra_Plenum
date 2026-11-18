# Handoff: Trace Lab exact expected values (continue in the cloud)

Written 18 Nov 2026, 20:30 IST, when Hussain closed his laptop and asked for the work to continue in a Claude cloud
session. Branch: `trace-lab-expected-values-wip` (from `9377105`, origin/Hoistra_Frontend at the time).

## What is on this branch

1. **Already built and tested locally (commit "Trace Lab: an expected answer on every live question; Reading in
   fixed")**: every live question gets an expected answer (no "no rule of the specification applies" placeholder,
   redrafts, newest first; `auto_expected.py`); live questions whose conversation is a report run / rerun / upload /
   Skill lab are filed so they open (`eval_store.file_waiting_live`, autosync + Sync now); Type column shows the
   channel. Local evidence: trace-lab suite 4288 passed + the two updated correction-loop tests, 95 skipped (scratch
   Postgres); frontend 3469/3469; lab build OK; verified on the local stack (R60/R53 no "Reading in…", R62 real draft).
2. **Approved design**: `docs/superpowers/specs/2026-11-18-trace-lab-expected-values-design.md` (Hussain approved;
   includes "Calls made after the builder check", and his answer: curators + platform team may run edited queries).
3. **Plan**: `docs/superpowers/plans/2026-11-18-trace-lab-expected-values.md`, 9 tasks + "Amendments from the builder
   check" (they override the task text). Execution method chosen by Hussain: **subagent-driven** (fresh implementer
   per task, fresh reviewer per task, one whole-branch review at the end).

## Progress

- Pre-flight scan done (table below). No task is complete. Task 1 was started and stopped; its partial work was
  discarded, so start at Task 1.

## Pre-flight scan and rulings (from the local ledger)

| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1↔T5 | T1 moves _ontology + Receipts to records_run (alias kept); T5 routes _run_plan through run_query | consistent |
| T1↔T6 | run_query mode="as_written" (amendment) used by T6 POST | consistent |
| T1↔T2 | run dict (+pending, receipt labels) → values_of | consistent |
| T2↔T4/T7 | facts kinds incl. pending/missing; changed_since | consistent |
| T3↔T4 | queries_for(nodes, question, propose) → drafting budget | consistent |
| T4↔T5 | evidence.queries[index].plan → recheck | consistent |
| T6↔T8 | GET {queries, map, can_run}; POST {index, plan} → {before, after, changes} | consistent |
| T7↔T8 | blocksOf, SOURCE_WORDS shared | consistent |
| T2 self | count label "Floor records found" vs list test "Work orders found" | Ruling 2 |
| T3 self | "read a real tree via signed-in GET": not possible in the cloud | Ruling 3 |

- Ruling 1 (cloud): commits ARE allowed on this branch only (Hussain authorised the WIP branch). Never push to
  `Hoistra_Frontend` or any other branch, never merge, never open a PR without Hussain.
- Ruling 2: count label is "<Concept words, singular, capitalised> records found" for every concept ("Floor records
  found", "Work order records found"); Task 2's list test expects "Work order records found".
- Ruling 3: Task 3 derives the records-tool node's input shape from `svc-deepagents/src/agents/trace.py` (how tool
  spans record input) and `svc-trace-lab/trace_lab/trace_tree.py`; Task 9's browser walk on the laptop confirms it.

## Hard rules (Hussain's, still binding)

- Never edit anything under `apps/backend/cafm-connector-service-final/svc-deepagents/` (Trace Lab only imports it).
- Never connect to any shared, production or Azure database. The only database allowed is a throwaway local
  Postgres you start yourself for the tests, named `tracelab_scratch` on 127.0.0.1 (the test helpers refuse anything
  else and DROP schemas in it).
- No automatic pass/fail verdicts in Trace Lab; a proposed query is always labelled as proposed.
- Do not deploy anything. Task 9 (local deploy + browser walk on real data) waits for Hussain's laptop; finish Tasks
  1-8 and the whole-branch review, then stop and write the result into this file's "Progress".

## Running the tests in the cloud

- Python: from `apps/backend/cafm-connector-service-final/svc-trace-lab`, install its deps and pytest
  (`pip install -e . pytest pytest-asyncio` plus what `../svc-deepagents/pyproject.toml` needs to import
  `src.agents.ontology_qa`), then `python -m pytest -q -p no:warnings tests/<file>` (`pythonpath` in pyproject adds
  `../svc-deepagents`). Set `OPENAI_API_KEY=dummy ANTHROPIC_API_KEY=dummy`.
- Real-SQL tests: start a local Postgres 16 if the environment allows, create database `tracelab_scratch`
  (user postgres, password scratch), and set
  `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@127.0.0.1:5432/tracelab_scratch` and the same as
  `DB_URL`. Without Postgres those tests skip; say so in the progress notes and do not claim them green.
- Frontend: `cd apps/frontend && npm ci && node --test "src/lab/__tests__/*.test.mjs"`; build:
  `VITE_TRACE_LAB=1 npx vite build`.
