# Trace Lab Eval Pipeline (Workstream B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace Lab stores every scenario run as a Langfuse-shaped Session → Trace → Span/Generation → Score tree, generates and versions scenarios with expected outcomes against spec.md, scores each trace at three levels (logic, UX, domain), lets people annotate, filter and compare run tags, and pushes local results to the dev Trace Lab database.

**Architecture:** A new, self-contained "eval store" inside Trace Lab's own database (migration 0014): eight append-only tables named as Langfuse names them (`sessions`, `traces`, `observations`, `scores`) plus `run_tags`, `scenarios`, `spec_sections`, `sync_log`. One writer (`eval_store.write_session`) fills it from two sources: Trace Lab's in-process orchestrator (the batch runner) and chat turns already recorded in the main database (read-only backfill). The tree is built with the existing `trace_tree.build_tree`, so the stored spans are exactly what Langfuse and today's Trace tab show. Because every table is append-only, the local → dev sync is "insert the rows the other side does not have, by primary key", which cannot duplicate or overwrite. Existing Trace Lab screens (Runs, Loop, Notes) and every table before 0014 are left as they are.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + asyncpg, Postgres 16 (schema `tracelab`), React 18 + Vite (`apps/frontend/src/lab`), `node --test`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-11-orchestrator-and-trace-lab-task-plan.md` (Workstream B, plus Priority 0 because B's runner and run tags are what P0 runs on).

Paths used below: `TL` = `apps/backend/cafm-connector-service-final/svc-trace-lab`, `FE` = `apps/frontend/src/lab`.

