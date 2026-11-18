# Trace Lab exact expected values — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every live question's expected answer is exact values read from the records, each value opening its derivation, with the whole query editable as fields and re-runnable.

**Architecture:** A faithful re-run (`records_run.py`) calls Hoistra's own records-engine steps in `answer_question`'s order through a receipt-recording reader; `records_values.py` turns a result into values and a sentence; `records_queries.py` finds each turn's queries (Hoistra's, the answer key's, or one Hoistra's planner proposes). Drafting, the routes and the page are moved onto these. Hoistra's code is not changed.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, pytest (+ pytest-asyncio auto mode); React 18 + node:test with esbuild bundles.

**Spec:** `docs/superpowers/specs/2026-11-18-trace-lab-expected-values-design.md`

Paths below: `TL = apps/backend/cafm-connector-service-final/svc-trace-lab`, `OQ = apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/ontology_qa.py`, `FE = apps/frontend/src/lab`.
Backend test runner (scratch DB `tl-scratch-pg` only, never `tl-local-pg`):
`/private/tmp/claude-501/-Users-hussain-Desktop-hoist/b0c71146-7cf6-4bdd-b086-2f54b2a522bc/scratchpad/tltest/run.sh <pytest args>` (run from `$TL`).
Frontend: `cd apps/frontend && PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH" node --test src/lab/__tests__/<file>`.

## Global Constraints

