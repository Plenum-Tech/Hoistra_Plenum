# Trace Lab: exact expected values on every live question

Date: 18 Nov 2026. Status: design, waiting for Hussain's approval. Scope: Trace Lab only (svc-trace-lab and
apps/frontend/src/lab). Builds on the uncommitted 18 Nov work in this worktree (every question gets an expected
answer; "Reading in…" fixed).

## Why

Hussain, 18 Nov 2026: "what i want is that the expected ans should the exact values and the when i press on that
values i can see how this was derived and i can change the query and check for the right ans my self make sure u do
this perfectly".

An annotator judges Hoistra's answer against the expected answer. A description of what a good answer contains ("the
answer should state the number of floors…") does not let them judge it: they need the actual figures, where each
figure came from, and a way to correct the query when it read the wrong thing. Today's R62 example shows the gap:
Hoistra answered "Building ABC has 25 recorded floors", Trace Lab's query check said "No floors", and the person could
not tell which was right or fix it.

## What Hussain decided (18 Nov 2026)

1. The expected answer is exact values from the records, each value clickable to show how it was derived, and the
   query can be changed and run again by the person to check the right answer.
2. When Hoistra answered without a records query (an engine tool: compliance, vendor scores, energy), Hoistra's own
   records planner proposes a query for the question; the person checks and fixes it before accepting. This reverses
   the 16 Nov rule "plan written here, never by a model": the planner can repeat Hoistra's mistake, so the check is
   the person's, and the page must say the query was proposed.
3. Every part of the query is editable, as form fields (not raw SQL). Every change is checked against Hoistra's map
   of the database before it runs.

Standing rules that still hold: Trace Lab is a separate module and never changes Hoistra's code or behaviour
(EVAL GOLDEN RULE); Trace Lab never judges Hoistra's answer by itself (no automatic verdicts); nothing writes to the
application database; a Codex scenario turn's flow is unchanged; nothing is committed or pushed without Hussain's word.

## Goals

- Every live question (hand-asked, any age) gets an expected answer drafted as values whenever any records query can
  be made for it: from Hoistra's own query, else a fixed answer-key query, else a query proposed by Hoistra's planner.
- Re-running Hoistra's query gives what Hoistra's records tool actually read (R62: Building ABC states floors = 25;
  0 individual floor records), not a partial re-run.
- Every value opens its derivation: the query, the values filled into it, the rows it read, Copy for psql, Run again.
- The person can change any part of the query (what is read, name, states, conditions, building and other limits,
  grouping, linked records), run it, compare with Hoistra's, and use the result as the expected answer.

## Non-goals

- No raw SQL editing, for anyone.
- No change to Hoistra's code, prompts, tools or answers.
- No automatic pass/fail of Hoistra's answer; the values sit beside it and the person decides.
- Values for engine judgements that are not stored in the records (e.g. a compliance verdict computed only in code)
  are out of scope; the proposed records query reads what the records hold.

## Anti-goals (would make this a failure even if it works)