> **Scope change, 11 Nov 2026 (Hussain, after the plan was written): "only do this task, nothing else" = Workstream B1-B5.**
> Not built: the batch runner and its command line (Task 4's `run_step` / `batch run`, which is Priority 0.3) and fault
> injection (Task 9). Task 4 became the routes only. Sessions enter the store by reading recorded chat conversations
> (`eval_store.ingest_from_main`). Task 8 has no grounding step. Task 13 was reduced to the shared trace view; the wording
> of the older Runs and Question pages is still decision D6. What was built, with every ruling, is in the ledger kept
> while executing (`.superpowers/sdd/2026-11-11-trace-lab-eval-pipeline/progress.md`, git-ignored).

## Global Constraints

- **Golden rule:** everything lives in `TL/` and `FE/`. Nothing in `svc-deepagents`, `svc-operations-intelligence`, `svc-udr` or the main app's screens is edited. Existing code is used by import only. Existing Trace Lab files this plan touches, and nothing else: `TL/trace_lab/app.py` (one router), `TL/trace_lab/proxy.py` (one fault hook), `TL/trace_lab/migrate.py` (`SHIPPED_CHECKSUMS` pin after 0014 ships), `TL/migrations/catalog.json` (regenerated), `TL/tests/test_trace_lab_migrate_sql.py` (table list), `TL/ops/README.md`, `FE/LabApp.jsx` (one tab), `FE/labApi.js` (new calls), `FE/__tests__/labScreens.test.mjs`.
- **No database writes outside a throwaway scratch Postgres.** Tests use `TRACE_LAB_TEST_DB_URL` (database `tracelab_scratch` on localhost). Applying 0014 to the Azure dev Trace Lab database, and any real sync run, need Hussain's explicit yes each time.
- **No commit, push, merge or stash unless Hussain names it.** Every "Commit" step below means: stage the listed files and ask.
- **Migrations:** one new numbered file, first statement exactly `SET LOCAL lock_timeout = '3s';`, objects only in schema `tracelab`, no `IF NOT EXISTS`, no views, no `GRANT`, one-line `COMMENT` strings with no apostrophes (`migrate.lint` enforces all of this). Every new table is append-only: two triggers calling `tracelab.refuse_change()` (row UPDATE/DELETE, and TRUNCATE), as `0012_scenarios.sql` does.
- **Langfuse names** in the UI and the new schema: Session, Trace, Span, Generation, Score. Observation `type` values are the ones `trace_tree.build_tree` already emits: `span`, `generation`, `agent`, `tool`.
- **Exact values from the spec:** the baseline run tag is `baseline_v0`; the batch is 250 scenarios; answer formats are `success`, `partial_success`, `denial`, `failure`; evidence states are `zero_results`, `not_recorded`, `unavailable`, `tool_failed`; score levels are `logic`, `ux`, `domain`; scenario types are `single_domain`, `multi_domain`, `multi_turn`, `failure`.
- **DEV_DB_URL** is the dev instance's *Trace Lab* database (schema `tracelab`), never the Hoistra application database. Its value is read from the environment at call time and never logged, stored or echoed; logs show `host/database` only.
- **Real data or nothing** in the UI: no sample rows; an empty state says what is missing. No internal folder or table names in UI copy (the `noInternalNames` guard test scans `src/**`).
- **Test commands.** Backend, from `TL/` inside the Trace Lab test image on the scratch Postgres network: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:<pw>@localhost:5432/tracelab_scratch python -m pytest tests/<file> -v` (SQL tests skip without the variable; pure tests run with plain `python -m pytest`). Frontend, from `apps/frontend`: `node --test "src/lab/__tests__/<file>.test.mjs"`.

## Review Focus

1. **The batch process dies between two turns of a multi-turn scenario.** Expected: nothing of that scenario is stored, and the next run asks it again once; no half session, no duplicate. Test in Task 4.
2. **Sync is interrupted halfway, or run twice.** Expected: the second run finishes the job and reports zero new rows on the third; never a duplicate, never a foreign-key failure from table order. Test in Task 6.
3. **DEV_DB_URL points at the local database, at a database with no Trace Lab schema, or at one on a different migration.** Expected: refused before any write, with a message that names the problem but not the URL. Test in Task 6.
4. **A trace from today's orchestrator (baseline_v0) has no structured plan, retry or scope spans.** Expected: those logic checks say `not_observable`; they do not fail the trace and do not crash the evaluator. Test in Task 10.
5. **spec.md is edited after expected outcomes were approved.** Expected: only scenarios citing a changed section are flagged out of date; the old expected outcome and old scores stay as they were. Test in Task 7.

---

## Decisions this plan assumes (confirm before Task 1)

| # | Decision | Assumed |
|---|---|---|
| D1 | Langfuse names "in the DB schema" | New tables with those names. The older `lab_*` tables are not renamed: their migrations are pinned by checksum and the Azure database already has them. |
| D2 | Sync transport | Database to database, as the spec says, but only between two Trace Lab databases and insert-only. The VPS needs a database login that can only `SELECT`/`INSERT` on schema `tracelab` of the dev Trace Lab database (Bala or a DBA creates it). |
| D3 | Where runs execute | Inside local Trace Lab, through its own in-process orchestrator (the same isolated path Synthetic and golden runs use: reads pass, writes are refused). One question at a time per process, so 250 scenarios take roughly 6 to 8 hours. |
| D4 | Branch | Built on `eval-2`, pushed to `Hoistra_Frontend`. The VPS must run `Hoistra_Frontend`: `main` has no Trace Lab. |
| D5 | Identities for authorization-failure scenarios | The runner can ask as a second signed-in user (`X-Ask-As`). Test users per role exist only on `main` today, so until Hoistra has them these scenarios run as the operator and their logic scope check is what catches a leak. |
| D6 | Wording pass on the older Trace Lab screens (Task 13) | Separate yes/no; everything else works without it. |

**Tonight's baseline, before any of this is built.** B's runner does not exist yet. If the 250 are asked tonight through the local chat, each conversation must use the session id `scn__baseline_v0__<scenario_id>__1` (one session per scenario, follow-ups in the same session). Task 2's backfill reads exactly that pattern from the main database and files the run under `baseline_v0` with its scenario ids, so the baseline is not lost.

## Interface with Workstream A (Bala)

B reads; A writes. Agree these two contracts once, then neither side blocks the other.

**spec.md sections.** B derives a section id from each heading by lower-casing and replacing every run of non-alphanumerics with `_`. A must give each area its own heading with these titles (ids in brackets): Final synthesis (`final_synthesis`), Multi-read planning (`multi_read_planning`), Dependency order (`dependency_order`), Context passing (`context_passing`), Scope preservation (`scope_preservation`), Retry rules (`retry_rules`), Partial failure (`partial_failure`), Total failure (`total_failure`), Authorization failure (`authorization_failure`), Missing evidence (`missing_evidence`), Evidence conflicts (`evidence_conflicts`), Multi-turn continuity (`multi_turn_continuity`), Answer completeness (`answer_completeness`), Output format (`output_format`), Few-shot examples (`few_shot_examples`). B needs the file's path in the repo (default assumed: `svc-deepagents/skills/spec/SPEC.md`).

**Span names B looks for** (all optional; absent means `not_observable`, which is the baseline's honest result):

| Span name | Type | What B reads |
|---|---|---|
| `plan` | span | `output.calls`: list of `{call_id, tool, params, depends_on}` |
| the tool's own name | tool | `input` (params), `output`, start and end time, `output.status` when the tool returns one of the four evidence states |
| `retry_attempt` | span, child of the tool span | `input` (params of that attempt), `metadata.reason` |
| `context_passing` | span | `metadata`: `{from_call, field, to_call, param}` |
| `scope_check` | span | `output`: `{ok, violations}` |
| `final_synthesis` | generation | model, input, output, tokens |

After A lands, the Trace Lab image must be rebuilt (it bakes its own copy of `svc-deepagents`) before the post-fix run.

## Spec → task map

| Spec item | Tasks |
|---|---|
| B1.1 session + turn tabs | 1, 2, 4, 5 |
| B1.2 linked child spans, full tree | 2, 5 (and A's span contract) |
| B1.3 Langfuse terms in UI and schema | 1, 5, 13 |
| B2.1 scenarios per spec area, tagged | 3, 8 |
| B2.2 expected outcome stored with each scenario | 3, 8 |
| B2.3 versioned with spec.md, stale flag | 7 |
| B3 logic / UX / domain | 10, 11, 12 |
| B4.1 score, verdict, comment per trace per level | 12 |
| B4.2 filters and side-by-side compare | 12 |
| B5.1–B5.4 sync | 6 |
| P0.2 scenario file with stable ids | 3, 8 |
| P0.3 resumable batch, no duplicates | 4 |
| P0.4 run tag | 1, 4 |

## File structure

New backend files in `TL/trace_lab/`:

| File | Responsibility |
|---|---|
| `eval_store.py` | Session ids, turning a turn + spans into trace and observation rows, the one writer, the backfill from the main database, session list and detail reads |
| `scenario_file.py` | The scenario JSON shape, validation, JSONL import and export, versioned storage, approval |
| `spec_doc.py` | Parsing spec.md into sections with hashes, loading them, deciding what is out of date |
| `scenario_gen.py` | Deterministic generation of a batch per spec area with expected-outcome skeletons and grounding |
| `faults.py` | The injected-fault holder for failure scenarios |
| `batch.py` | Running pending scenarios for a run tag, progress, and the command-line client |
| `eval_logic.py` | The automatic logic checks (pure functions) |
| `eval_judges.py` | UX and domain judges and the evaluate loop |
| `scores.py` | Human annotation, "current score" resolution, compare |
| `sync_push.py` | Preflight, delta push, status, command-line entry |
| `routes_eval.py` | Every `/eval/*` route |

New frontend files in `FE/`: `logic/sessions.js` (pure helpers), `Sessions.jsx` (list, filters, sync), `SessionPage.jsx` (scenario, turn tabs, tree, scores), `Compare.jsx`, `sessions.css`, `__tests__/sessions.test.mjs`. Task 13 adds `logic/terms.js` and `__tests__/terms.test.mjs`.

Milestones, each usable on its own: **M1** Tasks 1–5 (a scenario file runs under a run tag and opens as a session with turn tabs). **M2** Task 6 (local runs appear on dev). **M3** Tasks 7–9 (spec versioning, generation, failure injection). **M4** Tasks 10–12 (three-level evaluation, annotation, compare). **M5** Task 13.

---

### Task 1: Migration 0014, the eval store

**Files:**
- Create: `TL/migrations/0014_eval_store.sql`
- Modify: `TL/migrations/catalog.json` (regenerated, never hand-edited), `TL/tests/test_trace_lab_migrate_sql.py` (expected table list)
- Test: `TL/tests/test_trace_lab_eval_schema_sql.py`

**Interfaces:**
- Produces: tables `tracelab.run_tags`, `spec_sections`, `scenarios`, `sessions`, `traces`, `observations`, `scores`, `sync_log`. Every later task relies on these columns.

- [ ] **Step 1: Write the failing tests**

```python
pytestmark = requires_scratch

def test_0014_lints_clean():
    f = next(x for x in migrate.files() if x.name == "0014_eval_store.sql")
    assert migrate.lint(f) == []

async def test_eval_tables_are_append_only(lab_scratch):
    async with lab_scratch.begin() as c:
        await c.execute(text("INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES ('baseline_v0','baseline','test')"))
    for sql in ("UPDATE tracelab.run_tags SET note = 'x'", "DELETE FROM tracelab.run_tags"):
        with pytest.raises(DBAPIError):
            async with lab_scratch.begin() as c:
                await c.execute(text(sql))

async def test_checks_hold_the_spec_values(lab_scratch):
    # each must raise: tag containing '__', scores.name 'style', scenarios.type 'other',
    # traces.status 'refused', an ANNOTATION score with no author_id
    ...

def test_catalog_is_version_14_with_the_eight_tables():
    c = migrate.expected_catalog()
    assert c["version"] == 14
    assert {"run_tags","spec_sections","scenarios","sessions","traces","observations","scores","sync_log"} <= set(c["tables"])
```

- [ ] **Step 2: Run them and see them fail** (`0014_eval_store.sql` does not exist).

- [ ] **Step 3: Write `0014_eval_store.sql`** with exactly these tables. Each also gets the two append-only triggers, a `created_at` index named `ix_tl_<table>_created`, and a one-line `COMMENT ON TABLE`.

```sql
CREATE TABLE tracelab.run_tags (
    tag text PRIMARY KEY CHECK (tag ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$' AND tag !~ '__'),
    kind text NOT NULL CHECK (kind IN ('baseline', 'post_fix', 'adhoc')),
    spec_version text,
    code_revision text CHECK (code_revision IS NULL OR char_length(code_revision) <= 64),
    origin text NOT NULL CHECK (char_length(origin) BETWEEN 1 AND 64),
    note text CHECK (note IS NULL OR char_length(note) <= 500),
    created_by uuid, created_by_email text, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE tracelab.spec_sections (
    spec_version text NOT NULL, section_id text NOT NULL, title text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'), body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (spec_version, section_id));

CREATE TABLE tracelab.scenarios (
    scenario_id text NOT NULL CHECK (scenario_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$' AND scenario_id !~ '__'),
    version integer NOT NULL CHECK (version >= 1),
    organization_id uuid,
    type text NOT NULL CHECK (type IN ('single_domain', 'multi_domain', 'multi_turn', 'failure')),
    spec_sections text[] NOT NULL CHECK (cardinality(spec_sections) >= 1),
    role text CHECK (role IS NULL OR char_length(role) <= 64),
    space text CHECK (space IS NULL OR space IN ('ops', 'comp', 'assets', 'full')),
    turns jsonb NOT NULL CHECK (jsonb_typeof(turns) = 'array' AND jsonb_array_length(turns) BETWEEN 1 AND 6),
    scope jsonb NOT NULL DEFAULT '{}', faults jsonb NOT NULL DEFAULT '[]',
    grounded_on jsonb NOT NULL DEFAULT '[]', domain_rules jsonb NOT NULL DEFAULT '[]',
    spec_version text, spec_hashes jsonb NOT NULL DEFAULT '{}',
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('draft', 'approved', 'retired')),
    source text NOT NULL CHECK (source IN ('generator', 'import', 'manual')),
    created_by uuid, created_by_email text, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, version));

CREATE TABLE tracelab.sessions (
    id text PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 120),
    organization_id uuid NOT NULL,
    run_tag text REFERENCES tracelab.run_tags (tag),
    scenario_id text, scenario_version integer,
    attempt integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    origin text NOT NULL CHECK (char_length(origin) BETWEEN 1 AND 64),
    source text NOT NULL CHECK (source IN ('runner', 'chat')),
    user_id uuid, email text, role text, space text,
    started_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_sessions_scenario CHECK ((scenario_id IS NULL) = (scenario_version IS NULL)),
    CONSTRAINT fk_tl_sessions_scenario FOREIGN KEY (scenario_id, scenario_version)
        REFERENCES tracelab.scenarios (scenario_id, version));

CREATE TABLE tracelab.traces (
    id text PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 200),
    session_id text NOT NULL REFERENCES tracelab.sessions (id),
    turn_index integer NOT NULL CHECK (turn_index >= 1),
    organization_id uuid NOT NULL,
    name text NOT NULL DEFAULT 'chat',
    input text, output text,
    status text NOT NULL CHECK (status IN ('ok', 'error', 'timeout', 'interrupted')),
    header jsonb NOT NULL CHECK (jsonb_typeof(header) = 'object'),
    metadata jsonb NOT NULL DEFAULT '{}',
    started_at timestamptz, ended_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_traces_turn UNIQUE (session_id, turn_index));

CREATE TABLE tracelab.observations (
    trace_id text NOT NULL REFERENCES tracelab.traces (id), id text NOT NULL,
    parent_observation_id text, seq integer, depth integer NOT NULL CHECK (depth >= 0),
    type text NOT NULL CHECK (type IN ('span', 'generation', 'agent', 'tool', 'event')),
    name text NOT NULL, agent text, model text,
    start_time timestamptz, end_time timestamptz, latency_ms integer,
    level text NOT NULL CHECK (level IN ('DEFAULT', 'WARNING', 'ERROR')), status_message text,
    input jsonb, output jsonb, usage jsonb, cost_usd numeric(12,6),
    metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, id));

CREATE TABLE tracelab.scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id text NOT NULL REFERENCES tracelab.traces (id),
    session_id text NOT NULL REFERENCES tracelab.sessions (id),
    observation_id text,
    name text NOT NULL CHECK (name IN ('logic', 'ux', 'domain')),
    source text NOT NULL CHECK (source IN ('ANNOTATION', 'EVAL')),
    string_value text NOT NULL CHECK (string_value IN ('pass', 'fail', 'not_evaluated')),
    value numeric(4,3) CHECK (value IS NULL OR value BETWEEN 0 AND 1),
    comment text CHECK (comment IS NULL OR char_length(comment) <= 4000),
    details jsonb NOT NULL DEFAULT '{}', evaluator text,
    author_id uuid, author_email text, author_role text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tl_scores_author CHECK ((source = 'ANNOTATION') = (author_id IS NOT NULL)));

CREATE TABLE tracelab.sync_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    peer text NOT NULL, dry_run boolean NOT NULL DEFAULT false,
    status text NOT NULL CHECK (status IN ('done', 'failed', 'refused')),
    watermark timestamptz, pushed jsonb NOT NULL DEFAULT '{}',
    error text CHECK (error IS NULL OR length(error) <= 2000),
    started_by uuid, started_by_email text,
    started_at timestamptz NOT NULL, finished_at timestamptz NOT NULL DEFAULT now());
```

Further indexes: `ix_tl_sessions_tag (run_tag, scenario_id, attempt DESC)`, `ix_tl_sessions_org (organization_id, started_at DESC)`, `ix_tl_scores_trace (trace_id, name, created_at DESC)`.

- [ ] **Step 4: Regenerate the catalog** on the scratch database: `python -m trace_lab.migrate snapshot --db-url "$TRACE_LAB_TEST_DB_URL"`; add the eight names to the table list in `tests/test_trace_lab_migrate_sql.py`.
- [ ] **Step 5: Run** `python -m pytest tests/test_trace_lab_eval_schema_sql.py tests/test_trace_lab_migrate_sql.py -v`. Expected: all pass.
- [ ] **Step 6: Commit** (ask first): `feat(trace-lab): 0014 eval store - sessions, traces, observations, scores`.

---

### Task 2: `eval_store.py`, one writer and the reads

**Files:**
- Create: `TL/trace_lab/eval_store.py`
- Test: `TL/tests/test_trace_lab_eval_store.py` (pure), `TL/tests/test_trace_lab_eval_store_sql.py`

**Interfaces:**
- Consumes: `trace_tree.build_tree(turn, spans) -> {"turn", "nodes", "purged"}`; `conversations._read()`, `conversations.TURNS`, `conversations.SPANS`; `db.lab_engine()`; `labelling.Member`.
- Produces:

```python
ORIGIN: str                      # env TRACE_LAB_ORIGIN, default socket.gethostname()[:64]
def session_id_for(tag: str, scenario_id: str, attempt: int) -> str      # "scn__{tag}__{scenario_id}__{attempt}"
def parse_session_id(session_id: str) -> tuple[str, str, int] | None     # None when it is not a scenario session

@dataclass(frozen=True)
class SessionMeta:
    id: str; organization_id: str; run_tag: str | None; scenario_id: str | None
    scenario_version: int | None; attempt: int; origin: str; source: str        # 'runner' | 'chat'
    user_id: str | None; email: str | None; role: str | None; space: str | None; started_at: datetime

def rows_for(turn: Mapping, spans: Sequence[Mapping], *, status: str | None = None,
             extra_metadata: Mapping | None = None) -> tuple[dict, list[dict]]   # (trace row, observation rows); pure
def node_of(row: Mapping) -> dict                                                # observation row -> the node TraceView reads
async def write_session(conn, meta: SessionMeta,
                        traces: Sequence[tuple[dict, list[dict]]]) -> dict[str, int]   # rows_for(...) results, in turn order
async def ingest_from_main(*, since: datetime, limit: int = 500) -> dict[str, int]
async def list_sessions(m: Member, *, run_tag=None, type=None, status=None, level=None, verdict=None,
                        needs_sme=False, stale=False, all_attempts=False, before=None, limit=50) -> dict
async def get_session(m: Member, session_id: str) -> dict
async def trace_tree_of(m: Member, trace_id: str) -> dict                        # {"turn": header, "nodes": [...], "purged": False}
```

Decisions the signatures leave open:
- `rows_for` stores every node of `build_tree`, the root included (depth 0, no parent). Node → row: `parent_id`→`parent_observation_id`, `started_at`/`ended_at`→`start_time`/`end_time`, token counts→`usage` (`{"input", "output", "cache_read"}`), `error`→`status_message`, `kind`/`filled`/`truncated`→`metadata`. The trace row's `header` is `build_tree(...)["turn"]`; `input`/`output` are the question and answer; `status` defaults to `ok` when `turn["ok"]` is not false, else `error`; `metadata` gets `answer_words` (whitespace-split word count of the answer) and `tools` (tool names in call order) merged with `extra_metadata`.
- `write_session` runs inside the caller's transaction, inserts session, traces (turn_index = position, from 1) and observations with `ON CONFLICT DO NOTHING`, and returns how many rows of each were actually inserted.
- `ingest_from_main` reads turns whose `session_id LIKE 'scn\_\_%'` and `started_at >= since` through the read-only main engine, groups them by session ordered by `started_at`, skips sessions whose id does not parse or whose run tag is unknown (it creates a missing run tag with kind `adhoc`, or `baseline` when the tag is `baseline_v0`), and calls `write_session` with `source='chat'`. A session whose scenario id is not in `tracelab.scenarios` is stored with `scenario_id` NULL and the parsed id in each trace's `metadata.scenario_ref`.
- A member sees a session when `m.all_companies` or `organization_id == m.org`. `list_sessions` shows the highest attempt per `(run_tag, scenario_id)` unless `all_attempts`. Session `status` is `failed` when any of its traces is not `ok` or has an `ERROR` observation, else `ok`. The filters `level`, `verdict`, `needs_sme` and `stale` are implemented in Task 12 and Task 7; until then they are accepted and ignored.

- [ ] **Step 1: Write the failing tests**

```python
def test_rows_for_keeps_the_tree():
    turn, spans = sample_turn()            # 1 turn span, agent, tool (failed), llm with tokens
    trace, obs = eval_store.rows_for(turn, spans)
    nodes = trace_tree.build_tree(turn, spans)["nodes"]
    assert [o["id"] for o in obs] == [n["id"] for n in nodes]
    assert obs[0]["depth"] == 0 and obs[0]["parent_observation_id"] is None
    assert next(o for o in obs if o["type"] == "tool")["level"] == "ERROR"
    assert next(o for o in obs if o["type"] == "generation")["usage"]["input"] > 0
    assert trace["metadata"]["answer_words"] == len(turn["answer"].split())

def test_node_of_round_trips_what_the_tree_view_reads():
    turn, spans = sample_turn()
    _, obs = eval_store.rows_for(turn, spans)
    want = trace_tree.build_tree(turn, spans)["nodes"]
    keys = ("id","parent_id","depth","type","name","agent","model","started_at","ended_at","level","input","output","error")
    assert [{k: n[k] for k in keys} for n in map(eval_store.node_of, obs)] == [{k: n[k] for k in keys} for n in want]

def test_session_ids():
    sid = eval_store.session_id_for("baseline_v0", "MT-014", 1)
    assert sid == "scn__baseline_v0__MT-014__1" and len(sid) <= 120
    assert eval_store.parse_session_id(sid) == ("baseline_v0", "MT-014", 1)
    assert eval_store.parse_session_id("scenario-ops-0123456789ab") is None

# SQL
async def test_write_session_twice_inserts_nothing_the_second_time(lab_scratch): ...
async def test_backfill_groups_two_turns_into_one_session(lab_scratch, main_scratch):
    # seed 2 turns + spans with session_id 'scn__baseline_v0__MT-001__1' and 1 ordinary chat turn
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert out == {"sessions": 1, "traces": 2, "observations": N}
    # traces have turn_index 1, 2 in started_at order; the ordinary turn is not ingested
async def test_list_shows_only_the_latest_attempt(lab_scratch): ...
async def test_other_company_cannot_open_the_session(lab_scratch): ...
```

- [ ] **Step 2: Run them and see them fail** (`ModuleNotFoundError: trace_lab.eval_store`).
- [ ] **Step 3: Implement `eval_store.py`** to the Interfaces block.
- [ ] **Step 4: Run** `python -m pytest tests/test_trace_lab_eval_store.py tests/test_trace_lab_eval_store_sql.py -v`. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): eval store writer, backfill and reads`.

---

### Task 3: `scenario_file.py`, the scenario shape, import, versions

**Files:**
- Create: `TL/trace_lab/scenario_file.py`, `TL/tests/fixtures/eval_scenarios_sample.jsonl` (4 lines, one per type)
- Test: `TL/tests/test_trace_lab_scenario_file.py`, `TL/tests/test_trace_lab_scenario_file_sql.py`

**Interfaces:**
- Produces:

```python
TYPES = ("single_domain", "multi_domain", "multi_turn", "failure")
ANSWER_FORMATS = ("success", "partial_success", "denial", "failure")
EVIDENCE_STATES = ("zero_results", "not_recorded", "unavailable", "tool_failed")
FAULTS = ("timeout", "http_500", "http_429", "http_403")
SPEC_SECTIONS = ("final_synthesis", "multi_read_planning", "dependency_order", "context_passing",
                 "scope_preservation", "retry_rules", "partial_failure", "total_failure",
                 "authorization_failure", "missing_evidence", "evidence_conflicts",
                 "multi_turn_continuity", "answer_completeness", "output_format", "few_shot_examples")
MAX_BYTES = 5_000_000

def validate(s: Mapping) -> list[str]                       # problems; empty means valid
def content_hash(s: Mapping) -> str                         # sha256 of the canonical JSON without status/source/audit fields
def parse_jsonl(data: bytes) -> tuple[list[dict], list[dict]]   # (scenarios, [{"line", "error"}])
async def save_scenarios(m: Member, scenarios: Sequence[Mapping], *, source: str, status: str = "draft") -> dict
        # {"added": n, "new_versions": n, "unchanged": n}
async def approve(m: Member, scenario_ids: Sequence[str]) -> dict        # {"approved": n}
async def latest(conn, *, scenario_ids=None, type=None, section=None, status=None) -> list[dict]
async def list_scenarios(m: Member, *, type=None, section=None, status=None, stale: bool = False) -> list[dict]
        # the member-facing read behind GET /eval/scenarios: latest version per id, each row with
        # "stale": [section ids] (always empty until Task 7 fills it)
def export_jsonl(rows: Sequence[Mapping]) -> bytes
```

The scenario JSON (one object per line in a `.jsonl` file):

```json
{"scenario_id": "MT-014", "type": "multi_turn",
 "spec_sections": ["multi_turn_continuity", "scope_preservation"],
 "role": "property_manager", "space": "ops",
 "scope": {"buildings": ["B-301"], "vendors": [], "period": {"from": "2026-10-01", "to": "2026-10-31"}},
 "faults": [{"service": "ops-intelligence", "path_prefix": "/api/compliance", "fault": "timeout", "times": 1}],
 "grounded_on": [{"table": "buildings", "pk": "…"}],
 "domain_rules": [{"id": "R-1", "text": "…"}],
 "turns": [
   {"message": "Show the overdue work orders at B-301.",
    "expected": {"plan": [{"call_id": "c1", "domain": "wo_engine", "tool": null, "params": {}, "depends_on": []}],
                 "failure_handling": null, "answer_format": "success",
                 "must_include": [], "must_not_include": [], "answer": null}},
   {"message": "For each one, who is the vendor?", "expected": {"plan": [], "failure_handling": null,
                 "answer_format": "success", "must_include": [], "must_not_include": [], "answer": null}}]}
```

Rules `validate` enforces: the id and type patterns of Task 1; every `spec_sections` entry is in `SPEC_SECTIONS`; 1 to 6 turns, each with a non-empty `message` (≤ 4000 chars) and an `expected` object; `answer_format` in `ANSWER_FORMATS`; `failure_handling` is null or `{"state": "partial_failure" | "total_failure" | "authorization_failure" | one of EVIDENCE_STATES, "failed": [call_id…]}`; each plan entry has a unique `call_id`, a `domain` or a `tool`, and `depends_on` naming only earlier-declared call ids of the same turn (so no cycles); every fault's `fault` is in `FAULTS`; `type == "multi_turn"` needs ≥ 2 turns; `type == "failure"` needs a non-null `failure_handling` in at least one turn. Call order is not a separate field: it is what `depends_on` implies.

`parse_jsonl` rejects the whole file when any line is invalid or two lines share a `scenario_id`, the way the golden upload does. `save_scenarios` needs a curator (`runs.require_curator`); it writes version 1 for a new id, the next version when `content_hash` differs from the latest, nothing when it is equal; it stamps `spec_version` and `spec_hashes` from `spec_doc.current_hashes` when a spec is loaded (Task 7; until then both stay empty). `approve` inserts the next version with the same content and status `approved`.

- [ ] **Step 1: Write the failing tests**

```python
def test_sample_file_is_valid():
    rows, errors = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    assert errors == [] and {r["type"] for r in rows} == set(scenario_file.TYPES)

@pytest.mark.parametrize("patch, needle", [
    ({"type": "other"}, "type"), ({"spec_sections": ["made_up"]}, "made_up"),
    ({"turns": []}, "turns"), ({"scenario_id": "a__b"}, "scenario_id"),
])
def test_bad_scenarios_are_named(patch, needle): ...
def test_answer_format_must_be_one_of_the_four(): ...
def test_depends_on_must_name_an_earlier_call(): ...
def test_duplicate_ids_reject_the_whole_file(): ...
def test_multi_turn_needs_two_turns_and_failure_needs_failure_handling(): ...

# SQL
async def test_importing_the_same_file_twice_changes_nothing(lab_scratch): ...   # second: unchanged == 4
async def test_a_changed_expected_makes_version_2_and_keeps_version_1(lab_scratch): ...
async def test_approve_adds_an_approved_version(lab_scratch): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement `scenario_file.py`.**
- [ ] **Step 4: Run** both test files. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): scenario files with expected outcomes, versioned`.

---

### Task 4: `batch.py` and the first `/eval` routes, resumable runs under a run tag

**Files:**
- Create: `TL/trace_lab/batch.py`, `TL/trace_lab/routes_eval.py`
- Modify: `TL/trace_lab/app.py` (import `routes_eval`, append `routes_eval.router` to `ROUTERS`), `TL/ops/README.md` (how to run a batch)
- Test: `TL/tests/test_trace_lab_batch_sql.py`, `TL/tests/test_trace_lab_eval_routes.py`

**Interfaces:**
- Consumes: `eval_store.session_id_for`, `write_session`, `rows_for`; `scenario_file.latest`; `capture.run_message(orchestrator, *, question, session_id, page_context, principal, organization_id, authorization, space)` → `CapturedTurn` (`.turn`, `.spans`, `.answer`, `.ended_by`, `.refused`, `.usd`); `orchestrator_host.assert_isolated`, `ensure_overlays_readonly`, `get_orchestrator`, `release_session`; `scenario_run.NotSafe`; `src.services.principal.resolve(authorization)`; the `reviewer` dependency of `routes_scenarios.py`; `runs.require_curator`.
- Produces:

```python
MAX_STEP = 2
ROLE_SPACE = {"property_manager": "ops", "compliance_officer": "comp", "portfolio_manager": "full", "general_user": "full"}
STATUS_OF = {"workflow_completed": "ok", "gate_interrupt": "interrupted", "timeout": "timeout"}   # anything else: "error"

async def ensure_tag(m, tag: str, *, kind: str = "adhoc", note: str | None = None) -> dict
async def pending(tag: str, *, org: str, retry_failed: bool = False) -> list[dict]
async def progress(tag: str, *, org: str) -> dict       # {"total", "finished", "failed", "remaining"}
async def run_step(tag: str, *, count: int, m, principal, organization_id: str, authorization: str | None,
                   ask_principal=None, ask_authorization: str | None = None,
                   retry_failed: bool = False) -> dict   # {"asked": [...], "remaining": n, "usd": x}
def main(argv: list[str] | None = None) -> int          # python -m trace_lab.batch run|evaluate|ingest
```

Routes (all under the existing `/api/trace-lab` prefix): `GET /eval/run-tags`, `POST /eval/run-tags` `{tag, kind, note}`, `GET /eval/batches/{tag}`, `POST /eval/batches/{tag}/step` `{count: 1..2, retry_failed: bool}` with optional header `X-Ask-As: Bearer <token>`, `POST /eval/ingest` `{since}`, `POST /eval/scenarios/import` (multipart file, `dry_run` query), `GET /eval/scenarios`, `POST /eval/scenarios/approve` `{scenario_ids}`, `GET /eval/sessions`, `GET /eval/sessions/{id}`, `GET /eval/traces/{id}/tree` (answered through `trace_tree.json_answer` so it is gzipped). Writes need a curator; a not-isolated process answers 409 `not_safe`, as `/scenarios/run` does.

Behaviour that the signatures leave open:
- A scenario is **finished** for a tag when a session row exists for `(run_tag, scenario_id)`. `pending` returns the latest version of every non-retired scenario of the company (or company-less) that is not finished, ordered by `scenario_id`. With `retry_failed`, scenarios whose latest attempt has a trace status of `error` or `timeout` are returned too and run as the next attempt (at most attempt 2).
- `run_step` asks every turn of one scenario in **one session** (`session_id_for(tag, id, attempt)`), stops after the first turn whose `ended_by` is not `workflow_completed` (later turns assume an answer), then writes the session, its traces and observations in **one transaction** and only then moves to the next scenario. `release_session` runs in a `finally`. Nothing is written for a scenario whose process died or whose capture raised, so a rerun asks it again.
- The asked identity is `ask_principal`/`ask_authorization` when `X-Ask-As` was sent, else the caller. The space is the scenario's `space`, else `ROLE_SPACE[role]`, else none. Trace `metadata` gets `refused` (the proxy's refusals), `usd`, `asked_as` (the email).
- The command line is an HTTP client of these routes, so it has no second auth path: `python -m trace_lab.batch run --base-url URL --tag baseline_v0 --kind baseline --org UUID [--retry-failed] [--max N]`, bearer from env `TRACE_LAB_BATCH_TOKEN`, optional `TRACE_LAB_BATCH_ASK_AS`. It loops one scenario per request until `remaining == 0`, prints `finished/total` after each, retries a connection error 5 times with backoff, and exits 3 on HTTP 401 with: `The sign-in expired. Run the same command again with a fresh token: finished scenarios are skipped.` `evaluate` and `ingest` are the same loop over `/eval/evaluate` (Task 11) and `/eval/ingest`.
- `ops/README.md` gains: the command, that one process answers one question at a time (250 scenarios is a night), and that on a local stack the operator may raise the local sign-in lifetime for that night instead of re-running.

- [ ] **Step 1: Write the failing tests** (the orchestrator is replaced by monkeypatching `capture.run_message` with a function that returns scripted `CapturedTurn`s and counts calls; `orchestrator_host.assert_isolated` returns `[]`).

```python
async def test_a_multi_turn_scenario_becomes_one_session_with_two_traces(lab_scratch): ...
async def test_finished_scenarios_are_not_asked_again(lab_scratch):
    await batch.run_step("baseline_v0", count=2, ...); calls = fake.calls
    out = await batch.run_step("baseline_v0", count=2, ...)
    assert fake.calls == calls and out["remaining"] == 0
async def test_a_crash_between_turns_stores_nothing_and_the_next_step_asks_again(lab_scratch):
    fake.raise_on_call = 2                       # second turn of the first scenario
    with pytest.raises(RuntimeError): await batch.run_step(...)
    assert await count("tracelab.sessions") == 0
    fake.raise_on_call = None
    await batch.run_step(...); assert await count("tracelab.sessions") == 1
async def test_a_failed_turn_is_stored_and_its_follow_up_is_not_asked(lab_scratch): ...   # status 'timeout', 1 trace
async def test_retry_failed_runs_attempt_2_and_the_list_shows_it(lab_scratch): ...
async def test_step_refuses_a_process_that_is_not_isolated(lab_scratch): ...               # NotSafe -> route 409
async def test_ask_as_uses_the_second_identity(lab_scratch): ...                          # run_message got ask_principal
def test_cli_exits_3_on_401_and_says_how_to_resume(httpx_mock_or_fake_transport): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement `batch.py`, `routes_eval.py`, register the router.**
- [ ] **Step 4: Run** `python -m pytest tests/test_trace_lab_batch_sql.py tests/test_trace_lab_eval_routes.py -v` and the existing `tests/test_a_scenario_run_asks_for_real.py` (unchanged behaviour). Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): resumable scenario batches under a run tag`.

---

### Task 5: Sessions tab and session page (turn tabs, span tree)

**Files:**
- Create: `FE/logic/sessions.js`, `FE/Sessions.jsx`, `FE/SessionPage.jsx`, `FE/sessions.css`, `FE/__tests__/sessions.test.mjs`
- Modify: `FE/labApi.js`, `FE/LabApp.jsx` (add `['sessions', 'Sessions']` after `['runs', 'Runs']` and render it), `FE/__tests__/labScreens.test.mjs`

**Interfaces:**
- Consumes: the Task 4 read routes; `TraceView` exported by `FE/TraceTab.jsx` (`<TraceView tree={{turn, nodes}} />`, no `annotate` prop: read-only).
- Produces:

```js
// logic/sessions.js
export const LEVELS = ['logic', 'ux', 'domain'];
export function sessionQuery(filters)        // {runTag, type, status, level, verdict, needsSme, stale} -> query object, empty values dropped
export function turnTabs(traces)             // [{key, label: 'Turn 1', traceId, status}] ordered by turn_index
export function scoreSummary(scores)         // {logic: {verdict, value, source} | null, ux: …, domain: …}
export function sessionTitle(session)        // scenario id when there is one, else the session id
// labApi.js additions
evalRunTags(), evalSessions(query), evalSession(id, org), evalTraceTree(traceId, org),
evalBatch(tag, org), evalImportScenarios(file, dryRun), evalApproveScenarios(ids)
```

Screen decisions: the list shows one row per session (scenario id, type, run tag, origin, turns, status, the three level verdicts as text chips, started time), newest first, with a run-tag select and a type select (the other filters arrive in Task 12). The session page shows, top to bottom: header (session id, run tag, scenario id and version, type, role, origin); the scenario card with each turn's message and its expected outcome (plan as a short list, failure handling, answer format, must-include items); a tab list (`role="tablist"`) "Turn 1", "Turn 2", …; under the active tab the trace's status, its input and output, and `<TraceView>` loaded lazily from `/eval/traces/{id}/tree`. Words on screen are Session, Trace, Span, Generation, Score. Empty states: "No sessions for this run tag yet." and "This trace has no spans."

- [ ] **Step 1: Write the failing tests**

```js
test('turn tabs are numbered in turn order', () => {
  const tabs = turnTabs([{ id: 'b', turn_index: 2, status: 'ok' }, { id: 'a', turn_index: 1, status: 'timeout' }]);
  assert.deepEqual(tabs.map((t) => [t.label, t.traceId, t.status]), [['Turn 1', 'a', 'timeout'], ['Turn 2', 'b', 'ok']]);
});
test('empty filters are left out of the query', () => {
  assert.deepEqual(sessionQuery({ runTag: 'baseline_v0', type: '', status: null }), { run_tag: 'baseline_v0' });
});
test('a human score is shown in place of an automatic one', () => {
  const s = scoreSummary([{ name: 'ux', source: 'EVAL', string_value: 'pass', created_at: '2026-11-12T10:00Z' },
                          { name: 'ux', source: 'ANNOTATION', string_value: 'fail', created_at: '2026-11-12T09:00Z' }]);
  assert.equal(s.ux.verdict, 'fail'); assert.equal(s.ux.source, 'ANNOTATION'); assert.equal(s.logic, null);
});
```

Add `Sessions` and `SessionPage` to the screens `labScreens.test.mjs` already renders.

- [ ] **Step 2: Run** `node --test "src/lab/__tests__/sessions.test.mjs"` and see it fail.
- [ ] **Step 3: Implement the logic module, the two screens, the API calls and the tab.**
- [ ] **Step 4: Run** `node --test "src/lab/__tests__/*.test.mjs"` then `npm run build` in `apps/frontend` (with `VITE_TRACE_LAB=1`). Expected: tests pass, build and bundle check clean.
- [ ] **Step 5: Check it for real** on the local stack: import `tests/fixtures/eval_scenarios_sample.jsonl`, run `python -m trace_lab.batch run --tag m1_check …`, open `/lab/` → Sessions → the multi-turn scenario shows Turn 1 and Turn 2 tabs, each with its tree.
- [ ] **Step 6: Commit** (ask first): `feat(trace-lab): Sessions tab - a session opens with a tab per turn`.

---

### Task 6: `sync_push.py`, local results to the dev Trace Lab database

**Files:**
- Create: `TL/trace_lab/sync_push.py`
- Modify: `TL/trace_lab/routes_eval.py` (`GET /eval/sync`, `POST /eval/sync`), `FE/labApi.js`, `FE/Sessions.jsx` (Sync button), `FE/logic/sessions.js` (`syncLine`), `TL/ops/README.md`
- Test: `TL/tests/test_trace_lab_sync_push_sql.py`, `TL/tests/test_trace_lab_sync_push.py`, `FE/__tests__/sessions.test.mjs`

**Interfaces:**
- Consumes: `db.lab_engine()`; `tracelab.schema_migrations`; the Task 1 tables; `runs.require_platform`.
- Produces:

```python
TABLES = ("run_tags", "spec_sections", "scenarios", "sessions", "traces", "observations", "scores")   # foreign-key order
BATCH = 500
OVERLAP = timedelta(minutes=5)

class Refused(Exception):            # .reason in {"not_configured", "same_database", "schema_absent", "schema_version"}
def dest_url(env: Mapping[str, str] | None = None) -> str     # DEV_DB_URL; Refused("not_configured") when unset
def peer_name(url: str) -> str                                # "host/database", nothing else
async def preflight(dest: AsyncEngine) -> None                # raises Refused
async def push(*, dry_run: bool = False, full: bool = False, by: Member | None = None) -> dict
        # {"peer", "dry_run", "pushed": {table: n}, "watermark": iso}
async def status() -> dict                                    # {"enabled": bool, "peer": str | None, "last": {...} | None}
def main(argv: list[str] | None = None) -> int                # python -m trace_lab.sync_push [--dry-run] [--full]
```

Decisions:
- "Upsert by ID" on append-only tables is `INSERT … ON CONFLICT DO NOTHING` on the primary key. A change is always a new row (a new scenario version, a new score), so nothing ever needs updating on the other side. `pushed[table]` counts rows the destination actually inserted.
- Delta: rows with `created_at > last successful watermark − OVERLAP`, where the watermark is this push's start time and is saved to `tracelab.sync_log` only when the whole push ends `done`. `--full` ignores the watermark. `sync_log` itself is local and never pushed.
- `preflight` refuses when the destination is the local database (same host and database name), when it has no `tracelab.schema_migrations`, or when its highest applied version differs from the local one (message: `The dev database is on schema 13 and this one is on 14.`). A refusal writes a `refused` row to `sync_log` and nothing to the destination.
- Each table is pushed in batches of `BATCH`, one destination transaction per batch, in `TABLES` order. A failure stops the push, records `failed` with the table and error, and leaves earlier batches in place; the next push resumes because inserts are idempotent.
- `dry_run` counts what would be inserted (rows whose key is absent on the destination) and writes nothing.
- The route needs the platform team. The button shows only when `status().enabled`; after a push the page prints `syncLine(result)`, for example `Pushed to dev: 250 sessions, 312 traces, 9,804 spans, 0 scores.` ("spans" is the label for the observations count.)
- `ops/README.md` gains: set `DEV_DB_URL` to the dev **Trace Lab** database, the login it needs (D2), the commands, and that both sides must be on the same migration.

- [ ] **Step 1: Write the failing tests** (a `dest_scratch` fixture creates a second throwaway database on the scratch server and applies every migration to it the way `tl_scratch.lab_scratch` does; read `tests/tl_scratch.py` for the apply call and widen its database allow-list for this test only).

```python
def test_peer_name_hides_the_login():
    assert sync_push.peer_name("postgresql+asyncpg://u:secret@db.example.com:5432/hoist_tracelab?ssl=require") == "db.example.com/hoist_tracelab"
def test_unset_variable_is_refused_without_a_url_in_the_message(): ...

async def test_pushing_twice_creates_no_duplicates(lab_scratch, dest_scratch):
    first = await sync_push.push(); second = await sync_push.push(full=True)
    assert first["pushed"]["sessions"] == 3 and all(n == 0 for n in second["pushed"].values())
    assert await dest_count("sessions") == 3
async def test_an_interrupted_push_is_finished_by_the_next_one(lab_scratch, dest_scratch, monkeypatch): ...   # fail at 'traces'
async def test_a_score_added_after_the_first_sync_is_pushed_by_the_delta(lab_scratch, dest_scratch): ...
async def test_same_database_is_refused_and_nothing_is_written(lab_scratch): ...
async def test_a_database_without_trace_lab_is_refused(lab_scratch, empty_dest): ...        # reason "schema_absent"
async def test_a_different_schema_version_is_refused(lab_scratch, dest_scratch): ...        # dest ledger stops at 13
async def test_dry_run_counts_and_writes_nothing(lab_scratch, dest_scratch): ...
async def test_sync_log_records_peer_but_never_the_url(lab_scratch, dest_scratch): ...
```

```js
test('the sync line names what was pushed', () => {
  assert.equal(syncLine({ pushed: { sessions: 250, traces: 312, observations: 9804, scores: 0 } }),
    'Pushed to dev: 250 sessions, 312 traces, 9,804 spans, 0 scores.');
});
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement `sync_push.py`, the two routes, the button.**
- [ ] **Step 4: Run** the three test files. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): push local eval results to the dev Trace Lab database`.

A real push to Azure is a database write: do it only when Hussain says so, after Bala has applied 0014 there and created the login.

---

### Task 7: `spec_doc.py`, scenarios versioned with spec.md

**Files:**
- Create: `TL/trace_lab/spec_doc.py`
- Modify: `TL/trace_lab/scenario_file.py` (stamp hashes on save, fill `stale` in `list_scenarios`), `TL/trace_lab/eval_store.py` (`stale` on session and scenario reads, the `stale` filter), `TL/trace_lab/routes_eval.py` (`GET /eval/spec`, `POST /eval/spec/reload`), `FE/SessionPage.jsx` and `FE/Sessions.jsx` (an "Expected outcome is out of date" chip)
- Test: `TL/tests/test_trace_lab_spec_doc.py`, `TL/tests/test_trace_lab_spec_doc_sql.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Section: section_id: str; title: str; body: str; sha256: str

def spec_path(env: Mapping[str, str] | None = None) -> Path    # TRACE_LAB_SPEC_PATH, else <svc-deepagents>/skills/spec/SPEC.md
def section_id_of(heading: str) -> str                         # "Multi-read planning" -> "multi_read_planning"; a trailing {#id} wins
def parse(text: str) -> list[Section]                          # one per heading of level 2 to 4
def version_of(text: str) -> str                               # first 12 hex of the sha256 of the normalised text
async def load(m: Member, *, text: str | None = None) -> dict  # {"spec_version", "sections": n, "changed": [section ids]}
async def current_hashes(conn) -> tuple[str | None, dict[str, str]]     # (spec_version, {section_id: sha256}) of the newest load
def stale_sections(stamped: Mapping[str, str], current: Mapping[str, str]) -> list[str]
```

Decisions: normalising means LF line endings, trailing spaces stripped, runs of blank lines collapsed, so a whitespace-only edit changes nothing. A section's body runs to the next heading of the same or a higher level. `load` needs a curator, reads the file at `spec_path()` unless `text` is given, and inserts rows only for a new `spec_version`. A scenario is **out of date** when any section it cites has a current hash different from the one stamped on the scenario version the session ran with; a cited section that no longer exists counts as changed. Nothing is rewritten: bringing a scenario up to date is importing or approving a new version, which stamps the new hashes. New evaluations of an out-of-date scenario carry `details.stale_expected = true`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("title, want", [("Multi-read planning", "multi_read_planning"),
    ("Few-shot examples", "few_shot_examples"), ("A2.3 Context passing {#context_passing}", "context_passing")])
def test_section_ids(title, want): assert spec_doc.section_id_of(title) == want
def test_the_fifteen_area_titles_give_the_fifteen_ids(): ...       # titles from the Interface section -> scenario_file.SPEC_SECTIONS
def test_whitespace_only_edits_keep_the_hash_and_a_word_changes_it(): ...

async def test_a_spec_change_flags_only_scenarios_citing_that_section(lab_scratch):
    await spec_doc.load(m, text=SPEC_V1); await scenario_file.save_scenarios(m, [cites_retry, cites_format], source="import")
    await spec_doc.load(m, text=SPEC_V1.replace("at most 2 times", "at most 3 times"))   # inside Retry rules
    rows = {r["scenario_id"]: r["stale"] for r in await list_scenarios(m)}
    assert rows == {"cites-retry": ["retry_rules"], "cites-format": []}
async def test_the_old_version_and_its_scores_are_untouched(lab_scratch): ...
async def test_reimport_after_the_change_clears_the_flag(lab_scratch): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the two files plus `tests/test_trace_lab_scenario_file_sql.py`. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): expected outcomes are versioned with the spec`.

---

### Task 8: `scenario_gen.py`, 250 scenarios across the spec areas

**Files:**
- Create: `TL/trace_lab/scenario_gen.py`
- Modify: `TL/trace_lab/routes_eval.py` (`POST /eval/scenarios/generate`, `GET /eval/scenarios/export`), `TL/trace_lab/batch.py` (`generate` subcommand writing a `.jsonl`)
- Test: `TL/tests/test_trace_lab_scenario_gen.py`, `TL/tests/test_trace_lab_scenario_gen_sql.py`

**Interfaces:**
- Consumes: `scenarios.BY_ENGINE`, `scenarios.CROSS_DOMAIN`, `scenarios.routes_to`, `scenarios.engines_of`, `scenarios.STYLES`, `scenarios._rough`; `scenario_file.validate`, `SPEC_SECTIONS`; the read-only main engine (`conversations._read()`).
- Produces:

```python
MIX_250 = {"single_domain": 100, "multi_domain": 60, "multi_turn": 50, "failure": 40}
PREFIX = {"single_domain": "SD", "multi_domain": "MD", "multi_turn": "MT", "failure": "FL"}

def generate(*, count: int = 250, seed: int = 0, space: str = "full",
             mix: Mapping[str, int] | None = None) -> tuple[list[dict], list[dict]]   # (scenarios, dropped with reasons); pure
async def ground(scenarios: Sequence[dict], *, organization_id: str, seed: int = 0) -> tuple[list[dict], list[dict]]
```

Decisions:
- Pure and seeded like `scenarios.py`: no model, no network; the same `(count, seed, space, mix)` gives the same batch. Ids are `{PREFIX}-{seed}-{n:03d}` and never change for a seed, so a trace can always be matched back.
- Questions come from the pools the repo already has: `BY_ENGINE` for single-domain, `CROSS_DOMAIN` (with its `needs`) for multi-domain. Two small new tables in this module: `FOLLOW_UPS` (per engine, follow-ups that only make sense with the first answer's context, e.g. "for each of those, who is the vendor?") and `FAILURE_CASES` (per failure area: which service path gets which fault, and what the answer must then do). Every question is put through `routes_to` and dropped when it would leave the batch's space.
- Expected outcome skeleton, from the spec area and never from a model: a plan entry per needed domain (`tool` null, `domain` set; a multi-domain question lists its domains with no dependencies; a follow-up turn has its own plan with no dependency on the earlier turn, and continuity is checked through the scenario `scope`), `failure_handling` and `answer_format` from `FAILURE_CASES` (`partial_failure` → `partial_success`, `total_failure` → `failure`, `authorization_failure` → `denial`), `success` otherwise. `answer` and `must_include` stay empty and the status is `draft`: record-specific values are added by a person or a coding agent through export → edit → import, written before the run, never copied from an answer.
- Every id in `SPEC_SECTIONS` is tagged by at least one scenario; the generator asserts it.
- `ground` fills `{building}` and `{vendor}` placeholders from the company's real rows (deterministic by seed), sets `scope` and `grounded_on`, and drops (with a reason) a scenario it cannot ground. It only reads.

- [ ] **Step 1: Write the failing tests**

```python
def test_250_scenarios_with_the_requested_mix_and_unique_stable_ids():
    rows, _ = scenario_gen.generate(count=250, seed=7)
    assert len(rows) == 250 and len({r["scenario_id"] for r in rows}) == 250
    assert Counter(r["type"] for r in rows) == scenario_gen.MIX_250
    assert [r["scenario_id"] for r in scenario_gen.generate(count=250, seed=7)[0]] == [r["scenario_id"] for r in rows]
def test_every_spec_section_is_tagged_at_least_once():
    rows, _ = scenario_gen.generate(count=250, seed=7)
    assert set(scenario_file.SPEC_SECTIONS) <= {s for r in rows for s in r["spec_sections"]}
def test_every_scenario_carries_a_valid_expected_outcome():
    assert all(scenario_file.validate(r) == [] for r in scenario_gen.generate(count=250, seed=7)[0])
def test_failure_scenarios_carry_a_fault_and_say_how_the_answer_must_handle_it(): ...
def test_multi_turn_scenarios_have_two_or_more_turns_each_with_an_expected(): ...
def test_a_question_that_routes_outside_the_space_is_dropped_with_a_reason(): ...     # space "comp"
async def test_grounding_names_real_rows_and_records_them(lab_scratch, main_scratch): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both files and `tests/test_a_batch_is_generated_for_one_instance.py` (the older generator must be unchanged). Expected: the new files pass; the older file has 6 known failures on clean origin ("44 of 50"), not caused by this work.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): generate scenarios per spec area with expected outcomes`.

---

### Task 9: `faults.py`, making a tool fail on purpose

**Files:**
- Create: `TL/trace_lab/faults.py`
- Modify: `TL/trace_lab/proxy.py` (in `forward`, after the allow check and before the upstream call), `TL/trace_lab/batch.py` (begin/end around a scenario, fired faults into trace metadata), `FE/SessionPage.jsx` ("Fault injected" line on the trace)
- Test: `TL/tests/test_trace_lab_faults.py`, plus the existing proxy tests

**Interfaces:**
- Produces:

```python
class FaultPlan:
    def begin(self, case_ref: str, faults: Sequence[Mapping], *, authorization: str | None) -> None
    def match(self, service: str, method: str, path: str, *, authorization: str | None) -> Mapping | None
    def end(self) -> list[dict]          # what fired: [{"service", "path", "fault", "at"}]
plan = FaultPlan()
RESPONSES = {"timeout": (504, {"error": "upstream timeout (injected by Trace Lab)"}),
             "http_500": (500, {"error": "upstream error (injected by Trace Lab)"}),
             "http_429": (429, {"error": "rate limited (injected by Trace Lab)"}),
             "http_403": (403, {"detail": "Forbidden"})}
```

Decisions: one plan per process, like `proxy.refusals` (the runner asks one question at a time). A fault matches when the request carries the scenario's own Authorization header (compared as a sha256 digest), the service equals `service` and the path starts with `path_prefix`; `times` counts down (null means every time). The hook returns the injected response without calling the upstream; `http_429` adds `Retry-After: 1`. With no plan active `match` returns None and the proxy behaves exactly as before. Fired faults are stored as `metadata.injected_faults` on each trace of that scenario, so nobody mistakes an injected failure for a real outage.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_matching_request_gets_the_injected_status_and_is_recorded(): ...
def test_times_1_fires_once_and_then_lets_requests_through(): ...
def test_a_request_with_another_sign_in_is_not_touched(): ...
def test_with_no_plan_nothing_matches(): assert faults.plan.match("ops-intelligence", "GET", "/api/x", authorization="Bearer a") is None
async def test_fired_faults_land_in_the_trace_metadata(lab_scratch): ...        # through batch.run_step with a fake run_message
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement the holder, the hook, the batch wiring.**
- [ ] **Step 4: Run** `python -m pytest tests/test_trace_lab_faults.py tests/test_trace_lab_batch_sql.py -v` and every existing test file whose name contains `proxy`. Expected: pass, existing proxy tests unchanged.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): failure scenarios inject a fault through the read proxy`.

---

### Task 10: `eval_logic.py`, the automatic logic level

**Files:**
- Create: `TL/trace_lab/eval_logic.py`
- Test: `TL/tests/test_trace_lab_eval_logic.py` (pure; hand-built node lists)

**Interfaces:**
- Consumes: observation rows as `eval_store.node_of` returns them; a turn's `expected`; the scenario's `scope`.
- Produces:

```python
EVALUATOR = "logic-1"
RETRY_MAX = 2
TRANSIENT_STATUS = (429, 500, 502, 503, 504)
CHECKS = ("completed", "plan_coverage", "call_order", "parallel", "scope", "retries", "context_passing", "evidence_state")

@dataclass(frozen=True)
class Check: key: str; result: str; note: str = ""      # result: pass | fail | not_observable | not_applicable
@dataclass(frozen=True)
class LogicResult: verdict: str; value: float | None; checks: tuple[Check, ...]   # verdict: pass | fail | not_evaluated

def tool_calls(nodes: Sequence[Mapping]) -> list[dict]          # type == 'tool': name, domain, input, output, start, end, failed, retry_of
def planned_calls(nodes: Sequence[Mapping]) -> list[dict] | None  # output.calls of the span named 'plan'; None when there is none
def evaluate(expected: Mapping, scope: Mapping, nodes: Sequence[Mapping], *, trace_status: str) -> LogicResult
```

The rules, one per check:

| Check | Pass | Fail | Not observable / not applicable |
|---|---|---|---|
| `completed` | trace status `ok` | any other status | — |
| `plan_coverage` | every expected call is matched by a call (same `tool`, or same `domain` when `tool` is null); when a `plan` span exists, also by a planned call | an expected call has no match (note names it) | expected plan is empty: not applicable |
| `call_order` | for each `depends_on` edge a→b, b's first match starts at or after a's first match ends | b starts before a ends | no edges: not applicable; missing times: not observable |
| `parallel` | two expected calls with no dependency path overlap in time | they do not overlap | fewer than two such calls: not applicable; missing times: not observable |
| `scope` | every building, vendor and period value in a call's params is inside the scenario scope | a value is outside (note names call and value) | no call carries such a param: not observable; empty scope: not applicable |
| `retries` | each retry follows a transient failure, repeats the same params, and there are at most `RETRY_MAX` | changed params, a retry after 401/403/422, or too many | no failed call and no retry: not applicable |
| `context_passing` | for each edge a→b, a `context_passing` span links them, or a value from a's output (an id or name of 3+ characters) appears in b's input | neither | a has no output or b no input: not observable |
| `evidence_state` | the tool's `output.status` equals the expected `failure_handling.state` | they differ | no `status` on the tool output (today's tools): not observable; no expected state: not applicable |

A call's domain is the `agent` of the tool span or of its nearest ancestor that has one. A retry is a child span named `retry_attempt`, or a later call with the same tool and params whose predecessor failed. Scope keys read from params (case-insensitive, any depth): `building`, `building_id`, `site`, `site_id`, `building_ids`, `vendor`, `vendor_id`, `from`, `to`, `date_from`, `date_to`, `period`. Verdict: `fail` when any check fails; `pass` when none fails and at least one passes; otherwise `not_evaluated`. `value` is passes ÷ (passes + fails), None when both are zero.

- [ ] **Step 1: Write the failing tests**

```python
def test_todays_trace_without_plan_retry_or_status_is_not_observable_not_failed():
    r = eval_logic.evaluate(expected_one_call("compliance"), {}, baseline_nodes(), trace_status="ok")
    got = {c.key: c.result for c in r.checks}
    assert got["evidence_state"] in ("not_observable", "not_applicable") and got["retries"] == "not_applicable"
    assert r.verdict == "pass"                      # completed + plan_coverage passed, nothing failed
def test_a_missing_domain_fails_plan_coverage_and_names_it(): ...
def test_dependency_broken_when_b_starts_before_a_ends(): ...
def test_independent_calls_that_overlap_pass_parallel_and_sequential_ones_fail(): ...
def test_a_building_outside_scope_fails_and_names_the_call_and_value(): ...
def test_retry_with_changed_params_fails(): ...
def test_retry_after_403_fails_and_after_504_passes(): ...
def test_three_retries_fail_when_the_limit_is_two(): ...
def test_context_value_from_call_1_found_in_call_2_input_passes(): ...
def test_a_timed_out_trace_fails_completed_and_the_verdict(): ...
def test_malformed_span_payloads_do_not_raise(): ...   # input a string, output None, no times
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest tests/test_trace_lab_eval_logic.py -v`. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): logic level - the trace is checked against the expected plan`.

---

### Task 11: `eval_judges.py` and the evaluate loop (UX and domain levels)

**Files:**
- Create: `TL/trace_lab/eval_judges.py`
- Modify: `TL/trace_lab/routes_eval.py` (`POST /eval/evaluate`), `TL/trace_lab/batch.py` (`evaluate` subcommand, already stubbed in Task 4)
- Test: `TL/tests/test_trace_lab_eval_judges.py`, `TL/tests/test_trace_lab_evaluate_sql.py`

**Interfaces:**
- Consumes: `proposals.ask_json(system, user, schema, *, role, max_tokens) -> (reply, cost)`; `eval_logic.evaluate`; `eval_store` reads; the scenario version a session ran with.
- Produces:

```python
UX_EVALUATOR, DOMAIN_EVALUATOR = "ux-1", "domain-1"
CONCISE_WORDS = 250                                        # answer_words above this is not concise, whatever the model says

def ux_precheck(answer: str, expected: Mapping) -> dict    # {"answer_words", "missing": [...], "forbidden_present": [...]}
def ux_verdict(reply: Mapping, expected: Mapping, pre: Mapping) -> tuple[str, float, dict]     # (verdict, value, details)
def domain_verdict(reply: Mapping) -> tuple[str, float, dict]
async def judge_ux(*, question: str, history: Sequence[Mapping], answer: str, expected: Mapping) -> tuple[dict, float]
async def judge_domain(*, question: str, answer: str, evidence: str, rules: Sequence[Mapping]) -> tuple[dict, float]
async def evaluate_run(m: Member, run_tag: str, *, levels: Sequence[str] = ("logic", "ux", "domain"),
                       limit: int = 25, usd_cap: float = 5.0) -> dict
        # {"scored": {level: n}, "not_evaluated": {level: n}, "usd": x, "remaining": n}
```

Decisions:
- The model only fills in criteria; the code decides the verdict. UX reply schema: `{concise: bool, complete: bool, template: one of ANSWER_FORMATS or "none", failure_wording: "clear" | "unclear" | "n/a", reason: str}`. `ux_verdict` passes only when: concise (and `answer_words <= CONCISE_WORDS`), complete (and nothing in `missing`, nothing in `forbidden_present`), `template == expected.answer_format`, and `failure_wording != "unclear"`. `value` is the share of those four that hold. `details` keeps the reply, the precheck and `answer_words`.
- Domain reply schema: `{violations: [{rule, why}], confidence: "high" | "low", reason: str}`. Pass when there are no violations; `details.needs_sme` is true on a fail or on low confidence. The rules given to the judge are the scenario's `domain_rules` plus the bodies of the spec sections it cites; with neither, the score is `not_evaluated` with `details.reason = "no_domain_rules"` and no model call. Evidence is the tool outputs of the trace, cut to 6,000 characters.
- `evaluate_run` takes the latest attempt of each scenario of the tag, and for each trace and level writes one `EVAL` score when none exists yet for that evaluator version. Logic is free; the judges run through `proposals.ask_json` with roles `tracelab_eval_ux` and `tracelab_eval_domain`, `max_tokens=800`, and the loop stops when `usd_cap` is reached, reporting `remaining`. A session with no scenario (a backfilled chat with an unknown id) gets `not_evaluated`, reason `no_scenario`. A draft scenario is evaluated, with `details.expected_status = "draft"`.
- The route takes `{run_tag, levels, limit ≤ 25}` and needs a curator; the page and the command line call it until `remaining == 0`.

- [ ] **Step 1: Write the failing tests** (the model is replaced by monkeypatching `proposals.ask_json`).

```python
def test_missing_must_include_fails_even_when_the_model_says_complete(): ...
def test_wrong_template_fails(): ...                                     # expected partial_success, reply success
def test_an_answer_over_250_words_is_not_concise(): ...
def test_all_four_criteria_met_passes_with_value_1(): ...
def test_domain_violation_fails_and_asks_for_an_sme(): ...
async def test_domain_without_rules_is_not_evaluated_and_calls_no_model(lab_scratch): ...
async def test_evaluating_twice_scores_nothing_new(lab_scratch): ...
async def test_the_spend_cap_stops_the_loop_and_reports_what_is_left(lab_scratch): ...
async def test_an_out_of_date_scenario_is_scored_with_the_stale_flag(lab_scratch): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both files. Expected: pass.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): UX and domain judges, and the evaluate loop`.

---

### Task 12: Scores, annotation, filters, compare (B4)

**Files:**
- Create: `TL/trace_lab/scores.py`, `FE/Compare.jsx`
- Modify: `TL/trace_lab/eval_store.py` (the `level`/`verdict`/`needs_sme` filters, scores in session reads), `TL/trace_lab/routes_eval.py` (`PUT /eval/traces/{id}/scores/{level}`, `GET /eval/compare`), `FE/logic/sessions.js`, `FE/SessionPage.jsx` (Scores panel), `FE/Sessions.jsx` (filter bar, Compare entry, Evaluate button), `FE/labApi.js`
- Test: `TL/tests/test_trace_lab_scores_sql.py`, `FE/__tests__/sessions.test.mjs`

**Interfaces:**
- Produces:

```python
LEVELS = ("logic", "ux", "domain")
async def annotate(m: Member, trace_id: str, level: str, *, verdict: str, value: float | None, comment: str | None) -> dict
async def current_scores(conn, trace_ids: Sequence[str]) -> dict[str, dict[str, dict]]      # trace -> level -> score row
def session_verdict(per_trace: Sequence[Mapping | None]) -> str                               # pass | fail | not_evaluated
async def compare(m: Member, base: str, head: str) -> dict        # {"summary": {...}, "rows": [...]}
```

```js
export function compareRows(rows)          // adds change per level: 'fixed' | 'regressed' | 'same' | 'new' | 'missing'
export function compareSummary(rows)       // {logic: {fixed, regressed, same}, ux: …, domain: …, answerWords: {base, head}}
```

Decisions:
- The current score of a trace at a level is its newest `ANNOTATION` row when there is one, otherwise its newest `EVAL` row. A person's score is never overwritten: a correction is a new row. A session's verdict at a level is `fail` when any trace fails, `pass` when every trace passes, else `not_evaluated`.
- `annotate` needs an active member; the `domain` level needs the lab role `domain_expert`, the platform team or the consultant (the SME review). `verdict` is `pass` or `fail`; `value` is 0 to 1 or empty; `comment` up to 4,000 characters. The row stores the author's id, email and lab role.
- List filters: run tag, scenario type, failure status (`ok` | `failed`), level + verdict, "needs SME review" (the current domain score is an `EVAL` with `details.needs_sme`), "out of date".
- `compare(base, head)` pairs the latest attempt of each scenario in the two run tags: per row `scenario_id`, `type`, and for each side `session_id`, the three verdicts, `answer_words` (sum over traces), `latency_ms`, `status`. Change per level: `fixed` (fail → pass), `regressed` (pass → fail), `same`, `new` (only in head), `missing` (only in base). The summary counts them and gives the median `answer_words` on each side, which is the measure A1.2 asks for ("answer length drops against the baseline").
- Scores panel, per turn: three rows (Logic, UX, Domain), each showing the automatic result with its checks or criteria listed, and a form with Pass / Fail, a score, a comment and Save. Compare screen: two run-tag selects (defaults `baseline_v0` and the newest other tag), the summary as plain figures, the table, and a row opens the two sessions side by side on the same turn with both trees.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_human_score_wins_over_the_automatic_one(lab_scratch): ...
async def test_annotating_twice_keeps_both_rows_and_shows_the_newer(lab_scratch): ...
async def test_the_domain_level_needs_a_domain_expert(lab_scratch): ...                 # a 'tester' gets Forbidden
async def test_filters_by_run_tag_type_failure_status_and_verdict(lab_scratch): ...
async def test_compare_marks_fixed_regressed_new_and_missing(lab_scratch): ...
async def test_compare_reports_median_answer_words_per_side(lab_scratch): ...
```

```js
test('a fail that became a pass is fixed, the reverse is regressed', () => {
  const rows = compareRows([{ scenario_id: 'a', base: { verdicts: { logic: 'fail' } }, head: { verdicts: { logic: 'pass' } } },
                            { scenario_id: 'b', base: { verdicts: { logic: 'pass' } }, head: { verdicts: { logic: 'fail' } } }]);
  assert.deepEqual(rows.map((r) => r.change.logic), ['fixed', 'regressed']);
});
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement backend, then the panel, filters and Compare.**
- [ ] **Step 4: Run** `python -m pytest tests/test_trace_lab_scores_sql.py tests/test_trace_lab_eval_store_sql.py -v`, `node --test "src/lab/__tests__/*.test.mjs"`, `npm run build`. Expected: pass, build clean.
- [ ] **Step 5: Check it for real:** two run tags over the sample file on the local stack; score a trace by hand; filter; open Compare.
- [ ] **Step 6: Commit** (ask first): `feat(trace-lab): score traces at three levels, filter and compare run tags`.

---

### Task 13 (needs D6): Langfuse words on the older Trace Lab screens

**Files:**
- Create: `FE/logic/terms.js`, `FE/__tests__/terms.test.mjs`
- Modify: visible copy only in `FE/QuestionPage.jsx`, `FE/TraceTab.jsx`, `FE/Runs.jsx`, `FE/Notes.jsx`, `FE/LabelsView.jsx`, `FE/Loop.jsx`

**Interfaces:**
- Produces: `export const TERMS = { session: 'Session', trace: 'Trace', span: 'Span', generation: 'Generation', score: 'Score' }` and `export const RETIRED = ['Supporting answer', 'supporting answer', 'active answer', 'Recorded answers']`.

Scope: words about the trace structure only. "Supporting answers" becomes "Traces", "active answer" becomes "active trace", a step that is a model call is a "Generation" and any other step a "Span", a conversation is a "Session". Run, Question, Label, Note and Expected answer keep their names (Langfuse has no word for them here). No API field, route, table or stored note key changes.

- [ ] **Step 1: Write the failing test:** `terms.test.mjs` reads every `FE/*.jsx` and fails when a string literal contains an entry of `RETIRED`.
- [ ] **Step 2: Run it and see it fail** on today's copy.
- [ ] **Step 3: Replace the copy**, using `TERMS`.
- [ ] **Step 4: Run** `node --test "src/lab/__tests__/*.test.mjs"` (the existing `labScreens`, `traceTab`, `traceNotes` tests are updated only where they assert the old words) and `npm run build`.
- [ ] **Step 5: Commit** (ask first): `feat(trace-lab): the older screens use Langfuse's words for the trace`.

---

## Rollout (each step needs Hussain's yes)

1. Local: scratch tests green → run the sample file end to end on the local stack (Task 5 step 5).
2. Commit and push `eval-2:Hoistra_Frontend`.
3. Bala: rebuild the image with `VITE_TRACE_LAB=1`, `TRACE_LAB_ENABLED=1`; apply 0014 on the dev Trace Lab database with `python -m trace_lab.migrate apply --expect-db <name> --through 0014 --applied-by <name>` (Trace Lab answers 503 "behind" between the new image and the migration); then pin 0014 in `SHIPPED_CHECKSUMS`.
4. VPS: pull `Hoistra_Frontend`, apply 0014 to its local Trace Lab database, set `TRACE_LAB_ORIGIN` (for example `aasim-vps`), `TRACE_LAB_SPEC_PATH` if spec.md lives elsewhere, and `DEV_DB_URL`.
5. Baseline: import or generate the 250, `python -m trace_lab.batch run --tag baseline_v0 --kind baseline …`, or backfill tonight's chat-driven run with `python -m trace_lab.batch ingest --since …`.
6. `python -m trace_lab.sync_push --dry-run`, then the real push; the sessions appear on dev under Sessions with origin `aasim-vps`.
7. After Workstream A: rebuild Trace Lab, run tag `postfix_v1` (kind `post_fix`), evaluate both tags, Compare.

## Known limits

- One question at a time per Trace Lab process: 250 scenarios is a night, not an hour.
- Injected faults cover calls that go through the read proxy (service HTTP calls). A tool that reads the database directly cannot be made to fail this way.
- Until Hoistra has test users per role, authorization-failure scenarios run as the operator (D5).
- Sessions synced from another machine keep that machine's company id. If the company does not exist on dev, only people who see every company (platform team, consultant) can open them.
- Live production chats are not copied into the new store; they stay in Runs. The backfill takes scenario sessions only.