- Never edit anything under `svc-deepagents/` (EVAL GOLDEN RULE): Trace Lab only imports `ontology_qa`.
- No database writes outside Trace Lab's own `tracelab` schema; every records read is one READ ONLY transaction, company-scoped (`role="admin", organization_id=<question's company>, building_ids=None`), as `answer_key._run_plan` today.
- No raw SQL editing anywhere; plan names come only from Hoistra's map; values are bound parameters.
- A proposed query is always labelled "Proposed for this question - check it before accepting".
- Receipts keep at most `ROWS_KEPT = 50` rows; at most `VALUES_SHOWN = 20` listed records per query.
- Budget per drafting round: `PER_ROUND = 20` questions, of which at most `PROPOSALS_PER_ROUND = 10` may call the planner.
- `DRAFT_VERSION = 3`; drafts store `evidence.drafted_from = "records"` when they hold values.
- No `git commit` / `git push` (Hussain's rule): tasks end with the suite green; the final task saves a patch.
- Trace Lab never judges Hoistra's answer: no pass/fail words next to values.

## Review Focus

1. A multi-part question (two records-tool calls, e.g. R60-7) keeps one query per call, in order, each editable — test in Task 3.
2. A name that is not on record gives the value "Not on record: ABC", never "0" — test in Task 2.
3. The planner is unavailable (no key, model error, invalid plan after retries): no crash, the SPEC.md draft stays and the reason is stored — test in Task 4.
4. A list longer than 20 records shows 20 and "and N more" with the exact total — test in Task 2.
5. Another company's turn cannot be read or run through the query routes — test in Task 6.

## Amendments from the builder check (spec section "Calls made after the builder check"; they override the tasks)

- **Task 1:** `run_query(plan, *, question, org, mode="hoistra", scope=None)`. `mode="as_written"` runs only
  `AS_WRITTEN = ("resolve_plan", "stated_count_notes", "fill_date_filters", "validate_plan", "execute_plan")`.
  `scope` = `{"building_ids": list | None, "email": str | None}` passed into the reader's principal (`None` = whole
  company). In `hoistra` mode, after `execute_plan`, call `oq.pending_context(r, onto, plan)` exactly as
  `answer_question` does and return it as `pending` (its queries are receipts). Extra parity assertion:
  `answer_question` calls `pending_context` after `execute_plan`. The result's `plan` is the plan as run (after steps).
- **Task 2:** value kind `"pending"` from `run["pending"]` (read its shape at `OQ:2798`): every count (open by trade,
  vendor, type, priority; overdue; blocked) with its receipt; no risk-to-life values. New
  `def changed_since(receipts: list[dict], recorded: list[dict]) -> list[dict]` → `[{label, then, now}]` for receipts
  whose label matches a recorded query (same label, in order) with a different row count; recorded queries are the
  SQL spans under the records tool node (`input.label`, `output.row_count`).
- **Task 3:** `extract_queries` drops a records-tool call whose output says not found (`found: false`) or an error
  when a later records-tool call follows (a retry). Proposals only when the turn called a data tool other than the
  records tool: `def data_tools(nodes) -> list[str]` = tool node names except `answer_from_records`, `task`,
  `final_answer` and names starting with `verify` (pin the list from two real trees, e.g. R60-3 and R62-1);
  `queries_for(..., propose=True)` with no data tool → `{"queries": [], "none": "not_about_records"}`.
- **Task 4:** scope: rebuild the asker's access read-only with Hoistra's own principal code from the turn's user id
  (find it in `svc-deepagents/src/services/principal.py`; import, never edit); if it cannot be rebuilt, the whole
  company. Store per query `scope` (`"asker"` / `"company"`), `scope_email`, `changed` (from `changed_since`). A
  turn with no company is not drafted with values.
- **Task 6:** `POST .../expected/query` and the editor require a curator or the platform team (`runs.can_curate(m)`;
  others get 403 `Invalid`/`Forbidden` as the codebase does for curator-only routes). `GET .../expected/query` returns
  `can_run: bool`. The person's plan runs with `mode="as_written"`; the "before" side runs Hoistra's query with
  `mode="hoistra"`. The editor's starting plan is the plan as run.
- **Task 7:** each block shows the scope words ("Read with <email>'s access" / "Read for the whole company") and, when
  `changed` is not empty, "The records have changed since Hoistra answered: Hoistra read N rows, now M".
- **Task 8:** when `can_run` is false the editor is not shown; values and receipts still are.

---

### Task 1: Faithful re-run of a records query

**Files:**
- Create: `$TL/trace_lab/records_run.py`
- Modify: `$TL/trace_lab/answer_key.py` (move `_ontology` and the `Receipts` reader into `records_run`; keep `answer_key._ontology = records_run.load_map` as an alias)
- Test: `$TL/tests/test_trace_lab_records_run.py`

**Interfaces:**
- Produces: `STEPS: tuple[str, ...]`; `async def load_map() -> Any` (raises `answer_key.NotReady`, moved here as `records_run.NotReady`, re-exported by `answer_key`); `class ReceiptReader(oq.ScopedReader)` with `.receipts: list[dict]`; `async def run_query(plan: dict, *, question: str, org: str) -> dict` returning `{"found": bool, "unresolved": list[str], "plan": dict, "notes": list[str], "receipts": list[dict], "read_at": str, "result": dict | None}`; `def hoistra_steps(source: str, names: set[str]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
STEPS_AGG = tuple(s for s in rr.STEPS if s not in ("ensure_status_includes", "ensure_compliance_includes"))

def test_steps_match_hoistras_records_tool():
    import inspect
    from src.agents import ontology_qa as oq
    names = {n for n, v in vars(oq).items() if inspect.isfunction(v)}
    assert rr.hoistra_steps(inspect.getsource(oq.answer_question), names) == list(rr.STEPS), \
        "Hoistra's records tool changed its steps: update records_run.STEPS and run_query to match"

async def test_run_query_calls_every_step_in_hoistras_order(monkeypatch):   # fake oq functions record their names
    calls = _fake_pipeline(monkeypatch, aggregate=False)
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="how many floors in ABC", org=ORG)
    assert calls == list(rr.STEPS)
    assert out["found"] is True and out["receipts"][0]["label"] == "resolve Building (exact)"
    assert all(q == "how many floors in ABC" for q in _questions_seen())   # ensure_grouping, pin_live_vendors, add_missing_codes