- A value shown with no way to see how it was found.
- A re-run that silently differs from what Hoistra read (today's "0 floors").
- A proposed query presented as if it were Hoistra's own, or as verified.
- Any path that reads another company's records or writes to the application database.

## How it works today (facts the design rests on)

- `svc-deepagents/src/agents/ontology_qa.py: answer_question` is Hoistra's records tool pipeline: `plan_question`
  (model), then `ensure_grouping`, `swap_empty_links`, `apply_pinned` (twice), `pin_live_vendors`,
  `names_from_filters`, then inside a `ScopedReader`: `add_missing_codes`, `resolve_plan`, `stated_count_notes`,
  `fill_date_filters`, `finished_states_note`, `ensure_status_includes` + `ensure_compliance_includes` (not for
  aggregates), `validate_plan`, `execute_plan`, `evidence`, `pending_context`.
- `svc-trace-lab/trace_lab/answer_key.py: _run_plan` re-runs a plan with only `normalise_plan`, `validate_plan`,
  `resolve_plan`, `execute_plan`. It skips the rest, so "how many floors in building ABC" loses the stated figure
  (`stated_count_notes`: Building.floors = 25) and reports "No floors".
- `extract_plans` finds the planner's JSON plans in the trace; `used_first` orders them. `_turn_plans` falls back to an
  answer-key shape (5 shapes) when the turn has no plan. `try_query` re-runs the original and the changed plan.
- `plan_fields` / `apply_fields` let a person change states, building and dates only.
- Hoistra's trace keeps every SQL statement its records tool ran (`agents/trace.py: on_sql`): the SQL, parameters,
  row count and the first 50 rows, as child spans of the tool.
- The page (`TurnExpected.jsx`) already has: value buttons, a receipt panel (rows, SQL, parameters, Copy for psql,
  Run again now, This is right / This is wrong), the four-step query check (check, change, run, use), and "Use as the
  expected answer". Receipts are capped at 50 rows (`ROWS_KEPT`).
- `lab_expected_answers` is append-only; `evidence` is jsonb with no size limit; `expected` text is 1-8000 chars.
  No migration is needed.
- Trace Lab finds Hoistra's map of the database (`cafm_ontology_<fingerprint>.json`) in its own temp folder. In
  production Trace Lab and the chat share one container and one temp folder; locally they do not.

## Approaches considered (re-running Hoistra's query faithfully)

1. **Chosen: Trace Lab calls Hoistra's own pipeline steps, in the same order, itself.** Every query goes through
   Trace Lab's receipt-recording reader. A test reads `answer_question`'s source and fails when Hoistra's step list
   changes, so Trace Lab cannot silently drift from Hoistra. Hoistra's code is untouched.
2. A shared `answer_plan()` function inside ontology_qa that both use: one source of truth, but it changes Hoistra's
   code, which the Trace Lab rule forbids.
3. Re-running the whole `answer_from_records` tool with the question: it plans again with the model, so the query is
   not the one Hoistra used and cannot be edited.

The planner for proposed queries is Hoistra's own `plan_question` (same prompt, same map), for the same reason: no
second planner to drift from Hoistra's.

## Design

### 1. Where each question's query comes from

For a live question's turn, in this order:

1. **Hoistra's query**: every records plan the turn's trace holds (`extract_plans`, in the order they ran). A question
   with several parts ("decisions owed … after that, inspection reports …") has several; each is a query of its own.
2. **Trace Lab's fixed query**: when the turn has no records plan and an answer-key shape matches (`match`).
3. **Proposed query**: otherwise, Hoistra's planner (`plan_question`) proposes one. When Hoistra's router split the
   turn into several steps and the trace holds each step's own instruction text, one query is proposed per step;
   otherwise one for the question.
4. **No query**: a question that is not about records ("hello"), or the planner could not make a valid plan, or the
   names in it are not on record. Then the expected answer stays the SPEC.md contents draft (today's behaviour) and
   the page says why there are no values.

The source is stored with each query and shown in words: "Hoistra's query", "Trace Lab's fixed query", "Proposed for
this question - check it before accepting".

### 2. The faithful re-run (`trace_lab/records_run.py`, new)

`run_query(plan, *, question, org) -> result` runs the plan the way `answer_question` does after planning, through a
`Receipts` reader bound to the question's company (read-only transaction, statement timeout, company-scoped
principal - as `_run_plan` today), and returns: `found`, `unresolved`, the plan as run, `notes` (Hoistra's own
interpretation notes, e.g. "Deleted vendors are left out", the stated figure), `facts` (section 3), `receipts` (every
query, labelled as Hoistra labels them), `read_at`.

- `question` is the text Hoistra's records tool received, read from the tool call's input in the trace (it may carry
  the conversation's building hint); without it, the person's question. Steps that read the question
  (`ensure_grouping`, `pin_live_vendors`, `add_missing_codes`) get it.
- Page filters a person had on in Hoistra (`apply_pinned`) are not recorded in the trace, so they are not applied;
  the result says so when the original tool call had any (builder: check whether the trace records them; if it does,
  apply them).
- `answer_key._run_plan`, `try_query`, `recheck` and the drafting all use `run_query`, so the answer key's fixed
  queries get the same faithful steps.
- **Parity test**: parse `inspect.getsource(ontology_qa.answer_question)` for the ordered pipeline calls and compare
  with `records_run.STEPS`. Any change on Hoistra's side fails the Trace Lab suite with a message naming the step.

### 3. Values from a result

Each value is `{id, label, value, receipt}` and opens its receipt. Kinds:

- **Count**: "Floor records found: 0" (receipt `count`).
- **Stated figure**: "Building ABC states floors: 25" (receipt `stated floors on Building`). Read from the same
  query `stated_count_notes` runs, so the figure and its receipt match.
- **Groups**: one value per group of an aggregate, "Bishopsgate Tower: 12" (receipt `aggregate`).
- **Records**: for a list, each record's name or code (up to 20, then "and N more" with the full count), each opening
  the focus query with that row; for a single record, its main fields (status, dates, figures) as values.
- **Linked records** (includes): per record, the count and first items of each linked kind, e.g. "WO-4527 → vendor:
  Apex Mechanical".
- **Notes**: Hoistra's interpretation notes are shown as "How the query was read", not as values.

The expected answer's sentence is written from the values in fixed words (no model), e.g. "Building ABC states 25
floors; 0 individual floor records." With several queries, one block per query, in order.

### 4. Drafting every question (`auto_expected`)

Per round, newest question first (as today): find the turn's query (section 1), run it (section 2), store the values
as the draft expected answer: `source coding_agent`, `evidence = {drafted_from: "records", draft: 3, queries:
[{source, plan, question, facts, receipts, notes, found, unresolved}], read_at}`; `expected` = the sentence.
Fallback to the SPEC.md contents draft when there is no query (section 1.4).

- Untouched older drafts (SPEC.md contents drafts and draft-2 drafts) are drafted again with values (new version,
  old kept), never over a person's, an accepted or a reviewed answer (the 18 Nov redraft rules, `DRAFT_VERSION = 3`).
- Budget: at most 20 questions a round, of which at most 10 may need a planner call; each read has the statement
  timeout; when the map is not ready every question rests (as today).
- The answer key's 5 fixed shapes stay; they become one of the query sources.

### 5. The page

The expected answer box on a live turn shows:

- where the query came from, in words (section 1), and when it was read;
- the sentence, then the values as buttons; a click opens the receipt (existing panel);
- "Check and change the query" opening the editor on that query;
- Accept (saves the values with their receipts as the person's own version, as today) and Edit (words).

A proposed query is marked as proposed until a person accepts it or uses their own version.

### 6. The editor: every part of the query, as fields

Built from Hoistra's map of the database (concepts, their columns, states with descriptions, links). Fields:

- **Kind of answer**: list, count, one record.
- **What**: the kind of record read (any concept in the map).
- **Name or code**: the record's own name or code to match, or none.
- **States**: tick boxes with each state's description.
- **Conditions**: rows of column / condition / value. Columns from the concept; conditions in words (is, is not, one
  of, before, after, between, within a period, contains, is empty); values typed by the column (dates accept a date,
  "today+30d" or a period such as last_month or next_60_days).
- **Limited to**: other records the answer must be linked to (building, vendor, asset …), each with its own name,
  states and conditions; add and remove.
- **Grouped by**: count by a column of the focus or of a "limited to" record.
- **Also show**: linked records for each result (kind, states, conditions, sort, how many); add and remove.

Every change is applied to a copy of the plan and checked with `validate_plan` before it can run; problems are shown
in words beside the field. Run reads the changed query with the faithful re-run and shows, side by side, what Hoistra's
query found and what this version finds, value by value. "Use as the expected answer" saves the changed query, its
values and receipts as the person's version (with what changed, in words). A turn with several queries shows one tab
per query; the expected answer keeps all of them.

### 7. Safety

- Read-only transaction, statement timeout, the question's own company only (platform team: the company the run
  belongs to), as `_run_plan` today; never the application database's write path.
- Only names from the map can be used (concepts, columns, states, links), checked by `validate_plan`; values are bound
  parameters, never SQL text.
- No raw SQL anywhere.
- A proposed query is labelled as proposed everywhere it appears.

### 8. Cost and load

- Database: a few read-only queries per question (typically 3-8), at most 20 questions a round.
- Model: one planner call (with Hoistra's retries) per question that needs a proposal, at most 10 a round. Locally the
  first rounds work through the backlog; production likewise.

### 9. Testing

- Parity test (section 2).
- Real-SQL tests on the scratch database: the R62 shape (a building with `floors = 25` and no floor records) gives
  values "Floor records found: 0" and "Building ABC states floors: 25", each with its receipt; a two-query turn keeps
  both; an aggregate gives one value per group; an unresolved name gives "not on record"; another company's records
  are never read.
- Editor: every field round-trips; an invalid change is refused with words; a change of concept, condition, limit,
  grouping and linked records runs.
- Drafting: source order (Hoistra → fixed → proposed → none), redraft rules, budgets, failure keeps the old draft.
- Frontend unit tests for the value kinds, the source label and the editor; a browser walk on the local stack: R62
  (25 floors), R60-7 (two queries), an engine-answered question (proposed query), "hello" (no query).

### 10. Rollout

Local first (the trace-lab container gets the files; the local map file must match the schema), then push on
Hussain's word; Bala redeploys. No migration.

## Calls made after the builder check (18 Nov 2026, Claude's calls unless marked)

1. **Then vs now.** Values are read now and say "Read <time>". Each re-run query is matched to the query Hoistra
   recorded in the trace (same label, in order); when the row counts differ, the block says "The records have changed
   since Hoistra answered: Hoistra read N rows, now M" beside the values. Drafts are not held back.
2. **What the editor starts from and how an edited query runs.** The editor starts from the plan as Hoistra ran it
   (after its steps), so everything Hoistra added (grouping, the deleted-vendors filter, codes, linked records) is
   visible and editable. A person's edited plan runs as written: only `resolve_plan`, `stated_count_notes`,
   `fill_date_filters`, `validate_plan` and `execute_plan`, none of the steps that add to a plan. What you see is what
   runs. Hoistra's own query (the "before" side) still runs with all of Hoistra's steps.
3. **Read scope.** The re-run reads with the asker's access when Trace Lab can rebuild it with Hoistra's own principal
   code from the turn's user id (their buildings), else the whole company; the block says which ("Read with
   <email>'s access" / "Read for the whole company"). A platform-team turn with no company is not drafted with values.
4. **Hoistra's pending figures.** For a work-order question, the counts `pending_context` gives Hoistra (open by trade,
   vendor, type and priority; overdue; blocked) become values of kind "pending", each with its receipt. The
   risk-to-life flags are not values (a keyword match, not a stored fact).
5. **Which turns get a proposed query.** Only turns where Hoistra called at least one data tool other than the records
   tool (compliance, vendor scores, energy, maintenance, documents engines). "hello", clarifications, support and page
   actions get no proposal and keep the SPEC.md draft.
6. **Several records-tool calls in one turn.** A later call made after the earlier one returned not found (or an error)
   is a retry: only the last is kept. Otherwise each call is a separate part with its own query.
7. **Who may run edited queries.** Hussain (18 Nov): curators and the platform team. Everyone else sees the values
   and their receipts and can Accept or Edit the words, but cannot change or run a query.

## Left to the builder

- Exact wording of labels and sentences, within the rules above.
- How many fields of a single record are shown as values (keep it to the figures a question is likely about).
- Visual layout of the editor, within the existing Trace Lab look.
- Whether the receipt for a stated figure shows the whole `stated` query or only its row.
