# Trace Lab "Trace" Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Langfuse-style Trace tab (header, Tree / Timeline / Graph, Preview + Metadata pane) beside the existing answer view on Trace Lab's question page.

**Architecture:** One new read-only backend module `trace_lab/trace_tree.py` turns a stored turn and its spans into a Langfuse-shaped node list using `langfuse_export`'s own rules, behind `GET /lab/q/{code}/tree` with the same access path as `GET /lab/q/{code}`. The frontend adds pure helpers (`logic/traceTree.js`, `logic/miniMarkdown.js`), a `TraceTab.jsx` view, and an Answer | Trace switch inside `QuestionView`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, pytest (+ scratch Postgres); React 18 (JSX, no TS), `node --test` + esbuild helper + `react-dom/server`; `lucide-react` icons.

**Spec:** `docs/superpowers/specs/2026-11-09-trace-lab-trace-tab-design.md`

## Global Constraints

- EVAL GOLDEN RULE: touch only `svc-trace-lab/trace_lab/`, `svc-trace-lab/tests/`, `apps/frontend/src/lab/`. Import from `src.agents.langfuse_export` / `src.agents.trace`; never edit them or anything in the chat app.
- Existing answer view, notes, labels and every existing endpoint behave exactly as today; existing tests pass unchanged.
- Main-database reads only through `conversations._read()` (READ ONLY, 10 s); Trace Lab DB reads only through `conversations._lab_read()`. No INSERT/UPDATE/DELETE anywhere in new code.
- Every input/output value passes through `normalize.redact` before it leaves the backend.
- No new npm or Python dependencies.
- Nothing is committed, staged or pushed: Hussain commits only when he asks. Each task ends with a checkpoint instead of a commit.
- Number formats (Langfuse's): step time `0.01s`, `2.71s`, `14.86s`, `1m 02s`; step cost `$` + up to 6 decimals, trailing zeros trimmed (`$0.00769`, `$0.034392`); header cost 3 decimals (`$0.113`); tokens `50k tokens` at ≥ 1,000 (one decimal under 10k: `2.2k`), else `950 tokens`; start time `YYYY-MM-DD HH:MM:SS.mmm` in the browser's local time.
- Tab memory key: `tracelab.qtab.v1` in localStorage, every access in try/catch; URL `qtab=trace` wins.

**Test commands** (from `/Users/hussain/Desktop/hoist/Hoistra_Plenum`):

- Backend (`$T` = test files): `docker run --rm --network container:tl-scratch-pg -e TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@localhost:5432/tracelab_scratch -e DB_URL=postgresql+asyncpg://postgres:scratch@localhost:5432/tracelab_scratch -e OPENAI_API_KEY=sk-test-none -e PYTHONDONTWRITEBYTECODE=1 -e TRACE_LAB_ENABLED=0 -v $PWD:/repo:ro -w /repo/apps/backend/cafm-connector-service-final/svc-trace-lab tl-test:local python -m pytest -q -p no:cacheprovider -p no:warnings $T`
- Frontend: `cd apps/frontend && node --test src/lab/__tests__/<file>.test.mjs`

## Review Focus

1. **A turn whose spans were purged (older than 90 days)**: the tab still shows the header and a lone `chat` root with the question and answer, and says the steps are no longer kept. Test: Task 1 `test_a_purged_turn_is_a_root_alone`.
2. **Steps with no start or end time, or an end before the start** (a stage still open when the turn ended): Timeline bars never get NaN, negative or >100% widths. Test: Task 3 `timeline bars stay inside the axis`.
3. **Another company's answer, or a `turn` that isn't linked to the question**: never shown. An unlinked id falls back to the newest linked answer, as the page does, and another company's question is NotFound. Test: Task 2 `test_the_tree_follows_the_question_pages_access`.
4. **Model text with HTML or script tags** in Formatted mode is shown as text, never as markup. Test: Task 4 `html in markdown stays text`.
5. **A 300-step answer**: only the selected step's input and output are in the page, so the tab stays fast. Test: Task 5 `only the selected step's input and output are rendered`.

---

### Task 1: Backend tree builder (pure, no database)

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-trace-lab/trace_lab/trace_tree.py`
- Test: `apps/backend/cafm-connector-service-final/svc-trace-lab/tests/test_trace_lab_trace_tree.py`

**Interfaces:**
- Consumes: `langfuse_export._ordered, _is_generation, _failed, _shown, filled_io, otel_trace_id, otel_spans_for, OBSERVATION_TYPES, TRACE_NAME`; `normalize.redact`.
- Produces: `build_tree(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]` returning `{"turn": {...}, "nodes": [...], "purged": bool}` with exactly the keys listed in the spec ("Data" section) plus `"purged"`. Node ids and parent ids are strings; the root's id is the turn span's id, or `"root"` when synthesized; `parent_id` of the root is `None`.

- [ ] **Step 1: Write the failing tests** (fixture: one turn row + spans shaped like `agent_trace_spans`: turn span `chat`; `router` llm with model/tokens/cost; agent `maintenance`; under it an llm `model` with model and cost, a tool `answer_from_records` with db children `count`, `focus WorkOrder`; a `plan` llm stage with no model; a failed tool with `error="timeout after 30 s"`; one span whose `parent_id` is unknown).

```python
def test_nodes_are_depth_first_with_the_chat_root_first(): ...
    # names in order == ["chat", "router", "plan", "maintenance", "model", "answer_from_records", "count",
    #                    "focus WorkOrder", <failed tool>, <orphan>]; depths [0,1,1,1,2,2,3,3,...];
    # the orphan's parent_id == root id; every parent appears before its children
def test_types_follow_the_langfuse_export(): ...
    # chat/maintenance -> "agent"; router/model -> "generation"; plan (no model) -> "span";
    # answer_from_records -> "tool"; count -> "span"
def test_cost_and_tokens_only_on_model_calls(): ...
    # generation nodes carry cost_usd/model/tokens; agent "maintenance" cost_usd is None
def test_inputs_and_outputs_match_what_langfuse_receives(): ...
    # for every non-root node: json-equal to the otel_spans_for observation input/output for the same span
    # (parse langfuse.observation.input/output attributes); filled == {input_from/output_from} from filled_io
def test_a_failed_step_is_an_error_with_its_message(): ...
    # level == "ERROR", ok is False, output == {"error": "timeout after 30 s"}
def test_values_are_redacted(): ...
    # a tool input {"email": "a@b.com"} comes out equal to normalize.redact({"email": "a@b.com"})
def test_a_cut_value_says_so(): ...
    # output {"_truncated": "abc"} -> "abc … [cut: Hoist Traces keeps 16,000 characters]", truncated is True
def test_the_turn_header(): ...
    # turn_id, langfuse_trace_id == langfuse_export.otel_trace_id(turn_id), session_id, email, route, models,
    # latency_ms, cost_usd, total_tokens == input+output+cache_read, ok, error
def test_a_purged_turn_is_a_root_alone(): ...
    # build_tree(turn, []) -> one node "chat", id "root", input == question, output == answer, purged True
```

- [ ] **Step 2: Run them to see them fail**

Run (Test command, `$T=tests/test_trace_lab_trace_tree.py`). Expected: collection error, `No module named 'trace_lab.trace_tree'`.

- [ ] **Step 3: Implement `build_tree` in `trace_tree.py`**

Order: start from `_ordered(spans)`; root = first `kind == "turn"` span, else synthesized `{"id": "root", ...}` from the turn row; parent = span's `parent_id` when it is a known span, else the root; walk depth-first, children sorted by `seq`. Types: `"generation"` if `_is_generation(s)` else `OBSERVATION_TYPES.get(kind, "span")`; the root is `"agent"`, named `TRACE_NAME`, input = question, output = answer. Input/output/filled: `filled_io(turn, ordered)`, then `normalize.redact`. `truncated`: the raw input or output is a `{"_truncated": str}` dict. `cost_usd`, `model` and token fields are copied only for generations. Module docstring: dated 9 Nov 2026; read-only and why the rules come from the export.

- [ ] **Step 4: Run them to see them pass.** Same command. Expected: `9 passed`.

- [ ] **Step 5: Checkpoint.** No commit (Hussain's rule).

---

### Task 2: Endpoint with the question page's access

**Files:**
- Modify: `apps/backend/cafm-connector-service-final/svc-trace-lab/trace_lab/trace_tree.py` (add the loader)
- Modify: `apps/backend/cafm-connector-service-final/svc-trace-lab/trace_lab/routes_runs.py` (one route + one line in the module docstring's route list)
- Test: `apps/backend/cafm-connector-service-final/svc-trace-lab/tests/test_trace_lab_trace_tree_sql.py`

**Interfaces:**
- Consumes: `build_tree` (Task 1); `runs._question_row(conn, m, code)`; `labelling.require_active`, `labelling.for_org`, `labelling.company_view`; `conversations._read`, `_lab_read`, `scope_sql`, `TURNS`, `SPANS`, `is_golden_turn`, `is_scenario_turn`.
- Produces: `async def question_tree(m: labelling.Member, code: str, *, turn_id: str | None = None) -> dict[str, Any]` returning `{"turn_id": str | None, "missing": bool, "tree": dict | None}`. Route `GET /lab/q/{code}/tree?turn=` (max_length 200), dependency `_wide`, response `{"ok": True, **out}`.

- [ ] **Step 1: Write the failing tests** (scratch Postgres; use the `_turn` pattern from `tests/test_trace_lab_runs_sql.py`, plus inserting 3 spans into `plenum_cafm.agent_trace_spans`; `runs.sync_production(boss)` creates the runs).

```python
async def test_the_tree_of_a_questions_answer(main_scratch, lab_scratch): ...
    # question_tree(boss, "R1-1") -> missing False, tree.nodes names ["chat", ...] for "turn-a1"
async def test_the_tree_follows_the_question_pages_access(main_scratch, lab_scratch): ...
    # admin of ORG_A: question_tree(admin, <ORG_B question>) raises NotFound
    # turn="not-linked" falls back to the newest linked turn (same as runs.get_question)
    # a question with no linked answer -> {"turn_id": None, "missing": False, "tree": None}
async def test_a_turn_that_can_no_longer_be_read_is_missing(main_scratch, lab_scratch): ...
    # link a turn id with no agent_trace_turns row -> {"turn_id": tid, "missing": True, "tree": None}
def test_the_tree_never_writes(): ...
    # ast-strip docstrings of trace_tree.py; no INSERT/UPDATE/DELETE/TRUNCATE/ALTER/CREATE in the code;
    # every execute() sits inside conversations._read() or _lab_read()
```

- [ ] **Step 2: Run them to see them fail.** `$T=tests/test_trace_lab_trace_tree_sql.py`. Expected: `AttributeError: ... has no attribute 'question_tree'`.

- [ ] **Step 3: Implement `question_tree` and the route**

Mirror `runs.get_question` up to choosing the turn (`require_active`, `_question_row` and links in one `db.lab_engine().connect()`, then pick = linked turn equal to `turn_id`, else the newest link, else return `tree: None`). Read the raw rows. For a golden or scenario turn: `SELECT turn FROM tracelab.evidence_turns WHERE turn_id = :t AND organization_id = CAST(:org AS uuid)` and `SELECT span FROM tracelab.evidence_spans WHERE turn_id = :t ORDER BY seq`, inside `_lab_read()`. Otherwise use `scope_sql(labelling.company_view(mm.principal, mm.org), mm.org)` and `SELECT * FROM {TURNS} tt WHERE {scope} AND tt.turn_id = :tid`, then `SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq`, inside `_read()`. No row means `missing: True`. Route: add `@router.get("/lab/q/{code}/tree")` right after `lab_question`.

- [ ] **Step 4: Run Task 1 + Task 2 tests and the existing runs tests.** `$T="tests/test_trace_lab_trace_tree.py tests/test_trace_lab_trace_tree_sql.py tests/test_trace_lab_runs.py tests/test_trace_lab_runs_sql.py tests/test_trace_lab_golden_rule.py"`. Expected: all pass.

- [ ] **Step 5: Checkpoint.** No commit.

---

### Task 3: Frontend tree helpers (pure)

**Files:**
- Create: `apps/frontend/src/lab/logic/traceTree.js`
- Test: `apps/frontend/src/lab/__tests__/traceTree.test.mjs`

**Interfaces:**
- Consumes: the node list from Task 2 (`id, parent_id, depth, type, name, started_at, ended_at, latency_ms, cost_usd, ...`).
- Produces (all named exports):
  - `visibleRows(nodes, collapsed: Set<string>, query: string) -> Array<{node, depth, hasKids, open}>`. A query keeps matching nodes and their ancestors, case-insensitive on `name`.
  - `timelineBars(nodes, turn) -> Array<{id, depth, leftPct, widthPct}>`, with `0 <= leftPct`, `leftPct + widthPct <= 100` and `widthPct >= 0.5`.
  - `graphOf(nodes) -> {nodes: Array<{key, type, name, count, col, row, firstId}>, edges: Array<{from, to, count}>}`. One graph node per `type:name` for agents, generations and tools (plain spans are skipped and their children are linked through). The root is `chat`. Edges follow start order within the same parent. No self-edges.
  - `fmtTime(ms)`, `fmtCost(usd)`, `fmtTotalCost(usd)`, `fmtTokens(n)`, `fmtStart(iso)`, using the formats under Global Constraints. Each returns `''` for null.

- [ ] **Step 1: Write the failing tests**

```js
test('rows: depth-first, collapse hides children, search keeps ancestors', ...)
test('timeline bars stay inside the axis', ...)   // null started_at, ended_at < started_at, zero-length turn
test('graph: one node per agent/model/tool name, edges in run order, no self edges', ...)
test('formats match Langfuse', () => {
  assert.equal(fmtTime(10), '0.01s'); assert.equal(fmtTime(2710), '2.71s'); assert.equal(fmtTime(62000), '1m 02s');
  assert.equal(fmtCost(0.00769), '$0.00769'); assert.equal(fmtCost(0.034392), '$0.034392');
  assert.equal(fmtTotalCost(0.11312), '$0.113'); assert.equal(fmtTokens(50210), '50k tokens');
  assert.equal(fmtTokens(2210), '2.2k tokens'); assert.equal(fmtTokens(950), '950 tokens'); assert.equal(fmtCost(null), '');
});
```

- [ ] **Step 2: Run them to see them fail.** Expected: `Cannot find module .../traceTree.js`.
- [ ] **Step 3: Implement the exports in `logic/traceTree.js`.**
- [ ] **Step 4: Run them to see them pass.** Expected: `pass 4`.
- [ ] **Step 5: Checkpoint.** No commit.

---

### Task 4: Safe Markdown for Formatted mode

**Files:**
- Create: `apps/frontend/src/lab/logic/miniMarkdown.js`
- Test: `apps/frontend/src/lab/__tests__/miniMarkdown.test.mjs`

**Interfaces:**
- Produces: `parseMarkdown(text: string) -> Array<Block>`, where Block is one of `{t:'h', level, inline}`, `{t:'p', inline}`, `{t:'ul', items: inline[]}`, `{t:'ol', items: inline[]}`, `{t:'table', head: inline[], rows: inline[][]}` or `{t:'code', text}`. `inline` is an array of `{t:'text'|'b'|'i'|'code', text}`. It returns data, never HTML. TraceTab renders it as React elements.

- [ ] **Step 1: Write the failing tests**

```js
test('headings, bold, lists and a pipe table', ...)   // "**13 open**", "| WO | ISSUE |\n|---|---|\n| WO-1 | x |"
test('html in markdown stays text', ...)              // '<script>x</script>' -> {t:'p', inline:[{t:'text', text:'<script>x</script>'}]}
test('a fenced block is code', ...)
```

- [ ] **Step 2: Run them to see them fail. Step 3: Implement `parseMarkdown`. Step 4: Run them to see them pass (`pass 3`). Step 5: Checkpoint.**

---

### Task 5: TraceTab view

**Files:**
- Create: `apps/frontend/src/lab/TraceTab.jsx`, `apps/frontend/src/lab/traceTab.css`
- Modify: `apps/frontend/src/lab/labApi.js` (add `qTree`)
- Test: `apps/frontend/src/lab/__tests__/traceTab.test.mjs`

**Interfaces:**
- Consumes: Tasks 3 and 4; `labApi.qTree(code: string, turn?: string) -> Promise<{turn_id, missing, tree}>` = `call('GET', '/lab/q/' + enc(code) + '/tree', { query: turn ? { turn } : {} })`.
- Produces:
  - `export function TraceView({ tree, view = 'tree', selectedId, pane = 'preview', mode = 'formatted' })`: the header, the Tree / Timeline / Graph switch, and the detail pane. It holds its own state, seeded from these props so tests can render any state. The root is selected by default.
  - `export default function TraceTab({ code, turnId })`: loads with `labApi.qTree` on mount and when `turnId` changes. States: "Reading…", error (`lab-error`, role alert), missing ("This answer can no longer be read."), purged ("Hoist Traces no longer keeps this answer's steps (kept 90 days)."), else `TraceView`.
- Above the tree: a name search box (`visibleRows` query) and chevrons that collapse a step's children.
- Icons (`lucide-react`): agent `Bot`, generation `Sparkles`, tool `Wrench`, span `ArrowLeftRight`. Class prefix `lab-tt-`. Colours come from the existing lab CSS variables. Light theme only.

- [ ] **Step 1: Write the failing tests** (renderToStaticMarkup with a fixed `tree` fixture: 1 root, router, an agent with model + tool + 2 db steps, 1 failed step)

```js
test('header shows time, cost, tokens, session and user', ...)   // '18.79s', '$0.113', '50k tokens', email, route + model chips
test('tree lists every step with its time and cost, failed step marked', ...)  // names in order; 'lab-tt-error' on the failed row
test("only the selected step's input and output are rendered", ...)            // root input/output present; a child's input text absent
test('selecting a step shows its input, output and metadata', ...)             // selectedId=<tool>, pane='metadata': span id, seq, input_from, langfuse trace id
test('raw mode shows JSON', ...)                                               // mode='raw' -> '"rows"' JSON text in a <pre>
test('timeline and graph views render', ...)                                   // view='timeline' -> bars with style left/width; view='graph' -> <svg> with node names
test('formatted output renders the markdown table', ...)                       // root output with a pipe table -> <table> with 'WO'
```

- [ ] **Step 2: Run them to see them fail. Step 3: Implement `TraceView`, `TraceTab`, `traceTab.css` and `labApi.qTree`. Step 4: Run them to see them pass (`pass 7`), plus `runs.test.mjs` unchanged. Step 5: Checkpoint.**

---

### Task 6: Answer | Trace switch on the question page

**Files:**
- Modify: `apps/frontend/src/lab/QuestionPage.jsx` (`QuestionView` only: tab bar + Trace panel; nothing inside the existing answer markup changes)
- Test: `apps/frontend/src/lab/__tests__/traceTab.test.mjs` (add cases)

**Interfaces:**
- Consumes: `TraceTab` (Task 5).
- Produces: `QuestionView` takes an optional prop `tab` ('answer' | 'trace'). Without it, the initial tab is the URL `qtab` if it is `trace`, else localStorage `tracelab.qtab.v1`, else `'answer'`. Changing tab stores the choice and updates `qtab` in the address with `history.replaceState`, keeping the other parameters. The tab bar (`role="tablist"`, two `role="tab"` buttons, labels `Answer` and `Trace`) shows only when there is an active, readable answer. It sits after the supporting-answers row.

- [ ] **Step 1: Write the failing tests**

```js
test('the answer tab is the default and renders exactly the existing view', ...)  // markup of QuestionView(DATA) without tab === markup with tab='answer'; contains the block list
test('the trace tab shows the trace panel instead of the blocks', ...)            // tab='trace' -> 'lab-tt' container, no 'lab-tl' list, header/question/expected answer still present
test('no tab bar without an answer', ...)
```

- [ ] **Step 2: Run them to see them fail. Step 3: Implement it in `QuestionView`. Step 4: Run the whole lab suite (`node --test src/lab/__tests__/*.test.mjs`): everything passes, with the existing tests unchanged. Step 5: Checkpoint.**

---

### Task 7: Rebuild locally and verify against Langfuse

**Files:** none changed.

- [ ] **Step 1: Check for peer sessions.** Run ListAgents, then `git status --short` and compare against the files this plan touches. Another session's half-done work in `svc-trace-lab` would ship in the rebuild. If any is there, stop and ask.
- [ ] **Step 2: Full suites.** Run the backend Trace Lab suite (`$T=tests`). Expected: everything passes except the 5 known batch-generator failures (`test_a_batch_is_generated_for_one_instance` ×4, `test_the_scenarios_route_generates_for_one_space` ×1). Run the frontend with `cd apps/frontend && npm test`. Expected: all pass.
- [ ] **Step 3: Rebuild Trace Lab** (from the repo root): `docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml -f ~/Desktop/hoist/tracelab-local.override.yml up -d --build --no-deps trace-lab`, then `docker exec hoistra_plenum-gateway-app-1 nginx -s reload`. Expected: the container is up, and `docker exec hoistra_plenum-trace-lab-1 grep -c question_tree /app/svc-trace-lab/trace_lab/trace_tree.py` is ≥ 1.
- [ ] **Step 4: Click-test** with playwright-core and the installed Chrome (recipe in memory `hoistra-ui-verification`). Open `http://localhost:5174/lab/?run=R35&q=R35-6`, click **Trace**, take screenshots of the Tree, Timeline, Graph, a tool's Preview and Metadata, and Raw mode. Reload and confirm Trace opens again (remembered).
- [ ] **Step 5: Parity check.** For the same turn in the local Langfuse (`lf-check`, ClickHouse `events_full FINAL`), node count, names and parents match the tab's `nodes` (excluding the root). Report any difference.
- [ ] **Step 6: Report to Hussain** with the screenshots and what changed. No commit.