async def test_an_aggregate_skips_the_status_includes_like_hoistra(monkeypatch):
    calls = _fake_pipeline(monkeypatch, aggregate=True)
    await rr.run_query(PLAN_COUNT_BY_BUILDING, question="q", org=ORG)
    assert calls == list(STEPS_AGG)

async def test_a_name_not_on_record_stops_before_reading(monkeypatch):
    calls = _fake_pipeline(monkeypatch, unresolved=["ABC"])
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    assert out["found"] is False and out["unresolved"] == ["ABC"] and "execute_plan" not in calls

async def test_every_query_is_a_receipt_scoped_to_the_company(monkeypatch):
    ...  # ReceiptReader built with principal organization_id == ORG; each fetch/try_fetch adds
         # {"id": "q<n>", "label", "sql", "params", "row_count", "rows" (<= 50), "psql"}
```

`STEPS` value (copy exactly; it must equal what the parity test reads from `OQ:answer_question`):

```python
STEPS = ("ensure_grouping", "swap_empty_links", "apply_pinned", "apply_pinned", "pin_live_vendors",
         "names_from_filters", "add_missing_codes", "resolve_plan", "stated_count_notes", "fill_date_filters",
         "finished_states_note", "ensure_status_includes", "ensure_compliance_includes", "validate_plan", "execute_plan")
```

- [ ] **Step 2: Run to see them fail** — `run.sh tests/test_trace_lab_records_run.py -q` → FAIL (`No module named trace_lab.records_run`).

- [ ] **Step 3: Implement `records_run.py`**
`hoistra_steps(source, names)`: the names of `names` called in `source` (regex `\b(\w+)\s*\(`), in order, between the first `plan_question(` and the first `evidence(`. `run_query`: `oq.normalise_plan(plan)` → the steps in `STEPS` order exactly as `answer_question` calls them (`apply_pinned(onto, plan, None)` twice: page filters are not recorded in the trace), inside `ReceiptReader(principal)`; return early with `found=False` when `resolve_plan` returns unresolved names; skip the two `ensure_*` for an aggregate; `validate_plan` errors raise `ValueError("the plan does not fit this database: …")`. Notes = everything the steps return as notes, in order.

- [ ] **Step 4: Run** — same command → PASS. Then `run.sh tests/test_trace_lab_answer_key.py -q` → PASS (alias kept).

---

### Task 2: Values and a sentence from a result

**Files:**
- Create: `$TL/trace_lab/records_values.py`
- Test: `$TL/tests/test_trace_lab_records_values.py`

**Interfaces:**
- Consumes: Task 1 result dict.
- Produces: `def values_of(onto, plan: dict, run: dict) -> list[dict]` — each `{"id": "f<n>", "kind": "count"|"stated"|"group"|"record"|"field"|"linked"|"missing", "label": str, "value": str|int|float, "receipt": str|None}`; `def sentence_of(onto, plan: dict, run: dict, facts: list[dict]) -> str`; `VALUES_SHOWN = 20`.

- [ ] **Step 1: Write the failing tests** (fake onto as in `tests/test_trace_lab_answer_key.py::_onto`, receipts as Task 1 produces)

```python
def test_floors_give_the_count_and_the_figure_the_building_states():
    facts = rv.values_of(ONTO, PLAN_COUNT_FLOORS, RUN_ABC)   # receipts: count n=0; "stated floors on Building" rows [{"l":"ABC","v":"25"}]
    assert [(f["kind"], f["label"], f["value"], f["receipt"]) for f in facts] == [
        ("count", "Floor records found", 0, "q3"), ("stated", "ABC states floors", 25, "q2")]
    assert rv.sentence_of(ONTO, PLAN_COUNT_FLOORS, RUN_ABC, facts) == "ABC states floors: 25. Floor records found: 0."

def test_a_name_not_on_record_is_a_value_not_a_zero():
    run = {**RUN_ABC, "found": False, "unresolved": ["ABC"], "result": None}
    facts = rv.values_of(ONTO, PLAN_COUNT_FLOORS, run)
    assert [(f["kind"], f["value"]) for f in facts] == [("missing", "ABC")]
    assert rv.sentence_of(ONTO, PLAN_COUNT_FLOORS, run, facts) == "Not on record: ABC."

def test_a_long_list_shows_twenty_and_the_exact_total():
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run_with_records(37))
    assert facts[0] == {"id": "f1", "kind": "count", "label": "Work orders found", "value": 37, "receipt": "q1"}
    assert len([f for f in facts if f["kind"] == "record"]) == 20
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS, run_with_records(37), facts).endswith(", and 17 more.")

def test_groups_single_record_fields_and_linked_records():
    ...  # aggregate rows -> one "group" per row ("Bishopsgate Tower", 12, receipt "aggregate");
         # question_type "single" with one record -> its non-empty attributes except keys and created_at/updated_at as "field";
         # related {"vendor": [{"name": "Apex Mechanical"}]} -> "linked" "WO-4527 → vendor" = "Apex Mechanical" (its include receipt)
```

- [ ] **Step 2: Run** → FAIL (no module).
- [ ] **Step 3: Implement.** Stated values come from receipts whose label starts with `"stated "` (label `stated <table> on <Concept>`; rows `{"l", "v"}`; numeric text becomes int/float). Count receipt = label `count`; list receipt = label starting `focus `; group receipt = `aggregate`; linked receipt = label starting `include <name> `. Reuse `answer_key._generic_item` and `_words` (import them; no copies). Sentence: fixed words, values in fact order as shown in the tests, joined with ". ".
- [ ] **Step 4: Run** → PASS.

---

### Task 3: A turn's queries: Hoistra's, the answer key's, or proposed

**Files:**
- Create: `$TL/trace_lab/records_queries.py`
- Test: `$TL/tests/test_trace_lab_records_queries.py`

**Interfaces:**
- Consumes: trace nodes as `eval_store.nodes_of(conn, turn_id)` returns them (`id`, `parent_id`, `name`, `type`, `input`, `output`); `answer_key.match`, `KEYS`; `records_run.load_map`.
- Produces: `def extract_queries(nodes: list) -> list[dict]` → `[{"plan": dict, "question": str|None}]`; `async def queries_for(nodes: list, question: str, *, propose: bool) -> dict` → `{"queries": [{"source": "hoistra"|"answer_key"|"proposed", "plan": dict, "question": str}], "none": None | "not_about_records" | "planner_unavailable" | "no_map"}`; `SOURCE_WORDS = {"hoistra": "Hoistra’s query", "answer_key": "Trace Lab’s fixed query", "proposed": "Proposed for this question - check it before accepting"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_records_tool_call_keeps_its_last_plan_and_its_question():
    nodes = [tool("t1", "answer_from_records", {"question": "decisions owed at Bishopsgate"}),
             llm("p1", parent="t1", plan=PLAN_A_RETRY), llm("p2", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": "inspection reports to convert at Bishopsgate"}),
             llm("p3", parent="t2", plan=PLAN_B)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": "decisions owed at Bishopsgate"},
                                         {"plan": PLAN_B, "question": "inspection reports to convert at Bishopsgate"}]

async def test_order_of_sources(monkeypatch):
    # Hoistra's plans win; else the answer key; else a proposal; else none
    assert (await rq.queries_for(NODES_WITH_PLAN, "x", propose=True))["queries"][0]["source"] == "hoistra"
    assert (await rq.queries_for([], "How many vendors are blocked right now?", propose=True))["queries"][0]["source"] == "answer_key"
    _planner(monkeypatch, plan=PLAN_A)
    got = await rq.queries_for([], "What is the vendor score for Apex?", propose=True)
    assert got["queries"] == [{"source": "proposed", "plan": PLAN_A, "question": "What is the vendor score for Apex?"}]
    assert (await rq.queries_for([], "What is the vendor score for Apex?", propose=False)) == {"queries": [], "none": None}

async def test_a_planner_that_fails_gives_no_query_and_says_why(monkeypatch):
    _planner(monkeypatch, raise_=oq.OntologyError("Could not build a valid plan"))
    assert await rq.queries_for([], "hello", propose=True) == {"queries": [], "none": "not_about_records"}
    _planner(monkeypatch, raise_=RuntimeError("401 no key"))
    assert (await rq.queries_for([], "x", propose=True))["none"] == "planner_unavailable"
```

The tool node input may be a dict or a JSON string of one; the plan node is any descendant of the tool node whose output parses as a plan (reuse `answer_key.extract_plans` on each tool's descendants). Before writing the fixtures, read one real tree (`GET /backend/trace-lab/api/trace-lab/lab/q/R62-1/tree` on the local stack, signed in) and copy the tool node's `input` shape into the fixture.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Proposal = `await oq.plan_question(await records_run.load_map(), question)` (Hoistra's planner, its retries); `oq.OntologyError` → `"not_about_records"`, `records_run.NotReady` → `"no_map"`, any other exception → `"planner_unavailable"`.
- [ ] **Step 4: Run** → PASS.

---

### Task 4: Drafting every live question with values

**Files:**
- Modify: `$TL/trace_lab/auto_expected.py`, `$TL/trace_lab/runs.py:1061-1068` (`turn_expected` kind/taken)
- Test: `$TL/tests/test_trace_lab_auto_expected_sql.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: evidence `{"drafted_from": "records", "draft": 3, "read_at": str, "queries": [{"source", "question", "plan", "found", "unresolved", "notes", "facts", "receipts", "read_at", "sentence"}]}`; `runs.turn_expected(...)["kind"] == "records"` for such drafts and for a person's version saved from one.

- [ ] **Step 1: Write the failing tests** (fake `records_run.run_query` and `records_queries.queries_for` with monkeypatch; real scratch DB for the rows)

```python
async def test_a_question_with_a_query_gets_values_as_its_draft(...):
    out = await auto_expected.draft_for_live(None, now=NOW)
    ev = (await _newest(lab_scratch))[qid]["evidence"]
    assert ev["drafted_from"] == "records" and ev["draft"] == 3
    assert ev["queries"][0]["facts"][0]["value"] == 25 and ev["queries"][0]["source"] == "hoistra"
    assert (await runs.turn_expected(member(), turn))["kind"] == "records"

async def test_no_query_keeps_the_spec_draft_and_stores_why(...):            # Review Focus 3
    # queries_for -> {"queries": [], "none": "planner_unavailable"}
    assert ev["drafted_from"] == "SPEC.md" and ev["no_query"] == "planner_unavailable"

async def test_at_most_ten_proposals_a_round(...):                           # 12 questions needing a proposal
    assert proposals_asked == 10 and out["drafted"] == 10

async def test_older_drafts_get_values_but_people_and_reviews_are_kept(...):  # SPEC.md and answer_key drafts with draft < 3

async def test_a_values_draft_is_not_redrafted_when_the_spec_changes(...):
    # approve a new SPEC.md version; a draft with drafted_from "records", draft 3 stays
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** For each question: its turn id (`lab_question_traces`, sync link first), nodes via `eval_store.nodes_of(conn, turn_id)` read inside `conversations._lab_read()` only when `tracelab.traces.organization_id` equals the question's company (else skip this round); `queries_for(nodes, question, propose=<budget left>)`; run each with `records_run.run_query(plan, question=q["question"] or question, org)` and `records_values`; store as above, `expected = " ".join(sentences)[:runs.EXPECTED_MAX]`. Outdated-draft SQL becomes: `e.source = 'coding_agent' AND e.evidence->>'drafted_from' IN ('SPEC.md','answer_key','records') AND (COALESCE(e.evidence->>'draft','1') <> :dv OR (e.evidence->>'drafted_from' = 'SPEC.md' AND COALESCE(e.evidence->>'spec_version','') <> :sv))`. `turn_expected`: `"records": "records"` in the kind map; `taken` also for `"records"`.
- [ ] **Step 4: Run** `run.sh tests/test_trace_lab_auto_expected_sql.py tests/test_trace_lab_run_sessions_sql.py -q` → PASS.

---

### Task 5: The answer key and "Run again now" on the faithful re-run

**Files:**
- Modify: `$TL/trace_lab/answer_key.py` (`_run_plan`, `run`, `recheck`)
- Test: `$TL/tests/test_trace_lab_answer_key.py`, `$TL/tests/test_trace_lab_auto_expected_sql.py`

**Interfaces:**
- Produces: `async def run(key, params, org, *, question: str = "") -> dict` (unchanged keys plus `notes`); `async def recheck(m, turn_id, *, index: int = 0) -> dict` → for a `records` draft, the stored query `index` re-run: `{"found", "facts", "receipts", "read_at", "unresolved", "notes", "sentence"}`.

- [ ] **Step 1: Failing tests:** `_run_plan` goes through `records_run.run_query` (monkeypatched; assert it received the question); `recheck` on a records draft re-runs `evidence.queries[index].plan` for the turn's company; another company's turn → `NotFound`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (route `POST /lab/turns/{turn_id}/expected/check` takes optional `?index=`). **Step 4: Run** → PASS.

---

### Task 6: Editing the whole query: the map, checks and the routes

**Files:**
- Create: `$TL/trace_lab/records_form.py`
- Modify: `$TL/trace_lab/routes_runs.py:163-185` (replace `QueryChange` with `QueryRun`), `$TL/trace_lab/answer_key.py` (`orchestrator_query`, `try_query` move onto Tasks 1-3 and this module; `plan_fields`/`apply_fields` removed with their tests)
- Test: `$TL/tests/test_trace_lab_records_form.py`, `$TL/tests/test_trace_lab_answer_key.py`

**Interfaces:**
- Produces: `def query_map(onto) -> dict` → `{"concepts": [{"name", "words", "attributes": [{"name", "kind": "date"|"number"|"text"}], "states": [{"id", "description"}], "links": [concept names reachable via onto.resolve_path]}], "ops": [{"id", "words"}], "periods": [...]}`; `def check_plan(onto, plan: dict) -> list[str]` (normalise + `oq.validate_plan`, errors reworded in plain words); `def plan_changes(onto, old: dict, new: dict) -> list[str]`; routes: `GET /lab/turns/{turn_id}/expected/query` → `{"ready": bool, "queries": [{"index", "source", "source_words", "question", "plan"}], "none": str|None, "map": query_map}`; `POST /lab/turns/{turn_id}/expected/query` body `QueryRun {index: int = 0, plan: dict}` → `{"before": {...values of the turn's query index...}, "after": {...values of plan...}, "changes": [...]}`; `Invalid("bad_query", <words>)` when `check_plan` finds problems.

- [ ] **Step 1: Failing tests:** `query_map` lists concept attributes with kinds (date by name pattern `_DATE_COL`, number when the map says numeric, else text) and states with descriptions; `check_plan` refuses an unknown concept / state / column / op with words naming the field; `plan_changes` says "What: Floor records → Building records", "Condition added: expiry_date within next_60_days", "Limited to: Building ABC removed", "Grouped by: Building name"; routes: a member of another company gets 404 for both routes (Review Focus 5); POST returns before/after values for a changed `focus.concept`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Attribute kind: use the concept's column types when the map holds them (check `Concept` in `OQ:782-848` for a type field); else the name pattern. **Step 4: Run** → PASS; full `run.sh tests -q` → no failures.

---

### Task 7: The page shows values with their source

**Files:**
- Create: `$FE/logic/recordsValues.js`
- Modify: `$FE/TurnExpected.jsx` (`ExpectedView`, `acceptBody`, `whereFrom`), `$FE/turnExpected.css`
- Test: `$FE/__tests__/recordsValues.test.mjs`, `$FE/__tests__/turnExpected.test.mjs`

**Interfaces:**
- Produces: `export function blocksOf(evidence) -> [{index, source, sourceWords, question, sentence, facts, receipts, unresolved, notes, readAt}]`; `export const SOURCE_WORDS` (same strings as Task 3).

- [ ] **Step 1: Failing tests:** a `records` draft renders one block per query: the source words, "Read <time>", the sentence, a button per value (`factText`), "How the query was read" with the notes; a proposed block carries "Proposed for this question - check it before accepting"; clicking a value renders the existing `Receipt` with that value's receipt (rows, SQL, Copy to check in the database, Run again now); Accept saves `{expected, evidence: {...evidence, drafted_from: "records"}}`; a `missing` value reads "Not on record: ABC".
- [ ] **Step 2-4:** run → FAIL, implement, run → PASS.

---

### Task 8: The query editor over the whole query

**Files:**
- Create: `$FE/QueryEditor.jsx`, `$FE/logic/queryForm.js`
- Modify: `$FE/TurnExpected.jsx` (replace `QueryFix`, `editOf`, `bodyOf`, `planIndex`, `describe` and their tests with the editor), `$FE/labApi.js:77-78` (`turnExpectedTry(id, {index, plan})`)
- Test: `$FE/__tests__/queryForm.test.mjs`, `$FE/__tests__/turnExpected.test.mjs`

**Interfaces:**
- Consumes: Task 6 routes; Task 7 `blocksOf`.
- Produces: `formOf(plan) -> form`, `planOf(form) -> plan` (round-trip identical for every key of the plan schema in the spec), `OP_WORDS`, `describePlan(plan, map) -> string` ("Hoistra looked for …"), `<QueryEditor query map onRun onUse />`.

- [ ] **Step 1: Failing tests:** `planOf(formOf(p))` deep-equals `p` for the R62 plan, an aggregate plan, a plan with includes and sorts; the editor renders tabs per query, the fields of section 6 of the spec (kind of answer, what, name or code, states with descriptions, conditions rows with op words, limited to rows with add/remove, grouped by, also show rows); Run posts `{index, plan}`; the result shows "Hoistra's query found" values beside "Your version finds" values, each clickable to its receipt; server words from `bad_query` show beside the editor; "Use as the expected answer" saves `{expected: after.sentence, evidence: {drafted_from: "records", draft: 3, queries: [...with this index replaced by the person's plan, facts, receipts], changed: changes}}`.
- [ ] **Step 2-4:** run → FAIL, implement, run → PASS; `npm test` → no failures; `VITE_TRACE_LAB=1 npx vite build` → exit 0.

---

### Task 9: Prove it on real data

- [ ] **Step 1:** Full suites: `run.sh tests -q -p no:warnings` (0 failed) and `npm test` (0 failed).
- [ ] **Step 2:** Local deploy: `docker cp` the changed `trace_lab/*.py` into `hoistra_plenum-trace-lab-1` (back up the old files to the scratchpad first), restart; copy `apps/frontend/src/lab` changes into the scratchpad `fe/` copy Vite serves; make sure `/tmp/cafm_ontology_<current fingerprint>.json` exists in the trace-lab container (copy from `hoistra_plenum-svc-deepagents-1:/tmp`).
- [ ] **Step 3:** Browser walk (Hussain signed in, test Chrome on :9333), read-only except Trace Lab's own rows: R62 shows "ABC states floors: 25" and "Floor records found: 0", each opening its receipt; change "What" to Building and run; R60-7 shows two queries; a compliance question shows a proposed query labelled as proposed; "hello" shows no values and the SPEC.md words; no console errors.
- [ ] **Step 4:** Save the patch: `git -C <worktree> diff > /Users/hussain/Desktop/hoist/Hoistra_Plenum/.git/hoistra-expected-20261118/expected-values-on-9377105.patch`; report to Hussain; no commit.
