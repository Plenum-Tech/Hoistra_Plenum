# Trace Lab M0 — Foundation & safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give svc-deepagents a `plenum_tracelab` schema that is created only by reviewed, versioned migrations and only *verified* at startup; a resumable jobs table; write-time provenance for every traced turn; the asker's own words as the question; priced gate spans for the two post-loop answer rewriters; a span purge that spares evidence and stops when Trace Lab is not verified; and guards that keep every chat tool (and svc-udr's custom SELECT) away from evaluation data — without changing a single chat answer.

**Architecture:** A new package `src/trace_lab/` in svc-deepagents: `migrate.py` (files, ledger, checksums, catalog verify, apply, CLI), `db.py` (process state, 503 dependency, shared auth/scope helpers, purge keep-list registry, offboarding), `jobs.py` (resumable job rows), `provenance.py` (channel decision and the `turn_meta` write), `guards.py` (eval-data text guards), `__init__.py` (`startup()`, never raises). `migrations/trace_lab/0001_foundation.sql` creates `schema_migrations`, `jobs`, `turn_meta` and two trigger functions. The chat path gains small, wrapped hooks in `agents/trace.py`, `agents/udr_response_evaluator.py`, `agents/contract_answer.py`, `agents/udr_agent.py` and `api/routes/workflow.py`; svc-udr gains one SELECT guard; the frontend names an origin on every send. One read-only route, `GET /api/trace-lab/status`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, structlog, pytest (asyncio_mode=auto); Postgres 16 scratch container for SQL tests; React frontend modules tested with `node --test` on Node 24.18.1, esbuild for comment stripping.

**Spec:** `docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md` (sections: head — Decisions D1–D15, Reconciliations R1–R37, Milestones M0, Prerequisites P1/P3/P8; "Data model, migrations, jobs and guards" §1–§5; "Trace capture, normalizer and conversations" §1 and §2.1–§2.4)

**Depends on:** no earlier milestone. Prerequisites: **P1** (hoist-f5's company-boundary work landed) before any rebuild; **P3** (Hussain approves the SQL and the `migrate plan` output, reads `SHOW server_version` first) before `0001` is applied to hoistra_test; **P8** is done by Task 29 of this plan.

---

## Global Constraints

Shared (copied from the brief):

- No database writes and no direct SQL from a Claude session; verification against hoistra_test is Hussain's (memory: no-database-writes). Migrations are applied only by Hussain (`migrate apply` needs a TTY), after reviewing the SQL (P3).
- No git commit / stash / checkout / reset / push from a Claude session; every "Checkpoint" step leaves work uncommitted (memory: no-commits-without-asking).
- Other Claude sessions edit the same working tree: edit shared files with exact-string replacements on the current content, never whole-file rewrites; check for recent foreign edits before any rebuild.
- P1: never rebuild svc-deepagents / svc-udr / svc-operations-intelligence from this branch until hoist-f5's company-boundary work has landed; P2 (chat fixes) before golden runs mean anything.
- Startup never runs DDL: `TRACE_LAB_SCHEMA=verify` is the default; `apply` only on scratch/testenv/local-db or by Hussain.
- No dummy data on screen (real data, "—" or "Reading…"); no internal names in UI chrome (noInternalNames guard; only `RawPanel.jsx` is on its SKIP list).
- svc-deepagents tests run in the testenv container at real repo depth with every DB URL spelling pinned to a dead address:
  ```sh
  C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-$(date +%s)
  docker exec $C sh -lc "mkdir -p $R/apps/backend/cafm-connector-service-final/svc-deepagents $R/apps/backend/cafm-connector-service-final/svc-operations-intelligence $R/apps/frontend"   # the container's sh is dash: no {a,b} brace expansion
  for d in src tests skills migrations pyproject.toml; do docker cp apps/backend/cafm-connector-service-final/svc-deepagents/$d $C:$R/apps/backend/cafm-connector-service-final/svc-deepagents/$d; done
  docker cp apps/frontend/src $C:$R/apps/frontend/src
  docker cp apps/backend/cafm-connector-service-final/svc-operations-intelligence/src $C:$R/apps/backend/cafm-connector-service-final/svc-operations-intelligence/src
  N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
  docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy \
    -e TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch $C \
    sh -lc "cd $R/apps/backend/cafm-connector-service-final/svc-deepagents && python -m pytest <tests> -q -p no:cacheprovider -p no:warnings"
  ```
  (Fresh directory per run; never `rm -rf`. Real-SQL tests need a throwaway DB: `docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine`; they skip when TRACE_LAB_TEST_DB_URL is unset.) Hostile-path tests use tmp_path victims only.
- svc-udr tests: same pattern in `hoistra-testenv-svc-udr-1` with svc-udr's tree.
- Frontend: `cd apps/frontend && node --test test/<file>.test.mjs` on Node 24.18.1 (`nvm use 24.18.1`); run the full `npm test` and `npm run build` at the end of the milestone.
- Backend containers have no source mount; changes reach a running stack only after Hussain rebuilds with `docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps <svc>` and reloads the gateway (`docker exec hoistra_plenum-gateway-app-1 nginx -s reload`; confirm the container name with `docker ps` first).

M0-specific:

- **No chat answer changes.** Origins, `turn_meta`, gate spans and the eval context are recorded *beside* the turn. Nothing reads them for routing, scope or the answer. Every new hook is wrapped in `try/except` and logs `trace.<fn>_failed` or `trace_lab.<fn>_failed` with `str(exc)[:300]`.
- **`replay_guard.EVAL` is defined only** (variable + `eval_context()`): no reader changes in M0 — `trace.begin`, `activity_log.record`, `http_client`, threads and memories behave exactly as today. M3 makes the readers honour it.
- **The scratch database.** This plan uses the brief's container (`tl-scratch-pg`, database `tracelab_scratch`, host port 55432). The SQL-test guard (`migrate.scratch_url_ok`) accepts only the database name `tracelab_scratch` (spec §1.8), on hosts `localhost`, `127.0.0.1`, `host.docker.internal` only, and *fails* (never skips) on anything else. The name, the guard, `tests/trace_lab_scratch.py` and its fixtures `scratch_db` / `tl_scratch` (a fixture name, not a database name) are M0's; later milestones import them as a sibling module (`from trace_lab_scratch import ...`; `tests/` has no `__init__.py`, precedent `tests/test_loop_answers_get_cards.py:20`) and never define their own.
- **`0001_foundation.sql` may change until Hussain applies it to hoistra_test.** Every change regenerates `catalog.json` with `tlsnap.sh` (Task 9) and re-runs the SQL tests. After Hussain applies it, its checksum goes into `SHIPPED_CHECKSUMS` and the file is frozen; a correction is `0007+`.
- **svc-udr uses the spec's fallback path** (§4.2): F5's `scope.py` is not on eval-2. Expect a textual conflict in `execute_select` when F5 lands; keep both refusals. svc-operations-intelligence's `udr_scoped_views.sql` is **not** touched: `DENIED_TABLES` stays equal to that file's list (pinned by svc-udr's `test_scope.py`), so the new closed tables live in a separate `EVAL_DATA_TABLES` set.
- **Reconciled DDL.** `0001` follows R6 (job kinds/statuses) and R7 (channels, no `replay`) over §2.2's draft; comments are reworded so the file lint passes (no other schema named, no `;` inside a comment string, no apostrophes).
- **Behaviour changes to tell Bala** (deliver with the Task 30 report; Task 31 lists them in full): the 90-day span purge is skipped (logs `trace.purge_skipped`) until `0001` is verified on the database; EL-UDR and vendor-analyst model calls now appear on the turn's cost (they were unpriced); REST turns record the asker's words as the question; `/api/workflow/*` accept optional `origin`, `origin_params`, `page`; chat tools no longer list or read `agent_trace_*`, `agent_activity_log` or `plenum_tracelab.*`; svc-udr refuses those names in custom SELECT and on its table routes.

## Review Focus

1. **Trace Lab not (yet) applied, or drifted, on a shared database** — Trace Lab must answer 503 and the purge must skip, while the chat keeps writing its traces: `tests/test_trace_lab_db_sql.py::test_a_drifted_database_answers_503_and_skips_the_purge` (Task 11) and `tests/test_trace_lab_capture_sql.py::test_while_trace_lab_is_down_the_trace_is_still_written` (Task 18).
2. **A `turn_meta` insert that fails** (CHECK, JSON, uuid cast) must roll back only its SAVEPOINT, never the turn and its spans: `tests/test_trace_lab_capture_sql.py::test_turn_meta_failure_never_loses_the_trace` (Task 18).
3. **Eval data reached through a spelling** (quoted, commented, `U&`-escaped, unqualified through `plenum_scoped`): `tests/test_trace_lab_guards.py::test_sql_names_closed_catches_every_spelling` (Task 13) and svc-udr `tests/unit/test_trace_lab_schema_closed.py::test_execute_select_refuses_before_any_db_call` (Task 15).
4. **The wrapped REST prompt shown as the question**: `tests/test_trace_lab_capture.py::test_rest_turn_keeps_the_askers_words` (Task 18).
5. **`migrate apply` run from a pipe, or against the wrong database**: `tests/test_trace_lab_migrate.py::test_cli_apply_refuses_without_a_terminal` and `::test_cli_apply_refuses_another_database` (Task 8).

## File Structure

svc-deepagents (`DA` = `apps/backend/cafm-connector-service-final/svc-deepagents`):

| File | Action | Responsibility |
|---|---|---|
| `DA/src/trace_lab/__init__.py` | Create | `startup()`: choose mode, verify or apply, store state, sweep stale jobs; never raises |
| `DA/src/trace_lab/migrate.py` | Create | Files, checksums, splitter, lint, ledger, catalog read/compare, verify, apply, CLI |
| `DA/src/trace_lab/db.py` | Create | `STATE`, readiness, 503 dependency, error envelope, auth/scope helpers, keep-list registry, offboarding |
| `DA/src/trace_lab/jobs.py` | Create | Resumable job rows: create/claim/heartbeat/reserve/checkpoint/finish/resume, worker registry |
| `DA/src/trace_lab/guards.py` | Create | Eval-data text guards for chat tools + self-test |
| `DA/src/trace_lab/provenance.py` | Create | Request provenance, channel decision, `turn_meta` write, read-time classifier |
| `DA/src/api/routes/trace_lab_status.py` | Create | `GET /api/trace-lab/status` |
| `DA/migrations/trace_lab/0001_foundation.sql` | Create | Schema, ledger, `refuse_change()`, `supersede_only()`, `jobs`, `turn_meta` |
| `DA/migrations/trace_lab/catalog.json` | Create (generated) | Expected catalog at the head version |
| `DA/src/agents/replay_guard.py` | Modify | `EVAL` ContextVar + `eval_context()` |
| `DA/src/agents/trace.py` | Modify | Keep-list purge; `Turn.prov`; capture at turn open; question fix; `turn_meta` in `_write`; gate hooks |
| `DA/src/api/main.py` | Modify | `trace_lab.startup()` before the purge; register the status router |
| `DA/src/agents/udr_agent.py` | Modify | Guards on every generic table / SQL tool |
| `DA/src/agents/udr_response_evaluator.py` | Modify | EL-UDR gate span + priced judge calls |
| `DA/src/agents/contract_answer.py` | Modify | Vendor-analyst gate span + priced analyst call |
| `DA/src/api/routes/workflow.py` | Modify | Origin fields; `provenance.set_request` on every chat route |
| `DA/tests/trace_lab_scratch.py` | Create | Scratch-DB helper: `fresh_schema`, fixtures `scratch_db` / `tl_scratch` |
| `DA/tests/test_replay_guard_eval.py` | Create | EVAL placeholder |
| `DA/tests/test_trace_lab_migrate.py` | Create | Migrations without a database, file rules, CLI |
| `DA/tests/test_trace_lab_migrate_sql.py` | Create | Apply/verify on scratch |
| `DA/tests/test_trace_lab_db.py` | Create | State, 503, re-verify, keep registry, auth helpers |
| `DA/tests/test_trace_lab_db_sql.py` | Create | Drift → 503 + purge skipped; offboarding |
| `DA/tests/test_trace_lab_jobs.py` / `test_trace_lab_jobs_sql.py` | Create | Jobs without / with a database |
| `DA/tests/test_trace_lab_startup.py` | Create | `startup()` |
| `DA/tests/test_trace_lab_purge.py` / `test_trace_lab_purge_sql.py` | Create | Keep-list purge |
| `DA/tests/test_trace_lab_status_route.py` | Create | Status route |
| `DA/tests/test_trace_lab_guards.py` | Create | Guards + `udr_agent` call sites |
| `DA/tests/test_trace_lab_provenance.py` / `test_trace_lab_provenance_sql.py` | Create | Provenance |
| `DA/tests/test_trace_lab_capture.py` / `test_trace_lab_capture_sql.py` | Create | Trace hooks + question fix |
| `DA/tests/test_trace_lab_workflow_provenance.py` | Create | Route transport |
| `DA/tests/test_trace_lab_gates.py` | Create | Gate hooks, EL-UDR gate, compose gate |

svc-udr (`UDR` = `apps/backend/cafm-connector-service-final/svc-udr`):

| File | Action | Responsibility |
|---|---|---|
| `UDR/src/services/scope.py` | Modify | `EVAL_DATA_TABLES`, `EVAL_DATA_TABLE_PREFIXES`, `names_trace_lab()`, `is_denied` covers them |
| `UDR/src/services/database_service.py` | Modify | `execute_select` refuses eval-data names before any DB access |
| `UDR/tests/unit/test_trace_lab_schema_closed.py` | Create | Tests |

Frontend (`FE` = `apps/frontend`):

| File | Action | Responsibility |
|---|---|---|
| `FE/src/logic/traceLabOrigins.js` | Create | `ORIGINS`, `originOf`, `pageOf`, `provFields` |
| `FE/src/api/deepAgents.js` | Modify | `prov` on `run`, `runStateful`, `runStatefulWithFiles` (form), `stream` (ws.send) |
| `FE/src/api/documents.js` | Modify | `upload.documents_page` origin |
| `FE/src/logic/tracesPage.js` | Modify | `gate: 'Gate'` kind label |
| `FE/src/logic/complianceLive.js` | Modify | `askScoped`/`ccAsk`/`ccStreamTurn`/`ccEditRun`/`orchSubmitNow` carry the origin |
| `FE/src/logic/{renderVals,instances,compliance,support,spacesLive,maintenance,migration,energy,corrections,choiceCards,hoistGraph,vendorHoist,ingestionLive}.js` | Modify | Each send names its origin |
| `FE/test/traceLabOriginsWire.test.mjs`, `traceLabGateLabel.test.mjs`, `traceLabChatOrigins.test.mjs`, `traceLabOrigins.test.mjs` | Create | Wire, label, plumbing, guard |

Other:

| File | Action | Responsibility |
|---|---|---|
| `/Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh` | Modify | P8: copy `migrations/`, pass `TRACE_LAB_TEST_DB_URL` |
| `docker-compose.testenv.yml`, `docker-compose.local-db.yml` | Modify | `TRACE_LAB_SCHEMA: apply` on the two throwaway databases (spec §1.9) |

### Names later milestones import (fixed here, spec R36)

```python
# src/agents/replay_guard.py
EVAL: ContextVar[dict | None]                   # {"run_id": <uuid str>, "case_key": <str>, "rep": <int>} or None
def eval_context() -> dict | None

# src/trace_lab/migrate.py
SCHEMA = "plenum_tracelab"; MIGRATIONS_DIR: Path; FILE_RE; LOCK_TIMEOUT = "3s"; ADVISORY_LOCK_KEY = 7310152026
SHIPPED_CHECKSUMS: dict[int, str]
class MigrationFilesError(RuntimeError)
@dataclass(frozen=True) class MigrationFile: version, name, path, checksum, sql
@dataclass class SchemaState: status, expected_version, applied_version, pending, problems, checked_at; as_dict()
def iso(dt) -> str
def checksum(raw: bytes) -> str
def load_files(directory: Path = MIGRATIONS_DIR) -> list[MigrationFile]
def split_statements(sql: str) -> list[str]
def lint_file(f: MigrationFile) -> list[str]
async def read_ledger(conn) -> list[dict] | None
async def read_catalog(conn) -> dict
def expected_catalog(directory: Path = MIGRATIONS_DIR) -> dict
def compare_catalog(expected: dict, live: dict, *, same_major: bool = True) -> tuple[list[str], list[dict]]
async def verify(engine=None, *, directory: Path | None = None) -> SchemaState
async def apply_pending(engine=None, *, applied_by: str, through: int | None = None, dry_run: bool = False,
                        directory: Path | None = None) -> SchemaState
def apply_refusal(database: str, host: str) -> str | None   # None | "azure_host" | "not_in_allow_list"
def apply_allowed(database: str, host: str) -> bool
def schema_mode() -> str                                     # "verify" | "apply"
def scratch_url_ok(url: str) -> bool
async def current_database(engine=None) -> str
def db_host(url: str | None = None) -> str
def error_state(problem: str, *, expected_version: int | None = None) -> SchemaState
def main(argv: list[str] | None = None) -> int              # status | plan | apply | snapshot

# src/trace_lab/db.py
SCHEMA; STATE: SchemaState | None
def engine()                                               # database._get_engine(); no second engine
def ready() -> bool; def state() -> SchemaState | None
def set_state(st: SchemaState, *, ms: float | None = None) -> SchemaState
async def refresh_state(*, max_age_s: float = 30.0) -> SchemaState
async def require_ready() -> None                          # FastAPI dependency: 503 + Retry-After: 30
def require_trace_store() -> None                          # 503 reason trace_store_unavailable
def unavailable_body() -> dict[str, Any]
def http_error(status: int, reason: str, error: str, **extra) -> HTTPException
                                                           # one name, one signature: M2's ReviewError converter is
                                                           # db.http_error_for(exc) (built on this), never a second
                                                           # http_error, which would shadow this one module-wide
@dataclass(frozen=True) class Scope: org_id, platform, is_superadmin, is_reviewer, acting
def scope_for(principal, organization_id) -> Scope         # 403 not_your_company, 400 bad_company
def require_reader(principal, organization_id) -> Scope    # 403 reviewers_only
def require_reviewer(principal, organization_id) -> str    # 403 reviewers_only, 400 name_a_company
def require_platform(principal, organization_id=None) -> Scope   # 403 platform_only
def scope_sql(principal, org, alias="tt") -> tuple[str, dict]    # trace._scope_sql qualified by alias (params org/uid)
def company_where(org, *, alias="") -> tuple[str, dict]          # ("<a.>organization_id = CAST(:tl_org AS uuid)", {"tl_org": org})
KEEP_BY_VERSION: dict[int, tuple[tuple[str, str], ...]]   # the purge keep-list registry (R4); the literal stays {1: ()}.
                                                          # A later milestone appends one statement at the end of db.py:
                                                          # KEEP_BY_VERSION[N] = ((table, turn column), ...)
def register_keep(version: int, *pairs: tuple[str, str]) -> None
def keep_tables() -> tuple[tuple[str, str], ...] | None    # None unless ready
OFFBOARD_BY_VERSION: dict[int, tuple[tuple[str, str], ...]]   # (label, DELETE/UPDATE sql with :org); the literal stays {1: (...)}.
                                                              # A later milestone appends one statement at the end of db.py:
                                                              # OFFBOARD_BY_VERSION[N] = ((label, sql), ...)
def register_offboarding(version: int, *steps: tuple[str, str]) -> None
def offboarding_steps(applied_version: int) -> list[tuple[str, str]]
class CompanyHasOpenJobs(Exception)
async def delete_company_rows(organization_id: str, *, actor_id: str, dry_run: bool, job_id: str | None = None) -> dict[str, int]

# src/trace_lab/jobs.py
STALE_AFTER_S = 120; HEARTBEAT_EVERY_S = 15; KINDS; STATUSES; OPEN_STATUSES; FINISH_STATUSES
class JobConflict(Exception); class CapReached(Exception); class JobNotRunning(Exception)
async def create(*, kind, scope_key, organization_id, params, usd_cap, usd_estimate=None, total=None,
                 actor_id, actor_email=None) -> dict
async def claim(job_id, *, worker=None) -> dict | None
async def heartbeat(job_id, *, progress_patch=None) -> bool          # True = stop
async def reserve(job_id, usd: Decimal) -> None                      # CapReached | JobNotRunning
async def release(job_id, usd: Decimal) -> None
async def checkpoint(conn, job_id, *, usd, released, done_delta=1, progress_patch=None) -> None
async def add_spend(job_id, usd: Decimal) -> bool                    # False once the cap is reached
async def finish(job_id, status, error=None) -> dict | None          # done|failed|capped|paused|cancelled
async def request_stop(job_id) -> bool
async def resume(job_id, *, usd_cap: Decimal | None = None) -> dict | None
async def discard(job_id, *, error="Discarded by a person after an interruption.") -> dict | None
async def mark_interrupted(stale_after_s: int = STALE_AFTER_S) -> list[str]
async def get(job_id, *, organization_id: str | None, platform: bool = False) -> dict | None
async def list_jobs(*, organization_id: str | None, kind: str | None = None, limit: int = 50, platform: bool = False) -> list[dict]
def worker_id() -> str
def run_in_background(job_id, coro_factory) -> asyncio.Task
@dataclass class JobContext: job_id, job, worker; heartbeat(progress=None) -> bool; add_spend(usd) -> bool; stopping() -> bool
def register_kind(kind: str, worker: Callable[[JobContext], Awaitable[str]]) -> None
    # ONE worker per kind (a second call replaces the first). golden_grade is M3's worker; M6's
    # eval grading registers through M3's runner.register_grade_handler (params["handler"]), never here.
async def start(job_id) -> asyncio.Task | None

# src/trace_lab/provenance.py
PROVENANCE_RULES = "prov-1"; LEGACY_CLASSIFIER = "legacy-1"; CHANNELS; CLIENT_ORIGINS; SERVER_ORIGINS; ORIGIN_PARAM_KEYS; TEMPLATES
RequestProvenance, TurnProvenance, TurnMeta (frozen dataclasses); REQUEST: ContextVar[RequestProvenance | None]
def accept_origin(origin) -> str | None; def accept_origin_params(base, params) -> dict | None; def accept_page(page) -> str | None
def set_request(*, route, origin=None, origin_params=None, page=None, context=None, files=0, ingest_source=None) -> None
def form_params(raw: str | None) -> dict | None
def capture(payload, kw) -> TurnProvenance
def decide(p, *, turn_id, question, answer, route, session_id, organization_id=None) -> TurnMeta
def match_template(question) -> tuple[str, str] | None
def row_params(m: TurnMeta) -> dict
async def write_turn_meta(conn, t, answer) -> None
def infer_channel(turn, *, activity_input=None) -> dict
def inferred_channel_sql(alias: str = "tt") -> str; INFERRED_CHANNEL_SQL: str

# src/trace_lab/guards.py
CLOSED_SCHEMAS; CLOSED_TABLE_PREFIXES; CLOSED_TABLES; REFUSAL_TEXT; GUARDED_TOOLS
def closed_table(name) -> bool; def sql_names_closed(sql) -> str | None; def drop_closed(rows, *, key="table") -> list
def selftest() -> list[str]

# src/agents/trace.py
async def purge(days: int | None = None, *, keep: Sequence[tuple[str, str]] | None) -> int
def on_gate_open(name, *, agent, input) -> str | None
def on_gate_close(run_id, output, *, ok, error=None) -> None
def record_gate_call(*, agent, stage, model, system, user, output=None, usage=None, ms=0.0, error=None) -> None
Turn.prov  # TurnProvenance | None
# gate span: kind "gate"; name "el_udr" (agent "el_udr") or "contract_compose" (agent "contract_analyst");
# output {"outcome", "changed", "verdict", ..., "answer_after"}

# tests/trace_lab_scratch.py
SCRATCH_URL; requires_scratch; assert_scratch(url=SCRATCH_URL)
async def fresh_schema(monkeypatch, *, apply: bool = True) -> AsyncEngine
scratch_db, tl_scratch      # async pytest fixtures yielding the same engine
# tests/test_trace_lab_purge_sql.py: EXCUSED_TURN_COLUMNS (the set test_keep_tables_cover_every_turn_reference excuses;
#   the only excuse registry: M2 adds annotation_events.turn_id, M6 monitor_verdicts.turn_id; no db.KEEP_EXCUSED)
# tests/test_trace_lab_migrate_sql.py: test_catalog_snapshot_matches_the_files
```

```js
// apps/frontend/src/logic/traceLabOrigins.js
export const ORIGINS;            // frozen; values == provenance.CLIENT_ORIGINS keys
export function originOf(base, variant);
export function pageOf(state);
export function provFields(prov); // { origin, origin_params, page }
// deepAgentsApi: run(message, sessionId, context, prov); runStateful(message, sessionId, context, signal, prov);
// runStatefulWithFiles(..., opts) with opts.origin / opts.originParams / opts.page; stream(sessionId, message, context, on, prov)
```

---

## Before you start (once)

- [ ] **Save the three test scripts** to your scratchpad directory. In every step below, `tlda.sh`, `tludr.sh` and `tlsnap.sh` mean these saved files' full paths.

`tlda.sh` — svc-deepagents tests (the shared recipe; the scratch URL is passed only when you export it, so SQL tests skip otherwise; `migrations/` is copied once it exists):
```sh
#!/bin/sh
# Usage: sh tlda.sh <pytest targets...>
set -e
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-$(date +%s)-$$
D=apps/backend/cafm-connector-service-final
docker exec $C sh -lc "test ! -e $R && mkdir -p $R/$D/svc-deepagents $R/$D/svc-operations-intelligence $R/apps/frontend"
for d in src tests skills migrations pyproject.toml; do
  if [ -e "$D/svc-deepagents/$d" ]; then docker cp "$D/svc-deepagents/$d" "$C:$R/$D/svc-deepagents/$d" >/dev/null; fi
done
docker cp apps/frontend/src $C:$R/apps/frontend/src >/dev/null
docker cp $D/svc-operations-intelligence/src $C:$R/$D/svc-operations-intelligence/src >/dev/null
N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy \
  -e TRACE_LAB_TEST_DB_URL="${TRACE_LAB_TEST_DB_URL:-}" $C \
  sh -lc "cd $R/$D/svc-deepagents && python -m pytest $* -q -p no:cacheprovider -p no:warnings"
```

`tludr.sh` — svc-udr tests:
```sh
#!/bin/sh
# Usage: sh tludr.sh <pytest targets...>
set -e
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
C=hoistra-testenv-svc-udr-1; R=/tmp/tl-udr-$(date +%s)-$$
D=apps/backend/cafm-connector-service-final/svc-udr
docker exec $C sh -lc "test ! -e $R && mkdir -p $R/$D"
for d in src tests pyproject.toml; do docker cp "$D/$d" "$C:$R/$D/$d" >/dev/null; done
N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N $C \
  sh -lc "cd $R/$D && python -m pytest $* -q -p no:cacheprovider -p no:warnings"
```

`tlsnap.sh` — regenerate `catalog.json` against the scratch container and copy it back:
```sh
#!/bin/sh
# Usage: sh tlsnap.sh      (the tl-scratch-pg container must be running)
set -e
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-snap-$(date +%s)-$$
D=apps/backend/cafm-connector-service-final/svc-deepagents
docker exec $C sh -lc "test ! -e $R && mkdir -p $R/$D"
for d in src migrations pyproject.toml; do docker cp "$D/$d" "$C:$R/$D/$d" >/dev/null; done
N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy $C \
  sh -lc "cd $R/$D && python -m src.trace_lab.migrate snapshot --db-url postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch"
docker cp "$C:$R/$D/migrations/trace_lab/catalog.json" "$D/migrations/trace_lab/catalog.json"
echo "catalog.json copied back to $D/migrations/trace_lab/catalog.json"
```

- [ ] **Record the baselines** (task log): `sh tlda.sh tests/` (pass/fail counts; memory says about 992 in testenv), `sh tludr.sh tests/`, and `cd apps/frontend && npm test`. Task 30 compares against these.
- [ ] **Check the tree is yours to edit**: `git -C /Users/hussain/Desktop/hoist/Hoistra_Plenum status --short` — note any files other sessions have changed; do not touch them except through the exact-string edits below.

---

### Task 1: The golden-run context placeholder (`replay_guard.EVAL`)

**Files:**
- Modify: `DA/src/agents/replay_guard.py:22` (after `REPLAY`) and `:28-29` (after `is_replay`)
- Test: `DA/tests/test_replay_guard_eval.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `replay_guard.EVAL: ContextVar[dict | None]` (default None), `replay_guard.eval_context() -> dict | None`. M0's `provenance.capture` reads it; M3 sets it and makes `trace.begin`/`activity_log.record` honour it.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_replay_guard_eval.py`:
```python
"""A golden run's context (spec "Trace capture" 2.4). Defined in M0 so provenance can read it;
nothing sets it and no writer changes its behaviour until M3."""
from __future__ import annotations

from src.agents import replay_guard


def test_eval_context_is_none_for_every_live_turn():
    assert replay_guard.EVAL.get() is None
    assert replay_guard.eval_context() is None


def test_eval_context_is_independent_of_replay():
    ctx = {"run_id": "6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e", "case_key": "C1", "rep": 1}
    token = replay_guard.EVAL.set(ctx)
    try:
        assert replay_guard.eval_context() == ctx
        assert replay_guard.is_replay() is False      # setting EVAL alone is not a replay
    finally:
        replay_guard.EVAL.reset(token)
    assert replay_guard.eval_context() is None
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_replay_guard_eval.py`
Expected: FAIL — `AttributeError: module 'src.agents.replay_guard' has no attribute 'EVAL'`.

- [ ] **Step 3: Implement**

Edit `DA/src/agents/replay_guard.py` — old:
```python
REPLAY: ContextVar[bool] = ContextVar("cafm_skill_lab_replay", default=False)
```
new:
```python
REPLAY: ContextVar[bool] = ContextVar("cafm_skill_lab_replay", default=False)

#: A golden run (trace_lab/runner.py, Trace Lab M3). REPLAY stays set with it, so nothing is
#: written to other services, threads or memories; M3 turns the trace and the activity log back on
#: while this is set, so a golden answer can be reviewed and pinned to a span.
#: {"run_id": <uuid str>, "case_key": <str>, "rep": <int>} or None. Defined in M0 for provenance
#: (trace_lab/provenance.py capture); no writer reads it yet.
EVAL: ContextVar[dict | None] = ContextVar("hoist_trace_lab_eval", default=None)
```
Edit — old:
```python
def is_replay() -> bool:
    return bool(REPLAY.get())
```
new:
```python
def is_replay() -> bool:
    return bool(REPLAY.get())


def eval_context() -> dict | None:
    """The golden-run context set by the M3 runner, or None for every live turn."""
    return EVAL.get()
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_replay_guard_eval.py tests/test_skill_lab.py`
Expected: PASS (the skill-lab suite shows REPLAY is untouched).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/replay_guard.py`, `DA/tests/test_replay_guard_eval.py`.

---

### Task 2: `migrate.py` core — files, checksums, splitter, lint, modes, catalog compare

**Files:**
- Create: `DA/src/trace_lab/__init__.py` (temporary one-line package marker; Task 7 replaces it), `DA/src/trace_lab/migrate.py`
- Test: `DA/tests/test_trace_lab_migrate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `migrate.SCHEMA`, `MIGRATIONS_DIR`, `CATALOG_FILE`, `FILE_RE`, `LOCK_TIMEOUT`, `FIRST_STATEMENT`, `ADVISORY_LOCK_KEY`, `LOCK_RETRIES`, `LOCK_RETRY_SLEEP_S`, `SHIPPED_CHECKSUMS`, `MigrationFilesError`, `MigrationFile`, `SchemaState`, `iso`, `checksum`, `load_files`, `split_statements`, `lint_file`, `compare_catalog`, `schema_mode`, `apply_refusal`, `apply_allowed`, `scratch_url_ok`.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_migrate.py`:
```python
"""Trace Lab migrations without a database (spec "Data model" 1.2-1.4, 1.8)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.trace_lab import migrate

FUNC = """-- header comment
SET LOCAL lock_timeout = '3s';

CREATE FUNCTION plenum_tracelab.f() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- a comment inside the body stays
    RAISE EXCEPTION 'plenum_tracelab.% refused', TG_TABLE_NAME;
END
$$;
CREATE TABLE plenum_tracelab.t (id integer PRIMARY KEY);
"""


def _write(d: Path, name: str, body: str = "SET LOCAL lock_timeout = '3s';\n") -> None:
    (d / name).write_text(body, encoding="utf-8")


def test_checksum_ignores_bom_and_line_endings():
    lf = migrate.checksum(b"SELECT 1;\nSELECT 2;\n")
    assert migrate.checksum(b"\xef\xbb\xbfSELECT 1;\r\nSELECT 2;\r\n") == lf
    assert migrate.checksum(b"SELECT 1;\rSELECT 2;\r") == lf
    assert migrate.checksum(b"SELECT 1;\nSELECT 2;") != lf      # any other byte change is an edit
    assert len(lf) == 64 and lf == lf.lower()


def test_split_statements_keeps_dollar_bodies_whole():
    stmts = migrate.split_statements(FUNC)
    assert len(stmts) == 3
    assert stmts[0] == "SET LOCAL lock_timeout = '3s'"
    assert stmts[1].startswith("CREATE FUNCTION plenum_tracelab.f()") and stmts[1].endswith("$$")
    assert "-- a comment inside the body stays" in stmts[1] and "RAISE EXCEPTION" in stmts[1]
    assert stmts[2] == "CREATE TABLE plenum_tracelab.t (id integer PRIMARY KEY)"


def test_load_files_reads_versions_in_order_and_skips_catalog(tmp_path):
    _write(tmp_path, "0002_two.sql")
    _write(tmp_path, "0001_one.sql")
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    files = migrate.load_files(tmp_path)
    assert [(f.version, f.name) for f in files] == [(1, "0001_one.sql"), (2, "0002_two.sql")]
    assert files[0].checksum == migrate.checksum(b"SET LOCAL lock_timeout = '3s';\n")


@pytest.mark.parametrize("names, why", [
    (["0001_one.sql", "0003_three.sql"], "expected version 0002"),
    (["0002_two.sql"], "expected version 0001"),
    (["0001_one.sql", "0001_again.sql"], "is also"),
    (["0001_One.sql"], "not a migration file name"),
    (["1_one.sql"], "not a migration file name"),
])
def test_load_files_refuses_gaps_duplicates_and_bad_names(tmp_path, names, why):
    for n in names:
        _write(tmp_path, n)
    with pytest.raises(migrate.MigrationFilesError, match=why):
        migrate.load_files(tmp_path)


def test_a_missing_directory_is_a_load_error(tmp_path):
    with pytest.raises(migrate.MigrationFilesError, match="no migrations directory"):
        migrate.load_files(tmp_path / "nope")


def test_lint_catches_grants_other_schemas_if_not_exists_and_late_text(tmp_path):
    body = ("SET LOCAL lock_timeout = '3s';\n"
            "GRANT SELECT ON plenum_tracelab.jobs TO someone;\n"
            "CREATE INDEX ix_bad ON plenum_cafm.agent_trace_turns (turn_id);\n"
            "CREATE TABLE IF NOT EXISTS plenum_tracelab.x (id integer);\n"
            "CREATE INDEX CONCURRENTLY ix_c ON plenum_tracelab.x (id);\n"
            "CREATE TABLE plenum_tracelab.y (id integer); SELECT 1;\n"
            "COMMENT ON TABLE plenum_tracelab.y IS 'it''s wrong';\n")
    _write(tmp_path, "0001_bad.sql", body)
    problems = "\n".join(migrate.lint_file(migrate.load_files(tmp_path)[0]))
    for needle in ("GRANT is DBA work", "names another schema", "IF NOT EXISTS", "CONCURRENTLY",
                   "nothing may follow a ';'", "no apostrophes"):
        assert needle in problems, needle


def test_lint_wants_the_lock_timeout_first(tmp_path):
    _write(tmp_path, "0001_x.sql", "CREATE TABLE plenum_tracelab.x (id integer);\n")
    assert any("first statement" in p for p in migrate.lint_file(migrate.load_files(tmp_path)[0]))


def test_lint_accepts_a_clean_file(tmp_path):
    _write(tmp_path, "0001_ok.sql", FUNC + "COMMENT ON TABLE plenum_tracelab.t IS 'A clean comment.';\n")
    assert migrate.lint_file(migrate.load_files(tmp_path)[0]) == []


@pytest.mark.parametrize("value, mode", [
    (None, "verify"), ("verify", "verify"), ("APPLY", "apply"), (" apply ", "apply"), ("yes", "verify"), ("", "verify"),
])
def test_unknown_mode_falls_back_to_verify(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv("TRACE_LAB_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("TRACE_LAB_SCHEMA", value)
    assert migrate.schema_mode() == mode


@pytest.mark.parametrize("database, host, reason", [
    ("hoistra", "postgres", None),
    ("tracelab_scratch", "127.0.0.1", None),
    ("hoistra_test", "postgres", "not_in_allow_list"),
    ("hoistra", "plenum-agentic-ai.postgres.database.azure.com", "azure_host"),
    ("hoistra", "db.azure.example.net", "azure_host"),
])
def test_startup_apply_refused_off_the_allow_list_and_on_azure_hosts(monkeypatch, database, host, reason):
    monkeypatch.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    assert migrate.apply_refusal(database, host) == reason
    assert migrate.apply_allowed(database, host) is (reason is None)


def test_the_apply_allow_list_is_configurable(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_APPLY_DATABASES", "mine, other")
    assert migrate.apply_allowed("other", "localhost")
    assert not migrate.apply_allowed("hoistra", "localhost")


@pytest.mark.parametrize("url, ok", [
    ("postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch", True),
    ("postgresql+asyncpg://postgres:scratch@127.0.0.1:55433/tracelab_scratch", True),
    ("postgresql+asyncpg://postgres:scratch@localhost:55432/tl_scratch", False),       # the old name: refused
    ("postgresql+asyncpg://u:p@localhost/hoistra_test", False),
    ("postgresql+asyncpg://u:p@plenum.postgres.database.azure.com/tracelab_scratch", False),
    ("", False),
])
def test_scratch_url_guard(url, ok):
    assert migrate.scratch_url_ok(url) is ok


BASE = {
    "tables": ["jobs"],
    "columns": {"jobs": {"id": {"type": "uuid", "notnull": True, "default": "gen_random_uuid()", "identity": ""}}},
    "indexes": {"jobs_pkey": {"table": "jobs", "def": "CREATE UNIQUE INDEX jobs_pkey ON plenum_tracelab.jobs USING btree (id)"}},
    "constraints": {"jobs.jobs_pkey": {"type": "p", "def": "PRIMARY KEY (id)"}},
    "triggers": {},
    "functions": {"refuse_change()": "abc"},
}


def test_compare_catalog_names_each_difference():
    live = copy.deepcopy(BASE)
    live["columns"]["jobs"]["foo"] = {"type": "text", "notnull": False, "default": None, "identity": ""}
    del live["indexes"]["jobs_pkey"]
    live["constraints"]["jobs.jobs_pkey"]["def"] = "PRIMARY KEY (id, x)"
    live["functions"]["refuse_change()"] = "changed"
    problems, differs = migrate.compare_catalog(BASE, live, same_major=True)
    assert "jobs: extra column foo" in problems
    assert "jobs_pkey: missing" in problems
    assert "jobs.jobs_pkey: definition changed" in problems
    assert "refuse_change(): function body changed" in problems
    assert differs == []
    assert migrate.compare_catalog(BASE, copy.deepcopy(BASE)) == ([], [])


def test_definition_text_is_relaxed_across_major_versions():
    live = copy.deepcopy(BASE)
    live["constraints"]["jobs.jobs_pkey"]["def"] = "PRIMARY KEY ( id )x"
    problems, differs = migrate.compare_catalog(BASE, live, same_major=False)
    assert problems == [] and differs[0]["object"] == "jobs.jobs_pkey"
    live["columns"]["jobs"]["id"]["type"] = "text"
    problems, _ = migrate.compare_catalog(BASE, live, same_major=False)
    assert problems == ["jobs.id: type 'uuid' expected, 'text' found"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: collection error — `ModuleNotFoundError: No module named 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`DA/src/trace_lab/__init__.py` (Task 7 replaces this whole file):
```python
"""Hoist Trace Lab (svc-deepagents). Task 7 of the M0 plan writes this module in full."""
```

`DA/src/trace_lab/migrate.py`:
```python
"""Trace Lab's schema: numbered SQL files, a ledger, and a read-only verify at startup (spec D3).

Every other store in this service runs CREATE ... IF NOT EXISTS on every boot (chat_threads,
trace, activity_log). That silently accepts a table of another shape, which is how the earlier
evaluation build drifted. Trace Lab ships numbered files in migrations/trace_lab/, records each
applied file in plenum_tracelab.schema_migrations with its checksum, and at startup only READS:
the ledger against the files, then the live catalog against catalog.json. DDL runs only through
apply_pending: at startup on a scratch, testenv or local-db database named in
TRACE_LAB_APPLY_DATABASES (never an Azure host), or by Hussain through the CLI in a terminal.

split_statements and the lock-timeout retry are ports of svc-operations-intelligence
src/db.py (_split_sql, _run_ddl_statement).

CLI (python -m src.trace_lab.migrate):
  status                     verify and print the state (read-only)
  plan                       host, database, ledger, pending files and their statements, open
                             jobs (read-only)
  apply --expect-db NAME --through NNNN --applied-by NAME [--dry-run]
                             interactive terminal only; asks for the database name to be typed
  snapshot --db-url URL      apply every file to a scratch database and write catalog.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

SCHEMA = "plenum_tracelab"
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "trace_lab"
CATALOG_FILE = "catalog.json"
FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "60s"
VERIFY_STATEMENT_TIMEOUT = "15s"
FIRST_STATEMENT = f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"
ADVISORY_LOCK_KEY = 7310152026
LOCK_RETRIES = 4
LOCK_RETRY_SLEEP_S = 3.0
MODES = ("verify", "apply")
DEFAULT_APPLY_DATABASES = "hoistra,tracelab_scratch"
SCRATCH_DATABASES = frozenset({"tracelab_scratch"})
SCRATCH_HOSTS = frozenset({"localhost", "127.0.0.1", "host.docker.internal"})
OPEN_JOB_STATUSES = ("queued", "running", "paused", "interrupted")

#: Checksums of the files applied to hoistra_test. Append only: a pinned file is never edited
#: (tests/test_trace_lab_migrate.py::test_shipped_files_are_never_edited); a correction is a new
#: file. After Hussain applies a file the CLI prints the line to add here.
SHIPPED_CHECKSUMS: dict[int, str] = {}

Status = Literal["ready", "absent", "behind", "ahead", "checksum_mismatch", "drift", "error"]


class MigrationFilesError(RuntimeError):
    """The migrations directory, or catalog.json, is not usable."""


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    path: Path
    checksum: str
    sql: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """2026-10-15T09:12:03Z - the form every Trace Lab response uses for a check time."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SchemaState:
    status: Status
    expected_version: int
    applied_version: int | None
    pending: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "expected_version": self.expected_version,
                "applied_version": self.applied_version, "pending": list(self.pending),
                "problems": list(self.problems), "checked_at": iso(self.checked_at)}


# ── files ────────────────────────────────────────────────────────────────────────────

def _normalise(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def checksum(raw: bytes) -> str:
    """sha256 hex of the bytes after dropping a UTF-8 BOM and turning CRLF or CR into LF."""
    return hashlib.sha256(_normalise(raw)).hexdigest()


def load_files(directory: Path = MIGRATIONS_DIR) -> list[MigrationFile]:
    """Every NNNN_name.sql in the directory, in order. Versions start at 1 with no gap."""
    d = Path(directory)
    if not d.is_dir():
        raise MigrationFilesError(f"no migrations directory at {d}")
    files: list[MigrationFile] = []
    seen: dict[int, str] = {}
    for p in sorted(d.iterdir()):
        if p.suffix != ".sql" or p.name.startswith("."):
            continue
        m = FILE_RE.match(p.name)
        if not m:
            raise MigrationFilesError(f"{p.name}: not a migration file name (NNNN_name.sql)")
        version = int(m.group(1))
        if version in seen:
            raise MigrationFilesError(f"{p.name}: version {version:04d} is also {seen[version]}")
        seen[version] = p.name
        raw = p.read_bytes()
        files.append(MigrationFile(version=version, name=p.name, path=p, checksum=checksum(raw),
                                   sql=_normalise(raw).decode("utf-8")))
    files.sort(key=lambda f: f.version)
    for i, f in enumerate(files, start=1):
        if f.version != i:
            raise MigrationFilesError(f"{f.name}: expected version {i:04d}; versions start at 0001 with no gaps")
    return files


_DOLLAR_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")


def split_statements(sql: str) -> list[str]:
    """Statements on top-level ';' (port of svc-operations-intelligence src/db.py _split_sql).

    Dollar-quote aware: a ';' inside a $tag$ ... $tag$ body does not end a statement. Full-line
    '--' comments are dropped only outside a dollar-quoted body. A statement ends with ';' at the
    end of a line; the trailing ';' is removed."""
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    for line in sql.splitlines():
        if dollar_tag is None and line.strip().startswith("--"):
            continue
        buf.append(line)
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            if dollar_tag is None:
                dollar_tag = tag
            elif tag == dollar_tag:
                dollar_tag = None
            pos = m.end()
        if dollar_tag is None and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


_FORBIDDEN = (
    (re.compile(r"\bGRANT\b", re.I), "GRANT is DBA work"),
    (re.compile(r"\bREVOKE\b", re.I), "REVOKE is DBA work"),
    (re.compile(r"\bCONCURRENTLY\b", re.I), "CONCURRENTLY: every table is new"),
    (re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.I), "IF NOT EXISTS: a versioned file must fail loudly"),
    (re.compile(r"\b(?:plenum_cafm|plenum_scoped|plenum_eval|public)\s*\.", re.I), "names another schema"),
)
_TARGETS = (
    re.compile(r"^CREATE\s+SCHEMA\s+(\S+)", re.I),
    re.compile(r"^COMMENT\s+ON\s+SCHEMA\s+(\S+)", re.I),
    re.compile(r"^CREATE\s+(?:UNLOGGED\s+)?TABLE\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+\S+\s+.*?\bON\s+([^\s(]+)", re.I | re.S),
    re.compile(r"^ALTER\s+(?:TABLE|INDEX|FUNCTION)\s+(?:ONLY\s+)?([^\s(]+)", re.I),
    re.compile(r"^DROP\s+(?:TABLE|INDEX|FUNCTION)\s+([^\s(]+)", re.I),
    re.compile(r"^DROP\s+TRIGGER\s+\S+\s+ON\s+([^\s(]+)", re.I),
    re.compile(r"^COMMENT\s+ON\s+(?:TABLE|COLUMN|FUNCTION|INDEX|CONSTRAINT\s+\S+\s+ON)\s+([^\s(]+)", re.I),
    re.compile(r"^(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+([^\s(]+)", re.I),
)
_REFERENCES = re.compile(r"\bREFERENCES\s+([^\s(]+)", re.I)
_COMMENT_LITERAL = re.compile(r"^COMMENT\s+ON\b.*?\bIS\s+'(.*)'$", re.I | re.S)


def lint_file(f: MigrationFile) -> list[str]:
    """The file rules of spec 1.2: lock_timeout first; objects only in plenum_tracelab; no GRANT,
    REVOKE, CONCURRENTLY or IF NOT EXISTS; nothing after a ';' on its line; one-line comment
    strings without apostrophes. Empty list = clean."""
    problems: list[str] = []
    stmts = split_statements(f.sql)
    if not stmts or stmts[0] != FIRST_STATEMENT:
        problems.append(f"{f.name}: the first statement must be exactly {FIRST_STATEMENT};")
    dollar: str | None = None
    for n, line in enumerate(f.sql.splitlines(), start=1):
        if dollar is None and line.strip().startswith("--"):
            continue
        stripped = line.rstrip()
        if dollar is None and ";" in stripped and stripped.find(";") != len(stripped) - 1:
            problems.append(f"{f.name}:{n}: nothing may follow a ';' on its line")
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            dollar = tag if dollar is None else (None if tag == dollar else dollar)
            pos = m.end()
    for i, s in enumerate(stmts, start=1):
        if re.match(r"^SET\s+LOCAL\s+", s, re.I):
            continue
        for rx, why in _FORBIDDEN:
            if rx.search(s):
                problems.append(f"{f.name}: statement {i}: {why}")
        target = None
        for rx in _TARGETS:
            m = rx.match(s)
            if m:
                target = m.group(1)
                break
        if target is None:
            problems.append(f"{f.name}: statement {i}: not a statement this check understands "
                            f"({' '.join(s.split())[:60]})")
        elif re.match(r"^(?:CREATE|COMMENT\s+ON)\s+SCHEMA\b", s, re.I):
            if target != SCHEMA:
                problems.append(f"{f.name}: statement {i}: only schema {SCHEMA} may be created")
        elif not target.startswith(SCHEMA + "."):
            problems.append(f"{f.name}: statement {i}: {target} is outside {SCHEMA}")
        for ref in _REFERENCES.findall(s):
            if not ref.startswith(SCHEMA + "."):
                problems.append(f"{f.name}: statement {i}: references {ref} outside {SCHEMA}")
        if re.match(r"^COMMENT\s+ON\b", s, re.I):
            lit = _COMMENT_LITERAL.match(s)
            if lit is None or "'" in lit.group(1) or "\n" in lit.group(1):
                problems.append(f"{f.name}: statement {i}: a comment string is one line with no apostrophes")
    return problems


# ── catalog comparison ─────────────────────────────────────────────────────────────────

def _ws(s: Any) -> str:
    return " ".join(str(s or "").split())


def compare_catalog(expected: dict, live: dict, *, same_major: bool = True) -> tuple[list[str], list[dict[str, str]]]:
    """Problems that make the schema 'drift', and definition texts that differ only because the
    live Postgres major version formats them differently (logged, not failed)."""
    problems: list[str] = []
    differs: list[dict[str, str]] = []

    def text_diff(obj: str, exp: Any, got: Any) -> None:
        if _ws(exp) == _ws(got):
            return
        if same_major:
            problems.append(f"{obj}: definition changed")
        else:
            differs.append({"object": obj, "expected": _ws(exp)[:300], "found": _ws(got)[:300]})

    et, lt = set(expected.get("tables") or []), set(live.get("tables") or [])
    problems += [f"table {t}: missing" for t in sorted(et - lt)]
    problems += [f"table {t}: extra" for t in sorted(lt - et)]
    ec, lc = expected.get("columns") or {}, live.get("columns") or {}
    for t in sorted(et & lt):
        e_cols, l_cols = ec.get(t) or {}, lc.get(t) or {}
        problems += [f"{t}: missing column {c}" for c in sorted(set(e_cols) - set(l_cols))]
        problems += [f"{t}: extra column {c}" for c in sorted(set(l_cols) - set(e_cols))]
        for c in sorted(set(e_cols) & set(l_cols)):
            e, got = e_cols[c], l_cols[c]
            for k in ("type", "notnull", "identity"):
                if e.get(k) != got.get(k):
                    problems.append(f"{t}.{c}: {k} {e.get(k)!r} expected, {got.get(k)!r} found")
            text_diff(f"{t}.{c} default", e.get("default"), got.get("default"))
    for kind in ("indexes", "constraints", "triggers", "functions"):
        e_map, l_map = expected.get(kind) or {}, live.get(kind) or {}
        problems += [f"{name}: missing" for name in sorted(set(e_map) - set(l_map))]
        problems += [f"{name}: extra" for name in sorted(set(l_map) - set(e_map))]
        for name in sorted(set(e_map) & set(l_map)):
            e, got = e_map[name], l_map[name]
            if kind == "indexes":
                if e["table"] != got["table"]:
                    problems.append(f"{name}: on {got['table']}, expected {e['table']}")
                text_diff(name, e["def"], got["def"])
            elif kind == "constraints":
                if e["type"] != got["type"]:
                    problems.append(f"{name}: type {got['type']}, expected {e['type']}")
                text_diff(name, e["def"], got["def"])
            elif kind == "triggers":
                text_diff(name, e, got)
            elif e != got:   # functions: md5 of our own source text, never reformatted by Postgres
                problems.append(f"{name}: function body changed")
    return problems, differs


# ── modes and guards ──────────────────────────────────────────────────────────────────

def schema_mode() -> str:
    """TRACE_LAB_SCHEMA: 'verify' (default) or 'apply'. Anything else is verify, logged."""
    raw = (os.getenv("TRACE_LAB_SCHEMA") or "verify").strip().lower()
    if raw in MODES:
        return raw
    log.warning("trace_lab.schema.mode_invalid", value=raw[:40])
    return "verify"


def apply_refusal(database: str, host: str) -> str | None:
    """Why startup may not apply here, or None. Never on an Azure host (the test database.py:19-21
    uses), and only on a database named in TRACE_LAB_APPLY_DATABASES."""
    h = (host or "").lower()
    if h.endswith(".postgres.database.azure.com") or ".azure." in h:
        return "azure_host"
    allowed = {x.strip() for x in (os.getenv("TRACE_LAB_APPLY_DATABASES") or DEFAULT_APPLY_DATABASES).split(",") if x.strip()}
    if (database or "") not in allowed:
        return "not_in_allow_list"
    return None


def apply_allowed(database: str, host: str) -> bool:
    return apply_refusal(database, host) is None


def scratch_url_ok(url: str) -> bool:
    """A throwaway database the SQL tests and `snapshot` may drop plenum_tracelab in."""
    u = urlparse(url or "")
    return (u.hostname or "").lower() in SCRATCH_HOSTS and u.path.lstrip("/") in SCRATCH_DATABASES
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/__init__.py`, `DA/src/trace_lab/migrate.py`, `DA/tests/test_trace_lab_migrate.py`.

---

### Task 3: `0001_foundation.sql` and the file rules

**Files:**
- Create: `DA/migrations/trace_lab/0001_foundation.sql`
- Test: `DA/tests/test_trace_lab_migrate.py` (append)

**Interfaces:**
- Consumes: `migrate.load_files`, `migrate.lint_file`, `migrate.SHIPPED_CHECKSUMS` (Task 2).
- Produces: tables `plenum_tracelab.schema_migrations`, `plenum_tracelab.jobs` (R6 kinds and statuses), `plenum_tracelab.turn_meta` (R7 channels); functions `plenum_tracelab.refuse_change()`, `plenum_tracelab.supersede_only()` (later milestones attach them as triggers).

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_migrate.py`:
```python


# ── the shipped files (spec 1.2 file rules) ──────────────────────────────────────────

def test_files_are_named_and_numbered_contiguously():
    files = migrate.load_files()
    assert files and files[0].name == "0001_foundation.sql"
    assert [f.version for f in files] == list(range(1, len(files) + 1))


def test_every_file_starts_with_lock_timeout():
    for f in migrate.load_files():
        first = next(line for line in f.sql.splitlines() if line.strip() and not line.strip().startswith("--"))
        assert first == "SET LOCAL lock_timeout = '3s';", f.name


def test_no_file_touches_another_schema_or_grants():
    for f in migrate.load_files():
        assert migrate.lint_file(f) == [], f.name


def test_shipped_files_are_never_edited():
    files = {f.version: f for f in migrate.load_files()}
    for version, pinned in migrate.SHIPPED_CHECKSUMS.items():
        assert version in files, f"{version:04d} was applied to hoistra_test and its file is gone"
        assert files[version].checksum == pinned, (
            f"{files[version].name} was applied to hoistra_test and has been edited - write a new file instead")


def test_foundation_creates_the_ledger_jobs_turn_meta_and_the_two_guards():
    sql = migrate.load_files()[0].sql
    for name in ("CREATE TABLE plenum_tracelab.schema_migrations", "CREATE TABLE plenum_tracelab.jobs",
                 "CREATE TABLE plenum_tracelab.turn_meta", "CREATE FUNCTION plenum_tracelab.refuse_change()",
                 "CREATE FUNCTION plenum_tracelab.supersede_only()", "ux_jobs_one_open_per_scope"):
        assert name in sql, name
    assert "'replay'" not in sql and "turn_meta_backfill" not in sql      # R6, R7
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: FAIL — `MigrationFilesError: no migrations directory at .../migrations/trace_lab` in the five new tests.

- [ ] **Step 3: Implement**

`DA/migrations/trace_lab/0001_foundation.sql`:
```sql
-- 0001_foundation.sql (Trace Lab M0): schema, ledger, guard functions, jobs, turn_meta.
-- Spec: Data model 2.2, with R6 (job kinds and statuses) and R7 (channels). Applied only by
-- migrate.apply_pending; never edited once applied to hoistra_test (SHIPPED_CHECKSUMS).
SET LOCAL lock_timeout = '3s';

CREATE SCHEMA plenum_tracelab;
COMMENT ON SCHEMA plenum_tracelab IS 'Trace Lab evaluation data, owned by svc-deepagents src/trace_lab. Closed to every chat tool.';

CREATE TABLE plenum_tracelab.schema_migrations (
    version     integer     PRIMARY KEY CHECK (version > 0),
    name        text        NOT NULL CHECK (name ~ '^[0-9]{4}_[a-z0-9_]+[.]sql$'),
    checksum    text        NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL CHECK (length(applied_by) BETWEEN 1 AND 200)
);
COMMENT ON COLUMN plenum_tracelab.schema_migrations.checksum IS 'sha256 hex of the file bytes after dropping a UTF-8 BOM and turning CRLF or CR into LF.';
COMMENT ON COLUMN plenum_tracelab.schema_migrations.applied_by IS 'startup:<host> in apply mode, cli:<name> from the CLI, pytest or snapshot on a scratch database.';

CREATE FUNCTION plenum_tracelab.refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND coalesce(current_setting('plenum_tracelab.allow_purge', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'plenum_tracelab.% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END
$$;
COMMENT ON FUNCTION plenum_tracelab.refuse_change() IS 'Append-only guard. UPDATE always refused. DELETE and TRUNCATE only after SET LOCAL plenum_tracelab.allow_purge = on (company offboarding).';

CREATE FUNCTION plenum_tracelab.supersede_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.superseded_by IS NULL AND NEW.superseded_by IS NOT NULL
           AND (to_jsonb(NEW) - 'superseded_by' - 'superseded_at')
             = (to_jsonb(OLD) - 'superseded_by' - 'superseded_at') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'plenum_tracelab.%: a row is never edited; only superseded_by is set, once', TG_TABLE_NAME;
    END IF;
    IF coalesce(current_setting('plenum_tracelab.allow_purge', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'plenum_tracelab.%: rows are never deleted; write a superseding row', TG_TABLE_NAME;
END
$$;
COMMENT ON FUNCTION plenum_tracelab.supersede_only() IS 'Version-chain guard for annotations and labels: the only UPDATE sets superseded_by and superseded_at once.';

CREATE TABLE plenum_tracelab.jobs (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text          NOT NULL CHECK (kind IN ('golden_run','golden_grade','judge_run','proposal_grouping',
                                                             'proposal_depth_scan','proposal_cells','monitor_batch','company_delete')),
    status            text          NOT NULL DEFAULT 'queued'
                                    CHECK (status IN ('queued','running','paused','done','failed','capped','interrupted','cancelled')),
    organization_id   uuid,
    scope_key         text          NOT NULL CHECK (length(scope_key) BETWEEN 3 AND 200),
    params            jsonb         NOT NULL DEFAULT '{}'::jsonb,
    total             integer       CHECK (total IS NULL OR total >= 0),
    done              integer       NOT NULL DEFAULT 0 CHECK (done >= 0),
    progress          jsonb         NOT NULL DEFAULT '{}'::jsonb,
    usd_estimate      numeric(12,6) CHECK (usd_estimate IS NULL OR usd_estimate >= 0),
    usd_cap           numeric(12,6) CHECK (usd_cap IS NULL OR usd_cap >= 0),
    usd_spent         numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd_spent >= 0),
    usd_reserved      numeric(12,6) NOT NULL DEFAULT 0 CHECK (usd_reserved >= 0),
    stop_requested    boolean       NOT NULL DEFAULT false,
    worker            text,
    heartbeat_at      timestamptz,
    error             text          CHECK (error IS NULL OR length(error) <= 2000),
    created_by        uuid,
    created_by_email  text,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    updated_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT jobs_params_object CHECK (jsonb_typeof(params) = 'object'),
    CONSTRAINT jobs_params_no_secrets CHECK (NOT (params ?| ARRAY['authorization','bearer','token','access_token','refresh_token','api_key','password'])),
    CONSTRAINT jobs_model_spend_capped CHECK (kind = 'company_delete' OR usd_cap IS NOT NULL)
);
CREATE UNIQUE INDEX ux_jobs_one_open_per_scope ON plenum_tracelab.jobs (scope_key)
    WHERE status IN ('queued','running','paused','interrupted');
CREATE INDEX ix_jobs_org_kind_created ON plenum_tracelab.jobs (organization_id, kind, created_at DESC);
CREATE INDEX ix_jobs_running_heartbeat ON plenum_tracelab.jobs (heartbeat_at) WHERE status = 'running';
COMMENT ON COLUMN plenum_tracelab.jobs.organization_id IS 'NULL = platform scope: a judge run, monitoring across companies, or a company offboarding.';
COMMENT ON COLUMN plenum_tracelab.jobs.scope_key IS 'One open job per key, e.g. golden_run:<org>, judge_run:<judge>:<split>, monitor_batch:<judge>:<org or *>:<period>, company_delete:<org>.';
COMMENT ON COLUMN plenum_tracelab.jobs.params IS 'Inputs to resume the job. Never a bearer token or other credential: golden runs are browser-stepped for that reason.';
COMMENT ON COLUMN plenum_tracelab.jobs.progress IS 'Small per-kind detail for the UI, a few keys at most.';
COMMENT ON COLUMN plenum_tracelab.jobs.usd_reserved IS 'Spend reserved for calls in flight. Dispatch stops when usd_spent + usd_reserved + the next estimate exceeds usd_cap, and the job becomes capped.';
COMMENT ON COLUMN plenum_tracelab.jobs.heartbeat_at IS 'Set every 15 s and after every item. A running job whose heartbeat is older than 120 s is marked interrupted at startup.';
COMMENT ON COLUMN plenum_tracelab.jobs.worker IS 'hostname:pid of the process running the job.';

CREATE TABLE plenum_tracelab.turn_meta (
    turn_id             text        PRIMARY KEY,
    organization_id     uuid,
    channel             text        NOT NULL CHECK (channel IN ('chat','ui_action','support','upload','correction',
                                                                'report','workflow','golden')),
    origin              text        CHECK (origin IS NULL OR origin ~ '^[a-z][a-z0-9_.:-]{0,79}$'),
    origin_params       jsonb,
    page                text        CHECK (page IS NULL OR length(page) <= 200),
    channel_source      text        NOT NULL CHECK (channel_source IN ('recorded','client','inferred')),
    classifier_version  text        NOT NULL CHECK (length(classifier_version) BETWEEN 1 AND 40),
    context_text        text        CHECK (context_text IS NULL OR length(context_text) <= 16000),
    asker_question      text        CHECK (asker_question IS NULL OR length(asker_question) <= 4000),
    asker_role          text        CHECK (asker_role IN ('user','admin','superadmin')),
    asker_scope         jsonb,
    turn_mode           text        CHECK (turn_mode IN ('stream','rest','rerun','resume')),
    rerun_of            text,
    files_count         smallint    CHECK (files_count IS NULL OR files_count >= 0),
    golden_run_id       uuid,
    golden_case_key     text,
    rep                 smallint    CHECK (rep IS NULL OR rep >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT turn_meta_golden CHECK ((channel = 'golden') = (golden_run_id IS NOT NULL)),
    CONSTRAINT turn_meta_golden_case CHECK (golden_run_id IS NULL OR (golden_case_key IS NOT NULL AND rep IS NOT NULL))
);
CREATE INDEX ix_turn_meta_org_created ON plenum_tracelab.turn_meta (organization_id, created_at DESC);
CREATE INDEX ix_turn_meta_channel_created ON plenum_tracelab.turn_meta (channel, created_at DESC);
CREATE INDEX ix_turn_meta_golden ON plenum_tracelab.turn_meta (golden_run_id, golden_case_key, rep) WHERE golden_run_id IS NOT NULL;
COMMENT ON COLUMN plenum_tracelab.turn_meta.turn_id IS 'The trace turn id (agent_trace_turns.turn_id). No foreign key. Written in the same transaction as the turn row.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.channel_source IS 'recorded = server facts at write time; client = allow-listed origin sent by the page; inferred = templates or the fallback.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.origin IS 'Allow-listed origin id, e.g. chat.typed, chat.chip:home, ui.matrix_cell:risk, support.topic:ingest, report.card.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.context_text IS 'Page context sent with the question, cut to 16000 chars. Spans lose it after 90 days.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.asker_question IS 'The asker own words when the turn question holds something else. NULL when equal.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.asker_scope IS '{all_buildings, building_count, buildings (restricted askers, first 50), own_organization_id, acting_organization_id, view_as} at ask time.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.golden_run_id IS 'Soft reference to golden_runs.id until 0003 adds the foreign key (R8). Written under a SAVEPOINT, so a failure never drops the trace.';
COMMENT ON COLUMN plenum_tracelab.turn_meta.rerun_of IS 'For a correction re-run: the turn it re-answers (activity payload rerun_of).';
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: PASS. If `test_no_file_touches_another_schema_or_grants` lists a problem, fix the SQL line it names (never weaken the lint).
If this file already exists with the older `golden_run_id` comment ("no foreign key", which contradicts R8), reword it to the text above only while `0001` is unapplied, then re-run `sh tlsnap.sh` and the SQL tests (Task 9).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/migrations/trace_lab/0001_foundation.sql`, `DA/tests/test_trace_lab_migrate.py`. Report in the log: "0001 creates objects only in plenum_tracelab (lint clean)".

---

### Task 4: `migrate.py` database side — ledger, catalog read, verify, apply

**Files:**
- Modify: `DA/src/trace_lab/migrate.py` (append)
- Test: `DA/tests/test_trace_lab_migrate.py` (append)

**Interfaces:**
- Consumes: Task 2 names.
- Produces: `read_ledger(conn)`, `read_catalog(conn)`, `server_version_num(conn)`, `expected_catalog(directory)`, `error_state(problem, *, expected_version=None)`, `verify(engine=None, *, directory=None)`, `apply_pending(engine=None, *, applied_by, through=None, dry_run=False, directory=None)`, `current_database(engine=None)`, `db_host(url=None)`, private `_engine()`, `_apply_one(eng, f, applied_by) -> str | None`, `_is_lock_timeout(exc)`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_migrate.py`:
```python


# ── verify and apply without a database ──────────────────────────────────────────────

import asyncio  # noqa: E402


class _Dead:
    def connect(self):
        raise OSError("connection refused")

    def begin(self):
        raise OSError("connection refused")


async def test_verify_reports_error_when_the_database_cannot_be_reached():
    st = await migrate.verify(_Dead())
    assert st.status == "error" and "connection refused" in st.problems[0]
    assert st.expected_version >= 1 and st.applied_version is None


async def test_verify_reports_error_when_the_files_cannot_load(tmp_path):
    _write(tmp_path, "0002_x.sql")
    st = await migrate.verify(_Dead(), directory=tmp_path)
    assert st.status == "error" and st.problems[0].startswith("files:")


async def test_apply_pending_runs_nothing_unless_absent_or_behind(monkeypatch):
    async def drift(engine=None, *, directory=None):
        return migrate.SchemaState("drift", 1, 1, problems=["jobs: extra column foo"])

    async def never(*a, **k):
        raise AssertionError("applied on a drifted database")

    monkeypatch.setattr(migrate, "verify", drift)
    monkeypatch.setattr(migrate, "_apply_one", never)
    st = await migrate.apply_pending(_Dead(), applied_by="pytest")
    assert st.status == "drift"


async def test_apply_pending_applies_only_through_the_named_version(monkeypatch, tmp_path):
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0002_b.sql")
    states = iter([migrate.SchemaState("behind", 2, None, pending=[1, 2]),
                   migrate.SchemaState("behind", 2, 1, pending=[2])])

    async def fake_verify(engine=None, *, directory=None):
        return next(states)

    applied = []

    async def fake_apply_one(engine, f, applied_by):
        applied.append((f.version, applied_by))
        return None

    monkeypatch.setattr(migrate, "verify", fake_verify)
    monkeypatch.setattr(migrate, "_apply_one", fake_apply_one)
    st = await migrate.apply_pending(_Dead(), applied_by="cli:hussain", through=1, directory=tmp_path)
    assert applied == [(1, "cli:hussain")]
    assert st.status == "behind" and st.applied_version == 1


class _LockedBegin:
    async def __aenter__(self):
        raise RuntimeError("canceling statement due to lock timeout")

    async def __aexit__(self, *a):
        return False


class _Locked:
    def begin(self):
        return _LockedBegin()


async def test_a_lock_timeout_is_retried_then_reported(monkeypatch, tmp_path):
    _write(tmp_path, "0001_a.sql")
    monkeypatch.setattr(migrate, "LOCK_RETRY_SLEEP_S", 0)
    problem = await migrate._apply_one(_Locked(), migrate.load_files(tmp_path)[0], "pytest")
    assert problem.startswith("0001: could not get its lock after 4 attempts")


def test_applied_by_is_bounded():
    with pytest.raises(ValueError):
        asyncio.run(migrate.apply_pending(_Dead(), applied_by=""))


def test_db_host_reads_the_url():
    assert migrate.db_host("postgresql+asyncpg://u:p@Plenum.Postgres.Database.Azure.com:5432/x") == \
        "plenum.postgres.database.azure.com"
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: FAIL — `AttributeError: module 'src.trace_lab.migrate' has no attribute 'verify'` (and `_apply_one`, `db_host`).

- [ ] **Step 3: Implement**

Append to `DA/src/trace_lab/migrate.py`:
```python


# ── reading the database ──────────────────────────────────────────────────────────────

def _engine():
    from .. import database

    return database._get_engine()


async def read_ledger(conn) -> list[dict] | None:
    """The applied files, oldest first; None when there is no ledger table."""
    present = (await conn.execute(text("SELECT to_regclass('plenum_tracelab.schema_migrations') IS NOT NULL"))).scalar()
    if not present:
        return None
    rows = (await conn.execute(text(
        "SELECT version, name, checksum, applied_at, applied_by"
        " FROM plenum_tracelab.schema_migrations ORDER BY version"))).mappings().all()
    return [dict(r) for r in rows]


_TABLES_SQL = """
SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = :s AND c.relkind IN ('r', 'p') ORDER BY 1"""
_COLUMNS_SQL = """
SELECT c.relname AS tbl, a.attname AS col, format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull AS notnull, pg_get_expr(d.adbin, d.adrelid) AS dflt, a.attidentity::text AS ident
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY 1, 2"""
_INDEXES_SQL = """
SELECT i.relname AS name, t.relname AS tbl, pg_get_indexdef(i.oid) AS def
  FROM pg_index x
  JOIN pg_class i ON i.oid = x.indexrelid
  JOIN pg_class t ON t.oid = x.indrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s ORDER BY 1"""
_CONSTRAINTS_SQL = """
SELECT t.relname AS tbl, k.conname AS name, k.contype::text AS type, pg_get_constraintdef(k.oid) AS def
  FROM pg_constraint k
  JOIN pg_class t ON t.oid = k.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s AND k.contype IN ('c', 'p', 'u', 'f') ORDER BY 1, 2"""
_TRIGGERS_SQL = """
SELECT t.relname AS tbl, g.tgname AS name, pg_get_triggerdef(g.oid) AS def
  FROM pg_trigger g
  JOIN pg_class t ON t.oid = g.tgrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s AND NOT g.tgisinternal ORDER BY 1, 2"""
_FUNCTIONS_SQL = """
SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS sig, md5(p.prosrc) AS src
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = :s ORDER BY 1"""


async def read_catalog(conn) -> dict:
    """The live catalog of plenum_tracelab, in the shape catalog.json stores (spec 1.4)."""
    p = {"s": SCHEMA}
    tables = [r[0] for r in (await conn.execute(text(_TABLES_SQL), p)).all()]
    columns: dict[str, dict[str, dict[str, Any]]] = {}
    for r in (await conn.execute(text(_COLUMNS_SQL), p)).mappings().all():
        columns.setdefault(r["tbl"], {})[r["col"]] = {
            "type": r["type"], "notnull": bool(r["notnull"]), "default": r["dflt"], "identity": r["ident"] or ""}
    indexes = {r["name"]: {"table": r["tbl"], "def": _ws(r["def"])}
               for r in (await conn.execute(text(_INDEXES_SQL), p)).mappings().all()}
    constraints = {f"{r['tbl']}.{r['name']}": {"type": r["type"], "def": _ws(r["def"])}
                   for r in (await conn.execute(text(_CONSTRAINTS_SQL), p)).mappings().all()}
    triggers = {f"{r['tbl']}.{r['name']}": _ws(r["def"])
                for r in (await conn.execute(text(_TRIGGERS_SQL), p)).mappings().all()}
    functions = {r["sig"]: r["src"] for r in (await conn.execute(text(_FUNCTIONS_SQL), p)).mappings().all()}
    return {"tables": tables, "columns": columns, "indexes": indexes, "constraints": constraints,
            "triggers": triggers, "functions": functions}


async def server_version_num(conn) -> int:
    return int((await conn.execute(text("SELECT current_setting('server_version_num')::int"))).scalar() or 0)


def expected_catalog(directory: Path = MIGRATIONS_DIR) -> dict:
    p = Path(directory) / CATALOG_FILE
    if not p.is_file():
        raise MigrationFilesError(f"{CATALOG_FILE} is missing")
    return json.loads(p.read_text(encoding="utf-8"))


def error_state(problem: str, *, expected_version: int | None = None) -> SchemaState:
    if expected_version is None:
        try:
            expected_version = len(load_files())
        except MigrationFilesError:
            expected_version = 0
    return SchemaState("error", expected_version, None, problems=[problem[:300]])


# ── verify ──────────────────────────────────────────────────────────────────────────

async def verify(engine=None, *, directory: Path | None = None) -> SchemaState:
    """Read-only: the ledger against the files, then the live catalog against catalog.json.
    Runs in a READ ONLY transaction, so Postgres itself refuses any write. Never raises."""
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    try:
        files = load_files(d)
    except MigrationFilesError as exc:
        return error_state(f"files: {exc}")
    expected = files[-1].version if files else 0
    try:
        eng = engine if engine is not None else _engine()
        async with eng.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = '{VERIFY_STATEMENT_TIMEOUT}'"))
            try:
                return await _verify_in(conn, files, expected, d)
            finally:
                await conn.rollback()
    except Exception as exc:  # noqa: BLE001 - a failed check is a state, never a crash
        return error_state(f"check failed: {str(exc)[:280]}", expected_version=expected)


async def _verify_in(conn, files: list[MigrationFile], expected: int, directory: Path) -> SchemaState:
    exists = (await conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"),
                                 {"s": SCHEMA})).scalar()
    all_versions = [f.version for f in files]
    if not exists:
        return SchemaState("absent", expected, None, pending=all_versions)
    ledger = await read_ledger(conn)
    if ledger is None:
        return SchemaState("drift", expected, None, problems=["the schema exists but has no ledger"])
    by_version = {f.version: f for f in files}
    applied = [int(r["version"]) for r in ledger]
    top = max(applied) if applied else None
    ahead = [v for v in applied if v not in by_version]
    if ahead:
        return SchemaState("ahead", expected, top,
                           problems=[f"{v:04d}: applied, but this image has no such file" for v in ahead])
    mismatched = [f"{int(r['version']):04d}: checksum differs from {by_version[int(r['version'])].name}"
                  for r in ledger
                  if r["checksum"] != by_version[int(r["version"])].checksum
                  or r["name"] != by_version[int(r["version"])].name]
    if mismatched:
        return SchemaState("checksum_mismatch", expected, top, problems=mismatched)
    if applied != list(range(1, len(applied) + 1)):
        return SchemaState("drift", expected, top,
                           problems=["the ledger has a gap: " + ", ".join(f"{v:04d}" for v in applied)])
    pending = [v for v in all_versions if v not in applied]
    if pending:
        return SchemaState("behind", expected, top, pending=pending)
    try:
        want = expected_catalog(directory)
    except (MigrationFilesError, ValueError) as exc:
        return SchemaState("error", expected, top, problems=[str(exc)[:300]])
    if int(want.get("version") or 0) != expected:
        return SchemaState("error", expected, top,
                           problems=[f"{CATALOG_FILE} is for version {want.get('version')}, the files end at {expected}"])
    live = await read_catalog(conn)
    server = await server_version_num(conn)
    same_major = server // 10000 == int(want.get("server_version_num") or 0) // 10000
    problems, differs = compare_catalog(want, live, same_major=same_major)
    for diff in differs:
        log.warning("trace_lab.schema.definition_differs", **diff)
    if problems:
        return SchemaState("drift", expected, top, problems=problems[:50])
    return SchemaState("ready", expected, top)


# ── apply ───────────────────────────────────────────────────────────────────────────

_LEDGER_INSERT = ("INSERT INTO plenum_tracelab.schema_migrations (version, name, checksum, applied_by)"
                  " VALUES (:v, :n, :c, :b)")


def _is_lock_timeout(exc: BaseException) -> bool:
    t = str(exc).lower()
    return "lock timeout" in t or "lock_timeout" in t or "locknotavailable" in t or "55p03" in t


def _first_line(exc: BaseException) -> str:
    s = str(exc)
    return s.splitlines()[0][:200] if s else type(exc).__name__


async def _apply_one(eng, f: MigrationFile, applied_by: str) -> str | None:
    """One file in one transaction (spec 1.6). None on success, else the problem to report."""
    stmts = split_statements(f.sql)
    log.info("trace_lab.schema.applying", version=f.version, name=f.name, checksum=f.checksum)
    for attempt in range(1, LOCK_RETRIES + 1):
        current = "(transaction setup)"
        t0 = time.perf_counter()
        try:
            async with eng.begin() as conn:
                await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                await conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                await conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})")
                ledger = await read_ledger(conn) or []
                if any(int(r["version"]) == f.version for r in ledger):
                    log.info("trace_lab.schema.already_applied", version=f.version)
                    return None
                for current in stmts:
                    await conn.exec_driver_sql(current)
                await conn.execute(text(_LEDGER_INSERT), {"v": f.version, "n": f.name, "c": f.checksum, "b": applied_by})
            log.info("trace_lab.schema.applied", version=f.version, name=f.name, statements=len(stmts),
                     ms=round((time.perf_counter() - t0) * 1000))
            return None
        except Exception as exc:  # noqa: BLE001
            if _is_lock_timeout(exc) and attempt < LOCK_RETRIES:
                log.warning("trace_lab.schema.apply_lock_wait", version=f.version, attempt=attempt,
                            retry_in_s=LOCK_RETRY_SLEEP_S)
                await asyncio.sleep(LOCK_RETRY_SLEEP_S)
                continue
            snippet = " ".join(current.split())[:160]
            if _is_lock_timeout(exc):
                problem = f"{f.version:04d}: could not get its lock after {attempt} attempts: {snippet}"
            else:
                problem = f"{f.version:04d}: failed at: {snippet}: {_first_line(exc)}"
            log.error("trace_lab.schema.apply_failed", version=f.version, error=problem[:300])
            return problem
    return f"{f.version:04d}: not applied"


async def _dry_run(eng, todo: list[MigrationFile], applied_by: str) -> str | None:
    """Every selected file in one transaction, then rolled back. None when all of it ran."""
    async with eng.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            await conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            await conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})")
            for f in todo:
                current = "(start)"
                try:
                    for current in split_statements(f.sql):
                        await conn.exec_driver_sql(current)
                    await conn.execute(text(_LEDGER_INSERT), {"v": f.version, "n": f.name, "c": f.checksum, "b": applied_by})
                except Exception as exc:  # noqa: BLE001
                    return f"{f.version:04d}: dry run failed at: {' '.join(current.split())[:160]}: {_first_line(exc)}"
            return None
        finally:
            await trans.rollback()


async def apply_pending(engine=None, *, applied_by: str, through: int | None = None, dry_run: bool = False,
                        directory: Path | None = None) -> SchemaState:
    """Apply the pending files, oldest first, each in its own transaction; then re-verify.

    Runs only when the schema is absent or behind. It does not check TRACE_LAB_APPLY_DATABASES:
    startup does that before calling it, and the CLI has its own gates (terminal, --expect-db,
    --through, the typed database name). Returns status 'error' with the problem when a file
    fails; nothing of that file remains."""
    if not (1 <= len(applied_by or "") <= 200):
        raise ValueError("applied_by must be 1-200 characters")
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    eng = engine if engine is not None else _engine()
    state = await verify(eng, directory=d)
    if state.status not in ("absent", "behind"):
        if state.status != "ready":
            log.warning("trace_lab.schema.apply_refused", reason=f"status_{state.status}")
        return state
    files = {f.version: f for f in load_files(d)}
    todo = [files[v] for v in state.pending if through is None or v <= through]
    if not todo:
        return state
    if dry_run:
        problem = await _dry_run(eng, todo, applied_by)
        after = await verify(eng, directory=d)
        if problem:
            after.problems = [problem, *after.problems][:50]
        return after
    for f in todo:
        problem = await _apply_one(eng, f, applied_by)
        if problem:
            after = await verify(eng, directory=d)
            return SchemaState("error", after.expected_version, after.applied_version, pending=after.pending,
                               problems=[problem, *after.problems][:50])
    return await verify(eng, directory=d)


async def current_database(engine=None) -> str:
    eng = engine if engine is not None else _engine()
    async with eng.connect() as conn:
        return str((await conn.execute(text("SELECT current_database()"))).scalar())


def db_host(url: str | None = None) -> str:
    if url is None:
        from ..config import settings

        url = settings.db_url
    return (urlparse(url).hostname or "").lower()
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/migrate.py`, `DA/tests/test_trace_lab_migrate.py`.

---

### Task 5: `db.py` — state, 503, re-verify, error envelope, auth/scope helpers, keep-list, offboarding

**Files:**
- Create: `DA/src/trace_lab/db.py`
- Test: `DA/tests/test_trace_lab_db.py`

**Interfaces:**
- Consumes: `migrate.SCHEMA`, `migrate.SchemaState`, `migrate.iso`, `migrate.verify`, `migrate.error_state` (Tasks 2/4); `trace._scope_sql`, `trace.ready` (DA `src/agents/trace.py:779-791`, `:113-114`); `caller_principal` (DA `src/services/principal.py:182`), `caller_organization_id` (DA `src/http_client.py:136`).
- Produces: everything listed for `db.py` under "Names later milestones import". `jobs.mark_interrupted` is imported lazily (Task 6).

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_db.py`:
```python
"""Trace Lab's process state, the 503 dependency, re-verify, keep-list and the shared auth helpers
(spec "Data model" 1.5, 1.10, 3, 5.3; "Trace capture" 5.1)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal
from src.trace_lab import db, migrate

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"


def _p(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email="a@b.c", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def _st(status="ready", applied=1, expected=1, age_s=0.0):
    return migrate.SchemaState(status, expected, applied,
                               checked_at=datetime.now(timezone.utc) - timedelta(seconds=age_s))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(db, "STATE", None)
    monkeypatch.setattr(db, "engine", lambda: object())
    a, b = caller_principal.set(None), caller_organization_id.set(None)
    yield
    caller_principal.reset(a)
    caller_organization_id.reset(b)


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(db.require_ready)])
    async def probe():
        return {"ok": True}

    return app


def test_ready_follows_the_state():
    assert db.ready() is False and db.state() is None
    db.set_state(_st("behind", applied=0, expected=1))
    assert db.ready() is False
    db.set_state(_st("ready"))
    assert db.ready() is True and db.state().status == "ready"


def test_unavailable_body_names_no_table_or_schema():
    db.set_state(migrate.SchemaState("behind", 6, 2, pending=[3, 4, 5, 6], problems=["jobs: extra column foo"]))
    body = db.unavailable_body()
    assert body["ok"] is False and body["available"] is False and body["reason"] == "trace_lab_unavailable"
    assert body["error"] == "Trace Lab is not available right now. The chat is not affected."
    assert (body["status"], body["expected_version"], body["applied_version"]) == ("behind", 6, 2)
    assert body["checked_at"].endswith("Z") and body["retryable"] is True
    assert "jobs" not in json.dumps(body) and "plenum" not in json.dumps(body)


def test_routes_answer_503_while_unverified(monkeypatch):
    async def behind(engine=None, *, directory=None):
        return migrate.SchemaState("behind", 2, 1, pending=[2])

    monkeypatch.setattr(migrate, "verify", behind)
    r = TestClient(_probe_app()).get("/probe")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert r.json()["detail"]["reason"] == "trace_lab_unavailable"


def test_a_ready_store_lets_the_route_through():
    db.set_state(_st("ready"))
    assert TestClient(_probe_app()).get("/probe").json() == {"ok": True}


async def test_status_reverifies_at_most_every_30s(monkeypatch):
    calls = []

    async def slow_behind(engine=None, *, directory=None):
        calls.append(1)
        await asyncio.sleep(0.01)
        return migrate.SchemaState("behind", 2, 1, pending=[2])

    monkeypatch.setattr(migrate, "verify", slow_behind)
    db.set_state(_st("behind", applied=1, expected=2, age_s=5))
    await db.refresh_state()
    assert calls == []                                    # fresh enough: no new check
    db.set_state(_st("behind", applied=1, expected=2, age_s=31))
    await asyncio.gather(*(db.refresh_state() for _ in range(5)))
    assert calls == [1]                                   # concurrent requests share one verify
    db.set_state(_st("ready", age_s=3600))
    await db.refresh_state()
    assert calls == [1]                                   # a ready state is never re-checked


async def test_a_late_apply_brings_trace_lab_up_and_sweeps_jobs(monkeypatch):
    from src.trace_lab import jobs

    async def now_ready(engine=None, *, directory=None):
        return migrate.SchemaState("ready", 1, 1)

    swept = []

    async def sweep(stale_after_s=120):
        swept.append(stale_after_s)
        return []

    monkeypatch.setattr(migrate, "verify", now_ready)
    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    db.set_state(_st("behind", applied=0, expected=1, age_s=60))
    st = await db.refresh_state()
    assert st.status == "ready" and db.ready() and swept == [120]


async def test_a_failed_check_is_an_error_state_not_a_crash(monkeypatch):
    async def boom(engine=None, *, directory=None):
        raise RuntimeError("database went away")

    monkeypatch.setattr(migrate, "verify", boom)
    st = await db.refresh_state()
    assert st.status == "error" and "database went away" in st.problems[0] and not db.ready()


def test_keep_tables_is_none_until_verified_and_follows_the_applied_version(monkeypatch):
    monkeypatch.setattr(db, "KEEP_BY_VERSION", {1: ()})
    assert db.keep_tables() is None
    db.set_state(_st("drift"))
    assert db.keep_tables() is None
    db.set_state(_st("ready", applied=1))
    assert db.keep_tables() == ()
    db.register_keep(2, ("plenum_tracelab.annotations", "turn_id"), ("plenum_tracelab.sample_items", "turn_id"))
    assert db.keep_tables() == ()                          # version 2 is not applied here
    db.set_state(_st("ready", applied=2, expected=2))
    assert db.keep_tables() == (("plenum_tracelab.annotations", "turn_id"), ("plenum_tracelab.sample_items", "turn_id"))


def test_the_shipped_keep_map_is_well_formed():
    assert db.KEEP_BY_VERSION[1] == ()
    for version, pairs in db.KEEP_BY_VERSION.items():
        assert isinstance(version, int) and version >= 1
        for table, column in pairs:
            assert db._KEEP_TABLE.match(table) and db._KEEP_COLUMN.match(column), (table, column)


@pytest.mark.parametrize("pair", [("plenum_cafm.x", "turn_id"), ("plenum_tracelab.x", "turn_id; drop"), ("labels", "turn_id")])
def test_register_keep_refuses_names_outside_trace_lab(monkeypatch, pair):
    monkeypatch.setattr(db, "KEEP_BY_VERSION", {1: ()})
    with pytest.raises(ValueError):
        db.register_keep(2, pair)


def test_scope_for_rules():
    sc = db.scope_for(_p("admin"), None)
    assert (sc.org_id, sc.platform, sc.is_reviewer, sc.acting) == (ORG, False, True, False)
    assert caller_organization_id.get() == ORG
    with pytest.raises(HTTPException) as e:
        db.scope_for(_p("admin"), OTHER)
    assert e.value.status_code == 403 and e.value.detail["reason"] == "not_your_company"
    assert db.scope_for(_p("admin"), ORG).org_id == ORG     # naming your own company is fine
    sa = _p("superadmin", org=None)
    assert db.scope_for(sa, None).platform is True and db.scope_for(sa, None).org_id is None
    acting = db.scope_for(sa, OTHER)
    assert (acting.org_id, acting.acting, acting.platform) == (OTHER, True, False)
    with pytest.raises(HTTPException) as e:
        db.scope_for(sa, "not-a-uuid")
    assert e.value.status_code == 400 and e.value.detail["reason"] == "bad_company"
    assert db.scope_for(_p("user"), None).is_reviewer is False


def test_require_reviewer_needs_an_admin_and_a_company():
    assert db.require_reviewer(_p("admin"), None) == ORG
    with pytest.raises(HTTPException) as e:
        db.require_reviewer(_p("user"), None)
    assert e.value.status_code == 403 and e.value.detail["reason"] == "reviewers_only"
    with pytest.raises(HTTPException) as e:
        db.require_reviewer(_p("superadmin", org=None), None)
    assert e.value.status_code == 400 and e.value.detail["reason"] == "name_a_company"
    assert db.require_reviewer(_p("superadmin", org=None), OTHER) == OTHER
    assert db.require_reader(_p("superadmin", org=None), None).platform is True


def test_require_platform_is_a_superadmin_outside_any_company():
    assert db.require_platform(_p("superadmin", org=None)).platform is True
    for who, org in ((_p("superadmin", org=None), OTHER), (_p("admin"), None)):
        with pytest.raises(HTTPException) as e:
            db.require_platform(who, org)
        assert e.value.status_code == 403 and e.value.detail["reason"] == "platform_only"


def test_scope_sql_is_trace_scope_qualified_by_alias():
    admin = _p("admin")
    where, params = db.scope_sql(admin, ORG, alias="tt")
    assert "tt.organization_id = CAST(:org AS uuid)" in where and params["org"] == ORG and "user_id" not in where
    where, _ = db.scope_sql(_p("user"), ORG, alias="tt")
    assert "tt.user_id = CAST(:uid AS uuid)" in where
    assert db.scope_sql(_p("superadmin", org=None), None, alias="tt")[0] == "TRUE"
    assert db.scope_sql(admin, None, alias="tt")[0].startswith("FALSE")
    with pytest.raises(ValueError):
        db.scope_sql(admin, ORG, alias="tt; drop")


def test_company_where():
    assert db.company_where(ORG) == ("organization_id = CAST(:tl_org AS uuid)", {"tl_org": ORG})
    assert db.company_where(ORG, alias="a")[0] == "a.organization_id = CAST(:tl_org AS uuid)"


def test_offboarding_steps_run_newest_version_first(monkeypatch):
    monkeypatch.setattr(db, "OFFBOARD_BY_VERSION", {1: db.OFFBOARD_BY_VERSION[1]})
    db.register_offboarding(2, ("annotations", "DELETE FROM plenum_tracelab.annotations WHERE organization_id = CAST(:org AS uuid)"))
    assert [label for label, _ in db.offboarding_steps(2)] == ["annotations", "turn_meta", "jobs"]
    assert [label for label, _ in db.offboarding_steps(1)] == ["turn_meta", "jobs"]


def test_register_offboarding_refuses_sql_outside_trace_lab(monkeypatch):
    monkeypatch.setattr(db, "OFFBOARD_BY_VERSION", {1: db.OFFBOARD_BY_VERSION[1]})
    with pytest.raises(ValueError):
        db.register_offboarding(2, ("x", "DELETE FROM plenum_cafm.agent_trace_turns WHERE organization_id = :org"))


def test_require_trace_store(monkeypatch):
    from src.agents import trace

    monkeypatch.setattr(trace, "_ready", False)
    with pytest.raises(HTTPException) as e:
        db.require_trace_store()
    assert e.value.status_code == 503 and e.value.detail["reason"] == "trace_store_unavailable"
    monkeypatch.setattr(trace, "_ready", True)
    db.require_trace_store()
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_db.py`
Expected: collection error — `ImportError: cannot import name 'db' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`DA/src/trace_lab/db.py`:
```python
"""Trace Lab's state in this process, and the helpers every Trace Lab route and job shares.

M0 owns these names; later milestones import them and never define a second helper for the
same job (spec R36).

While the schema is not verified (absent, behind, ahead, checksum mismatch, drift, error) every
Trace Lab route except /status answers 503 through require_ready, keep_tables() is None so the
span purge is skipped, and turn_meta is not written. The chat is unaffected.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import text

from .migrate import SCHEMA, SchemaState, iso

if TYPE_CHECKING:
    from ..services.principal import Principal

log = structlog.get_logger(__name__)

__all__ = [
    "SCHEMA", "STATE", "Scope", "CompanyHasOpenJobs", "KEEP_BY_VERSION", "OFFBOARD_BY_VERSION",
    "engine", "ready", "state", "set_state", "refresh_state", "require_ready", "require_trace_store",
    "unavailable_body", "http_error", "iso", "scope_for", "require_reader", "require_reviewer",
    "require_platform", "scope_sql", "company_where", "register_keep", "keep_tables",
    "register_offboarding", "offboarding_steps", "delete_company_rows",
]

STATE: SchemaState | None = None
VERIFY_TIMEOUT_S = 20.0
UNAVAILABLE_TEXT = "Trace Lab is not available right now. The chat is not affected."
TRACE_STORE_TEXT = "The trace store is not available right now."
_ALIAS = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
_KEEP_TABLE = re.compile(r"^plenum_tracelab\.[a-z_]+$")
_KEEP_COLUMN = re.compile(r"^[a-z_]+$")
_OFFBOARD_SQL = re.compile(r"^\s*(?:DELETE\s+FROM|UPDATE)\s+plenum_tracelab\.[a-z_]+\s", re.I)

#: The span-purge keep-list (spec D5, R4): for each migration version, the (table, turn column)
#: pairs whose turns keep their spans. keep_tables() unions every entry up to the applied version.
#: The literal stays {1: ()}. A later milestone whose migration adds a table citing turns appends one
#: statement at the end of db.py: KEEP_BY_VERSION[N] = ((table, turn column), ...). Version 1 keeps
#: nothing: turn_meta cites every turn, which would stop the purge, and jobs cites none.
KEEP_BY_VERSION: dict[int, tuple[tuple[str, str], ...]] = {
    1: (),
}

#: Company offboarding (spec 5.3): per migration version, ordered (label, SQL) steps that remove one
#: company's rows; ':org' is the company. delete_company_rows runs the newest version's steps first,
#: so rows that reference older tables go before them. The literal stays {1: (...)}. A later
#: milestone appends one statement at the end of db.py: OFFBOARD_BY_VERSION[N] = ((label, sql), ...).
OFFBOARD_BY_VERSION: dict[int, tuple[tuple[str, str], ...]] = {
    1: (
        ("turn_meta", "DELETE FROM plenum_tracelab.turn_meta WHERE organization_id = CAST(:org AS uuid)"),
        ("jobs", "DELETE FROM plenum_tracelab.jobs WHERE organization_id = CAST(:org AS uuid)"),
    ),
}


# ── state ───────────────────────────────────────────────────────────────────────────

def engine():
    """The service's one asyncpg engine (database._get_engine). Trace Lab creates no other."""
    from .. import database

    return database._get_engine()


def ready() -> bool:
    return STATE is not None and STATE.status == "ready"


def state() -> SchemaState | None:
    return STATE


def set_state(st: SchemaState, *, ms: float | None = None) -> SchemaState:
    """Store the result of a check and log it (spec 1.5)."""
    global STATE
    STATE = st
    if st.status == "ready":
        log.info("trace_lab.schema.verified", version=st.applied_version, ms=None if ms is None else round(ms))
    elif st.status == "error":
        log.warning("trace_lab.schema.verify_failed", error="; ".join(st.problems)[:300])
    else:
        log.warning("trace_lab.schema.unverified", status=st.status, expected_version=st.expected_version,
                    applied_version=st.applied_version, pending=st.pending, problems=st.problems[:20])
    return st


_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def _verify_lock() -> asyncio.Lock:
    """One lock per event loop, so concurrent requests share one verify (and tests on fresh loops work)."""
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def _fresh(st: SchemaState | None, max_age_s: float) -> bool:
    if st is None:
        return False
    if st.status == "ready":
        return True
    return (datetime.now(timezone.utc) - st.checked_at).total_seconds() < max_age_s


async def refresh_state(*, max_age_s: float = 30.0) -> SchemaState:
    """The current state; re-verify when it is not ready and the last check is older than max_age_s,
    so a version Hussain applies starts working within 30 s with no restart."""
    if _fresh(STATE, max_age_s):
        return STATE  # type: ignore[return-value]
    async with _verify_lock():
        before = STATE
        if _fresh(before, max_age_s):
            return before  # type: ignore[return-value]
        from . import migrate

        t0 = time.perf_counter()
        try:
            st = await asyncio.wait_for(migrate.verify(engine()), timeout=VERIFY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a failed check is a state, not a crash
            st = migrate.error_state(f"check failed: {str(exc)[:280]}")
        set_state(st, ms=(time.perf_counter() - t0) * 1000)
        if st.status == "ready" and (before is None or before.status != "ready"):
            try:
                from . import jobs

                await jobs.mark_interrupted()
            except Exception as exc:  # noqa: BLE001
                log.warning("trace_lab.jobs.sweep_failed", error=str(exc)[:300])
        return st


def http_error(status: int, reason: str, error: str, **extra: Any) -> HTTPException:
    """The house error envelope: {ok: false, error: <sentence>, reason: <snake_code>, ...}."""
    return HTTPException(status_code=status, detail={"ok": False, "error": error, "reason": reason, **extra})


def unavailable_body() -> dict[str, Any]:
    """The 503 body. It never names a table or schema; problem details go only to the logs."""
    st = STATE
    return {"ok": False, "available": False, "error": UNAVAILABLE_TEXT, "reason": "trace_lab_unavailable",
            "retryable": True, "status": st.status if st else "error",
            "expected_version": st.expected_version if st else None,
            "applied_version": st.applied_version if st else None,
            "checked_at": iso(st.checked_at) if st else None}


async def require_ready() -> None:
    """FastAPI dependency for every /api/trace-lab route except /status."""
    st = await refresh_state()
    if st.status != "ready":
        raise HTTPException(status_code=503, detail=unavailable_body(), headers={"Retry-After": "30"})


def require_trace_store() -> None:
    """503 trace_store_unavailable while the trace tables are not ready (agents/trace.py)."""
    from ..agents import trace

    if not trace.ready():
        raise http_error(503, "trace_store_unavailable", TRACE_STORE_TEXT, retryable=True)


# ── who is asking, for which company ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Scope:
    org_id: str | None      # the company the request is for; None = the platform view
    platform: bool          # a superadmin naming no company
    is_superadmin: bool
    is_reviewer: bool       # an admin, or a superadmin (D7)
    acting: bool            # a superadmin naming a company


def scope_for(principal: "Principal", organization_id: str | None) -> Scope:
    """The traces._act rule (DA src/api/routes/traces.py:30-36) with the house envelope: sets the
    caller ContextVars; a non-superadmin naming another company is 403 not_your_company; no company
    named means the caller's own, or the platform view for a superadmin."""
    from ..http_client import caller_organization_id
    from ..services.principal import caller_principal

    caller_principal.set(principal)
    named = (str(organization_id).strip() if organization_id else "") or None
    if named:
        try:
            named = str(UUID(named))
        except ValueError:
            raise http_error(400, "bad_company", "That is not a company id.") from None
    is_super = principal.role == "superadmin"
    own = str(principal.organization_id) if principal.organization_id else None
    if named and not is_super and named != own:
        raise http_error(403, "not_your_company", "You can only open your own company.")
    org = named if named else (None if is_super else own)
    caller_organization_id.set(org)
    return Scope(org_id=org, platform=bool(is_super and not named), is_superadmin=is_super,
                 is_reviewer=principal.is_admin, acting=bool(is_super and named))


def require_reader(principal: "Principal", organization_id: str | None) -> Scope:
    sc = scope_for(principal, organization_id)
    if not sc.is_reviewer:
        raise http_error(403, "reviewers_only", "Only company admins and the platform team can open Trace Lab.")
    return sc


def require_reviewer(principal: "Principal", organization_id: str | None) -> str:
    """For anything written or read for one company: an admin (own company) or a superadmin acting
    for one. 403 reviewers_only, 400 name_a_company. Returns the company id."""
    sc = require_reader(principal, organization_id)
    if sc.org_id is None:
        raise http_error(400, "name_a_company", "Choose a company to view as first.")
    return sc.org_id


def require_platform(principal: "Principal", organization_id: str | None = None) -> Scope:
    """The platform owner (D7): a superadmin naming no company. Everyone else 403 platform_only."""
    sc = scope_for(principal, organization_id)
    if not sc.platform:
        raise http_error(403, "platform_only", "Only the platform team can do this, outside any one company.")
    return sc


def scope_sql(principal: "Principal", org: str | None, alias: str = "tt") -> tuple[str, dict[str, Any]]:
    """trace._scope_sql (trace.py:779-791) with its columns qualified by alias, the way trace.stats
    does it (trace.py:895). Parameters keep trace's names, org and uid."""
    from ..agents import trace

    if alias and not _ALIAS.match(alias):
        raise ValueError(f"bad alias {alias!r}")
    where, params = trace._scope_sql(principal, org)
    if alias:
        where = re.sub(r"(?<![\w.])(organization_id|user_id)\b", alias + r".\1", where)
    return where, params


def company_where(org: str, *, alias: str = "") -> tuple[str, dict[str, str]]:
    """Every company-scoped plenum_tracelab query uses this predicate."""
    if alias and not _ALIAS.match(alias):
        raise ValueError(f"bad alias {alias!r}")
    a = (alias + ".") if alias else ""
    return f"{a}organization_id = CAST(:tl_org AS uuid)", {"tl_org": org}


# ── the purge keep-list ────────────────────────────────────────────────────────────────

def register_keep(version: int, *pairs: tuple[str, str]) -> None:
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"not a migration version: {version!r}")
    cur = list(KEEP_BY_VERSION.get(version, ()))
    for table, column in pairs:
        if not (_KEEP_TABLE.match(table) and _KEEP_COLUMN.match(column)):
            raise ValueError(f"not a keep table: {table}.{column}")
        if (table, column) not in cur:
            cur.append((table, column))
    KEEP_BY_VERSION[version] = tuple(cur)


def keep_tables() -> tuple[tuple[str, str], ...] | None:
    """None unless Trace Lab is ready (the purge then deletes nothing). Otherwise the union of
    KEEP_BY_VERSION for every version up to the applied one."""
    st = STATE
    if st is None or st.status != "ready":
        return None
    applied = st.applied_version or 0
    out: list[tuple[str, str]] = []
    for v in sorted(KEEP_BY_VERSION):
        if v <= applied:
            out.extend(p for p in KEEP_BY_VERSION[v] if p not in out)
    return tuple(out)


# ── company offboarding (spec 5.3) ────────────────────────────────────────────────────

class CompanyHasOpenJobs(Exception):
    """The company still has a queued, running, paused or interrupted job."""


def register_offboarding(version: int, *steps: tuple[str, str]) -> None:
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"not a migration version: {version!r}")
    cur = list(OFFBOARD_BY_VERSION.get(version, ()))
    for label, sql in steps:
        if not _OFFBOARD_SQL.match(sql) or ":org" not in sql:
            raise ValueError(f"an offboarding step deletes or updates plenum_tracelab rows of :org - {label}")
        cur.append((label, sql))
    OFFBOARD_BY_VERSION[version] = tuple(cur)


def offboarding_steps(applied_version: int) -> list[tuple[str, str]]:
    return [step for v in sorted(OFFBOARD_BY_VERSION, reverse=True) if v <= applied_version
            for step in OFFBOARD_BY_VERSION[v]]


async def delete_company_rows(organization_id: str, *, actor_id: str, dry_run: bool,
                              job_id: str | None = None) -> dict[str, int]:
    """Remove one company's Trace Lab rows in one transaction (spec 5.3). dry_run runs everything
    and rolls back, returning the counts. Refused while the company has an open job; the
    offboarding job itself is platform-scoped (organization_id NULL, scope_key company_delete:<org>).
    Never touches plenum_cafm."""
    st = STATE
    if st is None or st.status != "ready":
        raise RuntimeError("Trace Lab is not verified here")
    org = str(UUID(organization_id))
    counts: dict[str, int] = {}
    async with engine().connect() as conn:
        trans = await conn.begin()
        try:
            await conn.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
            await conn.exec_driver_sql("SET LOCAL plenum_tracelab.allow_purge = 'on'")
            open_jobs = (await conn.execute(text(
                "SELECT count(*) FROM plenum_tracelab.jobs WHERE organization_id = CAST(:org AS uuid)"
                " AND status IN ('queued', 'running', 'paused', 'interrupted')"
                " AND id IS DISTINCT FROM CAST(:job AS uuid)"), {"org": org, "job": job_id})).scalar()
            if open_jobs:
                raise CompanyHasOpenJobs(f"{open_jobs} open job(s) for this company")
            for label, sql in offboarding_steps(st.applied_version or 0):
                res = await conn.execute(text(sql), {"org": org})
                counts[label] = counts.get(label, 0) + int(res.rowcount or 0)
            if dry_run:
                await trans.rollback()
            else:
                await trans.commit()
        except BaseException:
            if trans.is_active:
                await trans.rollback()
            raise
    log.info("trace_lab.company_rows_deleted", organization_id=org, actor_id=actor_id, dry_run=dry_run, counts=counts)
    return counts
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_db.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/db.py`, `DA/tests/test_trace_lab_db.py`.

---

### Task 6: `jobs.py` — resumable job rows and the in-process worker loop

**Files:**
- Create: `DA/src/trace_lab/jobs.py`
- Test: `DA/tests/test_trace_lab_jobs.py`

**Interfaces:**
- Consumes: `db.engine()` (Task 5); table `plenum_tracelab.jobs` (Task 3).
- Produces: everything listed for `jobs.py` under "Names later milestones import". `create` is the spec §2.9 name (M3 and M6 call it); there is no `create_job` (R36).

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_jobs.py`:
```python
"""Jobs without a database: kinds, refusals and the in-process worker loop (spec 2.9, R6)."""
from __future__ import annotations

import os
import re
import socket
from decimal import Decimal

import pytest

from src.trace_lab import db, jobs, migrate


def _no_db():
    raise AssertionError("no database in this test")


def test_kinds_and_statuses_match_the_ddl():
    sql = (migrate.MIGRATIONS_DIR / "0001_foundation.sql").read_text(encoding="utf-8")
    kinds = re.findall(r"'([a-z_]+)'", re.search(r"CHECK \(kind IN \((.*?)\)\)", sql, re.S).group(1))
    statuses = re.findall(r"'([a-z_]+)'", re.search(r"CHECK \(status IN \((.*?)\)\)", sql, re.S).group(1))
    assert tuple(kinds) == jobs.KINDS and tuple(statuses) == jobs.STATUSES
    assert set(jobs.OPEN_STATUSES) <= set(jobs.STATUSES) and set(jobs.FINISH_STATUSES) <= set(jobs.STATUSES)


@pytest.mark.parametrize("kw, why", [
    ({"kind": "replay"}, "unknown job kind"),
    ({"params": {"Access_Token": "x"}}, "credentials"),
    ({"usd_cap": None}, "usd_cap"),
])
async def test_create_refuses_before_touching_the_database(monkeypatch, kw, why):
    monkeypatch.setattr(db, "engine", _no_db)
    args = dict(kind="golden_run", scope_key="golden_run:x", organization_id=None, params={},
                usd_cap=Decimal("1"), actor_id=None)
    args.update(kw)
    with pytest.raises(ValueError, match=why):
        await jobs.create(**args)


async def test_finish_refuses_a_status_it_cannot_end_in(monkeypatch):
    monkeypatch.setattr(db, "engine", _no_db)
    with pytest.raises(ValueError):
        await jobs.finish("6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e", "running")


def test_worker_id_names_host_and_pid():
    assert jobs.worker_id() == f"{socket.gethostname()}:{os.getpid()}"


def test_register_kind_refuses_unknown_kinds(monkeypatch):
    monkeypatch.setattr(jobs, "_WORKERS", {})
    with pytest.raises(ValueError):
        jobs.register_kind("eval_grading", lambda ctx: None)


def _fake_rows(monkeypatch, kind="judge_run", claimed=True):
    finished = []

    async def claim(job_id, *, worker=None):
        return {"id": job_id, "kind": kind, "status": "running"} if claimed else None

    async def finish(job_id, status, error=None):
        finished.append((job_id, status, error))
        return None

    async def add_spend(job_id, usd):
        return True

    monkeypatch.setattr(jobs, "claim", claim)
    monkeypatch.setattr(jobs, "finish", finish)
    monkeypatch.setattr(jobs, "add_spend", add_spend)
    monkeypatch.setattr(jobs, "_WORKERS", {})
    return finished


async def test_start_runs_the_registered_worker_and_finishes_with_its_status(monkeypatch):
    finished = _fake_rows(monkeypatch)
    seen = {}

    async def worker(ctx):
        seen["kind"] = ctx.job["kind"]
        seen["under_cap"] = await ctx.add_spend(Decimal("0.01"))
        seen["stopping"] = ctx.stopping()
        return "done"

    jobs.register_kind("judge_run", worker)
    task = await jobs.start("J1")
    await task
    assert seen == {"kind": "judge_run", "under_cap": True, "stopping": False}
    assert finished == [("J1", "done", None)]


async def test_a_worker_that_raises_fails_its_job(monkeypatch):
    finished = _fake_rows(monkeypatch)

    async def worker(ctx):
        raise RuntimeError("boom")

    jobs.register_kind("judge_run", worker)
    await (await jobs.start("J2"))
    assert finished == [("J2", "failed", "boom")]


async def test_start_returns_none_when_someone_else_holds_the_job(monkeypatch):
    _fake_rows(monkeypatch, claimed=False)
    jobs.register_kind("judge_run", lambda ctx: None)
    assert await jobs.start("J3") is None


async def test_a_job_with_no_registered_worker_fails(monkeypatch):
    finished = _fake_rows(monkeypatch, kind="monitor_batch")
    assert await jobs.start("J4") is None
    assert finished == [("J4", "failed", "no worker registered for monitor_batch")]
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_jobs.py`
Expected: collection error — `ImportError: cannot import name 'jobs' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`DA/src/trace_lab/jobs.py`:
```python
"""Long Trace Lab work as resumable rows (spec D9, "Data model" 2.9): golden runs, grading, judge
runs, proposals, monitoring and company offboarding.

A job is a plenum_tracelab.jobs row. It is claimed atomically, heartbeats every 15 s and after
every item, reserves model spend before each call and checkpoints after each item in the SAME
transaction as the item's result row, so a restart loses at most the item in flight. At startup
(trace_lab.startup, schema verified) a running job whose heartbeat is older than 120 s becomes
`interrupted`; it blocks a new job in its scope until it is resumed or discarded (set to failed).
Golden runs are stepped by the browser (claim, one item, checkpoint per request); the other kinds
run in this process through register_kind() + start(). params never carry a credential (the
database refuses them too: jobs_params_no_secrets).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import db

log = structlog.get_logger(__name__)

STALE_AFTER_S = 120
HEARTBEAT_EVERY_S = 15
KINDS = ("golden_run", "golden_grade", "judge_run", "proposal_grouping", "proposal_depth_scan",
         "proposal_cells", "monitor_batch", "company_delete")
STATUSES = ("queued", "running", "paused", "done", "failed", "capped", "interrupted", "cancelled")
OPEN_STATUSES = ("queued", "running", "paused", "interrupted")
FINISH_STATUSES = ("done", "failed", "capped", "paused", "cancelled")
UNCAPPED_KINDS = frozenset({"company_delete"})
SECRET_KEYS = frozenset({"authorization", "bearer", "token", "access_token", "refresh_token", "api_key", "password"})
_ZERO = Decimal("0")


class JobConflict(Exception):
    """Another open job (queued, running, paused or interrupted) holds this scope. Routes answer 409."""


class CapReached(Exception):
    """The next call would take spend past usd_cap; the job should finish as capped."""


class JobNotRunning(Exception):
    """The job is not running (stopped, finished or interrupted), so nothing may be reserved."""


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("id", "organization_id", "created_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


def _uuid(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        return None


def _is_scope_conflict(exc: IntegrityError) -> bool:
    return "ux_jobs_one_open_per_scope" in str(getattr(exc, "orig", exc))


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def create(*, kind: str, scope_key: str, organization_id: str | None, params: dict[str, Any] | None,
                 usd_cap: Decimal | None, usd_estimate: Decimal | None = None, total: int | None = None,
                 actor_id: str | None, actor_email: str | None = None) -> dict[str, Any]:
    """A queued job. JobConflict when an open job already holds scope_key."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    p = dict(params or {})
    bad = sorted(k for k in p if str(k).lower() in SECRET_KEYS)
    if bad:
        raise ValueError(f"job params never carry credentials: {bad}")
    if kind not in UNCAPPED_KINDS and usd_cap is None:
        raise ValueError("a job that spends model money needs usd_cap")
    try:
        async with db.engine().begin() as conn:
            row = (await conn.execute(text(
                "INSERT INTO plenum_tracelab.jobs (kind, scope_key, organization_id, params, usd_cap, usd_estimate,"
                " total, created_by, created_by_email)"
                " VALUES (:kind, :scope, CAST(:org AS uuid), CAST(:params AS jsonb), :cap, :est, :total,"
                " CAST(:by AS uuid), :email) RETURNING *"),
                {"kind": kind, "scope": scope_key, "org": organization_id, "params": json.dumps(p, default=str),
                 "cap": usd_cap, "est": usd_estimate, "total": total, "by": actor_id,
                 "email": actor_email})).mappings().one()
    except IntegrityError as exc:
        if _is_scope_conflict(exc):
            raise JobConflict(scope_key) from None
        raise
    return _row(row)


async def claim(job_id: str, *, worker: str | None = None) -> dict[str, Any] | None:
    """Atomically take a queued, paused or interrupted job. None = someone else has it (or it ended)."""
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET status = 'running', worker = :w, heartbeat_at = now(),"
            " started_at = coalesce(started_at, now()), updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'paused', 'interrupted') RETURNING *"),
            {"id": job_id, "w": worker or worker_id()})).mappings().first()
    return _row(row) if row else None


async def heartbeat(job_id: str, *, progress_patch: dict[str, Any] | None = None) -> bool:
    """Mark the job alive. True means stop: a stop was requested, or the job is no longer running."""
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET heartbeat_at = now(), updated_at = now(),"
            " progress = progress || CAST(:patch AS jsonb)"
            " WHERE id = CAST(:id AS uuid) AND status = 'running' RETURNING stop_requested"),
            {"id": job_id, "patch": json.dumps(progress_patch or {}, default=str)})).first()
    return True if row is None else bool(row[0])


async def reserve(job_id: str, usd: Decimal) -> None:
    """Reserve spend for a call about to be made. CapReached past usd_cap; JobNotRunning otherwise."""
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET usd_reserved = usd_reserved + :usd, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status = 'running'"
            " AND (usd_cap IS NULL OR usd_spent + usd_reserved + :usd <= usd_cap) RETURNING id"),
            {"id": job_id, "usd": usd})).first()
        if row is not None:
            return
        status = (await conn.execute(text("SELECT status FROM plenum_tracelab.jobs WHERE id = CAST(:id AS uuid)"),
                                     {"id": job_id})).scalar()
    if status == "running":
        raise CapReached(job_id)
    raise JobNotRunning(job_id)


async def release(job_id: str, usd: Decimal) -> None:
    """Give back a reservation whose call produced no result row (the call failed)."""
    async with db.engine().begin() as conn:
        await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET usd_reserved = greatest(usd_reserved - :usd, 0), updated_at = now()"
            " WHERE id = CAST(:id AS uuid)"), {"id": job_id, "usd": usd})


async def checkpoint(conn, job_id: str, *, usd: Decimal | None, released: Decimal, done_delta: int = 1,
                     progress_patch: dict[str, Any] | None = None) -> None:
    """In the SAME transaction as the item's result row: done += done_delta, usd_spent += usd,
    usd_reserved -= released, progress ||= patch, heartbeat."""
    await conn.execute(text(
        "UPDATE plenum_tracelab.jobs SET done = done + :dd, usd_spent = usd_spent + :usd,"
        " usd_reserved = greatest(usd_reserved - :rel, 0), progress = progress || CAST(:patch AS jsonb),"
        " heartbeat_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": job_id, "dd": int(done_delta), "usd": usd if usd is not None else _ZERO, "rel": released,
         "patch": json.dumps(progress_patch or {}, default=str)})


async def add_spend(job_id: str, usd: Decimal) -> bool:
    """Spend outside an item transaction. False once usd_spent has reached usd_cap."""
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET usd_spent = usd_spent + :usd, heartbeat_at = now(), updated_at = now()"
            " WHERE id = CAST(:id AS uuid) RETURNING (usd_cap IS NULL OR usd_spent < usd_cap) AS under_cap"),
            {"id": job_id, "usd": usd})).first()
    return bool(row and row[0])


async def finish(job_id: str, status: str, error: str | None = None) -> dict[str, Any] | None:
    """End (or pause) an open job. paused keeps it resumable; the others free its scope."""
    if status not in FINISH_STATUSES:
        raise ValueError(f"a job cannot finish as {status!r}")
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET status = :st, error = :err, usd_reserved = 0, worker = NULL,"
            " finished_at = CASE WHEN :st = 'paused' THEN NULL ELSE now() END, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'running', 'paused', 'interrupted') RETURNING *"),
            {"id": job_id, "st": status, "err": (error or None) and str(error)[:2000]})).mappings().first()
    return _row(row) if row else None


async def request_stop(job_id: str) -> bool:
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET stop_requested = true, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'running', 'paused', 'interrupted') RETURNING id"),
            {"id": job_id})).first()
    return row is not None


async def resume(job_id: str, *, usd_cap: Decimal | None = None) -> dict[str, Any] | None:
    """Put a paused, interrupted or capped job back in the queue, optionally with a new cap.
    JobConflict when another job has opened in the same scope meanwhile."""
    try:
        async with db.engine().begin() as conn:
            row = (await conn.execute(text(
                "UPDATE plenum_tracelab.jobs SET status = 'queued', stop_requested = false, error = NULL, worker = NULL,"
                " usd_cap = coalesce(:cap, usd_cap), finished_at = NULL, updated_at = now()"
                " WHERE id = CAST(:id AS uuid) AND status IN ('paused', 'interrupted', 'capped') RETURNING *"),
                {"id": job_id, "cap": usd_cap})).mappings().first()
    except IntegrityError as exc:
        if _is_scope_conflict(exc):
            raise JobConflict(job_id) from None
        raise
    return _row(row) if row else None


async def discard(job_id: str, *, error: str = "Discarded by a person after an interruption.") -> dict[str, Any] | None:
    """Give up on an open job that is not running (it becomes failed and frees its scope)."""
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET status = 'failed', error = :err, usd_reserved = 0, finished_at = now(),"
            " updated_at = now() WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'paused', 'interrupted') RETURNING *"),
            {"id": job_id, "err": error[:2000]})).mappings().first()
    return _row(row) if row else None


async def mark_interrupted(stale_after_s: int = STALE_AFTER_S) -> list[str]:
    """The startup sweep (spec 2.9). Only called when Trace Lab is ready."""
    async with db.engine().begin() as conn:
        rows = (await conn.execute(text(
            "UPDATE plenum_tracelab.jobs SET status = 'interrupted', updated_at = now(),"
            " error = coalesce(error, 'The service restarted while this was running.')"
            " WHERE status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => :stale))"
            " RETURNING id, kind, organization_id"), {"stale": float(stale_after_s)})).mappings().all()
    ids = [str(r["id"]) for r in rows]
    if ids:
        log.warning("trace_lab.jobs.interrupted", count=len(ids), job_ids=ids[:20])
    return ids


async def get(job_id: str, *, organization_id: str | None, platform: bool = False) -> dict[str, Any] | None:
    """One job. organization_id names its company; None means a platform-scope job. platform=True
    reads any job (the platform owner, or a worker)."""
    jid = _uuid(job_id)
    if jid is None:
        return None
    where = "" if platform else " AND organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)"
    async with db.engine().connect() as conn:
        row = (await conn.execute(text(f"SELECT * FROM plenum_tracelab.jobs WHERE id = CAST(:id AS uuid){where}"),
                                  {"id": jid, "org": organization_id})).mappings().first()
    return _row(row) if row else None


async def list_jobs(*, organization_id: str | None, kind: str | None = None, limit: int = 50,
                    platform: bool = False) -> list[dict[str, Any]]:
    where = ["TRUE"] if platform else ["organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)"]
    if kind:
        where.append("kind = :kind")
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT * FROM plenum_tracelab.jobs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT :lim"),
            {"org": organization_id, "kind": kind, "lim": max(1, min(int(limit), 500))})).mappings().all()
    return [_row(r) for r in rows]


_TASKS: set[asyncio.Task] = set()


def run_in_background(job_id: str, coro_factory: Callable[[], Awaitable[None]]) -> asyncio.Task:
    """Run a job's coroutine on this loop, keeping a strong reference until it finishes."""
    task = asyncio.get_running_loop().create_task(coro_factory(), name=f"trace_lab_job:{job_id}")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


# ── in-process workers (proposals, judges, monitoring, offboarding) ─────────────────────

@dataclass
class JobContext:
    job_id: str
    job: dict[str, Any]
    worker: str
    _stop: bool = False

    async def heartbeat(self, progress: dict[str, Any] | None = None) -> bool:
        # The module-level heartbeat() below; a class body's names are not visible in its methods.
        self._stop = await heartbeat(self.job_id, progress_patch=progress) or self._stop
        return self._stop

    async def add_spend(self, usd: Decimal) -> bool:
        return await add_spend(self.job_id, usd)

    def stopping(self) -> bool:
        return self._stop


Worker = Callable[[JobContext], Awaitable[str]]
_WORKERS: dict[str, Worker] = {}


def register_kind(kind: str, worker: Worker) -> None:
    """The coroutine that runs a job of this kind; it returns the status to finish with."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    _WORKERS[kind] = worker


async def _tick(ctx: JobContext) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_EVERY_S)
        try:
            await ctx.heartbeat()
        except Exception as exc:  # noqa: BLE001
            log.warning("trace_lab.jobs.heartbeat_failed", job_id=ctx.job_id, error=str(exc)[:200])


async def start(job_id: str) -> asyncio.Task | None:
    """Claim the job and run its registered worker in the background. None when it could not be claimed."""
    w = worker_id()
    job = await claim(job_id, worker=w)
    if job is None:
        return None
    fn = _WORKERS.get(job["kind"])
    if fn is None:
        await finish(job_id, "failed", f"no worker registered for {job['kind']}")
        return None
    ctx = JobContext(job_id=job_id, job=job, worker=w)

    async def _run() -> None:
        ticker = asyncio.get_running_loop().create_task(_tick(ctx))
        try:
            status = await fn(ctx)
            if status in FINISH_STATUSES:
                await finish(job_id, status)
            else:
                await finish(job_id, "failed", f"the worker returned {status!r}")
        except Exception as exc:  # noqa: BLE001
            log.warning("trace_lab.jobs.worker_failed", job_id=job_id, kind=job["kind"], error=str(exc)[:300])
            await finish(job_id, "failed", str(exc)[:2000])
        finally:
            ticker.cancel()

    return run_in_background(job_id, _run)
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_jobs.py tests/test_trace_lab_db.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/jobs.py`, `DA/tests/test_trace_lab_jobs.py`.

---

### Task 7: `trace_lab.startup()`

**Files:**
- Modify: `DA/src/trace_lab/__init__.py` (replace the one-line marker from Task 2 — it is this plan's own new file)
- Test: `DA/tests/test_trace_lab_startup.py`

**Interfaces:**
- Consumes: `migrate.schema_mode`, `current_database`, `db_host`, `apply_refusal`, `apply_pending`, `verify`, `error_state` (Tasks 2/4); `db.engine`, `db.set_state` (Task 5); `jobs.mark_interrupted` (Task 6).
- Produces: `trace_lab.startup() -> SchemaState` (never raises); re-exports `trace_lab.keep_tables`, `trace_lab.ready`; `trace_lab.VERIFY_TIMEOUT_S = 20.0`, `trace_lab.APPLY_TIMEOUT_S = 60.0`.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_startup.py`:
```python
"""startup(): verify (or apply on a throwaway database), store the state, sweep stale jobs.
Never raises (spec "Data model" 1.4, 1.7)."""
from __future__ import annotations

import asyncio

import pytest

from src import trace_lab
from src.trace_lab import db, jobs, migrate


@pytest.fixture(autouse=True)
def swept(monkeypatch):
    monkeypatch.setattr(db, "STATE", None)
    monkeypatch.setattr(db, "engine", lambda: object())
    monkeypatch.delenv("TRACE_LAB_SCHEMA", raising=False)
    monkeypatch.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    monkeypatch.setattr(migrate, "db_host", lambda url=None: "postgres")
    calls: list[int] = []

    async def sweep(stale_after_s=120):
        calls.append(1)
        return []

    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    return calls


def _never(what):
    async def never(*a, **k):
        raise AssertionError(what)
    return never


def _state(status, **kw):
    async def fake(engine=None, *, directory=None):
        return migrate.SchemaState(status, 1, 1 if status == "ready" else None, **kw)
    return fake


async def test_startup_verifies_by_default_and_sweeps_jobs_when_ready(monkeypatch, swept):
    monkeypatch.setattr(migrate, "verify", _state("ready"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied without TRACE_LAB_SCHEMA=apply"))
    st = await trace_lab.startup()
    assert st.status == "ready" and db.ready() and swept == [1]


async def test_startup_never_raises_and_leaves_trace_lab_down(monkeypatch, swept):
    async def boom(engine=None, *, directory=None):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(migrate, "verify", boom)
    st = await trace_lab.startup()
    assert st.status == "error" and "no route to host" in st.problems[0]
    assert not db.ready() and db.keep_tables() is None and swept == []


async def test_a_slow_verify_times_out_to_error(monkeypatch, swept):
    async def slow(engine=None, *, directory=None):
        await asyncio.sleep(5)

    monkeypatch.setattr(trace_lab, "VERIFY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(migrate, "verify", slow)
    st = await trace_lab.startup()
    assert st.status == "error" and "timed out" in st.problems[0]


async def test_apply_mode_applies_on_an_allowed_database(monkeypatch, swept):
    monkeypatch.setenv("TRACE_LAB_SCHEMA", "apply")
    seen = {}

    async def db_name(engine=None):
        return "hoistra"

    async def apply(engine=None, *, applied_by, through=None, dry_run=False, directory=None):
        seen["by"] = applied_by
        return migrate.SchemaState("ready", 1, 1)

    monkeypatch.setattr(migrate, "current_database", db_name)
    monkeypatch.setattr(migrate, "apply_pending", apply)
    st = await trace_lab.startup()
    assert st.status == "ready" and seen["by"].startswith("startup:")


@pytest.mark.parametrize("database, host", [
    ("hoistra_test", "postgres"),
    ("hoistra", "plenum-agentic-ai.postgres.database.azure.com"),
])
async def test_apply_mode_is_refused_off_the_allow_list_and_on_azure(monkeypatch, swept, database, host):
    monkeypatch.setenv("TRACE_LAB_SCHEMA", "apply")

    async def db_name(engine=None):
        return database

    monkeypatch.setattr(migrate, "db_host", lambda url=None: host)
    monkeypatch.setattr(migrate, "current_database", db_name)
    monkeypatch.setattr(migrate, "verify", _state("behind", pending=[1]))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied where it must only verify"))
    st = await trace_lab.startup()
    assert st.status == "behind" and not db.ready()


async def test_an_unknown_mode_only_verifies(monkeypatch, swept):
    monkeypatch.setenv("TRACE_LAB_SCHEMA", "migrate-please")
    monkeypatch.setattr(migrate, "verify", _state("absent", pending=[1]))
    monkeypatch.setattr(migrate, "apply_pending", _never("an unknown mode applied"))
    st = await trace_lab.startup()
    assert st.status == "absent"


def test_the_package_reexports_what_main_uses():
    assert trace_lab.keep_tables is db.keep_tables and trace_lab.ready is db.ready
    assert asyncio.iscoroutinefunction(trace_lab.startup)
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_startup.py`
Expected: FAIL — `AttributeError: module 'src.trace_lab' has no attribute 'startup'`.

- [ ] **Step 3: Implement**

Replace the whole content of `DA/src/trace_lab/__init__.py` (written by Task 2 of this plan) with:
```python
"""Hoist Trace Lab (svc-deepagents): measures the chat's answers and makes them better.

M0 is the foundation: the plenum_tracelab schema (migrate.py), its state in this process and the
shared helpers (db.py), resumable jobs (jobs.py), turn provenance (provenance.py) and the
eval-data guards (guards.py). Nothing here may break or slow the chat: startup() never raises,
and while the schema is not verified every Trace Lab route answers 503, the span purge is
skipped and turn_meta is not written.
"""
from __future__ import annotations

import asyncio
import socket
import time

import structlog

from . import db
from .db import keep_tables, ready

__all__ = ["startup", "keep_tables", "ready"]

log = structlog.get_logger(__name__)

VERIFY_TIMEOUT_S = 20.0
APPLY_TIMEOUT_S = 60.0


async def startup():
    """Choose the mode, verify or apply, store the state, sweep stale jobs when ready. Never raises."""
    from . import migrate

    t0 = time.perf_counter()
    # M3 installs the replay SQL tripwire here, before the schema check (spec "Golden" 2.5).
    try:
        st = await _check_schema()
    except Exception as exc:  # noqa: BLE001 - startup never raises
        st = migrate.error_state(f"startup failed: {str(exc)[:280]}")
    db.set_state(st, ms=(time.perf_counter() - t0) * 1000)
    if st.status == "ready":
        try:
            from . import jobs

            await jobs.mark_interrupted()
        except Exception as exc:  # noqa: BLE001
            log.warning("trace_lab.jobs.sweep_failed", error=str(exc)[:300])
    log.info("trace_lab.startup.done", status=st.status, version=st.applied_version,
             ms=round((time.perf_counter() - t0) * 1000))
    return st


async def _check_schema():
    from . import migrate

    eng = db.engine()
    if migrate.schema_mode() == "apply":
        database = await asyncio.wait_for(migrate.current_database(eng), timeout=VERIFY_TIMEOUT_S)
        reason = migrate.apply_refusal(database, migrate.db_host())
        if reason:
            log.warning("trace_lab.schema.apply_refused", database=database, reason=reason)
        else:
            try:
                return await asyncio.wait_for(
                    migrate.apply_pending(eng, applied_by=f"startup:{socket.gethostname()}"[:200]),
                    timeout=APPLY_TIMEOUT_S)
            except TimeoutError:
                return migrate.error_state(f"apply timed out after {APPLY_TIMEOUT_S:.0f}s")
    try:
        return await asyncio.wait_for(migrate.verify(eng), timeout=VERIFY_TIMEOUT_S)
    except TimeoutError:
        return migrate.error_state(f"verify timed out after {VERIFY_TIMEOUT_S:.0f}s")
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_startup.py tests/test_trace_lab_db.py tests/test_trace_lab_jobs.py tests/test_trace_lab_migrate.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/__init__.py`, `DA/tests/test_trace_lab_startup.py`.

---

### Task 8: The `migrate` CLI (status, plan, apply with a TTY check, snapshot)

**Files:**
- Modify: `DA/src/trace_lab/migrate.py` (append)
- Test: `DA/tests/test_trace_lab_migrate.py` (append)

**Interfaces:**
- Consumes: Task 2/4 names.
- Produces: `migrate.main(argv) -> int`; `python -m src.trace_lab.migrate status|plan|apply|snapshot`; private `_open_jobs(eng)`, `_cli_plan(eng, through=None)`, `_cli_apply(eng, args)`, `_cli_snapshot(url)`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_migrate.py`:
```python


# ── the CLI (spec 1.9) ───────────────────────────────────────────────────────────────

import io  # noqa: E402
import json  # noqa: E402


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Eng:
    async def dispose(self):
        return None


def _cli_fakes(monkeypatch, status="behind", database="hoistra"):
    async def dbname(engine=None):
        return database

    async def state(engine=None, *, directory=None):
        return migrate.SchemaState(status, 1, None, pending=[1] if status in ("absent", "behind") else [])

    async def no_jobs(engine):
        return []

    monkeypatch.setattr(migrate, "_engine", lambda: _Eng())
    monkeypatch.setattr(migrate, "current_database", dbname)
    monkeypatch.setattr(migrate, "verify", state)
    monkeypatch.setattr(migrate, "_open_jobs", no_jobs)


def _never(what):
    async def never(*a, **k):
        raise AssertionError(what)
    return never


APPLY = ["apply", "--expect-db", "hoistra", "--through", "0001", "--applied-by", "hussain"]


def test_cli_apply_refuses_without_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    def no_engine():
        raise AssertionError("no database may be touched")

    monkeypatch.setattr(migrate, "_engine", no_engine)
    assert migrate.main(APPLY) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_apply_needs_through():
    with pytest.raises(SystemExit):
        migrate.main(["apply", "--expect-db", "hoistra_test", "--applied-by", "hussain"])


def test_cli_snapshot_refuses_a_shared_database(capsys):
    assert migrate.main(["snapshot", "--db-url", "postgresql+asyncpg://u:p@plenum.postgres.database.azure.com/hoistra_test"]) == 2
    assert "scratch" in capsys.readouterr().err


def test_cli_apply_refuses_another_database(monkeypatch, capsys):
    _cli_fakes(monkeypatch, database="hoistra_test")
    monkeypatch.setattr("sys.stdin", _TTY(""))
    monkeypatch.setattr(migrate, "apply_pending", _never("must not apply"))
    assert migrate.main(APPLY) == 2
    assert "not 'hoistra'" in capsys.readouterr().err


def test_cli_apply_refuses_unless_absent_or_behind(monkeypatch, capsys):
    _cli_fakes(monkeypatch, status="drift")
    monkeypatch.setattr("sys.stdin", _TTY("hoistra\n"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied on a drifted database"))
    assert migrate.main(APPLY) == 2
    assert "status is drift" in capsys.readouterr().err


def test_cli_apply_needs_the_typed_database_name(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("nope\n"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied without the typed confirmation"))
    assert migrate.main(APPLY) == 1
    out = capsys.readouterr()
    assert "nothing ran" in out.err and "SET LOCAL lock_timeout = '3s';" in out.out and "pending 0001_foundation.sql" in out.out


def test_cli_apply_prints_the_shipped_checksum_line(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("hoistra\n"))
    seen = {}

    async def apply(engine=None, *, applied_by, through=None, dry_run=False, directory=None):
        seen.update(by=applied_by, through=through, dry=dry_run)
        return migrate.SchemaState("ready", 1, 1)

    monkeypatch.setattr(migrate, "apply_pending", apply)
    assert migrate.main(APPLY) == 0
    out = capsys.readouterr().out
    f = migrate.load_files()[0]
    assert seen == {"by": "cli:hussain", "through": 1, "dry": False}
    assert f'    1: "{f.checksum}",  # 0001_foundation.sql' in out


def test_cli_status_prints_the_state(monkeypatch, capsys):
    _cli_fakes(monkeypatch, status="ready")
    assert migrate.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: FAIL — `AttributeError: module 'src.trace_lab.migrate' has no attribute 'main'`.

- [ ] **Step 3: Implement**

Append to `DA/src/trace_lab/migrate.py`:
```python


# ── CLI (spec 1.9; never run by a Claude session against a shared database) ──────────────

def _version_arg(v: str) -> int:
    if not re.fullmatch(r"\d{1,4}", v or ""):
        raise argparse.ArgumentTypeError("a version like 0002")
    return int(v)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.trace_lab.migrate", description="Trace Lab schema migrations.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="verify (read-only) and print the state")
    sub.add_parser("plan", help="print what apply would run (read-only)")
    a = sub.add_parser("apply", help="apply pending files - interactive terminal only")
    a.add_argument("--expect-db", required=True, help="must equal current_database()")
    a.add_argument("--through", required=True, type=_version_arg, help="the last version to apply, e.g. 0001")
    a.add_argument("--applied-by", required=True, help="recorded in the ledger as cli:<value>")
    a.add_argument("--dry-run", action="store_true", help="run the files in a transaction and roll back")
    s = sub.add_parser("snapshot", help="apply every file to a scratch database and write catalog.json")
    s.add_argument("--db-url", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "apply" and not sys.stdin.isatty():
        print("migrate apply runs only in an interactive terminal: a person types the database name to approve it.",
              file=sys.stderr)
        return 2
    if args.cmd == "snapshot" and not scratch_url_ok(args.db_url):
        print("snapshot runs only against a scratch database (tracelab_scratch on localhost, "
              "127.0.0.1 or host.docker.internal).", file=sys.stderr)
        return 2
    return asyncio.run(_run_cli(args))


async def _run_cli(args: argparse.Namespace) -> int:
    if args.cmd == "snapshot":
        return await _cli_snapshot(args.db_url)
    eng = _engine()
    try:
        if args.cmd == "status":
            st = await verify(eng)
            print(json.dumps(st.as_dict(), indent=2))
            return 0 if st.status == "ready" else 1
        if args.cmd == "plan":
            await _cli_plan(eng)
            return 0
        return await _cli_apply(eng, args)
    finally:
        await eng.dispose()


async def _open_jobs(eng) -> list[dict]:
    async with eng.connect() as conn:
        present = (await conn.execute(text("SELECT to_regclass('plenum_tracelab.jobs') IS NOT NULL"))).scalar()
        if not present:
            return []
        rows = (await conn.execute(text(
            "SELECT id::text AS id, kind, status, scope_key FROM plenum_tracelab.jobs"
            " WHERE status IN ('queued', 'running', 'paused', 'interrupted') ORDER BY created_at"))).mappings().all()
    return [dict(r) for r in rows]


async def _cli_plan(eng, through: int | None = None) -> SchemaState:
    st = await verify(eng)
    print(f"host: {db_host() or '?'}  database: {await current_database(eng)}")
    print(f"status: {st.status}  applied: {st.applied_version}  files end at: {st.expected_version}")
    for problem in st.problems[:20]:
        print(f"  problem: {problem}")
    files = {f.version: f for f in load_files()}
    for v in st.pending:
        if through is not None and v > through:
            continue
        f = files[v]
        print(f"\npending {f.name}  sha256 {f.checksum}")
        for problem in lint_file(f):
            print(f"  LINT: {problem}")
        for i, stmt in enumerate(split_statements(f.sql), start=1):
            print(f"-- statement {i}\n{stmt};")
    for j in await _open_jobs(eng):
        print(f"open job: {j['id']} {j['kind']} {j['status']} {j['scope_key']}")
    return st


async def _cli_apply(eng, args: argparse.Namespace) -> int:
    dbname = await current_database(eng)
    if dbname != args.expect_db:
        print(f"refused: connected to {dbname!r}, not {args.expect_db!r}.", file=sys.stderr)
        return 2
    st = await _cli_plan(eng, through=args.through)
    if st.status not in ("absent", "behind"):
        print(f"refused: the status is {st.status}; apply runs only when the schema is absent or behind.",
              file=sys.stderr)
        return 2
    todo = [v for v in st.pending if v <= args.through]
    if not todo:
        print("nothing to apply through that version.")
        return 0
    verb = "dry-run" if args.dry_run else "apply"
    typed = input(f"\nType the database name ({dbname}) to {verb} {', '.join(f'{v:04d}' for v in todo)}: ").strip()
    if typed != dbname:
        print("not confirmed; nothing ran.", file=sys.stderr)
        return 1
    after = await apply_pending(eng, applied_by=f"cli:{args.applied_by}", through=args.through, dry_run=args.dry_run)
    print(json.dumps(after.as_dict(), indent=2))
    if after.status == "error":
        return 1
    if args.dry_run:
        return 0
    files = {f.version: f for f in load_files()}
    print("\nAdd to SHIPPED_CHECKSUMS in src/trace_lab/migrate.py:")
    for v in todo:
        print(f'    {v}: "{files[v].checksum}",  # {files[v].name}, applied to {dbname} by cli:{args.applied_by}')
    return 0 if after.status in ("ready", "behind") else 1


async def _cli_snapshot(url: str) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        files = load_files()
        head = files[-1].version if files else 0
        st = await apply_pending(eng, applied_by="snapshot")
        if st.applied_version != head:
            print(f"snapshot failed: {json.dumps(st.as_dict())}", file=sys.stderr)
            return 1
        async with eng.connect() as conn:
            cat = await read_catalog(conn)
            server = await server_version_num(conn)
        out = {"version": head, "server_version_num": server, **cat}
        (MIGRATIONS_DIR / CATALOG_FILE).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {MIGRATIONS_DIR / CATALOG_FILE}: version {head}, server {server}, {len(cat['tables'])} tables")
        return 0
    finally:
        await eng.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_migrate.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/migrate.py`, `DA/tests/test_trace_lab_migrate.py`.

---

### Task 9: The scratch-database helper, `catalog.json`, and apply/verify on real Postgres

**Files:**
- Create: `DA/tests/trace_lab_scratch.py`, `DA/tests/test_trace_lab_migrate_sql.py`
- Create (generated by `tlsnap.sh`, never hand-edited): `DA/migrations/trace_lab/catalog.json`

**Interfaces:**
- Consumes: `migrate.*` (Tasks 2/4/8), `db.STATE`/`db.set_state` (Task 5), `trace.ensure_tables`, `trace._ready` (DA `src/agents/trace.py:50`, `:117-130`), `database._get_engine` (DA `src/database.py:34-50`).
- Produces: `tests/trace_lab_scratch.py` with `SCRATCH_URL`, `requires_scratch`, `assert_scratch()`, `new_engine()`, `async fresh_schema(monkeypatch, *, apply=True) -> AsyncEngine`, async fixtures `scratch_db` and `tl_scratch` (same engine; the names M1 and M4 import). `catalog.json` at version 1.

- [ ] **Step 1: Write the failing test**

`DA/tests/trace_lab_scratch.py`:
```python
"""Real-SQL test helper for Trace Lab (spec "Data model" 1.8). A plain module, not conftest.py.

Works only against a throwaway Postgres named by TRACE_LAB_TEST_DB_URL: the database must be
tracelab_scratch on localhost, 127.0.0.1 or host.docker.internal. Anything else
FAILS the run (it never skips), because these helpers drop plenum_tracelab.

    from trace_lab_scratch import requires_scratch, fresh_schema, scratch_db, tl_scratch
    pytestmark = requires_scratch
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from src import database
from src.agents import trace
from src.trace_lab import db, migrate

SCRATCH_URL = os.environ.get("TRACE_LAB_TEST_DB_URL", "")

requires_scratch = pytest.mark.skipif(
    not SCRATCH_URL, reason="TRACE_LAB_TEST_DB_URL is not set: real-SQL tests need a throwaway Postgres")


def assert_scratch(url: str = SCRATCH_URL) -> None:
    if not migrate.scratch_url_ok(url):
        pytest.fail("TRACE_LAB_TEST_DB_URL must name tracelab_scratch on localhost, 127.0.0.1 "
                    f"or host.docker.internal; got {url.split('@')[-1]!r}")


def new_engine() -> AsyncEngine:
    """A scratch engine. NullPool: no pooled connection outlives the test's event loop."""
    assert_scratch()
    return create_async_engine(SCRATCH_URL, poolclass=NullPool)


async def fresh_schema(monkeypatch, *, apply: bool = True) -> AsyncEngine:
    """Spec 1.8 steps 1-6: a scratch engine; plenum_tracelab dropped; plenum_cafm present;
    database._get_engine patched to it; the trace tables created and emptied; every migration
    applied through migrate.apply_pending (the production path), and db.STATE set to the result."""
    if not SCRATCH_URL:
        pytest.skip("TRACE_LAB_TEST_DB_URL is not set")
    engine = new_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS plenum_tracelab CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS plenum_cafm")
    monkeypatch.setattr(database, "_get_engine", lambda: engine)
    monkeypatch.setattr(trace, "_ready", False)
    monkeypatch.setattr(db, "STATE", None)
    assert await trace.ensure_tables(), "agent_trace_* could not be created on the scratch database"
    async with engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE plenum_cafm.agent_trace_turns, plenum_cafm.agent_trace_spans")
    if apply:
        st = await migrate.apply_pending(engine, applied_by="pytest")
        assert st.status == "ready", st.as_dict()
        db.set_state(st)
    return engine


@pytest.fixture
async def scratch_db(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    yield engine
    await engine.dispose()


@pytest.fixture
async def tl_scratch(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    yield engine
    await engine.dispose()
```

`DA/tests/test_trace_lab_migrate_sql.py`:
```python
"""Apply and verify on a throwaway Postgres (spec "Data model" 1.6, 1.8). Skips without
TRACE_LAB_TEST_DB_URL. Broken migration files are written to tmp_path, never to migrations/."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from src.trace_lab import migrate
from trace_lab_scratch import fresh_schema, new_engine, requires_scratch

pytestmark = requires_scratch

EXTRA = "SET LOCAL lock_timeout = '3s';\nCREATE TABLE plenum_tracelab.extra_thing (id integer PRIMARY KEY);\n"


def _copy_all(d: Path) -> int:
    """Copy the shipped files into d; return how many there are."""
    files = migrate.load_files()
    for f in files:
        shutil.copy(f.path, d / f.name)
    return len(files)


async def _top(engine) -> int:
    async with engine.connect() as conn:
        return max(int(r["version"]) for r in await migrate.read_ledger(conn))


async def test_apply_all_then_verify_ready(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    st = await migrate.verify(engine)
    assert st.status == "ready" and st.applied_version == st.expected_version == len(migrate.load_files())
    async with engine.connect() as conn:
        ledger = await migrate.read_ledger(conn)
    assert (ledger[0]["version"], ledger[0]["applied_by"]) == (1, "pytest")
    assert ledger[0]["checksum"] == migrate.load_files()[0].checksum
    assert (await migrate.apply_pending(engine, applied_by="pytest")).status == "ready"   # nothing pending: no-op
    await engine.dispose()


async def test_catalog_snapshot_matches_the_files(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    want = migrate.expected_catalog()
    assert want["version"] == len(migrate.load_files())
    async with engine.connect() as conn:
        live = await migrate.read_catalog(conn)
    assert migrate.compare_catalog(want, live) == ([], [])
    assert {"schema_migrations", "jobs", "turn_meta"} <= set(live["tables"])
    assert {"refuse_change()", "supersede_only()"} <= set(live["functions"])
    await engine.dispose()


async def test_verify_reports_behind_ahead_and_checksum_mismatch(monkeypatch, tmp_path):
    engine = await fresh_schema(monkeypatch)
    behind = tmp_path / "behind"
    behind.mkdir()
    n = _copy_all(behind)
    (behind / f"{n + 1:04d}_extra.sql").write_text(EXTRA, encoding="utf-8")
    st = await migrate.verify(engine, directory=behind)
    assert st.status == "behind" and st.pending == [n + 1] and st.applied_version == n
    await migrate.apply_pending(engine, applied_by="pytest", directory=behind)    # the database is now one ahead
    ahead = await migrate.verify(engine)                                          # this image stops at n
    assert ahead.status == "ahead" and ahead.problems == [f"{n + 1:04d}: applied, but this image has no such file"]
    edited = tmp_path / "edited"
    edited.mkdir()
    _copy_all(edited)
    with open(edited / "0001_foundation.sql", "a", encoding="utf-8") as fh:
        fh.write("-- edited after it was applied\n")
    (edited / f"{n + 1:04d}_extra.sql").write_text(EXTRA, encoding="utf-8")
    st = await migrate.verify(engine, directory=edited)
    assert st.status == "checksum_mismatch" and st.problems[0].startswith("0001: checksum differs")
    await engine.dispose()


async def test_verify_reports_drift_on_extra_column_missing_index_changed_check(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE plenum_tracelab.jobs ADD COLUMN foo text")
        await conn.exec_driver_sql("DROP INDEX plenum_tracelab.ix_jobs_running_heartbeat")
        await conn.exec_driver_sql("ALTER TABLE plenum_tracelab.jobs DROP CONSTRAINT jobs_params_object,"
                                   " ADD CONSTRAINT jobs_params_object CHECK (true)")
    st = await migrate.verify(engine)
    assert st.status == "drift"
    assert "jobs: extra column foo" in st.problems
    assert "ix_jobs_running_heartbeat: missing" in st.problems
    assert "jobs.jobs_params_object: definition changed" in st.problems
    await engine.dispose()


async def test_concurrent_apply_applies_once(monkeypatch):
    engine = await fresh_schema(monkeypatch, apply=False)
    other = new_engine()
    a, b = await asyncio.gather(migrate.apply_pending(engine, applied_by="pytest"),
                                migrate.apply_pending(other, applied_by="pytest"))
    assert a.status == b.status == "ready"
    async with engine.connect() as conn:
        assert [int(r["version"]) for r in await migrate.read_ledger(conn)] == [f.version for f in migrate.load_files()]
    await other.dispose()
    await engine.dispose()


async def test_failing_file_rolls_back_whole(monkeypatch, tmp_path):
    engine = await fresh_schema(monkeypatch)
    n = _copy_all(tmp_path)
    (tmp_path / f"{n + 1:04d}_broken.sql").write_text(
        "SET LOCAL lock_timeout = '3s';\nCREATE TABLE plenum_tracelab.half_done (id integer PRIMARY KEY);\nSELECT 1 / 0;\n",
        encoding="utf-8")
    st = await migrate.apply_pending(engine, applied_by="pytest", directory=tmp_path)
    assert st.status == "error" and st.problems[0].startswith(f"{n + 1:04d}: failed at: SELECT 1 / 0")
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT to_regclass('plenum_tracelab.half_done')"))).scalar() is None
    assert await _top(engine) == n
    await engine.dispose()


async def test_lock_timeout_aborts_instead_of_queueing(monkeypatch, tmp_path):
    engine = await fresh_schema(monkeypatch)
    n = _copy_all(tmp_path)
    (tmp_path / f"{n + 1:04d}_note.sql").write_text(
        "SET LOCAL lock_timeout = '3s';\nALTER TABLE plenum_tracelab.jobs ADD COLUMN note text;\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "LOCK_RETRIES", 2)
    monkeypatch.setattr(migrate, "LOCK_RETRY_SLEEP_S", 0.1)
    holder = new_engine()
    async with holder.connect() as conn:
        trans = await conn.begin()
        await conn.exec_driver_sql("LOCK TABLE plenum_tracelab.jobs IN ACCESS EXCLUSIVE MODE")
        t0 = asyncio.get_running_loop().time()
        st = await migrate.apply_pending(engine, applied_by="pytest", directory=tmp_path)
        took = asyncio.get_running_loop().time() - t0
        await trans.rollback()
    assert st.status == "error" and "could not get its lock after 2 attempts" in st.problems[0]
    assert took < 20
    assert await _top(engine) == n
    await holder.dispose()
    await engine.dispose()


async def test_append_only_and_supersede_guards_work_on_any_table(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        for stmt in (
            "CREATE TABLE plenum_tracelab.probe_events (id integer PRIMARY KEY, v text)",
            "CREATE TRIGGER probe_events_rows BEFORE UPDATE OR DELETE ON plenum_tracelab.probe_events"
            " FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.refuse_change()",
            "CREATE TRIGGER probe_events_truncate BEFORE TRUNCATE ON plenum_tracelab.probe_events"
            " FOR EACH STATEMENT EXECUTE FUNCTION plenum_tracelab.refuse_change()",
            "INSERT INTO plenum_tracelab.probe_events VALUES (1, 'a'), (2, 'b')",
            "CREATE TABLE plenum_tracelab.probe_versions (id integer PRIMARY KEY, body text, superseded_by integer,"
            " superseded_at timestamptz)",
            "CREATE TRIGGER probe_versions_rows BEFORE UPDATE OR DELETE ON plenum_tracelab.probe_versions"
            " FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.supersede_only()",
            "INSERT INTO plenum_tracelab.probe_versions (id, body) VALUES (1, 'x'), (2, 'y')",
        ):
            await conn.exec_driver_sql(stmt)
    for stmt in ("UPDATE plenum_tracelab.probe_events SET v = 'z' WHERE id = 1",
                 "DELETE FROM plenum_tracelab.probe_events WHERE id = 1",
                 "TRUNCATE plenum_tracelab.probe_events",
                 "UPDATE plenum_tracelab.probe_versions SET body = 'changed' WHERE id = 1",
                 "DELETE FROM plenum_tracelab.probe_versions WHERE id = 1"):
        with pytest.raises(Exception, match="plenum_tracelab"):
            async with engine.begin() as conn:
                await conn.exec_driver_sql(stmt)
    async with engine.begin() as conn:      # superseded_by may be set once
        await conn.exec_driver_sql("UPDATE plenum_tracelab.probe_versions SET superseded_by = 2, superseded_at = now() WHERE id = 1")
    with pytest.raises(Exception, match="only superseded_by is set, once"):
        async with engine.begin() as conn:
            await conn.exec_driver_sql("UPDATE plenum_tracelab.probe_versions SET superseded_by = 3 WHERE id = 1")
    async with engine.begin() as conn:      # the offboarding hatch allows DELETE ...
        await conn.exec_driver_sql("SET LOCAL plenum_tracelab.allow_purge = 'on'")
        await conn.exec_driver_sql("DELETE FROM plenum_tracelab.probe_events WHERE id = 1")
    with pytest.raises(Exception, match="append-only"):   # ... never UPDATE
        async with engine.begin() as conn:
            await conn.exec_driver_sql("SET LOCAL plenum_tracelab.allow_purge = 'on'")
            await conn.exec_driver_sql("UPDATE plenum_tracelab.probe_events SET v = 'z' WHERE id = 2")
    await engine.dispose()
```

- [ ] **Step 2: Run it and see it fail**

Start the scratch Postgres (once; it is removed when stopped):
```sh
docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine
```
Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_migrate_sql.py`
Expected: FAIL in every test at `fresh_schema` — `AssertionError: {'status': 'error', ..., 'problems': ['catalog.json is missing']}`.
Also run `sh tlda.sh tests/test_trace_lab_migrate_sql.py` (no URL): every test SKIPPED.

- [ ] **Step 3: Implement**

Generate the expected catalog from the files, on the same scratch server (this is the only way `catalog.json` is ever written):
```sh
sh tlsnap.sh
```
Expected output ends: `wrote .../migrations/trace_lab/catalog.json: version 1, server 160..., 3 tables` and `catalog.json copied back to ...`. Open the file and confirm it lists exactly `jobs`, `schema_migrations`, `turn_meta` under `tables` and `refuse_change()`, `supersede_only()` under `functions`.

- [ ] **Step 4: Run it and see it pass**

Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_migrate_sql.py tests/test_trace_lab_migrate.py`
Expected: PASS (the lock-timeout test takes about 7 s).

- [ ] **Step 5: Checkpoint** — leave uncommitted (keep the scratch container running for Tasks 10–18); task log: `DA/tests/trace_lab_scratch.py`, `DA/tests/test_trace_lab_migrate_sql.py`, `DA/migrations/trace_lab/catalog.json` (+ the `server_version_num` it recorded).

---

### Task 10: The keep-list purge, and `trace_lab.startup()` in the lifespan

**Files:**
- Modify: `DA/src/agents/trace.py:30-37` (imports), `:133-146` (`purge`)
- Modify: `DA/src/api/main.py:114-121` (lifespan)
- Test: `DA/tests/test_trace_lab_purge.py`, `DA/tests/test_trace_lab_purge_sql.py`

**Interfaces:**
- Consumes: `trace_lab.startup()`, `trace_lab.keep_tables()` (Task 7), `db.keep_tables` (Task 5), `fresh_schema` (Task 9).
- Produces: `trace.purge(days=None, *, keep)` — `keep` keyword-only with no default; `None` skips (logs `trace.purge_skipped`); `()` purges as before. `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS`, the only excuse registry (later milestones add only excused columns: M2 `annotation_events.turn_id`, M6 `monitor_verdicts.turn_id`; there is no `db.KEEP_EXCUSED`).

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_purge.py`:
```python
"""The span purge spares Trace Lab's evidence and stops while Trace Lab is not verified
(spec D5, "Data model" 3)."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from src import database
from src.agents import trace


def _no_engine():
    raise AssertionError("the purge must not touch the database here")


def test_purge_requires_keep_argument():
    p = inspect.signature(trace.purge).parameters["keep"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        trace.purge()


async def test_purge_skipped_when_keep_is_none(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    monkeypatch.setattr(database, "_get_engine", _no_engine)
    with capture_logs() as logs:
        assert await trace.purge(keep=None) == 0
    assert {"event": "trace.purge_skipped", "reason": "trace_lab_unverified", "log_level": "warning"} in logs


@pytest.mark.parametrize("keep", [
    (("plenum_cafm.agent_trace_turns", "turn_id"),),
    (("plenum_tracelab.labels", "turn_id = s.turn_id OR TRUE --"),),
])
async def test_purge_refuses_a_keep_table_outside_trace_lab(monkeypatch, keep):
    monkeypatch.setattr(trace, "_ready", True)
    monkeypatch.setattr(database, "_get_engine", _no_engine)
    with pytest.raises(ValueError, match="not a keep table"):
        await trace.purge(keep=keep)


def test_main_starts_trace_lab_before_the_purge_and_passes_the_keep_list():
    src = (Path(trace.__file__).resolve().parents[1] / "api" / "main.py").read_text(encoding="utf-8")
    assert "await trace_lab.startup()" in src
    assert "await _trace.purge(keep=trace_lab.keep_tables())" in src
    assert src.index("await trace_lab.startup()") < src.index("await _trace.purge(keep=trace_lab.keep_tables())")
    assert "await _trace.purge()" not in src
```

`DA/tests/test_trace_lab_purge_sql.py`:
```python
"""The keep-list purge on a throwaway Postgres (spec D5, R4). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from sqlalchemy import text

from src.agents import trace
from src.trace_lab import db, migrate
from trace_lab_scratch import fresh_schema, requires_scratch

pytestmark = requires_scratch

#: Turn columns deliberately NOT kept (R4). turn_meta cites every turn and would stop the purge. A
#: later milestone adds a column here only when it is not kept. Later additions: M2
#: annotation_events.turn_id (every event accompanies an annotation of the same turn), M6
#: monitor_verdicts.turn_id (R4). There is no db.KEEP_EXCUSED.
EXCUSED_TURN_COLUMNS = {("plenum_tracelab.turn_meta", "turn_id")}


async def _span(conn, turn_id: str, days_old: int) -> None:
    await conn.execute(text(
        "INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, seq, kind, name, started_at)"
        " VALUES (gen_random_uuid(), :t, 0, 'llm', 'model', now() - make_interval(days => :d))"),
        {"t": turn_id, "d": days_old})


async def test_purge_spares_every_kept_turn(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE plenum_tracelab.keep_probe (turn_id text PRIMARY KEY)")
        await conn.exec_driver_sql("INSERT INTO plenum_tracelab.keep_probe VALUES ('turn-aaaa000000000001')")
        await _span(conn, "turn-aaaa000000000001", 100)
        await _span(conn, "turn-bbbb000000000001", 100)
        await _span(conn, "turn-cccc000000000001", 5)
    assert await trace.purge(keep=None) == 0
    assert await trace.purge(keep=(("plenum_tracelab.keep_probe", "turn_id"),)) == 1
    async with engine.connect() as conn:
        left = sorted(r[0] for r in (await conn.execute(text("SELECT turn_id FROM plenum_cafm.agent_trace_spans"))).all())
    assert left == ["turn-aaaa000000000001", "turn-cccc000000000001"]
    assert await trace.purge(keep=()) == 1           # verified with nothing cited: purges as before
    await engine.dispose()


async def test_keep_tables_cover_every_turn_reference(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.connect() as conn:
        live = await migrate.read_catalog(conn)
    turn_columns = {(f"plenum_tracelab.{t}", c) for t, cols in live["columns"].items() for c in cols
                    if c == "turn_id" or c.endswith("_turn_id")}
    kept = set(db.keep_tables())
    assert turn_columns - kept - EXCUSED_TURN_COLUMNS == set()
    assert kept - turn_columns == set()              # nothing kept that does not exist at this version
    await engine.dispose()
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_purge.py`
Expected: FAIL — `KeyError: 'keep'` in `test_purge_requires_keep_argument`, `TypeError: purge() got an unexpected keyword argument 'keep'` in the others, and the main.py assertion.

- [ ] **Step 3: Implement**

Edit `DA/src/agents/trace.py` — old:
```python
import json
import time
import uuid
```
new:
```python
import json
import re
import time
import uuid
```
Edit — old:
```python
from typing import Any
```
new:
```python
from typing import Any, Sequence
```
Edit — old (the whole of `purge`, lines 133-146):
```python
async def purge(days: int | None = None) -> int:
    """Drop span payloads older than the retention window. Turn rows (and their totals) stay."""
    if not _ready:
        return 0
    import os

    d = days or int(os.environ.get("TRACE_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"DELETE FROM {SPANS} WHERE started_at < now() - make_interval(days => :d)"), {"d": d})
    if res.rowcount:
        log.info("trace.purged", spans=res.rowcount, older_than_days=d)
    return res.rowcount or 0
```
new:
```python
#: A keep table is a plenum_tracelab table and a keep column a bare name (Trace Lab spec, Data model 3).
_KEEP_TABLE = re.compile(r"^plenum_tracelab\.[a-z_]+$")
_KEEP_COLUMN = re.compile(r"^[a-z_]+$")


async def purge(days: int | None = None, *, keep: Sequence[tuple[str, str]] | None) -> int:
    """Drop span payloads older than the retention window, sparing every turn a Trace Lab row cites.

    keep=None: Trace Lab's tables are not verified (absent, behind, ahead, drifted or unreadable).
    Nothing is deleted - a purge that cannot see the keep-list would delete reviewed evidence.
    keep=(): verified, and no table cites turns yet - purges as before. Turn rows always stay.
    `keep` has no default, so every caller decides what to pass (trace_lab.keep_tables()).
    """
    if not _ready:
        return 0
    if keep is None:
        log.warning("trace.purge_skipped", reason="trace_lab_unverified")
        return 0
    import os

    d = days or int(os.environ.get("TRACE_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
    spare = ""
    for table, column in keep:
        if not (_KEEP_TABLE.match(table) and _KEEP_COLUMN.match(column)):
            raise ValueError(f"not a keep table: {table}.{column}")
        spare += f" AND NOT EXISTS (SELECT 1 FROM {table} k WHERE k.{column} = s.turn_id)"
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(
            f"DELETE FROM {SPANS} s WHERE s.started_at < now() - make_interval(days => :d){spare}"), {"d": d})
    if res.rowcount:
        log.info("trace.purged", spans=res.rowcount, older_than_days=d, kept_by=len(keep))
    return res.rowcount or 0
```

Edit `DA/src/api/main.py` — old (lines 114-121):
```python
    # Hoist Traces: every turn's span tree with cost (agents/trace.py); old span payloads purged.
    from ..agents import trace as _trace

    if await _trace.ensure_tables():
        try:
            await _trace.purge()
        except Exception as exc:  # noqa: BLE001
            log.warning("svc-deepagents.trace.purge_failed", error=str(exc)[:200])
```
new:
```python
    # Trace Lab (src/trace_lab): verify - or, on a scratch/testenv/local-db database with
    # TRACE_LAB_SCHEMA=apply, apply - its schema. Never raises. While it is not verified its
    # routes answer 503, the span purge below is skipped, and the chat is unaffected.
    from .. import trace_lab

    await trace_lab.startup()
    # Hoist Traces: every turn's span tree with cost (agents/trace.py); old span payloads purged,
    # sparing every turn a Trace Lab row cites.
    from ..agents import trace as _trace

    if await _trace.ensure_tables():
        try:
            await _trace.purge(keep=trace_lab.keep_tables())
        except Exception as exc:  # noqa: BLE001
            log.warning("svc-deepagents.trace.purge_failed", error=str(exc)[:200])
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_purge.py tests/test_trace.py tests/test_rest_turns_are_traced.py`
Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_purge_sql.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/trace.py`, `DA/src/api/main.py`, `DA/tests/test_trace_lab_purge.py`, `DA/tests/test_trace_lab_purge_sql.py`.

---

### Task 11: Jobs and offboarding on real Postgres; a drifted database answers 503

**Files:**
- Test: `DA/tests/test_trace_lab_jobs_sql.py`, `DA/tests/test_trace_lab_db_sql.py`

**Interfaces:**
- Consumes: `jobs.*` (Task 6), `db.refresh_state`, `db.require_ready`, `db.keep_tables`, `db.delete_company_rows`, `db.CompanyHasOpenJobs` (Task 5), `trace.purge` (Task 10), `fresh_schema` (Task 9).
- Produces: no new names; pins the SQL behaviour of the M0 APIs.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_jobs_sql.py`:
```python
"""Jobs on a throwaway Postgres (spec "Data model" 2.9, R6). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from src.trace_lab import jobs
from trace_lab_scratch import fresh_schema, requires_scratch

pytestmark = requires_scratch

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"
ACTOR = "00000000-0000-0000-0000-0000000000c3"


async def _job(scope=f"golden_run:{ORG}", org=ORG, cap=Decimal("1.00"), kind="golden_run"):
    return await jobs.create(kind=kind, scope_key=scope, organization_id=org, params={"set_id": "s1"},
                             usd_cap=cap, usd_estimate=Decimal("0.40"), total=3, actor_id=ACTOR,
                             actor_email="a@b.c")


async def test_one_open_job_per_scope(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    j = await _job()
    assert j["status"] == "queued" and j["organization_id"] == ORG and j["done"] == 0 and j["created_by"] == ACTOR
    with pytest.raises(jobs.JobConflict):
        await _job()
    assert (await jobs.finish(j["id"], "done"))["status"] == "done"
    assert (await _job())["status"] == "queued"          # a finished job frees its scope
    await engine.dispose()


async def test_the_database_refuses_credentials_and_uncapped_spend(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    for params, cap in (({"token": "abc"}, 1), ({}, None)):
        with pytest.raises(Exception, match="jobs_params_no_secrets|jobs_model_spend_capped"):
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO plenum_tracelab.jobs (kind, scope_key, params, usd_cap)"
                    " VALUES ('golden_run', 'golden_run:x', CAST(:p AS jsonb), :cap)"),
                    {"p": json.dumps(params), "cap": cap})
    offboard = await jobs.create(kind="company_delete", scope_key=f"company_delete:{ORG}", organization_id=None,
                                 params={"organization_id": ORG}, usd_cap=None, actor_id=ACTOR)
    assert offboard["usd_cap"] is None
    await engine.dispose()


async def test_claim_is_atomic_and_spend_stops_at_the_cap(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    j = await _job()
    assert (await jobs.claim(j["id"], worker="w1"))["status"] == "running"
    assert await jobs.claim(j["id"], worker="w2") is None
    await jobs.reserve(j["id"], Decimal("0.60"))
    with pytest.raises(jobs.CapReached):
        await jobs.reserve(j["id"], Decimal("0.50"))
    async with engine.begin() as conn:
        await jobs.checkpoint(conn, j["id"], usd=Decimal("0.55"), released=Decimal("0.60"), progress_patch={"case": "C1"})
    row = await jobs.get(j["id"], organization_id=ORG)
    assert row["done"] == 1 and row["usd_spent"] == Decimal("0.55") and row["usd_reserved"] == Decimal("0")
    assert row["progress"] == {"case": "C1"}
    assert await jobs.add_spend(j["id"], Decimal("0.40")) is True      # 0.95 < 1.00
    assert await jobs.add_spend(j["id"], Decimal("0.10")) is False     # 1.05: the cap is reached
    assert await jobs.get(j["id"], organization_id=OTHER) is None
    assert await jobs.get(j["id"], organization_id=None, platform=True) is not None
    await jobs.finish(j["id"], "capped")
    with pytest.raises(jobs.JobNotRunning):
        await jobs.reserve(j["id"], Decimal("0.01"))
    await engine.dispose()


async def test_a_stale_running_job_is_interrupted_and_blocks_its_scope_until_resumed(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    j = await _job()
    await jobs.claim(j["id"], worker="w1")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE plenum_tracelab.jobs SET heartbeat_at = now() - interval '10 minutes'"
                                " WHERE id = CAST(:id AS uuid)"), {"id": j["id"]})
    assert await jobs.mark_interrupted() == [j["id"]]
    row = await jobs.get(j["id"], organization_id=ORG)
    assert row["status"] == "interrupted" and row["error"] == "The service restarted while this was running."
    with pytest.raises(jobs.JobConflict):
        await _job()
    resumed = await jobs.resume(j["id"], usd_cap=Decimal("2.00"))
    assert resumed["status"] == "queued" and resumed["usd_cap"] == Decimal("2.00")
    await jobs.claim(j["id"])
    assert await jobs.request_stop(j["id"]) is True
    assert await jobs.heartbeat(j["id"], progress_patch={"step": 2}) is True
    await jobs.finish(j["id"], "paused")
    assert (await jobs.discard(j["id"]))["status"] == "failed"
    assert [r["id"] for r in await jobs.list_jobs(organization_id=ORG)] == [j["id"]]
    await engine.dispose()


async def test_a_registered_worker_runs_to_done(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(jobs, "_WORKERS", {})
    j = await jobs.create(kind="judge_run", scope_key="judge_run:J1:dev", organization_id=None, params={},
                          usd_cap=Decimal("1"), actor_id=ACTOR)

    async def worker(ctx):
        assert await ctx.add_spend(Decimal("0.25"))
        assert await ctx.heartbeat({"done_items": 1}) is False
        return "done"

    jobs.register_kind("judge_run", worker)
    await (await jobs.start(j["id"]))
    row = await jobs.get(j["id"], organization_id=None)
    assert row["status"] == "done" and row["usd_spent"] == Decimal("0.25") and row["progress"] == {"done_items": 1}
    await engine.dispose()
```

`DA/tests/test_trace_lab_db_sql.py`:
```python
"""Trace Lab state and offboarding on a throwaway Postgres (spec "Data model" 1.5, 3, 5.3)."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from src.agents import trace
from src.trace_lab import db, jobs
from trace_lab_scratch import fresh_schema, requires_scratch

pytestmark = requires_scratch

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"
ACTOR = "00000000-0000-0000-0000-0000000000c3"


async def test_a_drifted_database_answers_503_and_skips_the_purge(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE plenum_tracelab.jobs ADD COLUMN foo text")
    monkeypatch.setattr(db, "STATE", None)
    st = await db.refresh_state()
    assert st.status == "drift" and "jobs: extra column foo" in st.problems
    with pytest.raises(HTTPException) as e:
        await db.require_ready()
    assert e.value.status_code == 503 and e.value.detail["reason"] == "trace_lab_unavailable"
    assert "jobs" not in json.dumps(e.value.detail)
    assert db.keep_tables() is None and await trace.purge(keep=db.keep_tables()) == 0
    await engine.dispose()


async def _count(engine, table: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(f"SELECT count(*) FROM plenum_tracelab.{table}"))).scalar())


async def test_company_offboarding_deletes_only_that_company(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    a = await jobs.create(kind="golden_run", scope_key=f"golden_run:{ORG}", organization_id=ORG, params={},
                          usd_cap=Decimal("1"), actor_id=ACTOR)
    await jobs.finish(a["id"], "done")
    b = await jobs.create(kind="golden_run", scope_key=f"golden_run:{OTHER}", organization_id=OTHER, params={},
                          usd_cap=Decimal("1"), actor_id=ACTOR)
    async with engine.begin() as conn:
        for tid, org in (("turn-aaaa000000000001", ORG), ("turn-bbbb000000000001", OTHER)):
            await conn.execute(text(
                "INSERT INTO plenum_tracelab.turn_meta (turn_id, organization_id, channel, channel_source, classifier_version)"
                " VALUES (:t, CAST(:o AS uuid), 'chat', 'inferred', 'prov-1')"), {"t": tid, "o": org})
    # fresh_schema applies every migration, so later versions add steps that delete nothing here:
    # compare only the non-zero counts.
    counts = await db.delete_company_rows(ORG, actor_id=ACTOR, dry_run=True)
    assert {k: v for k, v in counts.items() if v} == {"turn_meta": 1, "jobs": 1}
    assert await _count(engine, "turn_meta") == 2 and await _count(engine, "jobs") == 2
    counts = await db.delete_company_rows(ORG, actor_id=ACTOR, dry_run=False)
    assert {k: v for k, v in counts.items() if v} == {"turn_meta": 1, "jobs": 1}
    assert await _count(engine, "turn_meta") == 1
    assert await jobs.get(b["id"], organization_id=OTHER) is not None
    await engine.dispose()


async def test_offboarding_waits_for_the_companys_open_jobs(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    await jobs.create(kind="golden_run", scope_key=f"golden_run:{ORG}", organization_id=ORG, params={},
                      usd_cap=Decimal("1"), actor_id=ACTOR)
    with pytest.raises(db.CompanyHasOpenJobs):
        await db.delete_company_rows(ORG, actor_id=ACTOR, dry_run=True)
    await engine.dispose()
```

- [ ] **Step 2: Run it and see it fail**

These tests exercise code that already exists, so they are expected to pass on the first run; a failure here is a real bug in Tasks 5–6. Run first *without* the scratch URL to confirm they are collected and skip:
Run: `sh tlda.sh tests/test_trace_lab_jobs_sql.py tests/test_trace_lab_db_sql.py`
Expected: all SKIPPED ("TRACE_LAB_TEST_DB_URL is not set").

- [ ] **Step 3: Implement**

No production code is written in this task. If Step 4 fails, fix the named function in `DA/src/trace_lab/jobs.py` or `db.py` (never the test) and note the fix in the task log.

- [ ] **Step 4: Run it and see it pass**

Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_jobs_sql.py tests/test_trace_lab_db_sql.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/tests/test_trace_lab_jobs_sql.py`, `DA/tests/test_trace_lab_db_sql.py`.

---

### Task 12: `GET /api/trace-lab/status`

**Files:**
- Create: `DA/src/api/routes/trace_lab_status.py`
- Modify: `DA/src/api/main.py:40` (import) and `:255` (include)
- Test: `DA/tests/test_trace_lab_status_route.py`

**Interfaces:**
- Consumes: `db.refresh_state`, `db.http_error`, `db.iso` (Task 5); `current_principal` (DA `src/services/principal.py:130-132`).
- Produces: `router` (prefix `/api/trace-lab`) with `GET /status` → `{ok: true, available, status, expected_version, applied_version, checked_at}`; 403 `reviewers_only` for plain users. Later milestones add their routers next to `trace_lab_status_router` in `main.py`.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_status_route.py`:
```python
"""GET /api/trace-lab/status: always 200 for admins, never names a table (spec "Data model" 1.5)."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import trace_lab_status as routes
from src.services.principal import Principal, current_principal
from src.trace_lab import db, jobs, migrate


def _p(role):
    return Principal(user_id=uuid.uuid4(), email="a@b.c", organization_id=uuid.uuid4(), role=role,
                     can_ingest=True, building_ids=None)


def _client(monkeypatch, role="admin", status=None) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    if role:
        app.dependency_overrides[current_principal] = lambda: _p(role)
    monkeypatch.setattr(db, "STATE", None)
    monkeypatch.setattr(db, "engine", lambda: object())

    async def no_sweep(stale_after_s=120):
        return []

    monkeypatch.setattr(jobs, "mark_interrupted", no_sweep)
    if status:
        async def fake(engine=None, *, directory=None):
            down = status != "ready"
            return migrate.SchemaState(status, 2, 1 if down else 2, pending=[2] if down else [],
                                       problems=["jobs: missing column x"] if down else [])
        monkeypatch.setattr(migrate, "verify", fake)
    return TestClient(app)


def test_status_needs_a_signed_in_admin(monkeypatch):
    assert _client(monkeypatch, role=None).get("/api/trace-lab/status").status_code in (401, 403)
    r = _client(monkeypatch, role="user").get("/api/trace-lab/status")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"


def test_status_answers_200_and_names_no_table_while_down(monkeypatch):
    r = _client(monkeypatch, status="behind").get("/api/trace-lab/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["available"] is False and body["status"] == "behind"
    assert (body["expected_version"], body["applied_version"]) == (2, 1) and body["checked_at"].endswith("Z")
    assert "jobs" not in r.text and "plenum" not in r.text


def test_status_reports_available_when_ready(monkeypatch):
    body = _client(monkeypatch, role="superadmin", status="ready").get("/api/trace-lab/status").json()
    assert body["available"] is True and body["applied_version"] == 2


def test_main_registers_the_status_router():
    src = (Path(routes.__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "from .routes.trace_lab_status import router as trace_lab_status_router" in src
    assert "app.include_router(trace_lab_status_router)" in src
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_status_route.py`
Expected: collection error — `ImportError: cannot import name 'trace_lab_status' from 'src.api.routes'`.

- [ ] **Step 3: Implement**

`DA/src/api/routes/trace_lab_status.py`:
```python
"""GET /api/trace-lab/status - is Trace Lab's schema verified here (read-only, always 200).

Admins and superadmins only. The answer names no table or schema: the page shows whether Trace
Lab is available, and the logs say why not (spec "Data model" 1.5). While the state is not ready
and the last check is older than 30 s this re-verifies, so a version Hussain applies starts
working without a restart. Every other /api/trace-lab route depends on db.require_ready.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...services.principal import Principal, current_principal
from ...trace_lab import db

router = APIRouter(prefix="/api/trace-lab", tags=["Trace Lab"])


@router.get("/status")
async def trace_lab_status(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    if not principal.is_admin:
        raise db.http_error(403, "reviewers_only", "Only company admins and the platform team can open Trace Lab.")
    st = await db.refresh_state()
    return {"ok": True, "available": st.status == "ready", "status": st.status,
            "expected_version": st.expected_version, "applied_version": st.applied_version,
            "checked_at": db.iso(st.checked_at)}
```

Edit `DA/src/api/main.py` — old:
```python
from .routes.traces import router as traces_router
```
new:
```python
from .routes.traces import router as traces_router
from .routes.trace_lab_status import router as trace_lab_status_router
```
Edit — old:
```python
app.include_router(traces_router)
app.include_router(skill_lab_router)
```
new:
```python
app.include_router(traces_router)
app.include_router(trace_lab_status_router)
app.include_router(skill_lab_router)
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_status_route.py tests/test_agent_modules_import.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/api/routes/trace_lab_status.py`, `DA/src/api/main.py`, `DA/tests/test_trace_lab_status_route.py`.

---

### Task 13: `guards.py` — the eval-data text guards

**Files:**
- Create: `DA/src/trace_lab/guards.py`
- Test: `DA/tests/test_trace_lab_guards.py`

**Interfaces:**
- Consumes: nothing (pure); `selftest()` lazily inspects `agents.udr_agent` (wired in Task 14).
- Produces: `guards.CLOSED_SCHEMAS`, `CLOSED_TABLE_PREFIXES`, `CLOSED_TABLES`, `REFUSAL_TEXT`, `GUARDED_TOOLS`, `REFUSED_SQL`, `closed_table(name)`, `sql_names_closed(sql)`, `drop_closed(rows, *, key="table")`, `selftest() -> list[str]` (M3's replay preflight calls it).

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_guards.py`:
```python
"""The chat never reads Trace Lab's data, the trace tables or the activity log
(spec D15, R26; "Data model" 4.1, 4.3)."""
from __future__ import annotations

import pytest

from src.trace_lab import guards

REFUSED = [
    "plenum_tracelab.labels", "PLENUM_TRACELAB.labels", '"plenum_tracelab"."labels"',
    "plenum_tracelab/**/.labels", "plenum_tracelab --x\r.labels", "SELECT '--', * FROM plenum_tracelab.labels",
    'U&"\\0070lenum_tracelab".labels',
    "agent_trace_turns", "plenum_cafm.agent_trace_spans", '"agent_trace_turns"',
    "query_to_xml('select * from plenum_tracelab.x', true, true, '')",
    "select * from agent_activity_log", "SELECT * FROM plenum_cafm.AGENT_ACTIVITY_LOG",
]


@pytest.mark.parametrize("name", [
    "agent_trace_turns", " AGENT_TRACE_SPANS ", "agent_trace_x", "plenum_tracelab.labels",
    '"plenum_tracelab"."labels"', "agent_activity_log", "plenum_cafm.agent_activity_log", '"agent_trace_turns"',
])
def test_closed_table_names(name):
    assert guards.closed_table(name)


@pytest.mark.parametrize("name", ["buildings", "vendors", "plenum_cafm.work_orders", "", None, "my_agent_trace_x"])
def test_open_table_names(name):
    assert not guards.closed_table(name)


@pytest.mark.parametrize("sql", REFUSED)
def test_sql_names_closed_catches_every_spelling(sql):
    assert guards.sql_names_closed(sql)


def test_an_ordinary_select_is_allowed():
    assert guards.sql_names_closed("select * from plenum_cafm.buildings") is None
    assert guards.sql_names_closed("SELECT count(*) FROM plenum_cafm.work_orders WHERE status = 'open'") is None
    assert guards.sql_names_closed(None) is None


def test_a_unicode_escaped_name_is_refused_outright():
    assert guards.sql_names_closed('select * from U&"\\0070lenum_tracelab".labels') == "unicode-escape"


def test_drop_closed_keeps_the_rest():
    rows = [{"table": "buildings"}, {"table": "agent_trace_turns"}, {"table": "agent_activity_log"}, {"table": "vendors"}, "x"]
    assert guards.drop_closed(rows) == [{"table": "buildings"}, {"table": "vendors"}, "x"]
    assert guards.drop_closed(None) == []
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_guards.py`
Expected: collection error — `ImportError: cannot import name 'guards' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`DA/src/trace_lab/guards.py`:
```python
"""Eval-data guards (spec D15, R26): the chat never reads Trace Lab's data or the trace tables.

Golden answers, reviewer notes, judge prompts and every traced turn live in plenum_tracelab.*,
plenum_cafm.agent_trace_* and plenum_cafm.agent_activity_log (which holds every turn's full
inputs, golden questions included). A chat tool that could read them would let the chat see the
expected answer to a golden question, or other companies' traces. These pure text checks run in
every generic table or SQL reader of agents/udr_agent.py before any database access or request.
They are defence in depth: the real fix is a database role without access to the schema (P6),
and svc-udr refuses the same names on its side.

Matching is deliberately blunt. Names are matched in the RAW text with double quotes removed and
nothing else stripped: a name in a comment, or in SQL passed as a string literal, is still
refused, and a U&"..." escaped identifier is refused outright. Matching the bare name (no
'schema.' needed) also covers an unqualified agent_trace_turns resolved through plenum_scoped.
"""
from __future__ import annotations

import re
from typing import Any

CLOSED_SCHEMAS = ("plenum_tracelab",)
CLOSED_TABLE_PREFIXES = ("agent_trace_",)
CLOSED_TABLES = frozenset({"agent_activity_log"})
REFUSAL_TEXT = "This data is not available to the chat."

_UNICODE_ESCAPE = re.compile(r"u&[\"']", re.IGNORECASE)
_CLOSED_NAME = re.compile(r"\bplenum_tracelab\b|\bagent_trace_[a-z0-9_]*|\bagent_activity_log\b", re.IGNORECASE)

#: The udr_agent tools that read a table or SQL named by the model; selftest() checks each one.
GUARDED_TOOLS = ("get_schema", "query_table", "udr_list_tables", "find_tables", "udr_describe_table", "table_card",
                 "udr_read_records", "udr_get_record", "udr_search_records", "udr_create_record",
                 "udr_update_record", "udr_delete_record", "udr_execute_select")
REFUSED_SQL = (
    "plenum_tracelab.labels", '"plenum_tracelab"."labels"', "plenum_tracelab/**/.labels",
    "SELECT '--', * FROM plenum_tracelab.labels", 'U&"\\0070lenum_tracelab".labels', "agent_trace_turns",
    "plenum_cafm.agent_trace_spans", "query_to_xml('select * from plenum_tracelab.x', true, true, '')",
    "select * from agent_activity_log",
)
ALLOWED_SQL = ("select * from plenum_cafm.buildings", "SELECT count(*) FROM plenum_cafm.work_orders")


def closed_table(name: str | None) -> bool:
    """A 'schema.table' is closed when the schema is closed; any table starting agent_trace_, and
    agent_activity_log, is closed whatever schema names it."""
    n = (name or "").strip().replace('"', "").lower()
    if not n:
        return False
    if "." in n:
        schema, _, table = n.rpartition(".")
        if schema.split(".")[-1] in CLOSED_SCHEMAS:
            return True
        n = table
    return n.startswith(CLOSED_TABLE_PREFIXES) or n in CLOSED_TABLES


def sql_names_closed(sql: str | None) -> str | None:
    """'unicode-escape', or the first closed name the raw SQL mentions, or None."""
    raw = sql or ""
    if _UNICODE_ESCAPE.search(raw):
        return "unicode-escape"
    m = _CLOSED_NAME.search(raw.replace('"', ""))
    return m.group(0).lower() if m else None


def drop_closed(rows: list | None, *, key: str = "table") -> list:
    """A table list without the closed tables (svc-udr's list and catalogue answers)."""
    out: list[Any] = []
    for r in rows or []:
        name = r.get(key) if isinstance(r, dict) else r
        if isinstance(name, str) and closed_table(name):
            continue
        out.append(r)
    return out


def selftest() -> list[str]:
    """Problems with the guards, or []. M3's replay preflight refuses a golden run unless this is []."""
    problems: list[str] = []
    for s in REFUSED_SQL:
        if not sql_names_closed(s):
            problems.append(f"custom SELECT not refused: {s[:60]}")
    for s in ALLOWED_SQL:
        if sql_names_closed(s):
            problems.append(f"custom SELECT wrongly refused: {s[:60]}")
    for n in ("agent_trace_turns", "plenum_tracelab.labels", "agent_activity_log"):
        if not closed_table(n):
            problems.append(f"table not closed: {n}")
    for n in ("buildings", "work_orders"):
        if closed_table(n):
            problems.append(f"table wrongly closed: {n}")
    try:
        import inspect

        from ..agents import udr_agent

        for name in GUARDED_TOOLS:
            tool = getattr(udr_agent, name)
            src = inspect.getsource(getattr(tool, "coroutine", None) or tool)
            if "_tl_guards." not in src and "_closed(" not in src:
                problems.append(f"chat tool {name} is not guarded")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"chat tool check failed: {str(exc)[:200]}")
    return problems
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_guards.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/guards.py`, `DA/tests/test_trace_lab_guards.py`.

---

### Task 14: Guard every generic table/SQL tool in `udr_agent.py`

**Files:**
- Modify: `DA/src/agents/udr_agent.py:18-19` (import), `:100-104` and `:131-135` (`get_schema`), `:208-210` (`query_table`), `:264-269` (new `_closed` helper after `_err`), `:495-508` (`udr_list_tables`), `:511-524` (`udr_describe_table`), `:550-558` (`find_tables`), `:561-577` (`table_card`), `:598-628` (`udr_read_records`), `:631-650` (`udr_get_record`), `:653-678` (`udr_search_records`), `:681-696`, `:699-719`, `:722-737` (create/update/delete), `:740-754` (`udr_execute_select`)
- Test: `DA/tests/test_trace_lab_guards.py` (append)

**Interfaces:**
- Consumes: `guards.closed_table`, `guards.sql_names_closed`, `guards.drop_closed`, `guards.REFUSAL_TEXT`, `guards.selftest` (Task 13).
- Produces: every `@tool` with a `table`, `table_name` or `sql` parameter refuses closed names with `{"error": REFUSAL_TEXT}` (or `[{"error": ...}]` for `query_table`) before any database access or request; `get_schema`, `udr_list_tables` and `find_tables` omit closed tables. Private helper `udr_agent._closed(table, op) -> dict | None`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_guards.py`:
```python


# ── the call sites in agents/udr_agent.py (spec 4.1) ─────────────────────────────────

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

from src.agents import udr_agent  # noqa: E402

REFUSAL = {"error": guards.REFUSAL_TEXT}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    COLUMNS = [{"table_name": "buildings", "column_name": "building_id", "data_type": "uuid"},
               {"table_name": "agent_trace_turns", "column_name": "question", "data_type": "text"},
               {"table_name": "agent_activity_log", "column_name": "payload", "data_type": "jsonb"}]
    FKS = [{"from_table": "assets", "from_column": "building_id", "to_table": "buildings", "to_column": "building_id"},
           {"from_table": "agent_trace_spans", "from_column": "turn_id", "to_table": "agent_trace_turns", "to_column": "turn_id"}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _Result(self.FKS if "FOREIGN KEY" in str(stmt) else self.COLUMNS)


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _no_session():
    raise AssertionError("the database was reached")


async def _no_request(*a, **k):
    raise AssertionError("a request left this process")


async def test_get_schema_hides_closed_tables(monkeypatch):
    monkeypatch.setattr(udr_agent, "_schema_cache", None)
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: _Session())
    out = await udr_agent.get_schema.ainvoke({})
    assert list(out["tables"]) == ["buildings"]
    assert out["relationships"] == [{"from": "assets.building_id", "to": "buildings.building_id"}]


@pytest.mark.parametrize("table", ["agent_trace_turns", "agent_trace_spans", "agent_activity_log"])
async def test_query_table_refuses_before_any_db_call(monkeypatch, table):
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", _no_session)
    assert await udr_agent.query_table.ainvoke({"table_name": table}) == [REFUSAL]


TOOL_CALLS = [
    ("udr_describe_table", {"table": "agent_trace_spans"}),
    ("table_card", {"table": "agent_trace_turns"}),
    ("udr_read_records", {"table": "agent_activity_log"}),
    ("udr_get_record", {"table": "agent_trace_turns", "record_id": "turn-1"}),
    ("udr_search_records", {"table": "agent_trace_turns", "search_term": "a", "search_columns": ["question"]}),
    ("udr_create_record", {"table": "agent_trace_spans", "data": {"x": 1}}),
    ("udr_update_record", {"table": "agent_trace_spans", "record_id": "x", "data": {"x": 1}}),
    ("udr_delete_record", {"table": "agent_activity_log", "record_id": "x"}),
    ("udr_execute_select", {"sql": "select * from plenum_tracelab.golden_cases"}),
    ("udr_execute_select", {"sql": 'SELECT * FROM "agent_trace_turns"'}),
]


@pytest.mark.parametrize("tool, args", TOOL_CALLS)
async def test_udr_table_tools_refuse_before_any_request(monkeypatch, tool, args):
    monkeypatch.setattr(udr_agent, "_request", _no_request)
    assert await getattr(udr_agent, tool).ainvoke(args) == REFUSAL


@pytest.mark.parametrize("tool, args", [("udr_list_tables", {}), ("find_tables", {"question": "which tables hold traces"})])
async def test_list_and_find_tables_drop_closed_tables(monkeypatch, tool, args):
    async def fake_request(*a, **k):
        return _Resp({"ok": True, "tables": [{"table": "buildings"}, {"table": "agent_trace_turns"},
                                             {"table": "agent_activity_log"}]})

    monkeypatch.setattr(udr_agent, "_request", fake_request)
    out = await getattr(udr_agent, tool).ainvoke(args)
    assert out["tables"] == [{"table": "buildings"}]


async def test_an_open_table_still_reads(monkeypatch):
    seen = {}

    async def fake_request(method, base, path, **k):
        seen["path"] = path
        return _Resp({"table": "buildings"})

    monkeypatch.setattr(udr_agent, "_request", fake_request)
    assert await udr_agent.udr_describe_table.ainvoke({"table": "buildings"}) == {"table": "buildings"}
    assert seen["path"] == "/api/tables/buildings/schema"


def test_every_generic_reader_is_guarded():
    source = Path(udr_agent.__file__).read_text(encoding="utf-8")
    checked, unguarded = [], []
    for node in ast.parse(source).body:
        if isinstance(node, ast.AsyncFunctionDef) and any(isinstance(d, ast.Name) and d.id == "tool"
                                                          for d in node.decorator_list):
            if {a.arg for a in node.args.args + node.args.kwonlyargs} & {"table", "table_name", "sql"}:
                checked.append(node.name)
                body = ast.get_source_segment(source, node) or ""
                if "_tl_guards." not in body and "_closed(" not in body:
                    unguarded.append(node.name)
    assert len(checked) >= 10, checked
    assert unguarded == []


def test_the_guard_selftest_passes():
    assert guards.selftest() == []
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_guards.py`
Expected: FAIL — `get_schema` lists `agent_trace_turns`; `query_table` reaches the session (`AssertionError: the database was reached` comes back as an error row, so the equality fails); each table tool raises `a request left this process` inside its `_err` path; `unguarded` lists 10 tools; `selftest()` reports 13 unguarded tools.

- [ ] **Step 3: Implement**

Edits to `DA/src/agents/udr_agent.py`, each an exact-string replacement:

(a) Import — old:
```python
from ..services.principal import restrict_records, table_building_clause
```
new:
```python
from ..services.principal import restrict_records, table_building_clause
from ..trace_lab import guards as _tl_guards
```

(b) `get_schema` columns — old:
```python
    tables: dict[str, list[str]] = {}
    for row in rows:
        tbl = row["table_name"]
        col = f"{row['column_name']} ({row['data_type']})"
        tables.setdefault(tbl, []).append(col)
```
new:
```python
    tables: dict[str, list[str]] = {}
    for row in rows:
        tbl = row["table_name"]
        # Trace Lab's data and the trace and activity tables are closed to the chat (D15, R26).
        if _tl_guards.closed_table(tbl):
            continue
        col = f"{row['column_name']} ({row['data_type']})"
        tables.setdefault(tbl, []).append(col)
```

(c) `get_schema` relationships — old:
```python
            relationships = [
                {"from": f"{r['from_table']}.{r['from_column']}",
                 "to": f"{r['to_table']}.{r['to_column']}"}
                for r in fks
            ]
```
new:
```python
            relationships = [
                {"from": f"{r['from_table']}.{r['from_column']}",
                 "to": f"{r['to_table']}.{r['to_column']}"}
                for r in fks
                if not (_tl_guards.closed_table(r["from_table"]) or _tl_guards.closed_table(r["to_table"]))
            ]
```

(d) `query_table` — old:
```python
    if not _SAFE_IDENT.match(table_name):
        log.warning("udr.query_table.unsafe_table", table=table_name)
        return [{"error": f"Unsafe table name: {table_name!r}"}]
```
new:
```python
    if not _SAFE_IDENT.match(table_name):
        log.warning("udr.query_table.unsafe_table", table=table_name)
        return [{"error": f"Unsafe table name: {table_name!r}"}]
    if _tl_guards.closed_table(table_name):
        log.warning("udr.query_table.closed_table", table=table_name)
        return [{"error": _tl_guards.REFUSAL_TEXT}]
```

(e) `_closed` helper after `_err` — old:
```python
    log.error(f"udr.{op}.error", error=str(exc)[:300])
    return {"error": str(exc)[:300]}
```
new:
```python
    log.error(f"udr.{op}.error", error=str(exc)[:300])
    return {"error": str(exc)[:300]}


def _closed(table: str | None, op: str) -> dict | None:
    """Trace Lab's data and the trace and activity tables are closed to every chat tool (spec D15,
    R26). Checked before any request, so a refused name never leaves this process."""
    if _tl_guards.closed_table(table):
        log.warning(f"udr.{op}.closed_table", table=table)
        return {"error": _tl_guards.REFUSAL_TEXT}
    return None
```

(f) `udr_list_tables` — old:
```python
            "/api/tables/",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_tables")
```
new:
```python
            "/api/tables/",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if isinstance(data, dict):
            data["tables"] = _tl_guards.drop_closed(data.get("tables"))
        return data
    except Exception as exc:
        return _err(exc, "list_tables")
```

(g) `udr_describe_table` — old:
```python
    """Describe table schema details including keys and relationships."""
    try:
```
new:
```python
    """Describe table schema details including keys and relationships."""
    if (refused := _closed(table, "describe_table")) is not None:
        return refused
    try:
```

(h) `find_tables` — old:
```python
        resp = await _request("GET", settings.udr_base_url, "/api/catalog/search",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        return resp.json()
```
new:
```python
        resp = await _request("GET", settings.udr_base_url, "/api/catalog/search",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        data = resp.json()
        if isinstance(data, dict):
            data["tables"] = _tl_guards.drop_closed(data.get("tables"))
        return data
```

(i) `table_card` — old:
```python
    purpose — treat its description as structural, not semantic.
    """
    try:
        resp = await _request("GET", settings.udr_base_url, f"/api/catalog/{table}",
```
new:
```python
    purpose — treat its description as structural, not semantic.
    """
    if (refused := _closed(table, "table_card")) is not None:
        return refused
    try:
        resp = await _request("GET", settings.udr_base_url, f"/api/catalog/{table}",
```

(j) `udr_read_records` — old:
```python
    For larger datasets use offset paging or write_file Mode 3 offload.
    """
    params: dict[str, Any] = {
```
new:
```python
    For larger datasets use offset paging or write_file Mode 3 offload.
    """
    if (refused := _closed(table, "read_records")) is not None:
        return refused
    params: dict[str, Any] = {
```

(k) `udr_get_record` — old:
```python
    """Fetch one record by primary key."""
    try:
```
new:
```python
    """Fetch one record by primary key."""
    if (refused := _closed(table, "get_record")) is not None:
        return refused
    try:
```

(l) `udr_search_records` — old:
```python
    """Search records using case-insensitive partial matches across columns."""
    try:
```
new:
```python
    """Search records using case-insensitive partial matches across columns."""
    if (refused := _closed(table, "search_records")) is not None:
        return refused
    try:
```

(m) `udr_create_record` — old:
```python
    """Create a record in a UDR table."""
    try:
```
new:
```python
    """Create a record in a UDR table."""
    if (refused := _closed(table, "create_record")) is not None:
        return refused
    try:
```

(n) `udr_update_record` — old:
```python
    """Update fields on a record in a UDR table."""
    try:
```
new:
```python
    """Update fields on a record in a UDR table."""
    if (refused := _closed(table, "update_record")) is not None:
        return refused
    try:
```

(o) `udr_delete_record` — old:
```python
    """Delete a record from a UDR table by primary key."""
    try:
```
new:
```python
    """Delete a record from a UDR table by primary key."""
    if (refused := _closed(table, "delete_record")) is not None:
        return refused
    try:
```

(p) `udr_execute_select` — old:
```python
    """Execute a custom SELECT statement through UDR with safety checks."""
    try:
```
new:
```python
    """Execute a custom SELECT statement through UDR with safety checks."""
    # Matched in the raw text: on eval-2 an unqualified agent_trace_turns resolves through
    # svc-udr's plenum_scoped views, so the bare name is refused too (spec 4.1).
    hit = _tl_guards.sql_names_closed(sql)
    if hit:
        log.warning("udr.execute_select.closed_table", name=hit)
        return {"error": _tl_guards.REFUSAL_TEXT}
    try:
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_guards.py tests/test_get_schema_relationships.py tests/test_table_catalog_tools.py tests/test_contract_tool_guards.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/udr_agent.py`, `DA/tests/test_trace_lab_guards.py`.

---

### Task 15: svc-udr refuses eval data in custom SELECT and on its table routes

**Files:**
- Modify: `UDR/src/services/scope.py:99-109` (closed sets, `is_denied`, `names_trace_lab`), `:291-308` (`__all__`)
- Modify: `UDR/src/services/database_service.py:544-550` (`execute_select`)
- Test: `UDR/tests/unit/test_trace_lab_schema_closed.py`

**Interfaces:**
- Consumes: `scope.DENIED_TABLES`, `scope.is_denied` (UDR `src/services/scope.py:99-109`), `DatabaseService.execute_select` (UDR `src/services/database_service.py:530-599`), `UnsafeQueryError` → HTTP 400 (UDR `src/api/routes/tables.py:54-60`).
- Produces: `scope.EVAL_DATA_TABLES`, `scope.EVAL_DATA_TABLE_PREFIXES`, `scope.names_trace_lab(sql) -> bool`; `is_denied()` also true for `agent_activity_log` and `agent_trace_*` (so list, describe, read, search, create, update and delete refuse them and the list omits them). `DENIED_TABLES` itself is unchanged (it must stay equal to ops-intel's `udr_scoped_views.sql`, pinned by `tests/unit/test_scope.py:261-276`).

- [ ] **Step 1: Write the failing test**

`UDR/tests/unit/test_trace_lab_schema_closed.py`:
```python
"""svc-udr refuses Trace Lab's data, the chat's trace tables and the activity log (Trace Lab spec
D15, R26; "Data model" 4.2-4.3). This is the spec's fallback path: F5's scope.py is not on this
branch. When F5 lands, keep both refusals."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/d")
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient  # noqa: E402

from src.app import app  # noqa: E402
from src.core.exceptions import UnsafeQueryError  # noqa: E402
from src.db import get_session  # noqa: E402
from src.services import principal as P  # noqa: E402
from src.services import scope  # noqa: E402
from src.services.database_service import DatabaseService  # noqa: E402

ORG = UUID("11111111-1111-1111-1111-111111111111")

REFUSED = [
    "plenum_tracelab.labels", "PLENUM_TRACELAB.labels", '"plenum_tracelab"."labels"',
    "plenum_tracelab/**/.labels", "plenum_tracelab --x\r.labels", "SELECT '--', * FROM plenum_tracelab.labels",
    'U&"\\0070lenum_tracelab".labels',
    "agent_trace_turns", "plenum_cafm.agent_trace_spans", '"agent_trace_turns"',
    "query_to_xml('select * from plenum_tracelab.x', true, true, '')",
    "select * from agent_activity_log", "SELECT * FROM plenum_cafm.AGENT_ACTIVITY_LOG",
]


def _principal(role: str) -> P.Principal:
    return P.Principal(user_id=uuid4(), email="dev@example.com", organization_id=ORG, role=role, building_ids=None)


class _FailSession:
    async def execute(self, *a, **k):
        raise AssertionError("the database was reached")

    async def commit(self):
        raise AssertionError("the database was reached")


def test_trace_tables_are_denied_on_every_table_route():
    for name in ("agent_trace_turns", " AGENT_TRACE_SPANS ", "agent_trace_x", "agent_activity_log"):
        assert scope.is_denied(name), name
    for name in ("buildings", "vendors", "work_orders"):
        assert not scope.is_denied(name), name
    assert scope.is_denied("auth_sessions")                       # the credential tables stay denied
    assert "agent_activity_log" not in scope.DENIED_TABLES          # kept apart from the views' mirror list


@pytest.mark.parametrize("sql", REFUSED)
def test_names_trace_lab_catches_every_spelling(sql):
    assert scope.names_trace_lab(sql)


def test_an_ordinary_select_is_not_refused():
    assert not scope.names_trace_lab("select * from plenum_cafm.buildings")


@pytest.mark.parametrize("role", ["admin", "superadmin"])
@pytest.mark.parametrize("sql", [
    "SELECT * FROM plenum_tracelab.golden_sets",
    "select count(*) from agent_trace_turns",
    "SELECT * FROM plenum_cafm.agent_activity_log",
    'SELECT * FROM U&"\\0070lenum_tracelab".labels',
])
async def test_execute_select_refuses_before_any_db_call(role, sql):
    svc = DatabaseService(_FailSession(), _principal(role))
    with pytest.raises(UnsafeQueryError, match="not available here"):
        await svc.execute_select(sql)


def test_route_answers_400():
    async def _me():
        return _principal("admin")

    async def _session():
        yield _FailSession()

    app.dependency_overrides[P.current_principal] = _me
    app.dependency_overrides[get_session] = _session
    try:
        r = TestClient(app).post("/api/tables/query/select", json={"sql": "SELECT * FROM plenum_tracelab.golden_sets"})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tludr.sh tests/unit/test_trace_lab_schema_closed.py`
Expected: FAIL — `AttributeError: module 'src.services.scope' has no attribute 'names_trace_lab'`; `is_denied('agent_trace_turns')` is False; `execute_select` reaches the session (`AssertionError: the database was reached`).

- [ ] **Step 3: Implement**

Edit `UDR/src/services/scope.py` — old:
```python
    "approval_action_tokens",  # single-use tokens that authorise an approval by possession
})


def is_denied(table: str) -> bool:
    """Whether this table is closed to the Universal Database Reader entirely."""
    return table.strip().lower() in DENIED_TABLES
```
new:
```python
    "approval_action_tokens",  # single-use tokens that authorise an approval by possession
})

#: Trace Lab's evaluation data (Trace Lab spec D15, R26), closed like DENIED_TABLES but kept apart
#: from that list, which svc-operations-intelligence's udr_scoped_views.sql mirrors
#: (tests/unit/test_scope.py pins the two equal; changing the views needs an ops-intel rebuild).
#: agent_activity_log holds every chat turn's full inputs, golden questions included;
#: agent_trace_* holds every company's questions and answers.
EVAL_DATA_TABLES: frozenset[str] = frozenset({"agent_activity_log"})
EVAL_DATA_TABLE_PREFIXES: tuple[str, ...] = ("agent_trace_",)

#: A caller-supplied SELECT naming Trace Lab's schema, a trace table or the activity log - matched in
#: the raw text with double quotes removed, so a name inside a comment or a string literal still
#: counts - or hiding a name behind a U&"..." escape.
_EVAL_DATA_NAMES = re.compile(r"\bplenum_tracelab\b|\bagent_trace_[a-z0-9_]*|\bagent_activity_log\b", re.IGNORECASE)
_UNICODE_ESCAPE = re.compile(r"u&[\"']", re.IGNORECASE)


def is_denied(table: str) -> bool:
    """Whether this table is closed to the Universal Database Reader entirely."""
    name = table.strip().lower()
    return name in DENIED_TABLES or name in EVAL_DATA_TABLES or name.startswith(EVAL_DATA_TABLE_PREFIXES)


def names_trace_lab(sql: str) -> bool:
    """True when a custom SELECT names evaluation data, or escapes a name with U&. Checked before any
    database access. Textual by design: the real fix is a database role without access to
    plenum_tracelab (Trace Lab prerequisite P6)."""
    raw = sql or ""
    return bool(_UNICODE_ESCAPE.search(raw) or _EVAL_DATA_NAMES.search(raw.replace('"', "")))
```
Edit — old:
```python
    "is_denied",
    "predicate",
```
new:
```python
    "is_denied",
    "names_trace_lab",
    "EVAL_DATA_TABLES",
    "EVAL_DATA_TABLE_PREFIXES",
    "predicate",
```

Edit `UDR/src/services/database_service.py` — old:
```python
            raise UnsafeQueryError("Multiple statements (semicolon) are not allowed.")

        # A caller-supplied SELECT cannot have a per-table predicate injected into it, so the
```
new:
```python
            raise UnsafeQueryError("Multiple statements (semicolon) are not allowed.")
        # Trace Lab's data, the chat's trace tables and the activity log are never readable here
        # (Trace Lab spec D15, R26): refused by name before any database access, for every role.
        if scope.names_trace_lab(stripped):
            log.warning(
                "db.security.eval_data_blocked",
                sql_preview=stripped[:120],
                reason="custom SELECT names Trace Lab or trace data",
            )
            raise UnsafeQueryError("This custom SELECT names data that is not available here.")

        # A caller-supplied SELECT cannot have a per-table predicate injected into it, so the
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tludr.sh tests/unit/test_trace_lab_schema_closed.py tests/unit/test_scope.py tests/unit/test_security_boundary.py`
Expected: PASS (`test_scope.py`'s parity test still compares `DENIED_TABLES`, unchanged; it skips when ops-intel's migration is not in the copied tree).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `UDR/src/services/scope.py`, `UDR/src/services/database_service.py`, `UDR/tests/unit/test_trace_lab_schema_closed.py`.

---

### Task 16: `provenance.py` — request provenance and the channel decision

**Files:**
- Create: `DA/src/trace_lab/provenance.py`
- Test: `DA/tests/test_trace_lab_provenance.py`

**Interfaces:**
- Consumes: `replay_guard.eval_context()` (Task 1); `caller_principal` (DA `src/services/principal.py:182`), `caller_organization_id` (DA `src/http_client.py:136`); `agent_router.is_support_session` and `SUPPORT_SESSION_MARK` (DA `src/agents/agent_router.py:54-58`).
- Produces: `PROVENANCE_RULES`, `LEGACY_CLASSIFIER`, `CHANNELS`, `ROUTES`, `CLIENT_ORIGINS`, `SERVER_ORIGINS`, `ORIGIN_PARAM_KEYS`, `TEMPLATES`, `UPLOAD_PROMPTS`, `FILES_NOTE`, `SUPPORT_MARK`, `RequestProvenance`, `TurnProvenance`, `TurnMeta`, `REQUEST`, `accept_origin`, `accept_origin_params`, `accept_page`, `set_request`, `form_params`, `match_template`, `capture`, `decide`.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_provenance.py`:
```python
"""Turn provenance: what a page may say, and how the channel is decided (spec "Trace capture"
2.1, R7)."""
from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import pytest

from src.agents import replay_guard
from src.agents.agent_router import SUPPORT_SESSION_MARK
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal
from src.trace_lab import migrate
from src.trace_lab import provenance as prov

RUN = "6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e"
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
OTHER = "00000000-0000-0000-0000-0000000000b2"
B1 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
TID = "turn-0123456789abcdef"
SUPPORT = SUPPORT_SESSION_MARK + ". The guide follows."


@pytest.fixture(autouse=True)
def _clean_context():
    tokens = (prov.REQUEST.set(None), replay_guard.EVAL.set(None), caller_principal.set(None),
              caller_organization_id.set(None))
    yield
    prov.REQUEST.reset(tokens[0])
    replay_guard.EVAL.reset(tokens[1])
    caller_principal.reset(tokens[2])
    caller_organization_id.reset(tokens[3])


def _p(**kw):
    base = dict(request=None, mode="stream", rerun_of=None, corrections=0, payload_context=None, eval=None,
                asker_role="admin", asker_scope=None, asker_message=None)
    base.update(kw)
    return prov.TurnProvenance(**base)


def _req(**kw):
    return prov.RequestProvenance(**{"route": "ws", **kw})


def test_channels_match_the_ddl_and_clients_never_claim_golden_or_workflow():
    sql = (migrate.MIGRATIONS_DIR / "0001_foundation.sql").read_text(encoding="utf-8")
    ddl = re.findall(r"'([a-z_]+)'", re.search(r"CHECK \(channel IN \((.*?)\)\)", sql, re.S).group(1))
    assert tuple(ddl) == prov.CHANNELS
    assert set(prov.CLIENT_ORIGINS.values()) <= set(prov.CHANNELS) - {"golden", "workflow"}
    assert set(prov.SERVER_ORIGINS) & set(prov.CLIENT_ORIGINS) == set()
    assert set(prov.ORIGIN_PARAM_KEYS) <= set(prov.CLIENT_ORIGINS)
    assert prov.SUPPORT_MARK == SUPPORT_SESSION_MARK


@pytest.mark.parametrize("raw, accepted", [
    ("chat.typed", "chat.typed"), ("chat.chip:home", "chat.chip:home"), ("ui.matrix_cell:risk", "ui.matrix_cell:risk"),
    ("support.topic:getting-started", "support.topic:getting-started"),
    ("golden.case", None), ("workflow.resume", None), ("correction.rerun", None),
    ("chat.chip:Home", None), ("chat.chip:", None), ("chat.typed:" + "x" * 41, None), ("nonsense", None),
    (None, None), (7, None),
])
def test_accept_origin(raw, accepted):
    assert prov.accept_origin(raw) == accepted


def test_accept_origin_params_keeps_only_allowed_keys_and_values():
    got = prov.accept_origin_params("ui.matrix_cell", {"building": "Kingsway House", "code": "GAS", "state": "risk",
                                                       "type": None, "extra": "x"})
    assert got == {"building": "Kingsway House", "code": "GAS", "state": "risk", "type": None}
    assert prov.accept_origin_params("ui.matrix_cell", {"building": "y" * 201}) is None
    assert prov.accept_origin_params("chat.edit", {"replaces_turn": TID}) == {"replaces_turn": TID}
    assert prov.accept_origin_params("chat.edit", {"replaces_turn": "turn-1"}) is None
    assert prov.accept_origin_params("upload.migrate", {"count": 3, "cmms": "Fiix"}) == {"count": 3, "cmms": "Fiix"}
    assert prov.accept_origin_params("upload.migrate", {"count": float("nan")}) is None
    assert prov.accept_origin_params("chat.typed", {"anything": 1}) is None
    assert prov.accept_origin_params("support.topic", ["topic"]) is None
    assert prov.accept_origin_params("support.topic", None) is None


@pytest.mark.parametrize("raw, accepted", [
    ("home", "home"), ("module:energy", "module:energy"), ("Home", None), ("x" * 61, None), ("a b", None), (None, None),
])
def test_accept_page(raw, accepted):
    assert prov.accept_page(raw) == accepted


def test_set_request_never_raises_and_drops_what_fails():
    prov.set_request(route="nope", origin="golden.case", origin_params={"x": 1}, page="Bad Page", context=123, files="2")
    r = prov.REQUEST.get()
    assert (r.route, r.origin, r.origin_params, r.page, r.context, r.files) == ("in_process", None, None, None, None, 2)
    prov.set_request(route="files", origin="upload.ingest_panel", origin_params={"building": "B", "count": 2},
                     page="home", context="ctx", files=2, ingest_source="files")
    r = prov.REQUEST.get()
    assert (r.route, r.origin, r.origin_params, r.files, r.ingest_source) == \
        ("files", "upload.ingest_panel", {"building": "B", "count": 2}, 2, "files")
    prov.set_request(route="ws", files=object())        # unreadable: logged, never raised
    assert prov.REQUEST.get().route == "files"


@pytest.mark.parametrize("raw, parsed", [
    ('{"building": "B"}', {"building": "B"}), ("[1]", None), ("not json", None),
    ("{" + " " * 2001 + "}", None), (None, None), ("", None),
])
def test_form_params(raw, parsed):
    assert prov.form_params(raw) == parsed


TEMPLATE_SAMPLES = [
    ("Re-run with corrections (query): How many boilers?", "correction", "correction.rerun"),
    ("Re-answer the previous question with this correction: use May. Keep the same building and period "
     "unless the correction changes them.", "correction", "correction.reanswer"),
    ("Validate and ingest this document.", "upload", "upload.files"),
    ("File this document on the building.", "upload", "upload.files"),
    ("Ingest 3 documents for Kingsway House.", "upload", "upload.ingest_panel"),
    ("Ingest this certificate for Harbour Point. They are certificates for the building.", "upload", "upload.ingest_panel"),
    ("Ingest these 2 certificates for the vendor Apex.", "upload", "upload.ingest_panel"),
    ("Migrate assets.xlsx, sites.csv", "upload", "upload.migrate"),
    ("Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?",
     "ui_action", "ui.matrix_cell:risk"),
    ("The Gas Safety Certificate at Kingsway House is expiring soon — what is the situation, and what should I do next?",
     "ui_action", "ui.matrix_cell:warn"),
    ("Why is there no Gas Safety Certificate on record for Kingsway House, and what should I do next?",
     "ui_action", "ui.matrix_cell:gap"),
    ("What is the status of the Gas Safety Certificate at Kingsway House, and who services it?",
     "ui_action", "ui.matrix_cell:ok"),
    ("Map each regulated area of my buildings to its compliance obligations: for every obligation ...",
     "ui_action", "ui.hoist_graph"),
    ("Why is AHU-1 at Kingsway House showing a baseload drift worth £1,200 a year, and what should I do about it?",
     "ui_action", "ui.energy_investigate:anomaly"),
    ("Why is Kingsway House at 241 kWh/m² against a reference of 180, and how much of that gap can I act on?",
     "ui_action", "ui.energy_investigate:eui"),
    ("What do we know about Kingsway House's energy performance, and what is missing to assess it properly?",
     "ui_action", "ui.energy_investigate:unknown"),
]


@pytest.mark.parametrize("question, channel, origin", TEMPLATE_SAMPLES)
def test_every_template_matches_its_source_prompt(question, channel, origin):
    assert prov.match_template(question) == (channel, origin)


def test_a_typed_question_matches_no_template():
    assert prov.match_template("How many boilers are at Kingsway House?") is None


DECIDE = [
    ("golden first", _p(eval={"run_id": RUN, "case_key": "C1", "rep": 1}, request=_req(files=2, context=SUPPORT)),
     "q", "a", None, "golden-1", ("golden", "recorded", "golden.case")),
    ("files", _p(request=_req(route="files", files=1)), "Validate and ingest this document.", "a", None, "s1",
     ("upload", "recorded", "upload.files")),
    ("files keep the client origin", _p(request=_req(route="files", files=1, origin="upload.ingest_panel")),
     "x", "a", None, "s1", ("upload", "recorded", "upload.ingest_panel")),
    ("rerun", _p(mode="rerun", rerun_of=TID, corrections=2), "Re-run with corrections (x): q", "a", None, "s1",
     ("correction", "recorded", "correction.rerun")),
    ("report session", _p(request=_req(origin="report.card")), "q", "a", None, "report-123",
     ("report", "recorded", "report.card")),
    ("support session", _p(request=_req(context=SUPPORT, origin="support.topic:ingest")), "how do I ingest?", "a",
     None, "s1", ("support", "recorded", "support.topic:ingest")),
    ("support from the payload context", _p(payload_context=SUPPORT), "how?", "a", None, "s1",
     ("support", "recorded", None)),
    ("resume", _p(request=_req(route="resume")), "", "a", None, "s1", ("workflow", "recorded", "workflow.resume")),
    ("client origin", _p(request=_req(origin="ui.matrix_cell:risk")), "anything", "a", None, "s1",
     ("ui_action", "client", "ui.matrix_cell:risk")),
    ("a report origin outside a report session is dropped", _p(request=_req(origin="report.card")), "q", "a",
     None, "s1", ("chat", "inferred", None)),
    ("template", _p(), "Map each regulated area of my buildings to its compliance obligations: list", "a", None,
     "s1", ("ui_action", "inferred", "ui.hoist_graph")),
    ("gate interrupt", _p(), "q", "gate_interrupt", None, "s1", ("workflow", "inferred", None)),
    ("blank question", _p(), "   ", "a", None, "s1", ("workflow", "inferred", None)),
    ("clarify", _p(), "q", "a", "single:clarify", "s1", ("workflow", "inferred", None)),
    ("default", _p(), "How many boilers?", "Three.", "single:udr", "s1", ("chat", "inferred", None)),
    ("no provenance at all", None, "How many boilers?", "Three.", None, None, ("chat", "inferred", None)),
]


@pytest.mark.parametrize("case, p, question, answer, route, session, want", DECIDE, ids=[c[0] for c in DECIDE])
def test_decide_precedence(case, p, question, answer, route, session, want):
    m = prov.decide(p, turn_id=TID, question=question, answer=answer, route=route, session_id=session)
    assert (m.channel, m.channel_source, m.origin) == want
    assert m.classifier_version == "prov-1" and m.turn_id == TID


def test_decide_fills_the_row():
    p = _p(mode="rerun", rerun_of=TID, corrections=2, asker_message="How many boilers?",
           asker_scope={"all_buildings": True}, request=_req(route="run_stateful", page="cc", context="ctx"))
    m = prov.decide(p, turn_id=TID, question="Re-run with corrections (x): How many boilers?", answer="a",
                    route=None, session_id="s1", organization_id=str(ORG))
    assert m.origin_params == {"rerun_of": TID, "corrections": 2}
    assert (m.page, m.context_text, m.turn_mode, m.rerun_of, m.organization_id) == ("cc", "ctx", "rerun", TID, str(ORG))
    assert m.asker_question == "How many boilers?"          # differs from the stored question
    same = prov.decide(_p(asker_message="q"), turn_id=TID, question="q", answer="a", route=None, session_id="s1")
    assert same.asker_question is None
    g = prov.decide(_p(eval={"run_id": RUN, "case_key": "C1", "rep": 2}), turn_id=TID, question="q", answer="a",
                    route=None, session_id="golden-x")
    assert (g.golden_run_id, g.golden_case_key, g.rep, g.origin_params) == (RUN, "C1", 2, None)
    f = prov.decide(_p(request=_req(route="files", files=3)), turn_id=TID, question="q", answer="a", route=None,
                    session_id="s")
    assert f.files_count == 3
    r = prov.decide(_p(request=_req(route="resume")), turn_id=TID, question="", answer="a", route=None, session_id="s")
    assert r.turn_mode == "resume"
    long = prov.decide(_p(payload_context="x" * 20000), turn_id=TID, question="q", answer="a", route=None, session_id="s")
    assert len(long.context_text) == 16000


def test_capture_reads_the_request_eval_and_the_asker():
    p = Principal(user_id=uuid.uuid4(), email="a@b.c", organization_id=ORG, role="user", can_ingest=False,
                  building_ids=(B1,), buildings=({"id": str(B1), "name": "Kingsway House", "building_code": "KH"},))
    caller_principal.set(p)
    prov.set_request(route="ws", origin="chat.chip:home", page="home", context="ctx")
    tp = prov.capture({"message": "How many?", "mode": "stream", "extra_context": "ctx"}, {})
    assert tp.request.origin == "chat.chip:home" and tp.mode == "stream" and tp.asker_role == "user"
    assert tp.payload_context == "ctx" and tp.asker_message == "How many?" and tp.eval is None
    assert tp.asker_scope == {"all_buildings": False, "building_count": 1,
                              "buildings": [{"id": str(B1), "name": "Kingsway House", "building_code": "KH"}],
                              "own_organization_id": str(ORG), "acting_organization_id": str(ORG), "view_as": False}


def test_a_superadmin_viewing_a_company_is_view_as():
    caller_principal.set(Principal(user_id=uuid.uuid4(), email="sa@b.c", organization_id=ORG, role="superadmin",
                                   can_ingest=True, building_ids=None))
    caller_organization_id.set(OTHER)
    tp = prov.capture({"message": "q", "mode": "rest", "context": "first 300"}, {})
    assert tp.asker_scope["view_as"] is True and tp.asker_scope["acting_organization_id"] == OTHER
    assert tp.asker_scope["all_buildings"] is True and tp.asker_scope["buildings"] == []
    assert tp.payload_context == "first 300" and tp.mode == "rest"


@pytest.mark.parametrize("ctx, want", [
    ({"run_id": RUN, "case_key": "C1", "rep": 2}, {"run_id": RUN, "case_key": "C1", "rep": 2}),
    (SimpleNamespace(run_id=RUN, case_key="C1", rep=1, kind="golden"), {"run_id": RUN, "case_key": "C1", "rep": 1}),
    ({"run_id": "not-a-uuid", "case_key": "C1", "rep": 1}, None),
    ({"run_id": RUN, "case_key": "", "rep": 1}, None),
    ({"run_id": RUN, "case_key": "C" * 121, "rep": 1}, None),
    ({"run_id": RUN, "case_key": "C1", "rep": 0}, None),
])
def test_capture_validates_the_eval_context(ctx, want):
    replay_guard.EVAL.set(ctx)
    assert prov.capture({"message": "q"}, {}).eval == want


def test_capture_reads_a_rerun():
    tp = prov.capture({"message": "Re-run with corrections (x): q", "mode": "rerun", "rerun_of": TID,
                       "corrections": [{"mode": "query"}, {"mode": "tool"}]}, {})
    assert (tp.mode, tp.rerun_of, tp.corrections) == ("rerun", TID, 2)
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_provenance.py`
Expected: collection error — `ImportError: cannot import name 'provenance' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`DA/src/trace_lab/provenance.py`:
```python
"""Where a chat turn came from: decided once, at write time, beside the trace (spec D6a, R7).

A typed question, a chip, a matrix cell, a support topic, an upload, a correction, a report card
and a golden run all reach the orchestrator through the same few routes, and the trace could not
tell them apart. Trace Lab samples and measures LIVE questions, so it has to. This module decides
one channel per turn and writes it to plenum_tracelab.turn_meta inside the trace's own
transaction, under a SAVEPOINT, so a Trace Lab failure never costs the trace.

Order of evidence (decide(); the first match wins): server facts - a golden run, attached files,
a re-run, a report- session, a support session, a HITL resume - then the origin the page sent
(allow-listed), then the app's own prompt templates, then the workflow fallback, then plain chat.
Origins are analytics only: they never change routing, scope or the answer.

Turns with no turn_meta row (written before M0, by older images, or whose insert failed) are
classified at read time by infer_channel / inferred_channel_sql. Nothing is written back.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

log = structlog.get_logger(__name__)

PROVENANCE_RULES = "prov-1"
LEGACY_CLASSIFIER = "legacy-1"
CHANNELS = ("chat", "ui_action", "support", "upload", "correction", "report", "workflow", "golden")
ROUTES = ("ws", "run", "run_stateful", "files", "resume", "in_process")

#: Origins a client may send (base -> channel). A base may carry ":<variant>" ([a-z0-9_-]{1,40}).
#: apps/frontend/src/logic/traceLabOrigins.js ORIGINS equals these keys
#: (apps/frontend/test/traceLabOrigins.test.mjs pins it).
CLIENT_ORIGINS: dict[str, str] = {
    "chat.typed": "chat",
    "chat.chip": "chat",
    "chat.recent": "chat",
    "chat.edit": "chat",
    "chat.choice_card": "chat",
    "ui.matrix_cell": "ui_action",
    "ui.hoist_graph": "ui_action",
    "ui.energy_investigate": "ui_action",
    "support.typed": "support",
    "support.topic": "support",
    "upload.files": "upload",
    "upload.ingest_panel": "upload",
    "upload.vendor_certificates": "upload",
    "upload.migrate": "upload",
    "upload.documents_page": "upload",
    "upload.ingestion_check": "upload",
    "correction.reanswer": "correction",
    "report.card": "report",
    "report.cron": "report",
}
#: Origins only the server writes. A client can never claim golden or workflow.
SERVER_ORIGINS: dict[str, str] = {
    "golden.case": "golden",
    "correction.rerun": "correction",
    "workflow.resume": "workflow",
}
#: Keys allowed in origin_params, per base. Other keys are dropped.
ORIGIN_PARAM_KEYS: dict[str, frozenset[str]] = {
    "ui.matrix_cell": frozenset({"building", "type", "code", "state"}),
    "ui.energy_investigate": frozenset({"kind", "asset", "building", "type"}),
    "support.topic": frozenset({"topic"}),
    "chat.edit": frozenset({"replaces_turn"}),
    "correction.reanswer": frozenset({"corrects_turn"}),
    "upload.vendor_certificates": frozenset({"vendor", "building", "count"}),
    "upload.ingest_panel": frozenset({"building", "count", "expect"}),
    "upload.migrate": frozenset({"count", "cmms"}),
    "upload.documents_page": frozenset({"doc_type"}),
}
#: App-written prompts (origin, regex, channel): decide() rule 8, and the read-time classifier.
#: Each regex is anchored on the exact prompt in the frontend source (spec 2.1 "Templates"); re.S so
#: '.' spans lines as Postgres '~' does in inferred_channel_sql. The warn prompt has an em dash.
TEMPLATES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("correction.rerun", re.compile(r"^Re-run with corrections \(", re.S), "correction"),
    ("correction.reanswer", re.compile(r"^Re-answer the previous question with this correction: ", re.S), "correction"),
    ("upload.files", re.compile(r"^(Validate and ingest this document\.|File this document on the building\.)$", re.S), "upload"),
    ("upload.ingest_panel", re.compile(r"^Ingest (\d+ documents?|this certificate|these \d+ certificates) for ", re.S), "upload"),
    ("upload.migrate", re.compile(r"^Migrate ", re.S), "upload"),
    ("ui.matrix_cell:risk", re.compile(r"^Why is the .+ at .+ lapsed or blocked, and what should I do next\?$", re.S), "ui_action"),
    ("ui.matrix_cell:warn", re.compile(r"^The .+ at .+ is expiring soon — what is the situation, and what should I do next\?$", re.S), "ui_action"),
    ("ui.matrix_cell:gap", re.compile(r"^Why is there no .+ on record for .+, and what should I do next\?$", re.S), "ui_action"),
    ("ui.matrix_cell:ok", re.compile(r"^What is the status of the .+ at .+, and who services it\?$", re.S), "ui_action"),
    ("ui.hoist_graph", re.compile(r"^Map each regulated area of my buildings to its compliance obligations:", re.S), "ui_action"),
    ("ui.energy_investigate:anomaly", re.compile(r"^Why is .+ showing a .+, and what should I do about it\?$", re.S), "ui_action"),
    ("ui.energy_investigate:eui", re.compile(r"^Why is .+ at \d+ kWh/m² against a reference of \d+, and how much of that gap can I act on\?$", re.S), "ui_action"),
    ("ui.energy_investigate:unknown", re.compile(r"^What do we know about .+'s energy performance, and what is missing to assess it properly\?$", re.S), "ui_action"),
)
UPLOAD_PROMPTS = ("Validate and ingest this document.", "File this document on the building.")
FILES_NOTE = "uploaded files were already processed by the single-door pipeline"   # api/routes/workflow.py:811
SUPPORT_MARK = "This is a Hoistra support session"   # = agents/agent_router.py SUPPORT_SESSION_MARK (a test pins it)
MAX_CONTEXT = 16000
MAX_QUESTION = 4000
MAX_PARAMS_JSON = 2000
MAX_PARAM_KEYS = 12
MAX_PARAM_STR = 200

_VARIANT = re.compile(r"^[a-z0-9_-]{1,40}$")
_PAGE = re.compile(r"^[a-z0-9_:.-]{1,60}$")
_TURN_REF = re.compile(r"^turn-[0-9a-f]{16}$")
_ALIAS = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")


@dataclass(frozen=True)
class RequestProvenance:
    route: str                                  # one of ROUTES
    origin: str | None = None                   # already allow-listed
    origin_params: dict[str, Any] | None = None
    page: str | None = None
    context: str | None = None                  # the client's page context, whole
    files: int = 0
    ingest_source: str | None = None


@dataclass(frozen=True)
class TurnProvenance:
    request: RequestProvenance | None
    mode: str | None                            # "stream" | "rest" | "rerun" | None
    rerun_of: str | None
    corrections: int
    payload_context: str | None                 # stream: the whole extra_context; REST: its first 300 chars
    eval: dict[str, Any] | None                 # {"run_id", "case_key", "rep"}, validated
    asker_role: str | None
    asker_scope: dict[str, Any] | None
    asker_message: str | None = None            # payload.message of the row that opened the turn


@dataclass(frozen=True)
class TurnMeta:                                 # one plenum_tracelab.turn_meta row
    turn_id: str
    organization_id: str | None
    channel: str
    origin: str | None
    origin_params: dict[str, Any] | None
    page: str | None
    channel_source: str
    classifier_version: str
    context_text: str | None
    asker_question: str | None
    asker_role: str | None
    asker_scope: dict[str, Any] | None
    turn_mode: str | None
    rerun_of: str | None
    files_count: int | None
    golden_run_id: str | None
    golden_case_key: str | None
    rep: int | None


REQUEST: ContextVar[RequestProvenance | None] = ContextVar("hoist_trace_lab_request", default=None)


def _base(origin: str | None) -> str:
    return (origin or "").split(":", 1)[0]


def accept_origin(origin: Any) -> str | None:
    """The origin when it is an allow-listed client base with an optional well-formed variant."""
    if not isinstance(origin, str):
        return None
    o = origin.strip()
    if not o or len(o) > 80:
        return None
    base, sep, variant = o.partition(":")
    if base not in CLIENT_ORIGINS or (sep and not _VARIANT.match(variant)):
        return None
    return o


def accept_origin_params(base: str | None, params: Any) -> dict[str, Any] | None:
    """Only the keys ORIGIN_PARAM_KEYS allows for the base; scalar values; at most 2000 chars of JSON."""
    if params is None:
        return None
    if not isinstance(params, dict):
        log.info("trace_lab.origin_params_dropped", base=base, reason="not_an_object")
        return None
    allowed = ORIGIN_PARAM_KEYS.get(base or "", frozenset())
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in params.items():
        key = str(k)
        if key not in allowed or len(out) >= MAX_PARAM_KEYS:
            dropped.append(key)
            continue
        if v is None or isinstance(v, bool) or isinstance(v, int):
            val: Any = v
        elif isinstance(v, float) and math.isfinite(v):
            val = v
        elif isinstance(v, str) and len(v) <= MAX_PARAM_STR:
            val = v
        else:
            dropped.append(key)
            continue
        if key.endswith("_turn") and not (isinstance(val, str) and _TURN_REF.match(val)):
            dropped.append(key)
            continue
        out[key] = val
    if out and len(json.dumps(out, default=str)) > MAX_PARAMS_JSON:
        dropped.extend(out)
        out = {}
    if dropped:
        log.info("trace_lab.origin_params_dropped", base=base, keys=dropped[:12])
    return out or None


def accept_page(page: Any) -> str | None:
    if not isinstance(page, str):
        return None
    p = page.strip()
    return p if _PAGE.match(p) else None


def set_request(*, route: str, origin: Any = None, origin_params: Any = None, page: Any = None,
                context: str | None = None, files: int = 0, ingest_source: str | None = None) -> None:
    """Called once per chat request, after the caller is authorised and before the orchestrator runs.
    Validates, drops what fails, and sets REQUEST. Never raises."""
    try:
        o = accept_origin(origin)
        REQUEST.set(RequestProvenance(
            route=route if route in ROUTES else "in_process", origin=o,
            origin_params=accept_origin_params(_base(o), origin_params) if o else None,
            page=accept_page(page), context=context if isinstance(context, str) else None,
            files=max(0, int(files or 0)), ingest_source=str(ingest_source)[:40] if ingest_source else None))
    except Exception as exc:  # noqa: BLE001 - analytics never break a request
        log.warning("trace_lab.set_request_failed", error=str(exc)[:300])


def form_params(raw: str | None) -> dict[str, Any] | None:
    """origin_params from a multipart form field: a JSON object of at most 2000 chars, else None."""
    if not raw or len(raw) > MAX_PARAMS_JSON:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def match_template(question: str) -> tuple[str, str] | None:
    """(channel, origin) of the first app-written prompt the question is, or None."""
    q = question or ""
    for origin, rx, channel in TEMPLATES:
        if rx.match(q):
            return channel, origin
    return None


def _eval_dict(raw: Any) -> dict[str, Any] | None:
    """The golden-run context as {"run_id", "case_key", "rep"}; a dict or an object with those attributes."""
    if raw is None:
        return None
    get = raw.get if isinstance(raw, Mapping) else (lambda k: getattr(raw, k, None))
    try:
        run = str(uuid.UUID(str(get("run_id"))))
        key = str(get("case_key") or "")
        rep_raw = get("rep")
        rep = int(rep_raw)
    except (TypeError, ValueError):
        log.warning("trace_lab.eval_context_invalid")
        return None
    if not key or len(key) > 120 or rep < 1 or isinstance(rep_raw, bool):
        log.warning("trace_lab.eval_context_invalid")
        return None
    return {"run_id": run, "case_key": key, "rep": rep}


def _asker(p: Any, acting: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if p is None:
        return None, None
    role = p.role if p.role in ("user", "admin", "superadmin") else None
    own = str(p.organization_id) if p.organization_id else None
    restricted = p.building_ids is not None
    buildings: list[dict[str, Any]] = []
    if restricted:
        for b in list(p.buildings or ())[:50]:
            if isinstance(b, Mapping):
                buildings.append({"id": str(b.get("id") or ""), "name": str(b.get("name") or "")[:200],
                                  "building_code": str(b["building_code"])[:60] if b.get("building_code") else None})
    return role, {"all_buildings": not restricted, "building_count": len(p.building_ids) if restricted else None,
                  "buildings": buildings, "own_organization_id": own, "acting_organization_id": acting or own,
                  "view_as": bool(acting) and acting != own}


def capture(payload: dict[str, Any], kw: dict[str, Any]) -> TurnProvenance:
    """What is known when a turn opens: REQUEST, the golden-run context, the asker, the payload facts."""
    from ..agents.replay_guard import eval_context
    from ..http_client import caller_organization_id
    from ..services.principal import caller_principal

    pl = payload if isinstance(payload, dict) else {}
    mode = pl.get("mode") if isinstance(pl.get("mode"), str) else None
    corrections = pl.get("corrections")
    ctx = pl.get("extra_context") or pl.get("context")
    msg = pl.get("message")
    role, scope = _asker(caller_principal.get(), caller_organization_id.get())
    return TurnProvenance(
        request=REQUEST.get(), mode=mode,
        rerun_of=str(pl["rerun_of"])[:64] if pl.get("rerun_of") else None,
        corrections=len(corrections) if isinstance(corrections, list) else 0,
        payload_context=str(ctx)[:MAX_CONTEXT] if ctx else None,
        eval=_eval_dict(eval_context()), asker_role=role, asker_scope=scope,
        asker_message=str(msg)[:MAX_QUESTION] if msg else None)


def decide(p: TurnProvenance | None, *, turn_id: str, question: str, answer: str, route: str | None,
           session_id: str | None, organization_id: str | None = None) -> TurnMeta:
    """The channel precedence of spec 2.1 (R7). `route` is the trace's own t.route (on_plan), not the
    HTTP route; `question` is t.question after the asker's-words fix."""
    from ..agents.agent_router import is_support_session

    req = (p.request if p else None) or RequestProvenance(route="in_process")
    client = req.origin
    if client and CLIENT_ORIGINS.get(_base(client)) == "report" and not (session_id or "").startswith("report-"):
        client = None                                   # only a report session may say it is a report
    ctx = req.context or (p.payload_context if p else None) or ""
    ev = (p.eval if p else None) or {}
    q = question or ""
    asked = p.asker_message if p else None
    turn_mode = "resume" if req.route == "resume" else (p.mode if p and p.mode in ("stream", "rest", "rerun") else None)

    def row(channel: str, source: str, origin: str | None, params: dict[str, Any] | None = None) -> TurnMeta:
        params = req.origin_params if params is None else params
        return TurnMeta(
            turn_id=turn_id, organization_id=organization_id, channel=channel, origin=origin,
            origin_params=params or None, page=req.page, channel_source=source,
            classifier_version=PROVENANCE_RULES, context_text=ctx[:MAX_CONTEXT] or None,
            asker_question=asked[:MAX_QUESTION] if asked and asked != q else None,
            asker_role=p.asker_role if p else None, asker_scope=p.asker_scope if p else None,
            turn_mode=turn_mode, rerun_of=p.rerun_of if p and p.mode == "rerun" else None,
            files_count=min(req.files, 32767) if req.route == "files" else None,
            golden_run_id=ev.get("run_id"), golden_case_key=ev.get("case_key"), rep=ev.get("rep"))

    if ev:
        return row("golden", "recorded", "golden.case", {})
    if req.files > 0:
        return row("upload", "recorded", client or "upload.files")
    if p and p.mode == "rerun":
        return row("correction", "recorded", "correction.rerun", {"rerun_of": p.rerun_of, "corrections": p.corrections})
    if (session_id or "").startswith("report-"):
        return row("report", "recorded", client)
    if is_support_session(ctx):
        return row("support", "recorded", client)
    if req.route == "resume":
        return row("workflow", "recorded", "workflow.resume")
    if client:
        return row(CLIENT_ORIGINS[_base(client)], "client", client)
    hit = match_template(q)
    if hit:
        return row(hit[0], "inferred", hit[1])
    if answer == "gate_interrupt" or not q.strip() or route == "single:clarify":
        return row("workflow", "inferred", None)
    return row("chat", "inferred", None)
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_provenance.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/provenance.py`, `DA/tests/test_trace_lab_provenance.py`.

---

### Task 17: `provenance.py` — the `turn_meta` write and the read-time classifier

**Files:**
- Modify: `DA/src/trace_lab/provenance.py` (append)
- Test: `DA/tests/test_trace_lab_provenance.py` (append), `DA/tests/test_trace_lab_provenance_sql.py` (create)

**Interfaces:**
- Consumes: Task 16 names; `db.ready()` (Task 5); `fresh_schema` (Task 9).
- Produces: `row_params(m) -> dict`, `write_turn_meta(conn, t, answer)` (no-op unless `db.ready()`; SAVEPOINT; never raises; `INSERT … ON CONFLICT (turn_id) DO NOTHING`), `infer_channel(turn, *, activity_input=None) -> {"channel", "origin", "channel_source": "inferred", "classifier_version": "legacy-1", "reason"}`, `inferred_channel_sql(alias="tt")`, `INFERRED_CHANNEL_SQL` (M1 and M6 import these).

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_provenance.py`:
```python


# ── the write, and the read-time classifier (spec 2.1, 2.8) ──────────────────────────

class _Nested:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.log.append("SAVEPOINT")
        return self

    async def __aexit__(self, et, e, tb):
        self.conn.log.append("RELEASE" if et is None else "ROLLBACK TO SAVEPOINT")
        return False


class _Conn:
    def __init__(self, fail=False):
        self.log, self.fail, self.params = [], fail, None

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, stmt, params=None):
        self.log.append(" ".join(str(stmt).split()))
        self.params = params
        if self.fail:
            raise RuntimeError("violates check constraint turn_meta_channel_check")


def _turn(**kw):
    base = dict(turn_id=TID, question="How many boilers?", route=None, session_id="s1", organization_id=str(ORG),
                prov=_p())
    base.update(kw)
    return SimpleNamespace(**base)


async def test_write_turn_meta_is_a_no_op_until_trace_lab_is_ready(monkeypatch):
    from src.trace_lab import db

    monkeypatch.setattr(db, "STATE", None)
    conn = _Conn()
    await prov.write_turn_meta(conn, _turn(), "Three.")
    assert conn.log == []


async def test_write_turn_meta_inserts_inside_a_savepoint(monkeypatch):
    from src.trace_lab import db

    monkeypatch.setattr(db, "STATE", migrate.SchemaState("ready", 1, 1))
    conn = _Conn()
    await prov.write_turn_meta(conn, _turn(), "Three.")
    assert conn.log[0] == "SAVEPOINT" and conn.log[2] == "RELEASE"
    assert conn.log[1].startswith("INSERT INTO plenum_tracelab.turn_meta") and conn.log[1].endswith("ON CONFLICT (turn_id) DO NOTHING")
    assert conn.params["channel"] == "chat" and conn.params["source"] == "inferred" and conn.params["org"] == str(ORG)


async def test_write_turn_meta_swallows_its_own_failure(monkeypatch):
    from src.trace_lab import db

    monkeypatch.setattr(db, "STATE", migrate.SchemaState("ready", 1, 1))
    conn = _Conn(fail=True)
    await prov.write_turn_meta(conn, _turn(), "Three.")        # never raises
    assert conn.log[-1] == "ROLLBACK TO SAVEPOINT"


def test_row_params_serialise_the_json_columns():
    m = prov.decide(_p(request=_req(origin="ui.matrix_cell:risk", origin_params={"building": "B"}),
                       asker_scope={"view_as": False}), turn_id=TID, question="q", answer="a", route=None, session_id="s1")
    params = prov.row_params(m)
    assert json.loads(params["origin_params"]) == {"building": "B"} and json.loads(params["scope"]) == {"view_as": False}
    bare = prov.row_params(prov.decide(None, turn_id=TID, question="q", answer="a", route=None, session_id=None))
    assert bare["origin_params"] is None and bare["scope"] is None and bare["run"] is None


INFER = [
    ({"session_id": "golden-r1-C1-1", "question": "q"}, "golden", None),
    ({"session_id": "report-abc", "question": "q"}, "report", None),
    ({"session_id": "s", "question": "Re-run with corrections (query): q"}, "correction", "correction.rerun"),
    ({"session_id": "s", "question": "Re-answer the previous question with this correction: x"}, "correction", "correction.reanswer"),
    ({"session_id": "rerun", "question": "q"}, "correction", "correction.rerun"),
    ({"session_id": "docs-abc", "question": "File this document on the building."}, "upload", None),
    ({"session_id": "s", "question": "Validate and ingest this document."}, "upload", "upload.files"),
    ({"session_id": "s", "question": "Ingest 3 documents for Kingsway House."}, "upload", "upload.ingest_panel"),
    ({"session_id": "s", "question": "Migrate assets.xlsx"}, "upload", "upload.migrate"),
    ({"session_id": "s", "question": "x uploaded files were already processed by the single-door pipeline y"}, "upload", None),
    ({"session_id": "s", "question": SUPPORT_SESSION_MARK + ".\n\n---\n\n**Current user message:**\nhow?"}, "support", None),
    ({"session_id": "s", "question": "Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?"},
     "ui_action", "ui.matrix_cell:risk"),
    ({"session_id": "s", "question": "What do we know about Kingsway House's energy performance, and what is missing to assess it properly?"},
     "ui_action", "ui.energy_investigate:unknown"),
    ({"session_id": "s", "question": "", "answer": "a"}, "workflow", None),
    ({"session_id": "s", "question": "q", "answer": "gate_interrupt"}, "workflow", None),
    ({"session_id": "s", "question": "q", "route": "single:clarify"}, "workflow", None),
    ({"session_id": "s", "question": "How many boilers?"}, "chat", None),
    ({"session_id": None, "question": "How many boilers?"}, "chat", None),
]


@pytest.mark.parametrize("turn, channel, origin", INFER)
def test_infer_channel(turn, channel, origin):
    out = prov.infer_channel(turn)
    assert (out["channel"], out["origin"]) == (channel, origin)
    assert out["channel_source"] == "inferred" and out["classifier_version"] == "legacy-1" and out["reason"]


def test_the_earliest_activity_row_refines_a_legacy_turn():
    t = {"session_id": "s", "question": "how do I ingest?"}
    assert prov.infer_channel(t)["channel"] == "chat"
    assert prov.infer_channel(t, activity_input={"payload": {"extra_context": SUPPORT_SESSION_MARK + " ..."}})["channel"] == "support"
    assert prov.infer_channel(t, activity_input={"payload": {"mode": "rerun"}})["channel"] == "correction"


def test_inferred_channel_sql_uses_the_alias_and_the_support_mark():
    sql = prov.inferred_channel_sql("t")
    assert sql.strip().startswith("CASE") and "t.session_id" in sql and "tt." not in sql
    assert SUPPORT_SESSION_MARK in sql and prov.INFERRED_CHANNEL_SQL == prov.inferred_channel_sql("tt")
    for channel in prov.CHANNELS:
        assert f"'{channel}'" in sql
    with pytest.raises(ValueError):
        prov.inferred_channel_sql("t; drop")
```

`DA/tests/test_trace_lab_provenance_sql.py`:
```python
"""turn_meta rows and the read-time classifier on a throwaway Postgres (spec 2.1, 2.8)."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

from src.trace_lab import provenance as prov
from test_trace_lab_provenance import INFER, ORG, RUN, TID
from trace_lab_scratch import fresh_schema, requires_scratch

pytestmark = requires_scratch


def _t(p, **kw):
    base = dict(turn_id=TID, question="How many boilers?", route=None, session_id="s1", organization_id=str(ORG), prov=p)
    base.update(kw)
    return SimpleNamespace(**base)


def _tp(**kw):
    base = dict(request=prov.RequestProvenance(route="ws", origin="chat.chip:home", page="home", context="ctx"),
                mode="stream", rerun_of=None, corrections=0, payload_context=None, eval=None, asker_role="admin",
                asker_scope={"all_buildings": True})
    base.update(kw)
    return prov.TurnProvenance(**base)


async def test_write_turn_meta_writes_one_row_and_never_rewrites_it(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await prov.write_turn_meta(conn, _t(_tp()), "Three.")
    async with engine.begin() as conn:      # a second flush of the same turn: the first provenance stays
        await prov.write_turn_meta(conn, _t(None, question="something else"), "Three.")
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT channel, origin, channel_source, page, context_text, asker_role, asker_scope, turn_mode,"
            " organization_id::text AS org FROM plenum_tracelab.turn_meta"))).mappings().all()
    assert [dict(r) for r in rows] == [{"channel": "chat", "origin": "chat.chip:home", "channel_source": "client",
                                        "page": "home", "context_text": "ctx", "asker_role": "admin",
                                        "asker_scope": {"all_buildings": True}, "turn_mode": "stream", "org": str(ORG)}]
    await engine.dispose()


async def test_a_golden_turn_row_satisfies_the_golden_constraints(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await prov.write_turn_meta(conn, _t(_tp(eval={"run_id": RUN, "case_key": "C1", "rep": 3}), session_id="golden-x"), "a")
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT channel, origin, golden_run_id::text AS run, golden_case_key, rep FROM plenum_tracelab.turn_meta"))).mappings().one()
    assert dict(row) == {"channel": "golden", "origin": "golden.case", "run": RUN, "golden_case_key": "C1", "rep": 3}
    await engine.dispose()


async def test_inferred_channel_sql_matches_python(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        for i, (turn, _, _) in enumerate(INFER):
            await conn.execute(text(
                "INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, question, answer, route, started_at)"
                " VALUES (:t, :s, :q, :a, :r, now())"),
                {"t": f"turn-{i:016x}", "s": turn.get("session_id"), "q": turn.get("question"),
                 "a": turn.get("answer"), "r": turn.get("route")})
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT turn_id, session_id, question, answer, route, {prov.INFERRED_CHANNEL_SQL} AS channel"
            " FROM plenum_cafm.agent_trace_turns tt ORDER BY turn_id"))).mappings().all()
    assert len(rows) == len(INFER)
    for r in rows:
        assert r["channel"] == prov.infer_channel(dict(r))["channel"], dict(r)
    await engine.dispose()
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_provenance.py`
Expected: FAIL — `AttributeError: module 'src.trace_lab.provenance' has no attribute 'write_turn_meta'` (and `row_params`, `infer_channel`, `inferred_channel_sql`).

- [ ] **Step 3: Implement**

Append to `DA/src/trace_lab/provenance.py`:
```python


# ── the write (spec 2.8) ────────────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO plenum_tracelab.turn_meta
    (turn_id, organization_id, channel, origin, origin_params, page, channel_source, classifier_version,
     context_text, asker_question, asker_role, asker_scope, turn_mode, rerun_of, files_count,
     golden_run_id, golden_case_key, rep)
VALUES (:turn_id, CAST(:org AS uuid), :channel, :origin, CAST(:origin_params AS jsonb), :page, :source, :version,
        :context, :asker_question, :role, CAST(:scope AS jsonb), :turn_mode, :rerun_of, :files_count,
        CAST(:run AS uuid), :case_key, :rep)
ON CONFLICT (turn_id) DO NOTHING"""


def row_params(m: TurnMeta) -> dict[str, Any]:
    return {"turn_id": m.turn_id, "org": m.organization_id, "channel": m.channel, "origin": m.origin,
            "origin_params": json.dumps(m.origin_params, default=str) if m.origin_params is not None else None,
            "page": m.page, "source": m.channel_source, "version": m.classifier_version,
            "context": m.context_text, "asker_question": m.asker_question, "role": m.asker_role,
            "scope": json.dumps(m.asker_scope, default=str) if m.asker_scope is not None else None,
            "turn_mode": m.turn_mode, "rerun_of": m.rerun_of, "files_count": m.files_count,
            "run": m.golden_run_id, "case_key": m.golden_case_key, "rep": m.rep}


async def write_turn_meta(conn: "AsyncConnection", t: Any, answer: str) -> None:
    """Inside trace._write's transaction (R5): decide the row and insert it under a SAVEPOINT. A
    failed insert rolls back only the savepoint and logs trace_lab.turn_meta_failed; the turn and
    its spans still commit. A no-op while Trace Lab is not verified. Never raises."""
    from . import db

    if not db.ready():
        return
    try:
        m = decide(getattr(t, "prov", None), turn_id=t.turn_id, question=getattr(t, "question", "") or "",
                   answer=answer or "", route=getattr(t, "route", None), session_id=getattr(t, "session_id", None),
                   organization_id=getattr(t, "organization_id", None))
        async with conn.begin_nested():
            await conn.execute(text(_INSERT_SQL), row_params(m))
    except Exception as exc:  # noqa: BLE001 - never the trace's problem
        log.warning("trace_lab.turn_meta_failed", turn_id=getattr(t, "turn_id", None), error=str(exc)[:300])


# ── read time: turns with no turn_meta row (spec 2.1 "Inferred classifier") ──────────────

_INFERRED_SQL_TEMPLATE = """CASE
  WHEN coalesce({a}.session_id,'') LIKE 'golden-%' THEN 'golden'
  WHEN coalesce({a}.session_id,'') LIKE 'report-%' THEN 'report'
  WHEN coalesce({a}.question,'') LIKE 'Re-run with corrections (%'
    OR coalesce({a}.question,'') LIKE 'Re-answer the previous question with this correction: %'
    OR coalesce({a}.session_id,'') LIKE 'rerun%' THEN 'correction'
  WHEN coalesce({a}.session_id,'') LIKE 'docs-%' OR coalesce({a}.session_id,'') LIKE 'ing-%'
    OR coalesce({a}.question,'') IN ('Validate and ingest this document.', 'File this document on the building.')
    OR coalesce({a}.question,'') LIKE '%uploaded files were already processed by the single-door pipeline%'
    OR coalesce({a}.question,'') ~ '^Ingest ([0-9]+ documents?|this certificate|these [0-9]+ certificates) for '
    OR coalesce({a}.question,'') LIKE 'Migrate %' THEN 'upload'
  WHEN ltrim(coalesce({a}.question,'')) LIKE 'This is a Hoistra support session%' THEN 'support'
  WHEN coalesce({a}.question,'') ~ '^(Why is the .+ at .+ lapsed or blocked, and what should I do next\\?|The .+ at .+ is expiring soon — what is the situation, and what should I do next\\?|Why is there no .+ on record for .+, and what should I do next\\?|What is the status of the .+ at .+, and who services it\\?)$'
    OR coalesce({a}.question,'') LIKE 'Map each regulated area of my buildings to its compliance obligations:%'
    OR coalesce({a}.question,'') ~ '^(Why is .+ showing a .+, and what should I do about it\\?|Why is .+ at [0-9]+ kWh/m² against a reference of [0-9]+, and how much of that gap can I act on\\?|What do we know about .+''s energy performance, and what is missing to assess it properly\\?)$'
    THEN 'ui_action'
  WHEN btrim(coalesce({a}.question,'')) = '' OR coalesce({a}.answer,'') = 'gate_interrupt'
    OR coalesce({a}.route,'') = 'single:clarify' THEN 'workflow'
  ELSE 'chat'
END"""


def inferred_channel_sql(alias: str = "tt") -> str:
    """The legacy classifier as a SQL CASE over agent_trace_turns columns of `alias`."""
    if not _ALIAS.match(alias or ""):
        raise ValueError(f"bad alias {alias!r}")
    return _INFERRED_SQL_TEMPLATE.replace("{a}", alias)


INFERRED_CHANNEL_SQL = inferred_channel_sql("tt")


def infer_channel(turn: Mapping[str, Any], *, activity_input: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The same rules as INFERRED_CHANNEL_SQL, in Python; detail views may pass the turn's earliest
    orchestrator/turn/input activity row, which refines a legacy row (a re-run, a WS support turn)."""
    sid = str(turn.get("session_id") or "")
    q = str(turn.get("question") or "")
    a = str(turn.get("answer") or "")
    route = str(turn.get("route") or "")

    def out(channel: str, reason: str, origin: str | None = None) -> dict[str, Any]:
        return {"channel": channel, "origin": origin, "channel_source": "inferred",
                "classifier_version": LEGACY_CLASSIFIER, "reason": reason}

    if sid.startswith("golden-"):
        return out("golden", "golden_session")
    if sid.startswith("report-"):
        return out("report", "report_session")
    if q.startswith("Re-run with corrections ("):
        return out("correction", "correction_prefix", "correction.rerun")
    if q.startswith("Re-answer the previous question with this correction: "):
        return out("correction", "correction_prefix", "correction.reanswer")
    if sid.startswith("rerun"):
        return out("correction", "rerun_session", "correction.rerun")
    if sid.startswith("docs-") or sid.startswith("ing-"):
        return out("upload", "upload_session")
    if q in UPLOAD_PROMPTS:
        return out("upload", "upload_prompt", "upload.files")
    if FILES_NOTE in q:
        return out("upload", "files_note")
    hit = match_template(q)
    if hit and hit[0] == "upload":
        return out("upload", "upload_template", hit[1])
    payload = (activity_input or {}).get("payload")
    pl = payload if isinstance(payload, Mapping) else {}
    if pl.get("mode") == "rerun":
        return out("correction", "activity_rerun", "correction.rerun")
    if q.lstrip(" ").startswith(SUPPORT_MARK):
        return out("support", "support_prefix")
    actx = pl.get("extra_context") or pl.get("context")
    if isinstance(actx, str) and actx.lstrip().startswith(SUPPORT_MARK):
        return out("support", "activity_support")
    if hit and hit[0] == "ui_action":
        return out("ui_action", "ui_template", hit[1])
    if q.strip(" ") == "" or a == "gate_interrupt" or route == "single:clarify":
        return out("workflow", "workflow")
    return out("chat", "default")
```

Note for the implementer: `_INFERRED_SQL_TEMPLATE` is a normal (not raw) Python string, so `\\?` is the two characters `\?` that Postgres' regex needs; `''s` is SQL's escaped apostrophe.

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_provenance.py`
Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_provenance_sql.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/provenance.py`, `DA/tests/test_trace_lab_provenance.py`, `DA/tests/test_trace_lab_provenance_sql.py`.

---

### Task 18: Trace hooks — capture at turn open, the asker's own words, `turn_meta` in `_write`

**Files:**
- Modify: `DA/src/agents/trace.py:232-234` (`Turn.__init__`), `:296-300` (helpers after `begin`), `:322-330` (turn-open branch of `on_activity`), `:771-772` (`_write`)
- Test: `DA/tests/test_trace_lab_capture.py`, `DA/tests/test_trace_lab_capture_sql.py`

**Interfaces:**
- Consumes: `provenance.capture`, `provenance.write_turn_meta`, `provenance.REQUEST`, `provenance.decide` (Tasks 16–17); `db.STATE`, `db.refresh_state` (Task 5); `fresh_schema` (Task 9).
- Produces: `trace.Turn.prov` (TurnProvenance or None); `trace._capture(payload, kw)`, `trace._write_turn_meta(conn, t, answer)` (private); `t.question` is the asker's own words on every path (spec 2.2).

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_capture.py`:
```python
"""Trace capture hooks: provenance at turn open, the asker's own words, turn_meta in the turn's own
transaction (spec "Trace capture" 2.1, 2.2; R5)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src import database
from src.agents import llm_cost, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal
from src.trace_lab import db, migrate
from src.trace_lab import provenance as prov

WRAPPED = "page context\n\n---\n\n**Current user message:**\nHow many boilers?"


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    writes = []

    async def fake_write(t, answer, tool_calls, summary):
        writes.append((t, answer))

    monkeypatch.setattr(trace, "_write", fake_write)
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tokens = (caller_principal.set(p), caller_organization_id.set(str(p.organization_id)), prov.REQUEST.set(None),
              trace._turn.set(None), llm_cost._ledger.set(None))
    yield writes
    caller_principal.reset(tokens[0])
    caller_organization_id.reset(tokens[1])
    prov.REQUEST.reset(tokens[2])
    trace._turn.reset(tokens[3])
    llm_cost._ledger.reset(tokens[4])


def _input(payload, turn_id="turn-aaaa000000000001"):
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": turn_id,
                       "session_id": "s1", "summary": "x", "payload": payload})


def test_rest_turn_keeps_the_askers_words(ready):
    async def run():
        _input({"message": "How many boilers?", "mode": "rest", "context": "page"})
        _input({"input": {"message_count": 3, "latest_user_message": WRAPPED}})   # _invoke's later row
        return trace.current()

    t = asyncio.run(run())
    assert t.question == "How many boilers?"
    assert t.root.input == {"message": "How many boilers?", "mode": "rest"}


def test_a_stateless_run_still_records_its_only_row(ready):
    async def run():
        _input({"input": {"message_count": 1, "latest_user_message": "How many chillers?"}})
        return trace.current()

    assert asyncio.run(run()).question == "How many chillers?"


def test_opening_a_turn_captures_its_provenance(ready):
    prov.set_request(route="run_stateful", origin="ui.matrix_cell:risk",
                     origin_params={"building": "B", "code": "GAS"}, page="cc")

    async def run():
        _input({"message": "Why is the gas certificate lapsed?", "mode": "stream", "extra_context": "ctx"})
        return trace.current()

    t = asyncio.run(run())
    assert t.prov.request.origin == "ui.matrix_cell:risk"
    assert t.prov.request.origin_params == {"building": "B", "code": "GAS"}
    assert t.prov.mode == "stream" and t.prov.asker_role == "admin" and t.prov.payload_context == "ctx"


def test_a_failing_capture_leaves_the_turn_recording(ready, monkeypatch):
    def boom(payload, kw):
        raise RuntimeError("capture broke")

    monkeypatch.setattr(prov, "capture", boom)

    async def run():
        _input({"message": "q", "mode": "stream"})
        return trace.current()

    t = asyncio.run(run())
    assert t.prov is None and t.question == "q"


class _Nested:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.log.append("SAVEPOINT")
        return self

    async def __aexit__(self, et, e, tb):
        self.conn.log.append("RELEASE" if et is None else "ROLLBACK TO SAVEPOINT")
        return False


class _Conn:
    def __init__(self):
        self.log: list[str] = []
        self.fail_on: str | None = None

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.log.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("violates check constraint")


class _Begin:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    def begin(self):
        return _Begin(self.conn)


def _turn_for_write(p):
    t = trace.Turn("turn-aaaa000000000002", "s1")
    t.question = "How many boilers?"
    t.prov = p
    t.root.close(output={"answer": "Three."})
    return t


@pytest.fixture
def engine(monkeypatch):
    eng = _Engine()
    monkeypatch.setattr(database, "_get_engine", lambda: eng)
    monkeypatch.setattr(trace, "_ready", True)
    monkeypatch.setattr(db, "STATE", migrate.SchemaState("ready", 1, 1))
    return eng


async def test_write_puts_turn_meta_after_the_spans_in_the_same_transaction(engine):
    await trace._write(_turn_for_write(prov.capture({"message": "How many boilers?", "mode": "stream"}, {})),
                       "Three.", [], None)
    log = engine.conn.log
    assert log[0].startswith("INSERT INTO plenum_cafm.agent_trace_turns")
    assert log[1].startswith("INSERT INTO plenum_cafm.agent_trace_spans")
    assert log[2] == "SAVEPOINT" and log[3].startswith("INSERT INTO plenum_tracelab.turn_meta") and log[4] == "RELEASE"


async def test_no_turn_meta_without_provenance_and_a_failure_rolls_back_only_its_savepoint(engine):
    await trace._write(_turn_for_write(None), "Three.", [], None)
    assert not any("turn_meta" in s for s in engine.conn.log)
    engine.conn.fail_on = "plenum_tracelab.turn_meta"
    await trace._write(_turn_for_write(prov.capture({"message": "q"}, {})), "Three.", [], None)
    assert engine.conn.log[-1] == "ROLLBACK TO SAVEPOINT"
```

`DA/tests/test_trace_lab_capture_sql.py`:
```python
"""The trace write and turn_meta together on a throwaway Postgres (R5). A Trace Lab failure never
costs the trace; while Trace Lab is down the trace is still written."""
from __future__ import annotations

import dataclasses

from sqlalchemy import text

from src.agents import trace
from src.trace_lab import db
from src.trace_lab import provenance as prov
from trace_lab_scratch import fresh_schema, requires_scratch

pytestmark = requires_scratch


def _p():
    return prov.TurnProvenance(request=prov.RequestProvenance(route="ws", origin="chat.typed", page="home"),
                               mode="stream", rerun_of=None, corrections=0, payload_context=None, eval=None,
                               asker_role="admin", asker_scope=None)


def _turn(turn_id):
    t = trace.Turn(turn_id, "s1")
    t.question = "How many boilers?"
    t.prov = _p()
    t.root.close(output={"answer": "Three."})
    return t


async def _counts(engine, turn_id):
    async with engine.connect() as conn:
        out = []
        for table in ("plenum_cafm.agent_trace_turns", "plenum_cafm.agent_trace_spans", "plenum_tracelab.turn_meta"):
            out.append(int((await conn.execute(text(f"SELECT count(*) FROM {table} WHERE turn_id = :t"),
                                               {"t": turn_id})).scalar()))
    return tuple(out)


async def test_a_traced_turn_gets_its_turn_meta_row(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    await trace._write(_turn("turn-aaaa000000000001"), "Three.", [], None)
    assert await _counts(engine, "turn-aaaa000000000001") == (1, 1, 1)
    await engine.dispose()


async def test_turn_meta_failure_never_loses_the_trace(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    real = prov.decide

    def bad(*a, **k):
        return dataclasses.replace(real(*a, **k), channel="bogus")   # violates turn_meta's channel CHECK

    monkeypatch.setattr(prov, "decide", bad)
    await trace._write(_turn("turn-bbbb000000000001"), "Three.", [], None)
    assert await _counts(engine, "turn-bbbb000000000001") == (1, 1, 0)
    await engine.dispose()


async def test_while_trace_lab_is_down_the_trace_is_still_written(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE plenum_tracelab.jobs ADD COLUMN foo text")
    monkeypatch.setattr(db, "STATE", None)
    assert (await db.refresh_state()).status == "drift"
    await trace._write(_turn("turn-cccc000000000001"), "Three.", [], None)
    assert await _counts(engine, "turn-cccc000000000001") == (1, 1, 0)
    await engine.dispose()
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_capture.py`
Expected: FAIL — `test_rest_turn_keeps_the_askers_words` gets the wrapped prompt; `AttributeError: 'Turn' object has no attribute 'prov'`; no `turn_meta` statement in the write log.

- [ ] **Step 3: Implement**

Edits to `DA/src/agents/trace.py`:

(a) `Turn.__init__` — old:
```python
        self.card_outputs: list[dict[str, Any]] = []
        from ..http_client import caller_organization_id
```
new:
```python
        self.card_outputs: list[dict[str, Any]] = []
        # Trace Lab provenance captured when the turn opened (trace_lab/provenance.py capture);
        # written beside the turn as plenum_tracelab.turn_meta. None when capture failed.
        self.prov: Any = None
        from ..http_client import caller_organization_id
```

(b) Helpers after `begin` — old:
```python
    t = Turn(turn_id or ("turn-" + uuid.uuid4().hex[:16]), session_id)
    _turn.set(t)
    return t
```
new:
```python
    t = Turn(turn_id or ("turn-" + uuid.uuid4().hex[:16]), session_id)
    _turn.set(t)
    return t


def _capture(payload: dict[str, Any], kw: dict[str, Any]) -> Any:
    """Trace Lab provenance for a turn being opened (trace_lab/provenance.py); None on any error."""
    try:
        from ..trace_lab.provenance import capture

        return capture(payload, kw)
    except Exception as exc:  # noqa: BLE001 - never the turn's problem
        log.warning("trace.capture_failed", error=str(exc)[:300])
        return None


async def _write_turn_meta(conn: Any, t: Turn, answer: str) -> None:
    """The turn's Trace Lab provenance row, in the turn's own transaction under a SAVEPOINT
    (trace_lab/provenance.py write_turn_meta): its failure rolls back only itself, never the trace."""
    try:
        from ..trace_lab.provenance import write_turn_meta

        await write_turn_meta(conn, t, answer)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.turn_meta_failed", turn_id=t.turn_id, error=str(exc)[:300])
```

(c) Turn-open branch of `on_activity` — old:
```python
            if direction == "input":
                if t is None or t.flushed:
                    t = begin(kw.get("turn_id"), kw.get("session_id"))
                if t is None:
                    return
                inp = payload.get("input") if isinstance(payload.get("input"), dict) else {}
                t.question = str(payload.get("message") or inp.get("latest_user_message") or kw.get("summary") or "")[:4000]
                t.root.input = _bound({"message": t.question, "mode": payload.get("mode")})
                return
```
new:
```python
            if direction == "input":
                if t is None or t.flushed:
                    t = begin(kw.get("turn_id"), kw.get("session_id"))
                    if t is not None:
                        t.prov = _capture(payload, kw)
                if t is None:
                    return
                inp = payload.get("input") if isinstance(payload.get("input"), dict) else {}
                # The asker's own words win. A later input row on the same turn (_invoke on the REST
                # path, orchestrator.py:7363-7370) carries the prompt built around them and must not
                # replace them; the wrapped prompt stays in the activity log and the prompt span.
                if payload.get("message") or not t.question:
                    t.question = str(payload.get("message") or inp.get("latest_user_message") or kw.get("summary") or "")[:4000]
                    t.root.input = _bound({"message": t.question, "mode": payload.get("mode")})
                return
```

(d) `_write` — old:
```python
                     "tin": s.input_tokens, "tout": s.output_tokens, "tcache": s.cache_read, "usd": s.cost_usd, "ok": s.ok, "err": s.error})
        log.info("trace.written", turn_id=t.turn_id, spans=len(t.spans), cost_usd=tot["cost_usd"])
```
new:
```python
                     "tin": s.input_tokens, "tout": s.output_tokens, "tcache": s.cache_read, "usd": s.cost_usd, "ok": s.ok, "err": s.error})
            if t.prov is not None:
                await _write_turn_meta(conn, t, answer)
        log.info("trace.written", turn_id=t.turn_id, spans=len(t.spans), cost_usd=tot["cost_usd"])
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_capture.py tests/test_trace.py tests/test_rest_turns_are_traced.py tests/test_rerun.py tests/test_rerun_of_a_loop_answer.py`
Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/test_trace_lab_capture_sql.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/trace.py`, `DA/tests/test_trace_lab_capture.py`, `DA/tests/test_trace_lab_capture_sql.py`.

---

### Task 19: Every chat route hands the page's origin to the turn (`workflow.py`)

**Files:**
- Modify: `DA/src/api/routes/workflow.py:52` (import), `:71` (`WorkflowRequest`), `:360-362` (`/run`), `:405-406` (`/run-stateful`), `:442-443` (files signature), `:483` (files route), `:913` (`/resume`), `:1065-1067` (WS)
- Test: `DA/tests/test_trace_lab_workflow_provenance.py`

**Interfaces:**
- Consumes: `provenance.set_request`, `provenance.form_params`, `provenance.REQUEST` (Task 16).
- Produces: `WorkflowRequest.origin`, `.origin_params`, `.page` (typed `Any`, so an odd value is dropped by provenance instead of failing the request with 422); files-route form fields `origin`, `origin_params` (JSON text), `page`; WS message keys `origin`, `origin_params`, `page`; `provenance.REQUEST` set on `/run`, `/run-stateful`, files, `/resume` and WS before the orchestrator runs.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_workflow_provenance.py`:
```python
"""Every chat route hands the page's origin to the turn (spec "Trace capture" 2.1 "Transport").
Origins are analytics only: an old client (no fields) and an odd one (anything) work as before."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

workflow = pytest.importorskip("src.api.routes.workflow", reason="the workflow router needs slowapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.deps import get_orchestrator  # noqa: E402
from src.limiter import limiter  # noqa: E402
from src.services import principal as P  # noqa: E402
from src.trace_lab import provenance as prov  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzYW0ifQ.c2lnbmF0dXJlLWdvZXMtaGVyZQ"
MARKER = "hoistra.auth.bearer"


def _principal():
    return P.Principal(user_id=uuid.uuid4(), email="a@b.c", organization_id=uuid.uuid4(), role="admin",
                       can_ingest=True, building_ids=None)


class _Orch:
    def __init__(self):
        self.seen = None

    async def run_stateful(self, user_message, session_id, extra_context=None, **kw):
        self.seen = prov.REQUEST.get()
        return {"session_id": session_id, "answer": "ok", "tool_calls": [], "success": True, "error": None}

    async def stream(self, user_message, session_id, extra_context=None):
        self.seen = prov.REQUEST.get()
        yield {"type": "workflow_completed", "answer": "ok", "session_id": session_id}


@pytest.fixture(autouse=True)
def _no_receipts(monkeypatch):
    async def record(**kw):
        return 1.0

    monkeypatch.setattr(workflow.usage_events, "record_usage", record)


def test_old_clients_and_odd_values_still_parse():
    assert workflow.WorkflowRequest(message="q").origin is None
    body = workflow.WorkflowRequest(message="q", origin="x" * 500, origin_params=["not", "a", "dict"], page=7)
    assert body.origin == "x" * 500        # accepted by the model, dropped by provenance: never a 422


def test_run_stateful_hands_the_origin_to_the_turn():
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(workflow.router)
    orch = _Orch()
    app.dependency_overrides[P.current_principal] = _principal
    app.dependency_overrides[get_orchestrator] = lambda: orch
    r = TestClient(app).post("/api/workflow/run-stateful", json={
        "message": "How many boilers?", "session_id": "s1", "context": "ctx",
        "origin": "chat.chip:home", "origin_params": {"x": 1}, "page": "home"})
    assert r.status_code == 200
    seen = orch.seen
    assert (seen.route, seen.origin, seen.page, seen.context, seen.origin_params) == \
        ("run_stateful", "chat.chip:home", "home", "ctx", None)


def test_the_socket_hands_the_origin_to_the_turn(monkeypatch):
    async def resolver(authorization):
        return _principal()

    monkeypatch.setattr(workflow, "resolve_principal", resolver)
    app = FastAPI()
    app.include_router(workflow.router)
    orch = _Orch()
    app.state.orchestrator = orch
    with TestClient(app).websocket_connect("/api/workflow/ws/s-1", subprotocols=[MARKER, TOKEN]) as ws:
        ws.send_text(json.dumps({"message": "Why is the gas certificate lapsed?", "context": "ctx",
                                 "origin": "ui.matrix_cell:risk", "origin_params": {"building": "B", "state": "risk"},
                                 "page": "cc"}))
        json.loads(ws.receive_text())
    seen = orch.seen
    assert (seen.route, seen.origin, seen.origin_params, seen.page) == \
        ("ws", "ui.matrix_cell:risk", {"building": "B", "state": "risk"}, "cc")


def _route_source(name: str) -> str:
    src = Path(workflow.__file__).read_text(encoding="utf-8")
    start = src.index(f"async def {name}(")
    end = src.find("\n@router.", start)
    return src[start:end if end != -1 else len(src)]


@pytest.mark.parametrize("fn, call, after", [
    ("run_workflow", 'tl_provenance.set_request(route="run"', "caller_organization_id.set(_acting)"),
    ("run_stateful_workflow", 'tl_provenance.set_request(route="run_stateful"', "caller_organization_id.set(_acting)"),
    ("run_stateful_workflow_with_files", 'tl_provenance.set_request(route="files"', "caller_organization_id.set(org)"),
    ("resume_workflow", 'tl_provenance.set_request(route="resume")', "async def resume_workflow("),
    ("ws_workflow", 'tl_provenance.set_request(route="ws"', "caller_organization_id.set(_acting)"),
])
def test_every_chat_route_sets_provenance_once_the_caller_is_known(fn, call, after):
    src = _route_source(fn)
    assert src.count(call) == 1 and src.index(after) < src.index(call)


def test_resume_sets_it_before_the_orchestrator_and_files_read_the_form():
    resume = _route_source("resume_workflow")
    assert resume.index("tl_provenance.set_request(") < resume.index("orchestrator.resume(")
    files = _route_source("run_stateful_workflow_with_files")
    for piece in ("origin: str | None = Form(None)", "origin_params: str | None = Form(None)",
                  "page: str | None = Form(None)", "tl_provenance.form_params(origin_params)", "files=len(files or [])"):
        assert piece in files, piece
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_workflow_provenance.py`
Expected: FAIL — `AttributeError: 'WorkflowRequest' object has no attribute 'origin'`; `orch.seen` is None; the source checks find no `tl_provenance.set_request`.

- [ ] **Step 3: Implement**

Edits to `DA/src/api/routes/workflow.py`:

(a) Import — old:
```python
from ...provider_errors import provider_refusal, refusal_detail
```
new:
```python
from ...provider_errors import provider_refusal, refusal_detail
from ...trace_lab import provenance as tl_provenance
```

(b) `WorkflowRequest` — old:
```python
    organization_id: str | None = Field(None, description="Superadmin-only: view as this company")


class ToolCallRecord(BaseModel):
```
new:
```python
    organization_id: str | None = Field(None, description="Superadmin-only: view as this company")
    # Where the question came from (Trace Lab provenance, trace_lab/provenance.py). Analytics only:
    # never routing, scope or the answer. Typed Any so an odd value is dropped by provenance rather
    # than refused with a 422; old clients send none of these and behave exactly as before.
    origin: Any = Field(None, description="Allow-listed origin id, e.g. chat.typed or chat.chip:home")
    origin_params: Any = Field(None, description="Allow-listed keys for the origin")
    page: Any = Field(None, description="The app view the question was asked from")


class ToolCallRecord(BaseModel):
```

(c) `/run` — old:
```python
    _acting = _resolve_acting_org(body.organization_id, principal)
    caller_organization_id.set(_acting)
    activity_log.set_current_session(body.session_id, body.session_id)
```
new:
```python
    _acting = _resolve_acting_org(body.organization_id, principal)
    caller_organization_id.set(_acting)
    tl_provenance.set_request(route="run", origin=body.origin, origin_params=body.origin_params,
                              page=body.page, context=body.context)
    activity_log.set_current_session(body.session_id, body.session_id)
```

(d) `/run-stateful` — old:
```python
    caller_organization_id.set(_acting)
    if not sid:
```
new:
```python
    caller_organization_id.set(_acting)
    tl_provenance.set_request(route="run_stateful", origin=body.origin, origin_params=body.origin_params,
                              page=body.page, context=body.context)
    if not sid:
```

(e) Files signature — old:
```python
    interactive_migration: bool = Form(False),
    files: list[UploadFile] = File(default_factory=list),
```
new:
```python
    interactive_migration: bool = Form(False),
    origin: str | None = Form(None),
    origin_params: str | None = Form(None),
    page: str | None = Form(None),
    files: list[UploadFile] = File(default_factory=list),
```

(f) Files route — old:
```python
    caller_organization_id.set(org)

    # Whether this person may add data at all. Read-only users see their buildings and stop
```
new:
```python
    caller_organization_id.set(org)
    tl_provenance.set_request(route="files", origin=origin, origin_params=tl_provenance.form_params(origin_params),
                              page=page, context=context, files=len(files or []), ingest_source=source)

    # Whether this person may add data at all. Read-only users see their buildings and stop
```

(g) `/resume` — old:
```python
    log.info("workflow.resume", session_id=session_id, decision_keys=list(body.decision.keys()))
```
new:
```python
    tl_provenance.set_request(route="resume")
    log.info("workflow.resume", session_id=session_id, decision_keys=list(body.decision.keys()))
```

(h) WS — old:
```python
            _acting = _resolve_acting_org(body.get("organization_id"), principal)
            caller_organization_id.set(_acting)
        except HTTPException as exc:
```
new:
```python
            _acting = _resolve_acting_org(body.get("organization_id"), principal)
            caller_organization_id.set(_acting)
            tl_provenance.set_request(route="ws", origin=body.get("origin"), origin_params=body.get("origin_params"),
                                      page=body.get("page"), context=context)
        except HTTPException as exc:
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_workflow_provenance.py tests/test_ws_auth.py tests/test_investigation_summary_route.py tests/test_ingestion_gate.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/api/routes/workflow.py`, `DA/tests/test_trace_lab_workflow_provenance.py`.

---

### Task 20: Gate spans in the trace (`on_gate_open`, `on_gate_close`, `record_gate_call`)

**Files:**
- Modify: `DA/src/agents/trace.py:589-598` (after `on_step_close`)
- Test: `DA/tests/test_trace_lab_gates.py`

**Interfaces:**
- Consumes: `activity_log.fire_exchange` (DA `src/agents/activity_log.py:87-123`), `llm_cost.record`, `llm_cost.current`, `llm_cost.price_usd` (DA `src/agents/llm_cost.py:64-74`, `:202-213`), `trace.on_llm_cost` (`:409-453`).
- Produces: `trace.on_gate_open(name, *, agent, input) -> str | None`, `trace.on_gate_close(run_id, output, *, ok, error=None)`, `trace.record_gate_call(*, agent, stage, model, system, user, output=None, usage=None, ms=0.0, error=None)`. A gate is a `kind="gate"` span under the open plan step (else the root), registered as `t.groups[agent]` while open, so its model exchanges nest under it as priced `llm` spans.

- [ ] **Step 1: Write the failing test**

`DA/tests/test_trace_lab_gates.py`:
```python
"""Gate spans: a post-loop step that may rewrite the answer is a span, with its own model calls
priced under it (spec "Trace capture" 2.3)."""
from __future__ import annotations

import uuid

import pytest

from src.agents import llm_cost, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

GPT = {"input_tokens": 1000, "output_tokens": 50, "cache_read": 0, "cache_write": 0}


@pytest.fixture
def turn(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    async def fake_write(t, answer, tool_calls, summary):
        return None

    monkeypatch.setattr(trace, "_write", fake_write)
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tokens = (caller_principal.set(p), caller_organization_id.set(str(p.organization_id)), trace._turn.set(None),
              llm_cost._ledger.set(None))
    yield
    caller_principal.reset(tokens[0])
    caller_organization_id.reset(tokens[1])
    trace._turn.reset(tokens[2])
    llm_cost._ledger.reset(tokens[3])


def _open_turn(q="How many boilers?") -> trace.Turn:
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-ffff000000000001",
                       "session_id": "s1", "summary": q, "payload": {"message": q, "mode": "stream"}})
    return trace.current()


def _gate(t):
    gate = next(s for s in t.spans if s.kind == "gate")
    return gate, [s for s in t.spans if s.parent_id == gate.id]


async def test_a_gate_holds_its_model_calls_with_their_cost(turn):
    t = _open_turn()
    rid = trace.on_gate_open("el_udr", agent="el_udr", input={"question": "q", "answer_before": "a"})
    trace.record_gate_call(agent="el_udr", stage="judge", model="gpt-4o-mini", system="sys", user={"user_request": "q"},
                           output="{}", usage={"input_tokens": 1000, "output_tokens": 50}, ms=12.0)
    trace.on_gate_close(rid, {"outcome": "passed", "changed": False}, ok=True)
    gate, calls = _gate(t)
    assert (gate.name, gate.agent, gate.parent_id, gate.open, gate.ok) == ("el_udr", "el_udr", t.root.id, False, True)
    assert gate.input == {"question": "q", "answer_before": "a"} and gate.output["outcome"] == "passed"
    assert [(c.kind, c.name, c.model, c.input_tokens) for c in calls] == [("llm", "judge", "gpt-4o-mini", 1000)]
    assert calls[0].cost_usd == llm_cost.price_usd("gpt-4o-mini", GPT)
    assert "el_udr" not in t.groups
    assert trace.totals(t, None)["llm_calls"] == 1


async def test_with_a_ledger_the_gate_call_is_on_the_turns_bill(turn):
    llm_cost.begin_turn("s1")
    t = _open_turn()
    rid = trace.on_gate_open("el_udr", agent="el_udr", input={})
    trace.record_gate_call(agent="el_udr", stage="judge", model="gpt-4o-mini", system="s", user="u", output="{}",
                           usage={"input_tokens": 1000, "output_tokens": 50}, ms=5.0)
    trace.on_gate_close(rid, {"outcome": "passed"}, ok=True)
    _, calls = _gate(t)
    assert llm_cost.current().summary()["calls"] == 1 and calls[0].cost_usd == llm_cost.price_usd("gpt-4o-mini", GPT)


async def test_a_gate_opens_under_the_running_plan_step(turn):
    t = _open_turn()
    step = trace.on_step_open("s1", "udr", {})
    trace.on_gate_close(trace.on_gate_open("contract_compose", agent="contract_analyst", input={}), {"outcome": "kept"}, ok=True)
    trace.on_step_close(step, {})
    gate, _ = _gate(t)
    assert gate.parent_id == next(s for s in t.spans if s.kind == "step").id


async def test_a_failed_call_is_an_error_span_and_a_failed_gate_carries_its_issue(turn):
    t = _open_turn()
    rid = trace.on_gate_open("el_udr", agent="el_udr", input={})
    trace.record_gate_call(agent="el_udr", stage="judge", model="gpt-4o-mini", system="s", user="u",
                           error="provider timed out", ms=30000.0)
    trace.on_gate_close(rid, {"outcome": "degraded"}, ok=False, error="count unclear")
    gate, calls = _gate(t)
    assert gate.ok is False and gate.error == "count unclear"
    assert calls[0].ok is False and calls[0].cost_usd is None


async def test_no_turn_means_no_gate_and_nothing_breaks(turn):
    assert trace.on_gate_open("el_udr", agent="el_udr", input={}) is None
    trace.record_gate_call(agent="el_udr", stage="judge", model="gpt-4o-mini", system="s", user="u",
                           usage={"input_tokens": 1}, ms=1.0)
    trace.on_gate_close(None, {"outcome": "passed"}, ok=True)


async def test_a_gate_left_open_is_closed_at_flush(turn):
    t = _open_turn()
    trace.on_gate_open("el_udr", agent="el_udr", input={})
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "ok",
                       "payload": {"answer": "ok"}})
    gate, _ = _gate(t)
    assert gate.open is False and gate.output is None
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_gates.py`
Expected: FAIL — `AttributeError: module 'src.agents.trace' has no attribute 'on_gate_open'`.

- [ ] **Step 3: Implement**

Edit `DA/src/agents/trace.py` — old:
```python
        s = t.tool_runs.pop(run_id, None)
        if s is not None:
            s.close(output=_bound(output), ok=ok, error=error)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_step_close_failed", error=str(exc)[:200])
```
new:
```python
        s = t.tool_runs.pop(run_id, None)
        if s is not None:
            s.close(output=_bound(output), ok=ok, error=error)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_step_close_failed", error=str(exc)[:200])


# ── gates: post-loop steps that may rewrite the answer (Trace Lab spec, Trace capture 2.3) ──

def on_gate_open(name: str, *, agent: str, input: dict[str, Any]) -> str | None:
    """A step that may rewrite the answer starts (EL-UDR, the vendor analyst). Opens a kind='gate'
    span under the open plan step, else the root, and registers it as t.groups[agent], so the model
    exchanges the gate fires with agent=<agent> nest under it. Returns a run id for on_gate_close;
    None when no turn is recording. on_llm_cost never targets a gate (it targets llm, router and
    stage spans) and _absorb_ledger_span never removes one (it removes only agent groups); a gate
    left open by an exception is closed at flush with no output."""
    try:
        t = _turn.get()
        if t is None or t.flushed:
            return None
        parent = t.last_open(lambda x: x.kind == "step") or t.root
        s = t.new(kind="gate", name=str(name)[:120], agent=agent, parent_id=parent.id, input=_bound(input))
        t.groups[agent] = s
        rid = "gate:" + agent + ":" + s.id
        t.tool_runs[rid] = s
        return rid
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_gate_open_failed", error=str(exc)[:200])
        return None


def on_gate_close(run_id: str | None, output: dict[str, Any], *, ok: bool, error: str | None = None) -> None:
    """Close the gate span with its outcome; unregister t.groups[agent] when it is this span."""
    try:
        t = _turn.get()
        if t is None or t.flushed or not run_id:
            return
        s = t.tool_runs.pop(run_id, None)
        if s is None:
            return
        s.close(output=_bound(output), ok=ok, error=error)
        if s.agent and t.groups.get(s.agent) is s:
            t.groups.pop(s.agent, None)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_gate_close_failed", error=str(exc)[:200])


def record_gate_call(*, agent: str, stage: str, model: str | None, system: Any, user: Any, output: Any = None,
                     usage: dict[str, int] | None = None, ms: float = 0.0, error: str | None = None) -> None:
    """One model call a gate made: an llm span under the gate (activity_log.fire_exchange) with
    prompt, reply and tokens, priced by llm_cost.record when the usage is known. The REST path opens
    no turn ledger (orchestrator.py:7519-7537), and without one llm_cost.record only logs and returns
    its entry (llm_cost.py:206-213), which is then handed to on_llm_cost directly. No ledger is
    opened here: the REST run panel shows the ledger as the turn's whole cost (orchestrator.py:4166-4175)."""
    try:
        from . import activity_log, llm_cost

        u = {k: int((usage or {}).get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read", "cache_write")}
        activity_log.fire_exchange(agent=agent, stage=stage, system=system, user=user, output=output, error=error,
                                   model=model, latency_ms=ms,
                                   usage={"input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"]})
        if model and usage is not None:
            entry = llm_cost.record(agent, model, u, ms)
            if llm_cost.current() is None and entry is not None:
                on_llm_cost(entry)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.record_gate_call_failed", error=str(exc)[:200])
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_gates.py tests/test_trace.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/trace.py`, `DA/tests/test_trace_lab_gates.py`.

---

### Task 21: EL-UDR is a gate span with priced judge calls (`udr_response_evaluator.py`)

**Files:**
- Modify: `DA/src/agents/udr_response_evaluator.py:221-237` (helpers + wrapper + renamed body), `:258-266` (judge call), `:294-296` (passed), `:302-310` (recheck call), `:344` (corrected), `:394` (degraded / not evaluated), `:400-406` (blocked)
- Test: `DA/tests/test_trace_lab_gates.py` (append)

**Interfaces:**
- Consumes: `trace.on_gate_open`, `on_gate_close`, `record_gate_call` (Task 20); `llm_cost.usage_from_langchain_messages` (DA `src/agents/llm_cost.py:91-132`); `settings.openai_model`.
- Produces: unchanged public `evaluate_udr_response(*, user_message, answer, tool_calls, llm) -> tuple[str, UdrEvaluation]` (the four call sites, orchestrator.py:7291/7410/7421/8333, stay as they are); the gate span `el_udr` with output `{"outcome": passed|corrected|degraded|not_evaluated|blocked, "changed", "verdict": {grounded, answers_question, count_consistent, score, issues, evaluated, passed}, "recheck", "missing_identifiers", "shrank", "answer_after"}`; private `_evaluate_udr_response(...) -> (answer, evaluation, gate_output)`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_gates.py`:
```python


# ── EL-UDR as a gate (spec 2.3) ──────────────────────────────────────────────────────

import inspect  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402

from src.agents.udr_response_evaluator import evaluate_udr_response  # noqa: E402

CALLS = [{"tool": "query_table", "input": {"table_name": "assets"}, "output": [{"id": 1}, {"id": 2}, {"id": 3}]}]
PASS = ('{"grounded": true, "answers_question": true, "count_consistent": true, "score": 0.95, "issues": [],'
        ' "corrected_answer": null}')
FAIL_FIXED = ('{"grounded": false, "answers_question": false, "count_consistent": false, "score": 0.2,'
              ' "issues": ["The count is wrong"], "corrected_answer": "There are 2 boilers."}')
DEGRADED = ('{"grounded": true, "answers_question": true, "count_consistent": false, "score": 0.6,'
            ' "issues": ["count unclear"], "corrected_answer": null}')
BLOCKED = ('{"grounded": false, "answers_question": false, "count_consistent": false, "score": 0.1,'
           ' "issues": ["nothing supports it"], "corrected_answer": null}')


class Judge:
    model_name = "gpt-4o-mini"

    def __init__(self, *payloads):
        self.payloads = list(payloads)

    async def ainvoke(self, _messages):
        return AIMessage(content=self.payloads.pop(0),
                         usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
                         response_metadata={"model_name": "gpt-4o-mini"})


async def _el_udr(*payloads, answer="There are 3 boilers."):
    t = _open_turn()
    out, ev = await evaluate_udr_response(user_message="How many boilers?", answer=answer, tool_calls=CALLS,
                                          llm=Judge(*payloads))
    gate, calls = _gate(t)
    return out, ev, gate, calls


async def test_a_corrected_answer_leaves_a_gate_span_with_its_cost(turn):
    out, ev, gate, calls = await _el_udr(FAIL_FIXED, PASS)
    assert out == "There are 2 boilers." and ev.passed
    assert gate.name == "el_udr" and gate.ok is False and gate.error == "The count is wrong"
    assert gate.input["question"] == "How many boilers?" and gate.input["answer_before"] == "There are 3 boilers."
    assert gate.output["outcome"] == "corrected" and gate.output["changed"] is True
    assert gate.output["answer_after"] == "There are 2 boilers." and gate.output["recheck"]["passed"] is True
    assert [c.name for c in calls] == ["judge", "recheck"]
    assert all(c.cost_usd == llm_cost.price_usd("gpt-4o-mini", GPT) for c in calls)


@pytest.mark.parametrize("payloads, outcome, changed", [
    ((PASS,), "passed", False),
    ((DEGRADED,), "degraded", True),
    (("not json at all",), "not_evaluated", True),
    ((BLOCKED,), "blocked", True),
])
async def test_every_el_udr_outcome_is_recorded(turn, payloads, outcome, changed):
    out, _, gate, calls = await _el_udr(*payloads)
    assert gate.output["outcome"] == outcome and gate.output["changed"] is changed
    assert gate.output["answer_after"] == (out if changed else None)
    assert gate.ok is (outcome == "passed") and len(calls) == 1


def test_the_public_signature_is_unchanged():
    assert list(inspect.signature(evaluate_udr_response).parameters) == ["user_message", "answer", "tool_calls", "llm"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_gates.py`
Expected: FAIL — `StopIteration` in `_gate` (no `gate` span is recorded).

- [ ] **Step 3: Implement**

Edits to `DA/src/agents/udr_response_evaluator.py`:

(a) Helpers, the wrapper and the renamed body — old:
```python
async def evaluate_udr_response(
    *,
    user_message: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    llm: Any,
) -> tuple[str, UdrEvaluation]:
    """Evaluate and, when necessary, replace a UDR answer before it reaches the UI."""
    system = SystemMessage(
```
new:
```python
def _not_evidence_calls(tool_calls: list[dict[str, Any]] | None) -> int:
    return sum(1 for c in tool_calls or [] if str(c.get("tool") or "") not in _NOT_EVIDENCE)


def _gate_input(user_message: str, answer: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """What the gate span records as its input: never the 80,000-character evidence itself, which the
    turn's own tool spans already hold."""
    ev = _evidence(tool_calls)
    return {"question": (user_message or "")[:2000], "answer_before": answer,
            "evidence": {"calls": _not_evidence_calls(tool_calls), "chars": len(ev),
                         "clipped": len(ev) >= _MAX_EVIDENCE_CHARS or "[clipped" in ev}}


def _judge_user(user_message: str, candidate: str, tool_calls: list[dict[str, Any]], evidence: str) -> dict[str, Any]:
    return {"user_request": (user_message or "")[:4000], "candidate_answer": candidate,
            "evidence_calls": _not_evidence_calls(tool_calls), "evidence_chars": len(evidence)}


def _model_of(llm: Any, response: Any) -> str:
    meta = getattr(response, "response_metadata", None) or {}
    model = (meta.get("model_name") or meta.get("model")) if isinstance(meta, dict) else None
    model = model or getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if not isinstance(model, str) or not model:
        from ..config import settings

        model = settings.openai_model
    return model


async def _judge_call(llm: Any, messages: list[Any], *, stage: str, user: dict[str, Any]) -> Any:
    """One EL-UDR model call, recorded under the gate span with tokens and cost. An exception is
    recorded and re-raised, so the existing except branches still decide the verdict."""
    import time as _time

    from . import llm_cost
    from . import trace as _trace

    system = getattr(messages[0], "content", None)
    t0 = _time.perf_counter()
    try:
        response = await llm.ainvoke(messages)
    except Exception as exc:
        _trace.record_gate_call(agent="el_udr", stage=stage, model=_model_of(llm, None), system=system, user=user,
                                error=str(exc)[:300], ms=(_time.perf_counter() - t0) * 1000)
        raise
    usage, _ = llm_cost.usage_from_langchain_messages([response])
    _trace.record_gate_call(agent="el_udr", stage=stage, model=_model_of(llm, response), system=system, user=user,
                            output=getattr(response, "content", response), usage=usage,
                            ms=(_time.perf_counter() - t0) * 1000)
    return response


def _verdict(e: "UdrEvaluation | None") -> dict[str, Any] | None:
    if e is None:
        return None
    return {"grounded": e.grounded, "answers_question": e.answers_question, "count_consistent": e.count_consistent,
            "score": e.score, "issues": list(e.issues)[:10], "evaluated": e.evaluated, "passed": e.passed}


def _gate_output(outcome: str, *, before: str, after: str, evaluation: "UdrEvaluation",
                 recheck: "UdrEvaluation | None", missing: list[str], shrank: bool) -> dict[str, Any]:
    """The gate span's output. answer_after only when the answer changed, so both answers fit the span."""
    changed = after != before
    return {"outcome": outcome, "changed": changed, "verdict": _verdict(evaluation), "recheck": _verdict(recheck),
            "missing_identifiers": list(missing or [])[:20], "shrank": shrank,
            "answer_after": after if changed else None}


def _first_issue(e: "UdrEvaluation") -> str:
    return next((i for i in e.issues if i), "")[:300]


async def evaluate_udr_response(
    *,
    user_message: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    llm: Any,
) -> tuple[str, UdrEvaluation]:
    """Evaluate and, when necessary, replace a UDR answer before it reaches the UI.

    Recorded as a gate span (agents/trace.py on_gate_open): what went in, the outcome and the
    verdict - which every caller discards - with the judge's own model calls under it, priced. The
    signature and the return value are unchanged, so the four call sites stay as they are."""
    from . import trace as _trace

    run_id = _trace.on_gate_open("el_udr", agent="el_udr", input=_gate_input(user_message, answer, tool_calls))
    try:
        sent, evaluation, gate = await _evaluate_udr_response(
            user_message=user_message, answer=answer, tool_calls=tool_calls, llm=llm)
    except Exception as exc:
        _trace.on_gate_close(run_id, {"outcome": "error"}, ok=False, error=str(exc)[:300])
        raise
    passed = gate.get("outcome") == "passed"
    _trace.on_gate_close(run_id, gate, ok=passed,
                         error=None if passed else (_first_issue(evaluation) or str(gate.get("outcome"))))
    return sent, evaluation


async def _evaluate_udr_response(
    *,
    user_message: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    llm: Any,
) -> tuple[str, UdrEvaluation, dict[str, Any]]:
    """The gate itself: the answer to send, the verdict, and the gate span's output."""
    evidence = _evidence(tool_calls)
    system = SystemMessage(
```

(b) The judge call — old:
```python
    human = HumanMessage(
        content=(
            f"USER REQUEST:\n{user_message}\n\n"
            f"CANDIDATE ANSWER:\n{answer}\n\n"
            f"UDR TOOL EVIDENCE:\n{_evidence(tool_calls)}"
        )
    )
    try:
        response = await llm.ainvoke([system, human])
```
new:
```python
    human = HumanMessage(
        content=(
            f"USER REQUEST:\n{user_message}\n\n"
            f"CANDIDATE ANSWER:\n{answer}\n\n"
            f"UDR TOOL EVIDENCE:\n{evidence}"
        )
    )
    try:
        response = await _judge_call(llm, [system, human], stage="judge",
                                     user=_judge_user(user_message, answer, tool_calls, evidence))
```

(c) Passed — old:
```python
    if evaluation.passed:
        log.info("udr.eval.passed", score=evaluation.score)
        return answer, evaluation
```
new:
```python
    if evaluation.passed:
        log.info("udr.eval.passed", score=evaluation.score)
        return answer, evaluation, _gate_output("passed", before=answer, after=answer, evaluation=evaluation,
                                                recheck=None, missing=missing, shrank=False)
```

(d) The recheck call — old:
```python
        corrected_human = HumanMessage(
            content=(
                f"USER REQUEST:\n{user_message}\n\n"
                f"CANDIDATE ANSWER:\n{corrected}\n\n"
                f"UDR TOOL EVIDENCE:\n{_evidence(tool_calls)}"
            )
        )
        try:
            corrected_response = await llm.ainvoke([system, corrected_human])
```
new:
```python
        corrected_human = HumanMessage(
            content=(
                f"USER REQUEST:\n{user_message}\n\n"
                f"CANDIDATE ANSWER:\n{corrected}\n\n"
                f"UDR TOOL EVIDENCE:\n{evidence}"
            )
        )
        try:
            corrected_response = await _judge_call(llm, [system, corrected_human], stage="recheck",
                                                   user=_judge_user(user_message, corrected, tool_calls, evidence))
```

(e) Corrected — old:
```python
            return corrected, corrected_evaluation
```
new:
```python
            return corrected, corrected_evaluation, _gate_output(
                "corrected", before=answer, after=corrected, evaluation=evaluation,
                recheck=corrected_evaluation, missing=missing, shrank=shrank)
```

(f) Degraded / not evaluated — old:
```python
        return best.strip() + note, best_eval
```
new:
```python
        sent = best.strip() + note
        return sent, best_eval, _gate_output(
            "not_evaluated" if not best_eval.evaluated else "degraded", before=answer, after=sent,
            evaluation=evaluation, recheck=(corrected_evaluation if corrected else None), missing=missing,
            shrank=shrank)
```

(g) Blocked — old:
```python
    return (
        "I found related data but couldn't fully verify an answer "
        f"({issue_text}). Try naming the table or entity — e.g. "
        "“list vendors with expired certificates” or “how many open work "
        "orders on chillers”.",
        evaluation,
    )
```
new:
```python
    refusal = (
        "I found related data but couldn't fully verify an answer "
        f"({issue_text}). Try naming the table or entity — e.g. "
        "“list vendors with expired certificates” or “how many open work "
        "orders on chillers”."
    )
    return refusal, evaluation, _gate_output(
        "blocked", before=answer, after=refusal, evaluation=evaluation,
        recheck=(corrected_evaluation if corrected else None), missing=missing, shrank=shrank)
```
(`corrected_evaluation` exists only when `corrected` is non-empty; the conditional expression reads it only then.)

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_gates.py tests/test_udr_response_evaluator.py tests/test_an_invented_code_fails_the_answer_without_a_judge.py tests/test_the_judge_sees_rows_and_reads_its_own_score.py`
Expected: PASS (the existing EL-UDR suites keep their exact answers and verdicts).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/udr_response_evaluator.py`, `DA/tests/test_trace_lab_gates.py`.

---

### Task 22: The vendor analyst (`contract_answer.compose`) is a gate span with a priced call

**Files:**
- Modify: `DA/src/agents/contract_answer.py:420-444` (`write_answer` call + record), `:447-450` (new `_compose_gate` before `compose`), `:476-483` (open the gate), `:521-525` (close it)
- Test: `DA/tests/test_trace_lab_gates.py` (append)

**Interfaces:**
- Consumes: `trace.on_gate_open`, `on_gate_close`, `record_gate_call` (Task 20); `llm_cost.usage_from_anthropic` (DA `src/agents/llm_cost.py:77-88`).
- Produces: unchanged `compose(...)` and `write_answer(...)` signatures and returns; gate span `contract_compose` (agent `contract_analyst`) with output `{"outcome": rewritten|kept|failed, "changed", "verdict": {grounded, issues, zones_filled}, "answer_after", "model", "error"}`; no gate when compliance owns the turn or no rows were fetched; private `_compose_gate(before, after, zones, issues, meta) -> (outcome, output)`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_gates.py`:
```python


# ── the vendor analyst as a gate (spec 2.3) ──────────────────────────────────────────

from src.agents import contract_answer as ca  # noqa: E402

ROWS = [{"vendor_name": "Gough and Kelly Ltd", "overall_score": 72.4, "score_month": "2026-08-01"}]
SCORECARD_CALLS = [{"tool": "list_vendor_scorecards", "output": {"scorecards": ROWS}}]
SONNET = {"input_tokens": 2000, "output_tokens": 300, "cache_read": 0, "cache_write": 0}


class _Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 2000, 300, 0, 0


class _Analyst:
    def __init__(self, text, fail=False):
        self._text, self._fail, self.messages = text, fail, self

    async def create(self, **kwargs):
        if self._fail:
            raise RuntimeError("model refused")
        return type("R", (), {"content": [type("B", (), {"text": self._text})()], "usage": _Usage()})()


async def _compose(client, tool_calls=SCORECARD_CALLS):
    t = _open_turn("does trend_delta agree with the scores")
    answer, calls, meta = await ca.compose(question="does trend_delta agree with the scores",
                                           answer="Gough and Kelly scored 72.4 in August.", tool_calls=tool_calls,
                                           api_key="k", model="claude-sonnet-5", client=client)
    return t, answer, meta


async def test_a_rewritten_vendor_answer_leaves_a_gate_with_the_analyst_call_priced(turn):
    t, answer, _ = await _compose(_Analyst('{"narrative": "Gough and Kelly scored 72.4.", "kpis": []}'))
    gate, calls = _gate(t)
    assert (gate.name, gate.agent, gate.ok) == ("contract_compose", "contract_analyst", True)
    assert gate.input["rows"] == 1 and gate.input["answer_before"] == "Gough and Kelly scored 72.4 in August."
    assert gate.output["outcome"] == "rewritten" and gate.output["answer_after"] == answer == "Gough and Kelly scored 72.4."
    assert [(c.kind, c.name, c.model) for c in calls] == [("llm", "analyst", "claude-sonnet-5")]
    assert calls[0].cost_usd == llm_cost.price_usd("claude-sonnet-5", SONNET)


async def test_a_failed_analyst_closes_the_gate_as_failed(turn):
    t, answer, meta = await _compose(_Analyst("", fail=True))
    gate, calls = _gate(t)
    assert answer == "Gough and Kelly scored 72.4 in August." and meta["error"] == "model refused"
    assert gate.output["outcome"] == "failed" and gate.ok is False and gate.error == "model refused"
    assert calls[0].ok is False


async def test_no_gate_when_compliance_owns_the_turn_or_no_rows_were_read(turn):
    for tool_calls in ([{"tool": "compliance_response", "output": {}}], [{"tool": "list_vendor_scorecards", "output": {}}]):
        trace._turn.set(None)
        t, _, _ = await _compose(_Analyst('{"narrative": "x"}'), tool_calls=tool_calls)
        assert not any(s.kind == "gate" for s in t.spans)
```

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlda.sh tests/test_trace_lab_gates.py`
Expected: FAIL — `StopIteration` in `_gate` for the two compose tests (no gate span).

- [ ] **Step 3: Implement**

Edits to `DA/src/agents/contract_answer.py`:

(a) `write_answer` — old:
```python
    t0 = time.perf_counter()
    try:
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=model,
            max_tokens=8000,
            # The facilities manager's order of what matters (skills/query-builder/fm-lens.md).
            system=_ANALYST_SYSTEM + ("\n\n---\n\n" + fm_lens() if fm_lens() else ""),
            messages=[{"role": "user", "content": human}],
        )
        blocks = getattr(resp, "content", None) or []
        text = "".join(
            getattr(b, "text", "") if not isinstance(b, dict) else b.get("text", "")
            for b in blocks
        ).strip()
        payload = _first_json_object(text)
        if payload is None:
            raise ValueError("no JSON object in the reply")
    except Exception as exc:  # noqa: BLE001 — a failed analyst keeps the prose answer
        log.warning("contract.analyst.failed", error=str(exc)[:200])
        return None, {**meta, "error": str(exc)[:200]}
    meta["ms"] = round((time.perf_counter() - t0) * 1000)
```
new:
```python
    from . import llm_cost
    from . import trace as _trace

    # The facilities manager's order of what matters (skills/query-builder/fm-lens.md).
    system = _ANALYST_SYSTEM + ("\n\n---\n\n" + fm_lens() if fm_lens() else "")
    # What the gate span shows of this call: the question, the prose and the size of the evidence -
    # never the 90,000-character row digest, which the turn's own tool spans already hold.
    seen = {"question": (question or "").strip()[:1000], "prose": (prose or "")[:2000],
            "rows": len(rows), "evidence_truncated": truncated}
    usage: dict[str, int] | None = None
    text = ""
    t0 = time.perf_counter()
    try:
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": human}],
        )
        usage = llm_cost.usage_from_anthropic(getattr(resp, "usage", None))
        blocks = getattr(resp, "content", None) or []
        text = "".join(
            getattr(b, "text", "") if not isinstance(b, dict) else b.get("text", "")
            for b in blocks
        ).strip()
        payload = _first_json_object(text)
        if payload is None:
            raise ValueError("no JSON object in the reply")
    except Exception as exc:  # noqa: BLE001 — a failed analyst keeps the prose answer
        log.warning("contract.analyst.failed", error=str(exc)[:200])
        _trace.record_gate_call(agent="contract_analyst", stage="analyst", model=model, system=system, user=seen,
                                output=text or None, usage=usage, ms=(time.perf_counter() - t0) * 1000,
                                error=str(exc)[:300])
        return None, {**meta, "error": str(exc)[:200]}
    meta["ms"] = round((time.perf_counter() - t0) * 1000)
    _trace.record_gate_call(agent="contract_analyst", stage="analyst", model=model, system=system, user=seen,
                            output=text, usage=usage, ms=meta["ms"])
```

(b) `_compose_gate` before `compose` — old:
```python
    return payload, meta


async def compose(
```
new:
```python
    return payload, meta


def _compose_gate(before: str, after: str, zones: dict[str, Any] | None, issues: list[str],
                  meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The contract_compose gate's outcome and output (Trace Lab spec, Trace capture 2.3)."""
    changed = after != before
    outcome = "failed" if meta.get("error") else ("rewritten" if changed else "kept")
    return outcome, {
        "outcome": outcome, "changed": changed,
        "verdict": {"grounded": not issues, "issues": list(issues)[:10],
                    "zones_filled": sorted(k for k, v in (zones or {}).items() if v)},
        "answer_after": after if changed else None, "model": meta.get("model"), "error": meta.get("error"),
    }


async def compose(
```

(c) Open the gate — old:
```python
    try:
        rows = evidence_rows(tool_calls)
        if not rows:
            return answer, tool_calls, {"skipped": "no rows fetched"}
        zones, meta = await write_answer(
```
new:
```python
    from . import trace as _trace

    gate: str | None = None
    try:
        rows = evidence_rows(tool_calls)
        if not rows:
            return answer, tool_calls, {"skipped": "no rows fetched"}
        before = str(answer or "")
        # A gate span (agents/trace.py): the analyst may replace the prose, so the trace keeps what went
        # in, what came out, and the analyst's own call with its cost.
        gate = _trace.on_gate_open("contract_compose", agent="contract_analyst", input={
            "question": (question or "")[:2000], "answer_before": before, "rows": len(rows),
            "evidence_truncated": len(json.dumps(rows, default=str)) > 90_000})
        zones, meta = await write_answer(
```

(d) Close it — old:
```python
            emitted = [tc for tc in emitted if tc.get("tool") != "compliance_pipeline"]
        return answer, [*tool_calls, *emitted], meta
    except Exception as exc:  # noqa: BLE001 — never lose the turn over presentation
        log.warning("contract.answer.failed", session_id=session_id, error=str(exc)[:200])
        return answer, tool_calls, {"error": str(exc)[:200]}
```
new:
```python
            emitted = [tc for tc in emitted if tc.get("tool") != "compliance_pipeline"]
        outcome, gate_out = _compose_gate(before, str(answer or ""), zones, issues, meta)
        _trace.on_gate_close(gate, gate_out, ok=outcome != "failed",
                             error=meta.get("error") if outcome == "failed" else None)
        return answer, [*tool_calls, *emitted], meta
    except Exception as exc:  # noqa: BLE001 — never lose the turn over presentation
        log.warning("contract.answer.failed", session_id=session_id, error=str(exc)[:200])
        _trace.on_gate_close(gate, {"outcome": "failed", "changed": False, "error": str(exc)[:300]}, ok=False,
                             error=str(exc)[:300])
        return answer, tool_calls, {"error": str(exc)[:200]}
```

- [ ] **Step 4: Run it and see it pass**

Run: `sh tlda.sh tests/test_trace_lab_gates.py tests/test_contract_answer.py tests/test_contract_performance_single_door.py tests/test_contract_skill_wiring.py`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/agents/contract_answer.py`, `DA/tests/test_trace_lab_gates.py`.

---


### Task 23: The Overview tree names a gate span (`tracesPage.js` `gate: 'Gate'`)

**Files:**
- Modify: `FE/src/logic/tracesPage.js:14` (`KIND_LABEL`)
- Test: `FE/test/traceLabGateLabel.test.mjs` (create)

**Interfaces:**
- Consumes: the gate span shape from Task 20 (`kind: "gate"`, name `el_udr` or `contract_compose`, its model calls as `kind: "llm"` children) and `buildTree(spans)` (FE `src/logic/tracesPage.js:42-63`, unchanged).
- Produces: `KIND_LABEL.gate === 'Gate'`, so `buildTree` rows for a gate carry `kindLabel: 'Gate'` (spec "Trace capture" 2.3; `corrections.js` `KIND_MODE` already falls back to `suggestion` for an unknown kind, no change there).

- [ ] **Step 1: Write the failing test**

`FE/test/traceLabGateLabel.test.mjs`:
```js
// The Overview tree names a gate (Trace Lab M0, 15 Oct 2026).
//
// A gate is a step after the answer is written that may rewrite it: the answer check that
// corrects or blocks a data answer, and the vendor analyst that recomposes a vendor answer.
// The trace records each as a span of kind "gate", with its own model calls under it, priced.
// Without a label the tree printed the raw kind.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTree } from '../src/logic/tracesPage.js';

const T = (s) => '2026-10-15T10:00:' + s + 'Z';
const SPANS = [
  { id: 'root', parent_id: null, seq: 0, kind: 'turn', name: 'turn', started_at: T('00.000'), ended_at: T('10.000'), latency_ms: 10000, ok: true },
  { id: 'a', parent_id: 'root', seq: 1, kind: 'agent', name: 'udr', started_at: T('00.100'), ended_at: T('06.000'), latency_ms: 5900, ok: true },
  { id: 'g', parent_id: 'root', seq: 2, kind: 'gate', name: 'el_udr', agent: 'el_udr', started_at: T('06.000'), ended_at: T('09.000'),
    latency_ms: 3000, ok: false, error: 'The count is wrong',
    input: { question: 'How many boilers?', answer_before: 'There are 3 boilers.' },
    output: { outcome: 'corrected', changed: true, answer_after: 'There are 2 boilers.' } },
  { id: 'j', parent_id: 'g', seq: 3, kind: 'llm', name: 'judge', agent: 'el_udr', model: 'gpt-4o-mini', started_at: T('06.000'), ended_at: T('07.500'), latency_ms: 1500, cost_usd: 0.0002, ok: true },
  { id: 'r', parent_id: 'g', seq: 4, kind: 'llm', name: 'recheck', agent: 'el_udr', model: 'gpt-4o-mini', started_at: T('07.500'), ended_at: T('09.000'), latency_ms: 1500, cost_usd: 0.0002, ok: true }
];

test('a gate span reads "Gate" in the tree, with its model calls under it', () => {
  const tree = buildTree(SPANS);
  const gate = tree.find((n) => n.name === 'el_udr');
  assert.equal(gate.kindLabel, 'Gate');
  assert.equal(gate.depth, 1);
  assert.equal(gate.children, 2);
  assert.equal(gate.ok, false, 'a gate that changed the answer is not shown as a clean pass');
  assert.deepEqual(tree.filter((n) => n.span.parent_id === 'g').map((n) => [n.name, n.kindLabel, n.depth]),
    [['judge', 'Model', 2], ['recheck', 'Model', 2]]);
});

test('every other kind keeps its label', () => {
  assert.deepEqual(buildTree(SPANS).map((n) => n.kindLabel), ['Turn', 'Agent', 'Gate', 'Model', 'Model']);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabGateLabel.test.mjs`
Expected: FAIL, both tests — `Expected values to be strictly equal: + actual - expected  + 'gate'  - 'Gate'` (the label falls back to the raw kind).

- [ ] **Step 3: Implement**

Edit `FE/src/logic/tracesPage.js` — old:
```js
const KIND_LABEL = { turn: 'Turn', agent: 'Agent', router: 'Router', llm: 'Model', tool: 'Tool', stage: 'Stage', db: 'Query', plan: 'Plan', step: 'Plan step' };
```
new:
```js
// gate: a step after the answer is written that may rewrite it (the answer check, the vendor analyst),
// recorded with its own model calls under it (svc-deepagents agents/trace.py on_gate_open).
const KIND_LABEL = { turn: 'Turn', agent: 'Agent', router: 'Router', llm: 'Model', tool: 'Tool', stage: 'Stage', db: 'Query', plan: 'Plan', step: 'Plan step', gate: 'Gate' };
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabGateLabel.test.mjs test/tracesPage.test.mjs`
Expected: PASS (`tracesPage.test.mjs` unchanged: its tree has no gate).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/tracesPage.js`, `FE/test/traceLabGateLabel.test.mjs`.

---

### Task 24: `traceLabOrigins.js` — the origin allow-list every send names

**Files:**
- Create: `FE/src/logic/traceLabOrigins.js`
- Test: `FE/test/traceLabOrigins.test.mjs` (create; Task 28 appends the send-path guard)

**Interfaces:**
- Consumes: `CLIENT_ORIGINS` in `DA/src/trace_lab/provenance.py` (Task 16), read as text by the test; the server's value rules (`accept_origin`, `accept_origin_params`, `accept_page`, Task 16): variant `[a-z0-9_-]{1,40}`, page `^[a-z0-9_:.-]{1,60}$`, param strings at most 200 chars, the whole object at most 2000 chars of JSON.
- Produces (spec R36 names, fixed here): `export const ORIGINS` (frozen; values equal the `CLIENT_ORIGINS` keys, including `report.card` / `report.cron`, which only the reports engine sends); `export function originOf(base, variant) -> string | null`; `export function pageOf(state) -> string` (`''` when unknown); `export function provFields(prov) -> { origin, origin_params, page }` where `prov` is `{ origin, params, page }` and each field is `null` when absent.

- [ ] **Step 1: Write the failing test**

`FE/test/traceLabOrigins.test.mjs`:
```js
// Where a chat question came from (Trace Lab M0, 15 Oct 2026): the allow-list every send names
// its origin from, held equal to the server's (svc-deepagents src/trace_lab/provenance.py
// CLIENT_ORIGINS), and the helpers that put it on the wire. Analytics only: the server records
// the origin beside the trace and never routes, scopes or answers on it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { ORIGINS, originOf, pageOf, provFields } from '../src/logic/traceLabOrigins.js';

const PROVENANCE = fileURLToPath(new URL('../../backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/provenance.py', import.meta.url));

// The keys of CLIENT_ORIGINS, read from the Python source: one allow-list, written twice.
function clientOrigins() {
  const py = readFileSync(PROVENANCE, 'utf8');
  const block = py.match(/CLIENT_ORIGINS: dict\[str, str\] = \{([\s\S]*?)\n\}/);
  assert.ok(block, 'CLIENT_ORIGINS is not in provenance.py');
  return [...block[1].matchAll(/"([a-z_]+\.[a-z_]+)":\s*"[a-z_]+"/g)].map((m) => m[1]).sort();
}

test("the allow-list is the server's, frozen, and never claims a golden run or a workflow step", () => {
  const values = Object.values(ORIGINS);
  assert.deepEqual(values.slice().sort(), clientOrigins());
  assert.ok(Object.isFrozen(ORIGINS));
  assert.equal(new Set(values).size, values.length);
  assert.ok(!values.some((o) => /^(golden|workflow)\./.test(o)));
});

test('originOf names a base, with a variant only when it is well formed', () => {
  assert.equal(originOf(ORIGINS.CHAT_TYPED), 'chat.typed');
  assert.equal(originOf(ORIGINS.CHAT_CHIP, 'home'), 'chat.chip:home');
  assert.equal(originOf(ORIGINS.UI_MATRIX_CELL, 'Risk'), 'ui.matrix_cell:risk');
  assert.equal(originOf(ORIGINS.SUPPORT_TOPIC, 'getting-started'), 'support.topic:getting-started');
  assert.equal(originOf(ORIGINS.CHAT_CHIP, 'two words'), 'chat.chip', 'a variant the server would refuse is left off');
  assert.equal(originOf(ORIGINS.CHAT_CHIP, 'x'.repeat(41)), 'chat.chip');
  assert.equal(originOf(ORIGINS.CHAT_CHIP, ''), 'chat.chip');
  assert.equal(originOf('golden.case'), null, 'a base outside the allow-list is never sent');
  assert.equal(originOf(undefined), null);
});

test('pageOf is the view, or the module on a module page', () => {
  assert.equal(pageOf({ view: 'home' }), 'home');
  assert.equal(pageOf({ view: 'module', module: 'energy' }), 'module:energy');
  assert.equal(pageOf({ view: 'module' }), 'module');
  assert.equal(pageOf({ view: 'Overview' }), 'overview');
  assert.equal(pageOf({ view: 'ph eye' }), '');
  assert.equal(pageOf({}), '');
  assert.equal(pageOf(null), '');
});

test('provFields puts origin, params and page on the wire, nulls when absent', () => {
  assert.deepEqual(provFields(undefined), { origin: null, origin_params: null, page: null });
  assert.deepEqual(provFields({ origin: 'chat.chip:home', page: 'home' }), { origin: 'chat.chip:home', origin_params: null, page: 'home' });
  assert.deepEqual(
    provFields({ origin: 'ui.matrix_cell:risk', params: { building: 'Kingsway House', type: 'Gas Safety Certificate', code: null, state: 'risk' }, page: 'cc' }),
    { origin: 'ui.matrix_cell:risk', origin_params: { building: 'Kingsway House', type: 'Gas Safety Certificate', code: null, state: 'risk' }, page: 'cc' });
});

test('provFields sends only what the server keeps', () => {
  const p = provFields({ origin: 'upload.migrate', params: { count: 3, cmms: 'x'.repeat(250), nested: { a: 1 }, list: [1], gone: undefined, bad: NaN }, page: 'Bad Page' });
  assert.deepEqual(p.origin_params, { count: 3, cmms: 'x'.repeat(200) });
  assert.equal(p.page, null);
  const big = {};
  for (let i = 0; i < 12; i += 1) big['k' + i] = 'y'.repeat(200);
  assert.equal(provFields({ origin: 'upload.migrate', params: big }).origin_params, null, 'over 2000 characters of JSON is not sent at all');
  assert.equal(provFields({ params: { count: 1 } }).origin_params, null, 'no params without an origin');
  assert.equal(provFields({ origin: 'chat.typed', params: ['x'] }).origin_params, null);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabOrigins.test.mjs`
Expected: FAIL — `ERR_MODULE_NOT_FOUND: Cannot find module '.../apps/frontend/src/logic/traceLabOrigins.js'`.

- [ ] **Step 3: Implement**

`FE/src/logic/traceLabOrigins.js`:
```js
// traceLabOrigins — where a chat question came from, named on every send (Trace Lab M0, 15 Oct 2026).
//
// A typed question, a chip, a matrix cell, a support topic, an upload, a correction and a report
// card all reach the orchestrator through the same few routes, and the trace could not tell them
// apart. Trace Lab samples and measures the questions people ask, so every send names its origin
// here and svc-deepagents records it beside the trace (src/trace_lab/provenance.py, which checks
// everything again against its own allow-list). Analytics only: an origin never changes routing,
// scope or the answer, and an older server ignores the fields.
//
// ORIGINS equals provenance.py CLIENT_ORIGINS (test/traceLabOrigins.test.mjs pins the two). A base
// may carry ":<variant>" ([a-z0-9_-]{1,40}), e.g. chat.chip:home or ui.matrix_cell:risk. Pure.

export const ORIGINS = Object.freeze({
  CHAT_TYPED: 'chat.typed',
  CHAT_CHIP: 'chat.chip',
  CHAT_RECENT: 'chat.recent',
  CHAT_EDIT: 'chat.edit',
  CHAT_CHOICE_CARD: 'chat.choice_card',
  UI_MATRIX_CELL: 'ui.matrix_cell',
  UI_HOIST_GRAPH: 'ui.hoist_graph',
  UI_ENERGY_INVESTIGATE: 'ui.energy_investigate',
  SUPPORT_TYPED: 'support.typed',
  SUPPORT_TOPIC: 'support.topic',
  UPLOAD_FILES: 'upload.files',
  UPLOAD_INGEST_PANEL: 'upload.ingest_panel',
  UPLOAD_VENDOR_CERTIFICATES: 'upload.vendor_certificates',
  UPLOAD_MIGRATE: 'upload.migrate',
  UPLOAD_DOCUMENTS_PAGE: 'upload.documents_page',
  UPLOAD_INGESTION_CHECK: 'upload.ingestion_check',
  CORRECTION_REANSWER: 'correction.reanswer',
  // Sent by the reports engine for a report card or a scheduled report, never by this app; listed
  // so the two allow-lists stay one list.
  REPORT_CARD: 'report.card',
  REPORT_CRON: 'report.cron'
});

const BASES = new Set(Object.values(ORIGINS));
const VARIANT = /^[a-z0-9_-]{1,40}$/;
const PAGE = /^[a-z0-9_:.-]{1,60}$/;
const MAX_PARAM_STR = 200;
const MAX_PARAMS_JSON = 2000;

// The base, or base + ':' + variant when the variant is well formed. null for a base the allow-list
// does not have: a malformed origin is never sent, because the server drops it whole.
export function originOf(base, variant) {
  if (!BASES.has(base)) return null;
  if (variant === undefined || variant === null || variant === '') return base;
  const v = String(variant).trim().toLowerCase();
  return VARIANT.test(v) ? base + ':' + v : base;
}

// The view the question was asked from: state.view, or module:<key> on a module page. '' when the
// view is unknown or not a plain name.
export function pageOf(state) {
  const s = state || {};
  const view = String(s.view || '').trim().toLowerCase();
  if (!view) return '';
  const page = view === 'module' && s.module ? 'module:' + String(s.module).trim().toLowerCase() : view;
  return PAGE.test(page) ? page : '';
}

// Scalar values only (a string cut to 200 characters, a finite number, a boolean or null), and
// nothing at all over 2000 characters of JSON: the server's own limits, so nothing it would drop
// is sent.
function cleanParams(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
  const out = {};
  Object.keys(params).forEach((k) => {
    const v = params[k];
    if (v === null || typeof v === 'boolean') out[k] = v;
    else if (typeof v === 'number' && isFinite(v)) out[k] = v;
    else if (typeof v === 'string') out[k] = v.slice(0, MAX_PARAM_STR);
  });
  if (!Object.keys(out).length) return null;
  return JSON.stringify(out).length <= MAX_PARAMS_JSON ? out : null;
}

// The wire fields of a send, { origin, origin_params, page }, each null when absent. `prov` is
// { origin, params, page } as ccAsk builds it (logic/complianceLive.js).
export function provFields(prov) {
  const p = prov || {};
  const origin = typeof p.origin === 'string' && p.origin ? p.origin : null;
  return {
    origin: origin,
    origin_params: origin ? cleanParams(p.params) : null,
    page: typeof p.page === 'string' && PAGE.test(p.page) ? p.page : null
  };
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabOrigins.test.mjs test/noInternalNames.test.mjs`
Expected: PASS (the new module's strings name no service, table or route).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/traceLabOrigins.js`, `FE/test/traceLabOrigins.test.mjs`.

---

### Task 25: The API client puts the origin on the wire (`deepAgents.js`, `documents.js`)

**Files:**
- Modify: `FE/src/api/deepAgents.js:12` (import), `:112-118` (`run`), `:133-136` (`runStateful`), `:168-169` (`runStatefulWithFiles` form), `:258` (`stream` signature), `:292` (`ws.send`)
- Modify: `FE/src/api/documents.js:5` (import), `:24-27` (`upload`)
- Test: `FE/test/traceLabOriginsWire.test.mjs` (create)

**Interfaces:**
- Consumes: `provFields`, `ORIGINS` (Task 24); the server fields of Task 19 (`WorkflowRequest.origin` / `.origin_params` / `.page`; files-route form fields `origin`, `origin_params` as JSON text, `page`; WS message keys `origin`, `origin_params`, `page`).
- Produces (R36): `deepAgentsApi.run(message, sessionId, context, prov)`, `deepAgentsApi.runStateful(message, sessionId, context, signal, prov)`, `deepAgentsApi.runStatefulWithFiles(message, sessionId, context, files, signal, buildingId, opts)` reading `opts.origin`, `opts.originParams`, `opts.page`, `deepAgentsApi.stream(sessionId, message, context, on, prov)`; `documentsApi.upload` sends `upload.documents_page` with `{ doc_type }`. A send that names nothing adds `origin: null, origin_params: null, page: null` to a JSON body or socket message and no field at all to a form.

- [ ] **Step 1: Write the failing test**

`FE/test/traceLabOriginsWire.test.mjs`:
```js
// The origin reaches the wire (Trace Lab M0, 15 Oct 2026): the socket message, the two JSON posts
// and the upload form each carry origin, origin_params and page when a send names them, and
// nothing new when it does not. svc-deepagents reads them in api/routes/workflow.py.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' } };
let posts = [];
globalThis.fetch = async (url, init) => {
  posts.push({ url: String(url), init: init || {} });
  return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify({ success: true, answer: 'ok' }) };
};
let sockets = [];
globalThis.WebSocket = class {
  constructor(url) { this.url = String(url); this.sent = []; sockets.push(this); }
  send(data) { this.sent.push(data); }
  close() {}
};

const { configureAuth, setActingOrg } = await import('../src/api/client.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');
const { documentsApi } = await import('../src/api/documents.js');

const PROV = { origin: 'ui.matrix_cell:risk', params: { building: 'Kingsway House', state: 'risk' }, page: 'cc' };
const pdf = () => new File(['%PDF-1.4 test'], 'gas.pdf', { type: 'application/pdf' });
const bodyOf = (i) => JSON.parse(posts[i].init.body);
const formOf = (i) => posts[i].init.body;

beforeEach(() => {
  posts = [];
  sockets = [];
  configureAuth({ getToken: () => 'tok-abc123' });
  setActingOrg(null);
});

test('the socket message carries the origin, its params and the page', () => {
  deepAgentsApi.stream('s-1', 'Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?', 'ctx', {}, PROV);
  sockets[0].onopen();
  const msg = JSON.parse(sockets[0].sent[0]);
  assert.ok(msg.message.startsWith('Why is the Gas Safety Certificate'));
  assert.equal(msg.context, 'ctx');
  assert.equal(msg.origin, 'ui.matrix_cell:risk');
  assert.deepEqual(msg.origin_params, { building: 'Kingsway House', state: 'risk' });
  assert.equal(msg.page, 'cc');
});

test('a send that names nothing adds only nulls, and a viewing superadmin still names the company', () => {
  setActingOrg('00000000-0000-0000-0000-0000000000b2');
  deepAgentsApi.stream('s-1', 'q', null, {});
  sockets[0].onopen();
  const msg = JSON.parse(sockets[0].sent[0]);
  assert.deepEqual([msg.origin, msg.origin_params, msg.page], [null, null, null]);
  assert.equal(msg.organization_id, '00000000-0000-0000-0000-0000000000b2');
});

test('run and run-stateful post the same three fields', async () => {
  await deepAgentsApi.runStateful('q', 's-1', 'ctx', undefined, PROV);
  await deepAgentsApi.run('q', 's-2', null, { origin: 'chat.typed', page: 'home' });
  assert.match(posts[0].url, /\/api\/workflow\/run-stateful$/);
  assert.deepEqual([bodyOf(0).origin, bodyOf(0).origin_params, bodyOf(0).page],
    ['ui.matrix_cell:risk', { building: 'Kingsway House', state: 'risk' }, 'cc']);
  assert.match(posts[1].url, /\/api\/workflow\/run$/);
  assert.deepEqual([bodyOf(1).origin, bodyOf(1).origin_params, bodyOf(1).page], ['chat.typed', null, 'home']);
  assert.equal(bodyOf(1).message, 'q');
});

test('the upload form carries origin, origin_params as JSON text, and page', async () => {
  await deepAgentsApi.runStatefulWithFiles('Ingest 1 document for Kingsway House.', 's-1', null, [pdf()], undefined, 'b-kw',
    { origin: 'upload.ingest_panel', originParams: { building: 'Kingsway House', count: 1 }, page: 'home' });
  const form = formOf(0);
  assert.equal(form.get('origin'), 'upload.ingest_panel');
  assert.deepEqual(JSON.parse(form.get('origin_params')), { building: 'Kingsway House', count: 1 });
  assert.equal(form.get('page'), 'home');
  assert.equal(form.get('building_id'), 'b-kw');
});

test('an upload that names nothing sends no origin fields at all', async () => {
  await deepAgentsApi.runStatefulWithFiles('q', 's-1', null, [pdf()]);
  const form = formOf(0);
  assert.equal(form.has('origin'), false);
  assert.equal(form.has('origin_params'), false);
  assert.equal(form.has('page'), false);
});

test('the documents page upload says it is the documents page, with the type chosen', async () => {
  await documentsApi.upload(pdf(), 'b-kw', 'gas_safety');
  const form = formOf(0);
  assert.equal(form.get('message'), 'File this document on the building.');
  assert.equal(form.get('origin'), 'upload.documents_page');
  assert.deepEqual(JSON.parse(form.get('origin_params')), { doc_type: 'gas_safety' });
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabOriginsWire.test.mjs`
Expected: FAIL in 5 of 6 tests — `msg.origin` is `undefined`, `form.get('origin')` is `null`, `bodyOf(0).origin` is `undefined`. ("an upload that names nothing" passes already.)

- [ ] **Step 3: Implement**

Edits to `FE/src/api/deepAgents.js`:

(a) Import — old:
```js
import { BASES, apiFetch, accessToken, getActingOrg } from './client.js';
```
new:
```js
import { BASES, apiFetch, accessToken, getActingOrg } from './client.js';
import { provFields } from '../logic/traceLabOrigins.js';
```

(b) `run` — old:
```js
  // POST /api/workflow/run → { session_id, answer, tool_calls[], success, error, interrupted }
  run: (message, sessionId, context) =>
    apiFetch(B, '/api/workflow/run', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, orgOverride()),
      timeoutMs: T_ASK
    }),
```
new:
```js
  // POST /api/workflow/run → { session_id, answer, tool_calls[], success, error, interrupted }
  // `prov` is where the question came from, { origin, params, page } (logic/traceLabOrigins.js):
  // recorded beside the trace for Trace Lab, never used to route, scope or answer.
  run: (message, sessionId, context, prov) =>
    apiFetch(B, '/api/workflow/run', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, provFields(prov), orgOverride()),
      timeoutMs: T_ASK
    }),
```

(c) `runStateful` — old:
```js
  runStateful: (message, sessionId, context, signal) =>
    apiFetch(B, '/api/workflow/run-stateful', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, orgOverride()),
```
new:
```js
  runStateful: (message, sessionId, context, signal, prov) =>
    apiFetch(B, '/api/workflow/run-stateful', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, provFields(prov), orgOverride()),
```

(d) `runStatefulWithFiles` form — old:
```js
    if (opts && opts.docType) form.append('doc_type', opts.docType);
    const org = getActingOrg();
```
new:
```js
    if (opts && opts.docType) form.append('doc_type', opts.docType);
    // Where the question came from (logic/traceLabOrigins.js), for Trace Lab only: opts.origin,
    // opts.originParams (sent as JSON text, the one shape a form field carries) and opts.page.
    const pf = provFields({ origin: opts && opts.origin, params: opts && opts.originParams, page: opts && opts.page });
    if (pf.origin) form.append('origin', pf.origin);
    if (pf.origin_params) form.append('origin_params', JSON.stringify(pf.origin_params));
    if (pf.page) form.append('page', pf.page);
    const org = getActingOrg();
```

(e) `stream` signature — old:
```js
  stream(sessionId, message, context, on) {
```
new:
```js
  stream(sessionId, message, context, on, prov) {
```

(f) The socket message — old:
```js
        ws.send(JSON.stringify(Object.assign({ message: message, context: context || null }, orgOverride())));
```
new:
```js
        ws.send(JSON.stringify(Object.assign({ message: message, context: context || null }, provFields(prov), orgOverride())));
```

Edits to `FE/src/api/documents.js`:

(g) Import — old:
```js
import { deepAgentsApi } from './deepAgents.js';
```
new:
```js
import { deepAgentsApi } from './deepAgents.js';
import { ORIGINS } from '../logic/traceLabOrigins.js';
```

(h) `upload` — old:
```js
  // Filed on the building with the type chosen; an identity type is filed personal and never indexed.
  upload: (file, buildingId, docType) => deepAgentsApi.runStatefulWithFiles(
    'File this document on the building.', 'docs-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    null, [file], undefined, buildingId, { docType: docType })
```
new:
```js
  // Filed on the building with the type chosen; an identity type is filed personal and never indexed.
  // The origin tells Trace Lab the turn was this page's upload (logic/traceLabOrigins.js).
  upload: (file, buildingId, docType) => deepAgentsApi.runStatefulWithFiles(
    'File this document on the building.', 'docs-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    null, [file], undefined, buildingId,
    { docType: docType, origin: ORIGINS.UPLOAD_DOCUMENTS_PAGE, originParams: { doc_type: docType || null } })
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabOriginsWire.test.mjs test/streamAuth.test.mjs test/streamAnswer.test.mjs test/chatBuilding.test.mjs test/askErrorText.test.mjs test/noInternalNames.test.mjs`
Expected: PASS (the existing socket, post and upload tests are unchanged by three extra fields).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/api/deepAgents.js`, `FE/src/api/documents.js`, `FE/test/traceLabOriginsWire.test.mjs`.

---

### Task 26: The chat plumbing carries the origin (`complianceLive.js`)

**Files:**
- Modify: `FE/src/logic/complianceLive.js:22` (import), `:796-799` (`askScoped`), `:1268-1271` (`ccAsk` builds `prov`), `:1423-1426` (files send and `ccStreamTurn` call), `:1545` (`ccStreamTurn` signature), `:1656-1660` (end of the `stream` call), `:1642` and `:1652` (both POST fallbacks), `:1769-1776` (`ccEditRun`), `:1807-1809` (`orchSubmitNow`)
- Test: `FE/test/traceLabChatOrigins.test.mjs` (create; Tasks 27 and 28 append)

**Interfaces:**
- Consumes: `ORIGINS`, `pageOf` (Task 24); `deepAgentsApi.stream(..., on, prov)`, `.runStateful(..., signal, prov)`, `.runStatefulWithFiles(..., opts)` (Task 25).
- Produces: `askScoped(q, origin, params)` (passes `{ origin, originParams: params }` to `ccAsk`; the off-chat `ask()` page sends nothing); `ccAsk(q, opts)` reads `opts.origin` and `opts.originParams` (no default) and builds `prov = { origin, params, page: pageOf(state) }` before the view changes; `ccStreamTurn(q, context, ctrl, prov)`; `ccEditRun` sends `chat.edit` with `{ replaces_turn: <turnId of the answer below the edited question> }`; `orchSubmitNow` sends `chat.typed`, or `support.typed` while a support session is on (the composer is the support composer then).

- [ ] **Step 1: Write the failing test**

`FE/test/traceLabChatOrigins.test.mjs`:
```js
// Every chat send carries where its question came from (Trace Lab M0, 15 Oct 2026).
//
// askScoped -> ccAsk -> the socket, its POST fallback, or the upload route: the origin a call site
// names, its params and the page the question was asked from reach the API client, which puts
// them on the wire (test/traceLabOriginsWire.test.mjs). Analytics only: the question, the context
// and the session are exactly what they were.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in these tests'); } };

const { configureAuth } = await import('../src/api/client.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { ORIGINS } = await import('../src/logic/traceLabOrigins.js');

const REAL = { stream: deepAgentsApi.stream, runStateful: deepAgentsApi.runStateful, runStatefulWithFiles: deepAgentsApi.runStatefulWithFiles };
const ACCOUNT = { id: 'u-1', email: 'fm@test.local', role: 'admin', organization_id: 'org-1', buildings: [] };
const settle = () => new Promise((r) => setTimeout(r, 50));
const pdf = (name) => new File(['%PDF-1.4 test'], name, { type: 'application/pdf' });

let sends;
let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  sends = [];
  configureAuth({ getToken: () => 'tok-abc123' });
  // The three transports, recorded. The socket opens and completes on the next tick.
  deepAgentsApi.stream = (sid, q, context, on, prov) => {
    sends.push({ via: 'stream', q: q, prov: prov });
    setTimeout(() => { on.onOpen(); on.onDone({ type: 'workflow_completed', answer: 'Three.', session_id: sid, tool_calls: [] }); }, 0);
    return { close: () => {} };
  };
  deepAgentsApi.runStateful = async (q, sid, context, signal, prov) => {
    sends.push({ via: 'runStateful', q: q, prov: prov });
    return { session_id: sid, answer: 'Three.', tool_calls: [], success: true };
  };
  deepAgentsApi.runStatefulWithFiles = async (q, sid, context, files, signal, buildingId, opts) => {
    sends.push({ via: 'files', q: q, opts: opts });
    return { session_id: sid, answer: 'Filed.', tool_calls: [], success: true };
  };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'chat', role: 'admin', account: ACCOUNT });
});
afterEach(() => {
  Object.assign(deepAgentsApi, REAL);
  clearInterval(c._ccTick); clearInterval(c._orchTick); clearInterval(c._ingT);
  clearTimeout(c._tt); clearTimeout(c._mgTimer);
});

test('a typed question streams with its origin and the page it was asked from', async () => {
  await c.askScoped('How many boilers are at Kingsway House?', ORIGINS.CHAT_TYPED);
  assert.deepEqual(sends, [{ via: 'stream', q: 'How many boilers are at Kingsway House?', prov: { origin: 'chat.typed', params: null, page: 'chat' } }]);
});

test('the page is the one the question was asked from, read before the chat page opens', async () => {
  c.setState({ view: 'home' });
  await c.askScoped('Which buildings put me at risk this month?', 'chat.chip:home');
  assert.deepEqual(sends[0].prov, { origin: 'chat.chip:home', params: null, page: 'home' });
  assert.equal(c.state.view, 'chat', 'the question still opens the chat page');
});

test('params ride with the origin', async () => {
  await c.askScoped('Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?',
    'ui.matrix_cell:risk', { building: 'Kingsway House', state: 'risk' });
  assert.deepEqual(sends[0].prov, { origin: 'ui.matrix_cell:risk', params: { building: 'Kingsway House', state: 'risk' }, page: 'chat' });
});

test('when the socket cannot open, the POST fallback carries the same origin', async () => {
  deepAgentsApi.stream = (sid, q, context, on, prov) => {
    sends.push({ via: 'stream', q: q, prov: prov });
    setTimeout(() => on.onError(new Error('stream unavailable')), 0);
    return { close: () => {} };
  };
  await c.askScoped('How many boilers?', ORIGINS.CHAT_TYPED);
  assert.deepEqual(sends.map((s) => s.via), ['stream', 'runStateful']);
  assert.deepEqual(sends[1].prov, { origin: 'chat.typed', params: null, page: 'chat' });
});

test('an upload carries the origin in its options, beside its own', async () => {
  c.setState({ ccFiles: [pdf('gas.pdf')] });
  await c.ccAsk('Ingest 1 document for Kingsway House.', { origin: ORIGINS.UPLOAD_INGEST_PANEL, originParams: { building: 'Kingsway House', count: 1 } });
  assert.equal(sends.length, 1);
  assert.equal(sends[0].via, 'files');
  assert.deepEqual(sends[0].opts, { interactiveMigration: false, cmmsName: null, origin: 'upload.ingest_panel',
    originParams: { building: 'Kingsway House', count: 1 }, page: 'chat' });
});

test('editing a question re-asks it as an edit of the answer it replaces', async () => {
  c.setState({
    ccChat: [{ role: 'you', text: 'How many boilers?' }, { role: 'bot', text: 'Three.', turnId: 'turn-0123456789abcdef' }],
    ccEditIdx: 0, ccEditText: 'How many chillers?'
  });
  c.ccEditRun();
  await settle();
  assert.deepEqual(sends, [{ via: 'stream', q: 'How many chillers?',
    prov: { origin: 'chat.edit', params: { replaces_turn: 'turn-0123456789abcdef' }, page: 'chat' } }]);
});

test('the composer sends what was typed as typed, and in a support session as a support question', async () => {
  c.setState({ orchQuery: 'Which vendors are blocked right now?' });
  c.orchSubmitNow();
  await settle();
  c.setState({ sessionId: null, ccChat: [], supportPending: true, orchQuery: 'How do I add a building?' });
  c.orchSubmitNow();
  await settle();
  assert.deepEqual(sends.map((s) => [s.q, s.prov.origin]), [
    ['Which vendors are blocked right now?', 'chat.typed'],
    ['How do I add a building?', 'support.typed']
  ]);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs`
Expected: FAIL in all 7 tests — `prov` arrives `undefined` (`ccAsk` builds none), the upload options carry no `origin`, and the composer test throws `TypeError: Cannot read properties of undefined (reading 'origin')`.

- [ ] **Step 3: Implement**

Edits to `FE/src/logic/complianceLive.js`:

(a) Import — old:
```js
import { withoutCellDuplicates } from './complianceCell.js';
```
new:
```js
import { withoutCellDuplicates } from './complianceCell.js';
import { ORIGINS, pageOf } from './traceLabOrigins.js';
```

(b) `askScoped` — old:
```js
  askScoped(q) {
    if (this.chatView()) return this.ccAsk(q);
    return this.ask(q);
  },
```
new:
```js
  // `origin` and `params` say where the question came from (logic/traceLabOrigins.js); every caller
  // names one (test/traceLabOrigins.test.mjs). The answer page off the chat views sends nothing.
  askScoped(q, origin, params) {
    if (this.chatView()) return this.ccAsk(q, { origin: origin, originParams: params });
    return this.ask(q);
  },
```

(c) `ccAsk` builds `prov` first — old:
```js
    // Support answers some messages itself (logic/support.js): "I need support" opens it, a
    // request for a person opens the email to Plenum, and a message after a request was
    // resolved starts a new request. Before the context is read — it may be a support one now.
    if (typeof this.supIntercept === "function" && this.supIntercept(q)) return;
```
new:
```js
    // Where the question came from, for Trace Lab (logic/traceLabOrigins.js): analytics only, it
    // never changes routing, scope or the answer. The page is read first, before support or the
    // chat page can move the view. opts.origin has no default: every call site names one.
    const prov = { origin: (opts && opts.origin) || null, params: (opts && opts.originParams) || null, page: pageOf(this.state) || null };
    // Support answers some messages itself (logic/support.js): "I need support" opens it, a
    // request for a person opens the email to Plenum, and a message after a request was
    // resolved starts a new request. Before the context is read — it may be a support one now.
    if (typeof this.supIntercept === "function" && this.supIntercept(q)) return;
```

(d) The files send and the stream turn — old:
```js
            { interactiveMigration: files.some(isSpreadsheet),
              // Only meaningful alongside a spreadsheet, and only sent then.
              cmmsName: files.some(isSpreadsheet) ? cmmsName(this.state.mgCmms) : null })
        : await this.ccStreamTurn(q, context, ctrl);
```
new:
```js
            { interactiveMigration: files.some(isSpreadsheet),
              // Only meaningful alongside a spreadsheet, and only sent then.
              cmmsName: files.some(isSpreadsheet) ? cmmsName(this.state.mgCmms) : null,
              origin: prov.origin, originParams: prov.params, page: prov.page })
        : await this.ccStreamTurn(q, context, ctrl, prov);
```

(e) `ccStreamTurn` signature — old:
```js
  ccStreamTurn(q, context, ctrl) {
```
new:
```js
  ccStreamTurn(q, context, ctrl, prov) {
```

(f) The end of the `deepAgentsApi.stream(sid, q, context, {` call — old:
```js
          resolve({ session_id: sid, answer: "", tool_calls: [], success: true, _partial: partial, _trace: partial.trace.slice() });
        }
      });

      // The stop button closes the socket.
```
new:
```js
          resolve({ session_id: sid, answer: "", tool_calls: [], success: true, _partial: partial, _trace: partial.trace.slice() });
        }
      }, prov);

      // The stop button closes the socket.
```

(g) Both POST fallbacks (lines 1642 and 1652, the same line twice; replace both) — old:
```js
            deepAgentsApi.runStateful(q, sid, context, ctrl && ctrl.signal).then(resolve, reject);
```
new:
```js
            deepAgentsApi.runStateful(q, sid, context, ctrl && ctrl.signal, prov).then(resolve, reject);
```

(h) `ccEditRun` — old:
```js
  ccEditRun() {
    const i = this.state.ccEditIdx;
    const q = String(this.state.ccEditText || "").trim();
    if (i === null || i === undefined || !q) return;
    this.setState((p) => ({ ccChat: (p.ccChat || []).slice(0, i), ccEditIdx: null, ccEditText: "" }));
    // Let the truncation land before the new turn appends to the transcript.
    setTimeout(() => this.ccAsk(q), 0);
  },
```
new:
```js
  ccEditRun() {
    const i = this.state.ccEditIdx;
    const q = String(this.state.ccEditText || "").trim();
    if (i === null || i === undefined || !q) return;
    // The answer the edited question replaces, read before the transcript is cut: a bot entry
    // carries its turnId (Trace Lab provenance, chat.edit).
    const below = (this.state.ccChat || [])[i + 1];
    const replaces = below && below.role === "bot" && below.turnId ? String(below.turnId) : null;
    this.setState((p) => ({ ccChat: (p.ccChat || []).slice(0, i), ccEditIdx: null, ccEditText: "" }));
    // Let the truncation land before the new turn appends to the transcript.
    setTimeout(() => this.ccAsk(q, { origin: ORIGINS.CHAT_EDIT, originParams: replaces ? { replaces_turn: replaces } : null }), 0);
  },
```

(i) `orchSubmitNow` — old:
```js
    this.setState({ orchQuery: "" });
    this.askScoped(q);
  },
```
new:
```js
    this.setState({ orchQuery: "" });
    // In a support session the composer is the support composer (Trace Lab provenance).
    const inSupport = typeof this.supIsOn === "function" && this.supIsOn();
    this.askScoped(q, inSupport ? ORIGINS.SUPPORT_TYPED : ORIGINS.CHAT_TYPED);
  },
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs test/streamAnswer.test.mjs test/streamDraft.test.mjs test/chatBuilding.test.mjs test/aStagedDocumentIsSentAndShown.test.mjs test/chatMigration.test.mjs test/supportFlow.test.mjs test/rerunKeepsTheQuestion.test.mjs test/moduleDock.test.mjs`
Expected: PASS (existing chat tests still pass: the extra argument is optional everywhere).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/complianceLive.js`, `FE/test/traceLabChatOrigins.test.mjs`.

---

### Task 27: Typed questions, chips, Recent tasks, option cards and Support name their origin

**Files:**
- Modify: `FE/src/logic/renderVals.js:29` (import), `:427-428` and `:433-434` (Home bar), `:450` (pinned chips), `:1517-1518` and `:1526-1527` (page ask bar), `:1566` (ask-bar chips), `:2739` (Recent tasks), `:3476` (module asks), `:3509-3510` (energy asks)
- Modify: `FE/src/logic/instances.js:29` (import), `:323`, `:364`, `:366`
- Modify: `FE/src/logic/maintenance.js:11` (import), `:136` (reading cards), `:203` (chips), `:284-289` (`mxInspAsk`)
- Modify: `FE/src/logic/spacesLive.js:21` (import), `:289-293` (`spaceAskRun`)
- Modify: `FE/src/logic/choiceCards.js:16` (import), `:53` (`orchChoice`)
- Modify: `FE/src/logic/support.js:21` (import), `:348-353` (`supAskFromIndex`, also reached from `supAskCustom`)
- Test: `FE/test/traceLabChatOrigins.test.mjs` (append)

**Interfaces:**
- Consumes: `ORIGINS`, `originOf` (Task 24); `askScoped(q, origin, params)` (Task 26).
- Produces: the origins of spec "Trace capture" 2.1 ("Frontend: every send names an origin"): Home bar, page ask bar, Maintenance inspection box and a space's bar → `chat.typed`; the Support space's bar → `support.typed`; pinned chips → `chat.chip:home`; ask-bar chips → `chat.chip:<view>`; module asks → `chat.chip:module`; energy asks → `chat.chip:energy`; an instance's pinned and ask-bar chips → `chat.chip:instance`; the inspection chips and reading cards → `chat.chip:inspection`; Recent tasks → `chat.recent`; an option card's `ask` → `chat.choice_card`; a help-index question → `support.topic:<supTopic>` with `{ topic }`, else `support.typed`. `mxInspAsk(question, origin)` (origin defaults to `chat.typed`).

- [ ] **Step 1: Write the failing test**

Append to `FE/test/traceLabChatOrigins.test.mjs`:
```js

// ── each page's own sends name their origin (Task 27) ──────────────────────────────────

const { originOf } = await import('../src/logic/traceLabOrigins.js');
const { makeSession } = await import('../src/logic/sessions.js');

// What a page's control hands to askScoped / ccAsk, recorded instead of asked.
function capture(x) {
  const asked = [];
  x.askScoped = (q, origin, params) => { asked.push({ q: q, origin: origin === undefined ? null : origin, params: params === undefined ? null : params }); };
  x.ccAsk = (q, opts) => { asked.push({ q: q, origin: (opts && opts.origin) || null, params: (opts && opts.originParams) || null }); };
  return asked;
}

test('the Home bar and a page ask bar send what was typed as typed', () => {
  const asked = capture(c);
  c.setState({ view: 'home', query: 'How many boilers are at Kingsway House?' });
  c.renderVals().runQuery();
  c.setState({ query: 'Which vendors are blocked?' });
  c.renderVals().onKey({ key: 'Enter' });
  c.setState({ view: 'cc', pq: 'Which lapses void insurance?' });
  c.renderVals().pqRun();
  c.setState({ pq: 'What needs my approval today?' });
  c.renderVals().pqKey({ key: 'Enter' });
  assert.deepEqual(asked.map((a) => [a.q, a.origin]), [
    ['How many boilers are at Kingsway House?', 'chat.typed'], ['Which vendors are blocked?', 'chat.typed'],
    ['Which lapses void insurance?', 'chat.typed'], ['What needs my approval today?', 'chat.typed']
  ]);
});

test('a chip names the surface it sits on', () => {
  const asked = capture(c);
  c.setState({ view: 'home' });
  c.renderVals().pinned[0].run();
  c.setState({ view: 'vp' });
  c.renderVals().abChips[0].run();
  c.setState({ view: 'module', module: 'compliance' });
  c.renderVals().modAsks[0].run();
  c.setState({ view: 'module', module: 'energy' });
  const v = c.renderVals();
  v.modAsks[0].run();
  v.abChips[0].run();
  assert.deepEqual(asked.map((a) => a.origin), ['chat.chip:home', 'chat.chip:vp', 'chat.chip:module', 'chat.chip:energy', 'chat.chip:energy']);
});

test("an instance's chips say they are the instance's", () => {
  const asked = capture(c);
  c.setState({ role: 'user', account: Object.assign({}, ACCOUNT, { role: 'user' }), instance: 'ops', view: 'home' });
  c.renderVals().pinned[0].run();
  c.setState({ view: 'buildings' });
  c.renderVals().abChips[0].run();
  c.setState({ view: 'vp' });
  c.renderVals().abChips[0].run();
  assert.deepEqual(asked.map((a) => a.origin), ['chat.chip:instance', 'chat.chip:instance', 'chat.chip:instance']);
  assert.equal(asked[0].q, 'What needs my approval today?');
});

test('Recent tasks re-asks a past question as recent', () => {
  const asked = capture(c);
  c.setState({ view: 'chat', orchTask: null, sessions: [makeSession({ id: 's-old', title: 'Which vendors are blocked right now?', owner: ACCOUNT.email })] });
  c.renderVals().orchRecent[0].click();
  assert.deepEqual(asked, [{ q: 'Which vendors are blocked right now?', origin: 'chat.recent', params: null }]);
});

test('the inspection box is typed; its chips and reading cards name the inspection surface', () => {
  const asked = capture(c);
  c.setState({ view: 'module', module: 'ops', inspDraft: 'Which PPM contracts are behind plan?' });
  const real = c.mxModel.bind(c);
  c.mxModel = () => Object.assign({}, real(), {
    chips: [{ question: 'Which inspection recommendations were never actioned?' }],
    intelligence: [{ l: 'Repeat findings', n: 4, answerable: true, q: 'Which findings repeat across inspections?', tone: 'warn' }]
  });
  c.renderVals().mxInspRun();
  c.renderVals().mxInspChips[0].run();
  c.renderVals().mxInsights[0].ask();
  assert.deepEqual(asked.map((a) => [a.q, a.origin]), [
    ['Which PPM contracts are behind plan?', 'chat.typed'],
    ['Which inspection recommendations were never actioned?', 'chat.chip:inspection'],
    ['Which findings repeat across inspections?', 'chat.chip:inspection']
  ]);
});

test('a space bar is typed; the Support space bar asks a support question', () => {
  const asked = capture(c);
  c.openSupport = () => {};
  c.setState({ view: 'space', spaceKey: 'sp-1', spaceAsk: 'Which buildings are in this space?' });
  c.spaceAskRun();
  c.setState({ spaceKey: 'support', spaceAsk: 'How do I invite a colleague?' });
  c.spaceAskRun();
  assert.deepEqual(asked.map((a) => [a.q, a.origin]), [
    ['Which buildings are in this space?', 'chat.typed'], ['How do I invite a colleague?', 'support.typed']
  ]);
});

test('an option card asks its own text as a choice card', () => {
  const asked = capture(c);
  c.orchChoice({ title: 'CSV / Excel data migration', action: { kind: 'ask', text: 'Migrate my CSV export' } });
  assert.deepEqual(asked, [{ q: 'Migrate my CSV export', origin: 'chat.choice_card', params: null }]);
});

test('a help-index question carries its topic; with none open it is a typed support question', () => {
  const asked = capture(c);
  c.setState({ supTopic: 'ingest' });
  c.supAskFromIndex('Which file types can I upload?');
  c.setState({ supTopic: '' });
  c.supAskFromIndex('Is my data encrypted?');
  assert.deepEqual(asked, [
    { q: 'Which file types can I upload?', origin: 'support.topic:ingest', params: { topic: 'ingest' } },
    { q: 'Is my data encrypted?', origin: 'support.typed', params: null }
  ]);
  assert.equal(originOf(ORIGINS.SUPPORT_TOPIC, 'ingest'), asked[0].origin);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs`
Expected: the 7 Task 26 tests PASS; the 8 new tests FAIL — every recorded `origin` is `null` (the call sites pass none), e.g. `[ 'How many boilers are at Kingsway House?', null ]` against `'chat.typed'`.

- [ ] **Step 3: Implement**

Edits to `FE/src/logic/renderVals.js`:

(a) Import — old:
```js
import { scrubInternal } from './publicText.js';
```
new:
```js
import { scrubInternal } from './publicText.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(b) The Home bar, `onKey` and `runQuery` (two occurrences of the same two lines; replace both) — old:
```js
        this.setState({ query: "" });
        this.askScoped(q);
```
new:
```js
        this.setState({ query: "" });
        this.askScoped(q, ORIGINS.CHAT_TYPED);
```

(c) Pinned chips — old:
```js
          .map((p) => ({ label: p.label, run: () => this.askScoped(p.q) }));
```
new:
```js
          .map((p) => ({ label: p.label, run: () => this.askScoped(p.q, originOf(ORIGINS.CHAT_CHIP, "home")) }));
```

(d) The page ask bar, `pqKey` and `pqRun` (two occurrences; replace both) — old:
```js
        this.setState({ pq: "" });
        this.askScoped(q);
```
new:
```js
        this.setState({ pq: "" });
        this.askScoped(q, ORIGINS.CHAT_TYPED);
```

(e) Ask-bar chips — old:
```js
        return q.map((a) => ({ label: a, run: () => this.askScoped(a) }));
```
new:
```js
        return q.map((a) => ({ label: a, run: () => this.askScoped(a, originOf(ORIGINS.CHAT_CHIP, s.view)) }));
```

(f) Recent tasks — old:
```js
              click: () => (canAsk ? this.askScoped(question) : this.ask(question, q.k || null))
```
new:
```js
              click: () => (canAsk ? this.askScoped(question, ORIGINS.CHAT_RECENT) : this.ask(question, q.k || null))
```

(g) Module asks — old:
```js
      modAsks: mod ? mod.asks.map((a) => ({ label: a, run: () => this.askScoped(a) })) : [],
```
new:
```js
      modAsks: mod ? mod.asks.map((a) => ({ label: a, run: () => this.askScoped(a, originOf(ORIGINS.CHAT_CHIP, "module")) })) : [],
```

(h) Energy asks — old:
```js
        vals.modAsks = en.enAsks.map((a) => ({ label: a, run: () => this.askScoped(a) }));
        vals.abChips = en.enAsks.map((a) => ({ label: a, run: () => this.askScoped(a) }));
```
new:
```js
        vals.modAsks = en.enAsks.map((a) => ({ label: a, run: () => this.askScoped(a, originOf(ORIGINS.CHAT_CHIP, "energy")) }));
        vals.abChips = en.enAsks.map((a) => ({ label: a, run: () => this.askScoped(a, originOf(ORIGINS.CHAT_CHIP, "energy")) }));
```

Edits to `FE/src/logic/instances.js`:

(i) Import — old:
```js
import { t } from './constants.js';
```
new:
```js
import { t } from './constants.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(j) Pinned — old:
```js
    out.pinned = c.pinned.map((p) => ({ label: p[0], run: () => this.askScoped(p[1]) }));
```
new:
```js
    out.pinned = c.pinned.map((p) => ({ label: p[0], run: () => this.askScoped(p[1], originOf(ORIGINS.CHAT_CHIP, 'instance')) }));
```

(k) Buildings ask-bar chips — old:
```js
      out.abChips = c.asks.buildings.map((q) => ({ label: q, run: () => this.askScoped(q) }));
```
new:
```js
      out.abChips = c.asks.buildings.map((q) => ({ label: q, run: () => this.askScoped(q, originOf(ORIGINS.CHAT_CHIP, 'instance')) }));
```

(l) Vendors ask-bar chips — old:
```js
    if (s.view === 'vp' && c.asks) out.abChips = c.asks.vp.map((q) => ({ label: q, run: () => this.askScoped(q) }));
```
new:
```js
    if (s.view === 'vp' && c.asks) out.abChips = c.asks.vp.map((q) => ({ label: q, run: () => this.askScoped(q, originOf(ORIGINS.CHAT_CHIP, 'instance')) }));
```

Edits to `FE/src/logic/maintenance.js`:

(m) Import — old:
```js
import { fmtTime } from './complianceLive.js';
```
new:
```js
import { fmtTime } from './complianceLive.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(n) Reading cards — old:
```js
      ask: () => (c.answerable ? this.mxInspAsk(c.q) : this.flash(c.l + " — " + (c.s || "this database cannot answer that card."))),
```
new:
```js
      ask: () => (c.answerable ? this.mxInspAsk(c.q, originOf(ORIGINS.CHAT_CHIP, "inspection")) : this.flash(c.l + " — " + (c.s || "this database cannot answer that card."))),
```

(o) Chips — old:
```js
      mxInspChips: chips.map((c) => ({ label: c, run: () => this.mxInspAsk(c) })),
```
new:
```js
      mxInspChips: chips.map((c) => ({ label: c, run: () => this.mxInspAsk(c, originOf(ORIGINS.CHAT_CHIP, "inspection")) })),
```

(p) `mxInspAsk` — old:
```js
  mxInspAsk(question) {
    const q = String(question || "").trim();
    if (!q) return this.openInsp("");
    this.setState({ inspDraft: "" });
    return this.askScoped(q);
  },
```
new:
```js
  // `origin`: a chip or a reading card names itself; the box is typed (logic/traceLabOrigins.js).
  mxInspAsk(question, origin) {
    const q = String(question || "").trim();
    if (!q) return this.openInsp("");
    this.setState({ inspDraft: "" });
    return this.askScoped(q, origin || ORIGINS.CHAT_TYPED);
  },
```

Edits to `FE/src/logic/spacesLive.js`:

(q) Import — old:
```js
import { isStaleScope } from '../api/client.js';
```
new:
```js
import { isStaleScope } from '../api/client.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(r) `spaceAskRun` — old:
```js
      return q ? this.askScoped(q) : undefined;
    }
    if (!q) return this.ccOpenChat();
    this.setState({ spaceAsk: '' });
    this.askScoped(q);
```
new:
```js
      return q ? this.askScoped(q, ORIGINS.SUPPORT_TYPED) : undefined;
    }
    if (!q) return this.ccOpenChat();
    this.setState({ spaceAsk: '' });
    this.askScoped(q, ORIGINS.CHAT_TYPED);
```

Edits to `FE/src/logic/choiceCards.js`:

(s) Import — old:
```js
import { accountCanIngest } from './auth.js';
```
new:
```js
import { accountCanIngest } from './auth.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(t) `orchChoice` — old:
```js
    if (a.kind === 'ask') return this.askScoped(String(a.text || ''));
```
new:
```js
    if (a.kind === 'ask') return this.askScoped(String(a.text || ''), ORIGINS.CHAT_CHOICE_CARD);
```

Edits to `FE/src/logic/support.js`:

(u) Import — old:
```js
import { scrubInternal } from './publicText.js';
```
new:
```js
import { scrubInternal } from './publicText.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(v) `supAskFromIndex` — old:
```js
  supAskFromIndex(q) {
    const text = String(q || '').trim();
    if (!text) return;
    this.setState({ supAskedTopic: this.state.supTopic || '' });
    this.askScoped(text);
  },
```
new:
```js
  supAskFromIndex(q) {
    const text = String(q || '').trim();
    if (!text) return;
    const topic = this.state.supTopic || '';
    this.setState({ supAskedTopic: topic });
    // Trace Lab provenance (logic/traceLabOrigins.js): asked under the topic on screen.
    this.askScoped(text, topic ? originOf(ORIGINS.SUPPORT_TOPIC, topic) : ORIGINS.SUPPORT_TYPED, topic ? { topic: topic } : null);
  },
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs test/instances.test.mjs test/pinnedRuns.test.mjs test/moduleDock.test.mjs test/supportFlow.test.mjs test/support.test.mjs test/spacesLive.test.mjs test/noInternalNames.test.mjs`
Expected: PASS (`instances.test.mjs:390` and `pinnedRuns.test.mjs:25` record only the question, so the extra argument changes nothing for them).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/renderVals.js`, `FE/src/logic/instances.js`, `FE/src/logic/maintenance.js`, `FE/src/logic/spacesLive.js`, `FE/src/logic/choiceCards.js`, `FE/src/logic/support.js`, `FE/test/traceLabChatOrigins.test.mjs`.

---

### Task 28: App-written questions name their origin, and a guard that every send path names one

**Files:**
- Modify: `FE/src/logic/renderVals.js:2470` (ingest panel)
- Modify: `FE/src/logic/compliance.js:8` (import), `:624` (`ccCellAsk`)
- Modify: `FE/src/logic/hoistGraph.js:23` (import), `:185-188` (`hgAskMap`)
- Modify: `FE/src/logic/energy.js:17` (import), `:333-347` (`investigateLive`)
- Modify: `FE/src/logic/vendorHoist.js:38` (import), `:344-346` (`vhUploadAfterSave`)
- Modify: `FE/src/logic/migration.js:28` (import), `:596` (`mgStartFromChat`)
- Modify: `FE/src/logic/corrections.js:19` (import), `:244-249` (`crReanswer`)
- Modify: `FE/src/logic/ingestionLive.js:30` (import), `:290` (`ingRunLive`)
- Test: `FE/test/traceLabChatOrigins.test.mjs` (append), `FE/test/traceLabOrigins.test.mjs` (append: the guard)

**Interfaces:**
- Consumes: `ORIGINS`, `originOf`, `pageOf` (Task 24); `askScoped(q, origin, params)`, `ccAsk(q, opts)` (Task 26); `deepAgentsApi.runStatefulWithFiles(..., opts)` (Task 25); `capture(x)` (Task 27, same test file).
- Produces: matrix cell → `ui.matrix_cell:<state>` with `{ building, type, code, state }`; Compliance Graph map → `ui.hoist_graph`; Investigate → `ui.energy_investigate:anomaly|eui|unknown` with `{ kind, asset, building, type }` (anomaly) or `{ kind, building }`; ingest panel → `upload.ingest_panel` with `{ building, count, expect }`; Hoist vendors certificates → `upload.vendor_certificates` with `{ vendor, building, count }`; chat migration → `upload.migrate` with `{ count, cmms }`; correction re-answer → `correction.reanswer` with `{ corrects_turn }`; ingestion check → `upload.ingestion_check` with the page. The guard `test/traceLabOrigins.test.mjs` "every send path names where its question came from" (spec "Frontend: every send names an origin", guard steps 1-2; step 3 is `traceLabOriginsWire.test.mjs`).

- [ ] **Step 1: Write the failing test**

Append to `FE/test/traceLabChatOrigins.test.mjs`:
```js

// ── questions the app writes: cells, the graph, Investigate, uploads, corrections (Task 28) ──

test('a matrix cell names its state, building, type and code', () => {
  const asked = capture(c);
  c.ccData = () => ({
    mx: { 'Kingsway House': ['risk'] }, mxTypes: ['Gas Safety Certificate'], mxCodes: ['GAS'], mxLabel: { risk: 'Lapsed' },
    packTypes: {}, certs: [], vendors: []
  });
  c.ccCellAsk('Kingsway House', 0);
  assert.deepEqual(asked, [{
    q: 'Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?',
    origin: 'ui.matrix_cell:risk',
    params: { building: 'Kingsway House', type: 'Gas Safety Certificate', code: 'GAS', state: 'risk' }
  }]);
});

test('the Compliance Graph map question says it is the graph', () => {
  const asked = capture(c);
  c.hgAskMap();
  assert.equal(asked[0].origin, 'ui.hoist_graph');
  assert.match(asked[0].q, /^Map each regulated area of my buildings to its compliance obligations:/);
});

test('Investigate names which kind wrote the question, with the asset or building', () => {
  const asked = capture(c);
  c.investigateLive('anomaly', { asset: 'AHU-1', building: 'Kingsway House', type: 'Baseload drift', impact: '£1,200' });
  c.investigateLive('building', { name: 'Kingsway House', euiN: 241.2, benchN: 180 });
  c.investigateLive('building', { name: 'Harbour Point' });
  assert.deepEqual(asked.map((a) => [a.origin, a.params]), [
    ['ui.energy_investigate:anomaly', { kind: 'anomaly', asset: 'AHU-1', building: 'Kingsway House', type: 'Baseload drift' }],
    ['ui.energy_investigate:eui', { kind: 'eui', building: 'Kingsway House' }],
    ['ui.energy_investigate:unknown', { kind: 'unknown', building: 'Harbour Point' }]
  ]);
  assert.equal(asked[1].q, 'Why is Kingsway House at 241 kWh/m² against a reference of 180, and how much of that gap can I act on?');
});

test('the ingest panel names the building, the count and what the register lacks', () => {
  const asked = capture(c);
  c.setState({ flow: 'ingest', declFor: 'Kingsway House', ingExpect: 'Gas Safety Certificate', ccFiles: [pdf('gas.pdf')] });
  c.renderVals().iRun();
  assert.deepEqual(asked, [{
    q: 'Ingest 1 document for Kingsway House. It is the Gas Safety Certificate the register has no record of.',
    origin: 'upload.ingest_panel',
    params: { building: 'Kingsway House', count: 1, expect: 'Gas Safety Certificate' }
  }]);
});

test("a vendor's certificates upload names the vendor, the building and the count", async () => {
  const asked = capture(c);
  c.vhCountOnRecord = async () => {};
  c.iBuildingIdFor = (name) => (name === 'Kingsway House' ? 'b-kw' : null);
  const own = pdf('insurance.pdf');
  const site = pdf('gas-record.pdf');
  await c.vhUploadAfterSave({ name: 'Crown Gas Services' }, { created: true, vendorId: 'v-new' }, [own, site],
    { ['gas-record.pdf:' + site.size]: 'Kingsway House' });
  assert.deepEqual(asked.map((a) => [a.origin, a.params]), [
    ['upload.vendor_certificates', { vendor: 'Crown Gas Services', building: null, count: 1 }],
    ['upload.vendor_certificates', { vendor: 'Crown Gas Services', building: 'Kingsway House', count: 1 }]
  ]);
});

test('a migration started from the chat names how many spreadsheets and the source system', async () => {
  const asked = capture(c);
  c.setState({ mgCmms: 'Fiix', ccFiles: [new File(['a,b\n1,2'], 'assets.csv', { type: 'text/csv' }), new File(['x'], 'sites.xlsx'), pdf('notes.pdf')] });
  await c.mgStartFromChat();
  assert.deepEqual(asked, [{ q: 'Migrate assets.csv, sites.xlsx', origin: 'upload.migrate', params: { count: 2, cmms: 'Fiix' } }]);
});

test('a re-answer names the answer it corrects', () => {
  const asked = capture(c);
  c.setState({ crOpen: { turnId: 'turn-0123456789abcdef', mode: 'query', spanId: null }, crNote: 'use May, not April' });
  c.crReanswer();
  assert.deepEqual(asked, [{
    q: 'Re-answer the previous question with this correction: Use May, not April. Keep the same building and period unless the correction changes them.',
    origin: 'correction.reanswer',
    params: { corrects_turn: 'turn-0123456789abcdef' }
  }]);
  assert.equal(c.state.crOpen, null);
});

test('the ingestion check names itself and the page it ran on', async () => {
  let opts = null;
  deepAgentsApi.runStatefulWithFiles = async (message, sid, context, files, signal, bid, o) => {
    opts = o;
    return { validation_cases: [{ ok: true, id: 'case-1', verdict: 'matched', question: 'Clear to ingest.' }] };
  };
  c.setState({ view: 'ingestion', axBldsLive: [{ id: 'b-rc', name: 'Riverside Court', building_code: 'B-002' }], ingOn: true, ingB: 'Riverside Court', ingDoc: 0 });
  await c.ingRunLive();
  clearInterval(c._ingT);
  assert.deepEqual(opts, { origin: 'upload.ingestion_check', page: 'ingestion' });
});
```

Append to `FE/test/traceLabOrigins.test.mjs`:
```js

// ── every send path names an origin (Task 28) ──────────────────────────────────────────
//
// Walks src/ with its comments stripped (esbuild, as test/noInternalNames.test.mjs does) and finds
// every call that puts a question to the orchestrator. Each must name its origin from ORIGINS
// (ORIGINS.X or originOf(...)), or be one of the forwarders in complianceLive.js that hand on the
// origin they were given. A new send without an origin fails here, not in the trace.
import { readdirSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { transformSync } from 'esbuild';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SRC = join(ROOT, 'src');
const LOADER = { '.js': 'jsx', '.jsx': 'jsx', '.ts': 'ts', '.tsx': 'tsx', '.mjs': 'js' };
const SEND = /\.(ccAsk|askScoped|ccStreamTurn)\(|deepAgentsApi\.(stream|run|runStateful|runStatefulWithFiles)\(/g;
const NAMES_ORIGIN = /\bORIGINS\.[A-Z_]+|\boriginOf\(/;
// [file, call, what its arguments must show]: askScoped -> ccAsk; ccAsk -> the upload and
// ccStreamTurn; ccStreamTurn -> the socket and both POST fallbacks.
const FORWARDERS = [
  ['src/logic/complianceLive.js', 'ccAsk', /\borigin\b[\s\S]*\boriginParams:\s*params\b/],
  ['src/logic/complianceLive.js', 'runStatefulWithFiles', /\borigin:\s*prov\.origin\b[\s\S]*\bpage:\s*prov\.page\b/],
  ['src/logic/complianceLive.js', 'ccStreamTurn', /,\s*prov\s*$/],
  ['src/logic/complianceLive.js', 'stream', /,\s*prov\s*$/],
  ['src/logic/complianceLive.js', 'runStateful', /,\s*prov\s*$/]
];
// The files that send today; each must still be found, so a broken pattern cannot pass empty.
const SENDERS = ['src/api/documents.js', 'src/logic/choiceCards.js', 'src/logic/compliance.js', 'src/logic/complianceLive.js',
  'src/logic/corrections.js', 'src/logic/energy.js', 'src/logic/hoistGraph.js', 'src/logic/ingestionLive.js',
  'src/logic/instances.js', 'src/logic/maintenance.js', 'src/logic/migration.js', 'src/logic/renderVals.js',
  'src/logic/spacesLive.js', 'src/logic/support.js', 'src/logic/vendorHoist.js'];

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (LOADER[extname(p)]) out.push(p);
  }
  return out;
}

function stripComments(file) {
  return transformSync(readFileSync(file, 'utf8'),
    { loader: LOADER[extname(file)], legalComments: 'none', minifyWhitespace: true, jsx: 'preserve', format: 'esm' }).code;
}

// The argument text of the call whose "(" is at `open`: balanced parentheses, strings skipped.
function argsAt(code, open) {
  let depth = 0;
  for (let i = open; i < code.length; i += 1) {
    const ch = code[i];
    if (ch === '"' || ch === "'" || ch === '`') {
      for (i += 1; i < code.length && code[i] !== ch; i += 1) if (code[i] === '\\') i += 1;
      continue;
    }
    if (ch === '(') depth += 1;
    else if (ch === ')') { depth -= 1; if (depth === 0) return code.slice(open + 1, i); }
  }
  throw new Error('unbalanced call at ' + open);
}

function sends() {
  const out = [];
  for (const file of walk(SRC, [])) {
    const rel = relative(ROOT, file).split('\\').join('/');
    const code = stripComments(file);
    SEND.lastIndex = 0;
    let m;
    while ((m = SEND.exec(code))) {
      out.push({ file: rel, call: m[1] || m[2], args: argsAt(code, m.index + m[0].length - 1).trim() });
    }
  }
  return out;
}

test('every send path names where its question came from', () => {
  const all = sends();
  const files = new Set(all.map((s) => s.file));
  assert.deepEqual(SENDERS.filter((f) => !files.has(f)), [], 'a file that sends was not found: the pattern is broken');
  assert.ok(all.length >= 35, 'found ' + all.length + ' sends, expected at least 35');
  const bad = all.filter((s) => !NAMES_ORIGIN.test(s.args)
    && !FORWARDERS.some(([f, call, rx]) => f === s.file && call === s.call && rx.test(s.args)));
  assert.deepEqual(bad.map((s) => s.file + ' ' + s.call + '(' + s.args.slice(0, 120) + ')'), []);
});

test('the forwarders are the only sends that name no origin of their own', () => {
  const forwarded = sends().filter((s) => !NAMES_ORIGIN.test(s.args)).map((s) => s.file + ' ' + s.call).sort();
  assert.deepEqual(forwarded, [
    'src/logic/complianceLive.js ccAsk', 'src/logic/complianceLive.js ccStreamTurn',
    'src/logic/complianceLive.js runStateful', 'src/logic/complianceLive.js runStateful',
    'src/logic/complianceLive.js runStatefulWithFiles', 'src/logic/complianceLive.js stream'
  ]);
});
```
(The 35 sends: `complianceLive.js` 8 — the forwarders, `ccEditRun`, `orchSubmitNow`; `renderVals.js` 11; `instances.js` 3; `spacesLive.js` 2; one each in the other 11 files.)

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs test/traceLabOrigins.test.mjs`
Expected: the 8 new chat tests FAIL (each recorded `origin` is `null`; the ingestion check sends no options), and "every send path names where its question came from" FAILS listing the eight sites this task fixes: `renderVals.js askScoped("Ingest "+n+…)`, `compliance.js ccAsk(q,{nextSteps:…})`, `hoistGraph.js ccAsk(q)`, `energy.js askScoped(q)`, `vendorHoist.js ccAsk(g.building?…)`, `migration.js ccAsk("Migrate "+names)`, `corrections.js ccAsk("Re-answer …")`, `ingestionLive.js runStatefulWithFiles("Validate and ingest this document.",…)`; "the forwarders are the only sends…" FAILS with the same eight beside the six forwarders. The Task 24, 26 and 27 tests still pass.

- [ ] **Step 3: Implement**

(a) `FE/src/logic/renderVals.js`, the ingest panel — old:
```js
        this.askScoped("Ingest " + n + (n === 1 ? " document" : " documents") + " for " + s.declFor + "." + expect);
```
new:
```js
        this.askScoped("Ingest " + n + (n === 1 ? " document" : " documents") + " for " + s.declFor + "." + expect,
          ORIGINS.UPLOAD_INGEST_PANEL, { building: s.declFor, count: n, expect: s.ingExpect || null });
```

(b) `FE/src/logic/compliance.js`, import — old:
```js
import { cellSubject, cellQuestion, cellActions, cellContext, cellStepsIntro, visitRequestDraft } from './complianceCell.js';
```
new:
```js
import { cellSubject, cellQuestion, cellActions, cellContext, cellStepsIntro, visitRequestDraft } from './complianceCell.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(c) `ccCellAsk` — old:
```js
    return this.ccAsk(q, { nextSteps: { text: cellStepsIntro(subject), choices: actions, code: subject.code } });
```
new:
```js
    return this.ccAsk(q, {
      nextSteps: { text: cellStepsIntro(subject), choices: actions, code: subject.code },
      // Trace Lab provenance (logic/traceLabOrigins.js): the app wrote this question from a cell.
      origin: originOf(ORIGINS.UI_MATRIX_CELL, subject.state),
      originParams: { building: subject.building, type: subject.type, code: subject.code, state: subject.state }
    });
```

(d) `FE/src/logic/hoistGraph.js`, import — old:
```js
import { instanceOf, usesInstances } from './instances.js';
```
new:
```js
import { instanceOf, usesInstances } from './instances.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(e) `hgAskMap` — old:
```js
    this.ccChatReset();
    this.setState({ flow: null, flowDone: '' });
    return this.ccAsk(q);
  },
```
new:
```js
    this.ccChatReset();
    this.setState({ flow: null, flowDone: '' });
    return this.ccAsk(q, { origin: ORIGINS.UI_HOIST_GRAPH });
  },
```

(f) `FE/src/logic/energy.js`, import — old:
```js
import { moneyGBP, IMPLEMENTED_RULE_IDS } from './energyLive.js';
```
new:
```js
import { moneyGBP, IMPLEMENTED_RULE_IDS } from './energyLive.js';
import { ORIGINS, originOf } from './traceLabOrigins.js';
```

(g) `investigateLive` — old:
```js
  investigateLive(kind, o) {
    let q;
    if (kind === "anomaly") {
      const where = o.building && o.building !== "Unattributed" ? " at " + o.building : "";
      q = "Why is " + o.asset + where + " showing a " + String(o.type).toLowerCase() +
        (o.impact && o.impact !== "—" ? " worth " + o.impact + " a year" : "") + ", and what should I do about it?";
    } else {
      const hasEui = typeof o.euiN === "number" && typeof o.benchN === "number";
      q = hasEui
        ? "Why is " + o.name + " at " + Math.round(o.euiN) + " kWh/m² against a reference of " + Math.round(o.benchN) + ", and how much of that gap can I act on?"
        : "What do we know about " + o.name + "'s energy performance, and what is missing to assess it properly?";
    }
    this.setState({ flow: null, flowDone: "" });
    this.askScoped(q);
  },
```
new:
```js
  investigateLive(kind, o) {
    let q;
    // Trace Lab provenance (logic/traceLabOrigins.js): which Investigate wrote the question.
    let variant;
    let params;
    if (kind === "anomaly") {
      const where = o.building && o.building !== "Unattributed" ? " at " + o.building : "";
      q = "Why is " + o.asset + where + " showing a " + String(o.type).toLowerCase() +
        (o.impact && o.impact !== "—" ? " worth " + o.impact + " a year" : "") + ", and what should I do about it?";
      variant = "anomaly";
      params = { kind: variant, asset: o.asset || null, building: o.building || null, type: o.type || null };
    } else {
      const hasEui = typeof o.euiN === "number" && typeof o.benchN === "number";
      q = hasEui
        ? "Why is " + o.name + " at " + Math.round(o.euiN) + " kWh/m² against a reference of " + Math.round(o.benchN) + ", and how much of that gap can I act on?"
        : "What do we know about " + o.name + "'s energy performance, and what is missing to assess it properly?";
      variant = hasEui ? "eui" : "unknown";
      params = { kind: variant, building: o.name || null };
    }
    this.setState({ flow: null, flowDone: "" });
    this.askScoped(q, originOf(ORIGINS.UI_ENERGY_INVESTIGATE, variant), params);
  },
```

(h) `FE/src/logic/vendorHoist.js`, import — old:
```js
import { connectorApi } from '../api/connector.js';
```
new:
```js
import { connectorApi } from '../api/connector.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(i) The certificates upload in `vhUploadAfterSave` — old:
```js
          await this.ccAsk(g.building
            ? 'Ingest ' + n + ' for ' + g.building + '. They are certificates for the building, and they name ' + name + ' as the contractor.'
            : 'Ingest ' + n + ' for the vendor ' + name + '. They are ' + name + "'s own certificates — accreditations, registrations or insurance — not a building's.");
```
new:
```js
          await this.ccAsk(g.building
            ? 'Ingest ' + n + ' for ' + g.building + '. They are certificates for the building, and they name ' + name + ' as the contractor.'
            : 'Ingest ' + n + ' for the vendor ' + name + '. They are ' + name + "'s own certificates — accreditations, registrations or insurance — not a building's.",
          { origin: ORIGINS.UPLOAD_VENDOR_CERTIFICATES, originParams: { vendor: name, building: g.building || null, count: g.files.length } });
```

(j) `FE/src/logic/migration.js`, import — old:
```js
import { currentOrgId, isStaleScope } from '../api/client.js';
```
new:
```js
import { currentOrgId, isStaleScope } from '../api/client.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(k) `mgStartFromChat` — old:
```js
    await this.ccAsk('Migrate ' + names);
```
new:
```js
    await this.ccAsk('Migrate ' + names, { origin: ORIGINS.UPLOAD_MIGRATE, originParams: { count: staged.length, cmms: cmmsName(this.state.mgCmms) } });
```

(l) `FE/src/logic/corrections.js`, import — old:
```js
import { extractComplianceAnswer } from './complianceLive.js';
```
new:
```js
import { extractComplianceAnswer } from './complianceLive.js';
import { ORIGINS } from './traceLabOrigins.js';
```

(m) `crReanswer` — old:
```js
  crReanswer() {
    const text = correctionText(this.crEdits(), this.crContext());
    if (!text) { this.setState({ crMsg: 'Choose or write a correction first.' }); return; }
    this.setState({ crOpen: null });
    this.ccAsk('Re-answer the previous question with this correction: ' + text + ' Keep the same building and period unless the correction changes them.');
  },
```
new:
```js
  crReanswer() {
    const text = correctionText(this.crEdits(), this.crContext());
    if (!text) { this.setState({ crMsg: 'Choose or write a correction first.' }); return; }
    // The answer being corrected, read before the drawer closes (Trace Lab provenance).
    const o = this.state.crOpen;
    this.setState({ crOpen: null });
    this.ccAsk('Re-answer the previous question with this correction: ' + text + ' Keep the same building and period unless the correction changes them.',
      { origin: ORIGINS.CORRECTION_REANSWER, originParams: o && o.turnId ? { corrects_turn: String(o.turnId) } : null });
  },
```

(n) `FE/src/logic/ingestionLive.js`, import — old:
```js
import { checksSummary, OUTCOME_LABEL } from './auditLive.js';
```
new:
```js
import { checksSummary, OUTCOME_LABEL } from './auditLive.js';
import { ORIGINS, pageOf } from './traceLabOrigins.js';
```

(o) `ingRunLive` — old:
```js
      const r = await deepAgentsApi.runStatefulWithFiles("Validate and ingest this document.", sid, null, [file], undefined, bid);
```
new:
```js
      const r = await deepAgentsApi.runStatefulWithFiles("Validate and ingest this document.", sid, null, [file], undefined, bid,
        { origin: ORIGINS.UPLOAD_INGESTION_CHECK, page: pageOf(this.state) });
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabChatOrigins.test.mjs test/traceLabOrigins.test.mjs test/complianceCellFlow.test.mjs test/complianceCell.test.mjs test/vendorHoist.test.mjs test/chatMigration.test.mjs test/migration.test.mjs test/ingestionLive.test.mjs test/ingest.test.mjs test/rerunKeepsTheQuestion.test.mjs test/noInternalNames.test.mjs`
Expected: PASS. (`ingestionLive.test.mjs:198` reads the form's `building_id`, `session_id`, `message` and `files`; the two new form fields do not disturb them.)

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/renderVals.js`, `FE/src/logic/compliance.js`, `FE/src/logic/hoistGraph.js`, `FE/src/logic/energy.js`, `FE/src/logic/vendorHoist.js`, `FE/src/logic/migration.js`, `FE/src/logic/corrections.js`, `FE/src/logic/ingestionLive.js`, `FE/test/traceLabChatOrigins.test.mjs`, `FE/test/traceLabOrigins.test.mjs`.

---

### Task 29: P8 — `run_da_tests.sh` carries the migrations and the scratch URL; testenv and local-db may apply

**Files:**
- Modify: `/Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh:5-6` (header), `:13` (the helper's folder), `:15` (what is copied), `:27` (what is passed). Outside the repository; exact-string edits only.
- Modify: `docker-compose.testenv.yml:320-323` (the `svc-deepagents` environment block at :312-322)
- Modify: `docker-compose.local-db.yml:85-88` (the `svc-deepagents` environment block)

**Interfaces:**
- Consumes: `TRACE_LAB_TEST_DB_URL` and the `tracelab_scratch` guard (Tasks 2 and 9); `migrate.schema_mode()` and `DEFAULT_APPLY_DATABASES = "hoistra,tracelab_scratch"` (Task 2), which already allow `apply` on both throwaway databases (both are named `hoistra`: docker-compose.testenv.yml:55, docker-compose.local-db.yml:38).
- Produces: P8 done (spec Prerequisites; "Data model" 1.8 and 1.9). `sh run_da_tests.sh <label> <tests>` copies `migrations/` when it exists and passes `TRACE_LAB_TEST_DB_URL` through; the testenv and local-db `svc-deepagents` get `TRACE_LAB_SCHEMA: apply`. No other compose file sets it.

Line 13 of the script today names a scratchpad folder of a session that has ended (`/private/tmp/claude-501/-Users-hussain-Desktop-hoist/cad28849-0984-40a5-9754-1701471feb3c/scratchpad`, which no longer exists), so the script stops at line 18 before any test runs. It gets a fresh `mktemp -d` folder per run instead (never removed, like the container folders).

- [ ] **Step 1: Write the failing test**

The checks (run from `/Users/hussain/Desktop/hoist/Hoistra_Plenum`, with the scratch container running):
```sh
sh -n /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh && echo syntax-ok
grep -c "migrations pyproject.toml" /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh                       # want 1
grep -c 'TRACE_LAB_TEST_DB_URL="${TRACE_LAB_TEST_DB_URL:-}"' /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh   # want 1
grep -c "cad28849" /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh                                        # want 0
TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch \
  sh /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh p8 tests/test_trace_lab_migrate_sql.py
docker compose -f docker-compose.testenv.yml config svc-deepagents | grep -n "TRACE_LAB_SCHEMA"                  # want: TRACE_LAB_SCHEMA: apply
docker compose -f docker-compose.single-url.local.yml -f docker-compose.dev-ports.yml -f docker-compose.local-db.yml \
  config svc-deepagents | grep -n "TRACE_LAB_SCHEMA"                                                             # want: TRACE_LAB_SCHEMA: apply
grep -ln "TRACE_LAB_SCHEMA" docker-compose*.yml                                                                  # want: the two files only
```
(`config` renders the files without starting anything; the `grep` keeps every other variable, secrets included, off the screen.)

- [ ] **Step 2: Run it and see it fail**

Run the block above.
Expected: `syntax-ok`; the three counts print `0`, `0`, `1`; the script run stops with `.../cad28849-.../scratchpad/eval-dbcheck.py: No such file or directory` (were that folder ever recreated, the SQL tests would all be SKIPPED instead, because neither `migrations/` nor the URL reaches the container); both `config | grep` lines print nothing; the last `grep -ln` prints nothing.

- [ ] **Step 3: Implement**

Edits to `/Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh`:

(a) Header — old:
```sh
# pytest does not start unless the resolved URL is that address. EVAL_TEST_DB_URL (the throwaway
# eval_scratch database) is passed through for the opt-in SQL tests.
```
new:
```sh
# pytest does not start unless the resolved URL is that address. EVAL_TEST_DB_URL (the throwaway
# eval_scratch database) and TRACE_LAB_TEST_DB_URL (Trace Lab's throwaway tracelab_scratch database)
# are passed through for the opt-in SQL tests; migrations/ is copied so those tests can apply the schema.
```

(b) The helper's folder — old:
```sh
S=/private/tmp/claude-501/-Users-hussain-Desktop-hoist/cad28849-0984-40a5-9754-1701471feb3c/scratchpad
```
new:
```sh
# A fresh host folder for the database-check helper below, one per run; nothing removes it.
S=$(mktemp -d "${TMPDIR:-/tmp}/run-da-tests.XXXXXX")
```

(c) What is copied — old:
```sh
for d in src tests skills pyproject.toml; do docker cp $D/svc-deepagents/$d $C:$R/$D/svc-deepagents/$d >/dev/null; done
```
new:
```sh
for d in src tests skills migrations pyproject.toml; do
  if [ -e "$D/svc-deepagents/$d" ]; then docker cp "$D/svc-deepagents/$d" "$C:$R/$D/svc-deepagents/$d" >/dev/null; fi
done
```

(d) What is passed — old:
```sh
  -e EVAL_TEST_DB_URL="${EVAL_TEST_DB_URL:-}" $C \
```
new:
```sh
  -e EVAL_TEST_DB_URL="${EVAL_TEST_DB_URL:-}" -e TRACE_LAB_TEST_DB_URL="${TRACE_LAB_TEST_DB_URL:-}" $C \
```

(e) `docker-compose.testenv.yml` (shared file: exact-string edit) — old:
```yaml
      OPERATIONS_INTELLIGENCE_BASE_URL: http://svc-operations-intelligence:8009
      PORT: "8008"
      DEBUG: "false"
    # --root-path: the gateway serves this behind a prefix, and without it every
```
new:
```yaml
      OPERATIONS_INTELLIGENCE_BASE_URL: http://svc-operations-intelligence:8009
      PORT: "8008"
      DEBUG: "false"
      # Trace Lab: this database is testenv's own throwaway `hoistra`, so the service applies its
      # schema at startup (svc-deepagents src/trace_lab; never set on a shared database).
      TRACE_LAB_SCHEMA: apply
    # --root-path: the gateway serves this behind a prefix, and without it every
```

(f) `docker-compose.local-db.yml` (shared file: exact-string edit) — old:
```yaml
  svc-deepagents:
    extra_hosts: *host_gateway
    environment:
      <<: *db_env
```
new:
```yaml
  svc-deepagents:
    extra_hosts: *host_gateway
    environment:
      <<: *db_env
      # Trace Lab: the local throwaway `hoistra` database, so the service applies its schema at
      # startup (svc-deepagents src/trace_lab; never set on a shared database).
      TRACE_LAB_SCHEMA: apply
```

Do not recreate the testenv or local-db containers here: the setting takes effect the next time Hussain brings those stacks up. (After that, `docker exec` test runs in `hoistra-testenv-svc-deepagents-1` inherit `TRACE_LAB_SCHEMA=apply`; the only tests that read the mode, `test_trace_lab_startup.py` and `test_unknown_mode_falls_back_to_verify`, set or delete it themselves.)

- [ ] **Step 4: Run it and see it pass**

Run the Step 1 block again.
Expected: `syntax-ok`; counts `1`, `1`, `0`; the script prints `tests reach database: 127.0.0.1:1/t` and then the SQL tests PASS (none skipped); both `config | grep` lines print `TRACE_LAB_SCHEMA: apply`; `grep -ln` prints `docker-compose.local-db.yml` and `docker-compose.testenv.yml`. Also run `sh /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh p8b tests/test_trace_lab_migrate_sql.py` without the URL: every test SKIPPED.

- [ ] **Step 5: Checkpoint** — leave uncommitted (the script is outside the repository); task log: `/Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh`, `docker-compose.testenv.yml`, `docker-compose.local-db.yml`; note "P8 done".

---

### Task 30: Milestone gate — every suite green, the build passes, no DDL at boot in verify mode

**Files:**
- Create (scratchpad, not the repository): `tlgate.sh`, the milestone's static checks.
- No repository change, unless a check fails: the fix then goes to the task that owns the file, and this task restarts at Step 2.

**Interfaces:**
- Consumes: everything Tasks 1-29 produced; the baselines recorded in "Before you start".
- Produces: the milestone report in the task log (counts against the baselines, the files changed, the behaviour changes for Bala) that Task 31 starts from.

- [ ] **Step 1: Write the failing test**

Save `tlgate.sh` to the scratchpad:
```sh
#!/bin/sh
# Usage: sh tlgate.sh   Static checks of Trace Lab M0: one OK or FAIL line per check; exit 1 on any FAIL.
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
DA=apps/backend/cafm-connector-service-final/svc-deepagents
fail=0
ok() { echo "OK   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# 1. Startup never runs DDL in verify mode: apply_pending is reached only from _check_schema's apply
#    branch, after apply_refusal, and the lifespan calls nothing but trace_lab.startup().
n=$(grep -c "apply_pending(" $DA/src/trace_lab/__init__.py)
mode=$(grep -n 'if migrate.schema_mode() == "apply":' $DA/src/trace_lab/__init__.py | cut -d: -f1)
refusal=$(grep -n "reason = migrate.apply_refusal(" $DA/src/trace_lab/__init__.py | cut -d: -f1)
call=$(grep -n "apply_pending(" $DA/src/trace_lab/__init__.py | cut -d: -f1)
if [ "$n" = 1 ] && [ -n "$mode" ] && [ -n "$refusal" ] && [ "$mode" -lt "$refusal" ] && [ "$refusal" -lt "$call" ]; then
  ok "apply_pending only behind TRACE_LAB_SCHEMA=apply and apply_refusal"
else
  bad "apply_pending reachable outside the apply branch ($n call(s))"
fi
if grep -nE "apply_pending|exec_driver_sql|plenum_tracelab" $DA/src/api/main.py; then bad "main.py runs Trace Lab SQL itself"; else ok "main.py only calls trace_lab.startup()"; fi
if grep -nE "\b(CREATE|ALTER|DROP)\b" $DA/src/trace_lab/__init__.py $DA/src/trace_lab/db.py $DA/src/trace_lab/jobs.py \
     $DA/src/trace_lab/provenance.py $DA/src/trace_lab/guards.py $DA/src/api/routes/trace_lab_status.py; then
  bad "DDL text outside migrate.py"
else
  ok "no DDL text outside migrate.py"
fi
if [ "$(grep -c 'SET TRANSACTION READ ONLY' $DA/src/trace_lab/migrate.py)" -ge 1 ]; then ok "verify runs READ ONLY"; else bad "verify is not READ ONLY"; fi
if [ "$(grep -c '^DEFAULT_APPLY_DATABASES = "hoistra,tracelab_scratch"$' $DA/src/trace_lab/migrate.py)" = 1 ]; then ok "apply allow-list is hoistra,tracelab_scratch"; else bad "apply allow-list changed"; fi
files=$(grep -l "TRACE_LAB_SCHEMA" docker-compose*.yml | sort | tr '\n' ' ')
if [ "$files" = "docker-compose.local-db.yml docker-compose.testenv.yml " ]; then ok "TRACE_LAB_SCHEMA set only on the two throwaway databases"; else bad "TRACE_LAB_SCHEMA set in: $files"; fi

# 2. 0001 touches only plenum_tracelab and is not pinned yet (Task 31 pins it after Hussain applies it).
if grep -nE "plenum_cafm|GRANT|REVOKE|OWNER TO|DROP" $DA/migrations/trace_lab/0001_foundation.sql; then bad "0001 names another schema, drops or grants"; else ok "0001 creates objects only in plenum_tracelab"; fi
if [ "$(grep -c '^SHIPPED_CHECKSUMS: dict\[int, str\] = {}$' $DA/src/trace_lab/migrate.py)" = 1 ]; then ok "SHIPPED_CHECKSUMS still empty (0001 not applied yet)"; else bad "SHIPPED_CHECKSUMS changed before Hussain applied 0001"; fi
v=$(python3 -c "import json; d = json.load(open('$DA/migrations/trace_lab/catalog.json')); print(d['version'], sorted(d['tables']), d['server_version_num'] // 10000)")
if [ "$v" = "1 ['jobs', 'schema_migrations', 'turn_meta'] 16" ]; then ok "catalog.json: version 1, three tables, Postgres 16"; else bad "catalog.json: $v"; fi

# 3. The scratch guard requires the one name.
if [ "$(grep -c 'SCRATCH_DATABASES = frozenset({"tracelab_scratch"})' $DA/src/trace_lab/migrate.py)" = 1 ]; then ok "scratch guard requires tracelab_scratch"; else bad "scratch guard accepts another name"; fi
exit $fail
```
(Postgres 16 is hoistra_test's major today, per memory psql-connection-recipe; Task 31 Step 2 reads it again before anything is applied.)

- [ ] **Step 2: Run it and see it fail**

Run: `sh tlgate.sh`
Expected: this task adds no behaviour, so nothing should fail: ten `OK` lines and exit status 0. A `FAIL` line is a defect in the task that owns the named file: fix it there, re-run that task's tests, then re-run this step.

- [ ] **Step 3: Implement**

No code. Run every suite (start the scratch container first if `docker ps --filter name=tl-scratch-pg` shows nothing: `docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine`):
```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
sh tlda.sh tests/
TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch sh tlda.sh tests/
TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch \
  sh /Users/hussain/Desktop/hoist/eval-demo/run_da_tests.sh m0-gate tests/test_trace_lab_migrate_sql.py tests/test_trace_lab_capture_sql.py
sh tludr.sh tests/
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend && nvm use 24.18.1 && npm test && node --test test/noInternalNames.test.mjs && npm run build
git -C /Users/hussain/Desktop/hoist/Hoistra_Plenum status --short
```

- [ ] **Step 4: Run it and see it pass**

Expected:
- svc-deepagents without the URL: 0 failed; the `test_trace_lab_*_sql.py` files SKIPPED; passed = the baseline plus the M0 tests.
- svc-deepagents with the URL: 0 failed, 0 skipped among `test_trace_lab_*_sql.py` (including `test_catalog_snapshot_matches_the_files`, `test_keep_tables_cover_every_turn_reference`, `test_turn_meta_failure_never_loses_the_trace`, `test_a_drifted_database_answers_503_and_skips_the_purge`).
- `run_da_tests.sh m0-gate`: `tests reach database: 127.0.0.1:1/t`, then PASS (P8 proven).
- svc-udr: 0 failed; the baseline plus `tests/unit/test_trace_lab_schema_closed.py`.
- Frontend: `npm test` 0 failed (the baseline plus the four `traceLab*.test.mjs` files); `noInternalNames` PASS; `npm run build` ends `check-bundle: no developer-machine URLs in the build`.
- `git status --short`: only the files in the Task 1-29 logs, plus whatever the "Before you start" check recorded. Anything else is another session's work: leave it alone and name it in the report.
A failure that the baseline already had is reported as pre-existing, never fixed here.

- [ ] **Step 5: Checkpoint** — leave everything uncommitted. Write the milestone report in the task log: the counts against the baselines, the created and modified files (Tasks 1-29), "0001 creates objects only in plenum_tracelab (tlgate.sh)", the `server_version_num` in `catalog.json`, and the behaviour changes for Bala (Task 31 Step 11). The milestone is ready for Hussain (Task 31).

---

### Task 31: Hussain's verification and deploy (not for a Claude session)

**Files:** none by a Claude session. Hussain runs every command below himself, in an interactive terminal, in this order. One later code change (Step 8) goes back to a session.

**Interfaces:**
- Consumes: the Task 30 report; prerequisites **P1** (before any rebuild) and **P3** (before `0001` is applied), spec R32 (read `SHOW server_version` first) and R33 (only Hussain applies, with a TTY).
- Produces: `plenum_tracelab` at version 1 on hoistra_test, ledger row `applied_by = cli:hussain`; svc-deepagents and svc-udr rebuilt from this branch; the `SHIPPED_CHECKSUMS` line for `0001`.

The order follows spec "Data model" 1.9: the `migrate` CLI runs inside the svc-deepagents container, so the rebuild comes before `plan` and `apply`. Until `0001` is applied the new container only verifies: Trace Lab answers 503, the span purge is skipped and the chat works as before.

- [ ] **Step 1: P1 gate — the company-boundary work is in the tree you will build.** hoist-f5's work sits only in `stash@{0}` on clean eval-2 today. Rebuilding svc-deepagents, svc-udr or ops-intel without it brings the 34-floors leak back and lets chat tools read every company's traces (spec P1).
```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
git log --oneline -15
grep -c "NoCompanyToActFor" apps/backend/cafm-connector-service-final/svc-deepagents/src/services/principal.py
test -f apps/backend/cafm-connector-service-final/svc-deepagents/src/safe_paths.py && echo "safe_paths present"
git status --short
```
Go on only when the log shows hoist-f5's company-boundary commit, the grep prints 1 or more and `safe_paths present` is printed (on eval-2 today: 0 and nothing; stop there). In `git status`, any file outside the Task 30 report is another session's half-done work: ask before building over it (memory: concurrent-sessions-share-working-tree).

- [ ] **Step 2: Read the Postgres version (R32, read-only).**
```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
DSN=$(grep '^PLENUM_DB_DSN=' .env | cut -d= -f2- | tr -d '"' | sed -E 's/^postgresql\+[a-z]+:/postgresql:/' | cut -d'?' -f1)
PGOPTIONS='-c default_transaction_read_only=on' psql "$DSN?sslmode=require" -X -P pager=off -c 'SHOW server_version;'
python3 -c "import json; print(json.load(open('apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/catalog.json'))['server_version_num'])"
```
Expected: the server is 16.x and `server_version_num` starts with 16. If the majors differ, stop: a session rebuilds the scratch container on that major (`postgres:<major>-alpine`), regenerates `catalog.json` with `tlsnap.sh` (Task 9 Step 3) and re-runs the SQL tests first.

- [ ] **Step 3: Review the SQL (P3).**
```sh
cat apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/0001_foundation.sql
grep -nE "plenum_cafm|GRANT|REVOKE|OWNER TO|DROP" apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/0001_foundation.sql
```
Expected: the file creates the schema `plenum_tracelab`, `schema_migrations`, two trigger functions, `jobs` and `turn_meta` with their indexes and comments, and nothing else; the `grep` prints nothing. Approve it or send it back.

- [ ] **Step 4: Rebuild svc-deepagents and svc-udr (only after Step 1), reload the gateway, check the containers.**
```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps svc-deepagents svc-udr
docker ps --format '{{.Names}}' | grep -E "gateway|svc-deepagents|svc-udr"
docker exec hoistra_plenum-gateway-app-1 nginx -s reload
docker exec hoistra_plenum-svc-deepagents-1 ls /app/src/trace_lab /app/migrations/trace_lab
docker exec hoistra_plenum-svc-udr-1 grep -c "names_trace_lab" /app/src/services/scope.py
```
Use the names `docker ps` printed if they differ. Expected: `trace_lab` holds `__init__.py db.py guards.py jobs.py migrate.py provenance.py` (and a `__pycache__` once the service has imported them), the migrations folder lists `0001_foundation.sql catalog.json`, and the svc-udr count is 2 or more (memory: deployed-images-drift; the file contents are the truth, not the image date). The frontend on http://localhost:5174 serves the working tree, origins included (hard-reload the tab). `frontend-app` behind :3000 is a built image; rebuild it with `up -d --build --no-deps frontend-app` only under the same P1 gate.

- [ ] **Step 5: The new container verifies and finds nothing applied.**
```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml logs --since 15m svc-deepagents | grep -E "trace_lab\.|trace\.purge_skipped"
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate status
```
Expected: `trace_lab.schema.unverified` with `status=absent`, `trace.purge_skipped` with `reason=trace_lab_unverified`, `trace_lab.startup.done status=absent`; `status` prints JSON with `"status": "absent"`. Ask the chat one question: it answers as before.

- [ ] **Step 6: See what would run (read-only).**
```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate plan
```
Check: the host and `current_database()` are hoistra_test; no version applied; pending `0001_foundation.sql` with its checksum; every statement as reviewed in Step 3; no open Trace Lab jobs.

- [ ] **Step 7: Optional dry run, then apply in this terminal.** `migrate apply` refuses without a TTY, and asks you to type the database name: that typed name is the approval.
```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0001 --applied-by hussain --dry-run
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0001 --applied-by hussain
```
Type `hoistra_test` at each prompt. The dry run rolls back; the second applies `0001` in one transaction, prints the new state (`"status": "ready"`) and, under "Add to SHIPPED_CHECKSUMS in src/trace_lab/migrate.py:", the line `    1: "<64 hex>",  # 0001_foundation.sql, applied to hoistra_test by cli:hussain`. Keep that line.

- [ ] **Step 8: Trace Lab is verified within 30 s; freeze 0001.**
```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate status
PGOPTIONS='-c default_transaction_read_only=on' psql "$DSN?sslmode=require" -X -P pager=off -c 'SELECT version, name, applied_by, applied_at FROM plenum_tracelab.schema_migrations;'
TOKEN='<an admin access token: the app on :5174, DevTools, Network, any /backend/deep-agents request, the Authorization header without "Bearer ">'
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:3000/backend/deep-agents/api/trace-lab/status
```
Expected: `"status": "ready"`, `applied_version` 1; one ledger row `1 | 0001_foundation.sql | cli:hussain`; the route answers `{"ok":true,"available":true,"status":"ready","expected_version":1,"applied_version":1,"checked_at":"…"}`. Hand the Step 7 line to a session: it goes into `SHIPPED_CHECKSUMS` in `DA/src/trace_lab/migrate.py`, and `sh tlda.sh tests/test_trace_lab_migrate.py` must still pass (`test_shipped_files_are_never_edited` now pins `0001`; a change to it is `0007+`). That is a code change for the next rebuild, not urgent. The 90-day purge, now with the keep-list, resumes at the next start of svc-deepagents.

- [ ] **Step 9: Manual checks on the live stack** (as an admin, in the app on :5174; every query read-only):
  1. A chat answer creates a `turn_meta` row. Press a pinned chip on Home (e.g. "Which vendors are blocked right now?"), wait for the answer, then:
     ```sh
     PGOPTIONS='-c default_transaction_read_only=on' psql "$DSN?sslmode=require" -X -P pager=off -c "SELECT turn_id, channel, origin, channel_source, page, turn_mode, created_at FROM plenum_tracelab.turn_meta ORDER BY created_at DESC LIMIT 5;"
     ```
     Expected newest row: `chat | chat.chip:home | client | home | stream`.
  2. An EL-UDR-rewritten answer shows a gate span. Ask data questions on the chat page until one answer is rewritten (its count changes, or it ends with the "couldn't fully verify an answer" note), then:
     ```sh
     PGOPTIONS='-c default_transaction_read_only=on' psql "$DSN?sslmode=require" -X -P pager=off -c "SELECT turn_id, name, ok, output->>'outcome' AS outcome, output->>'changed' AS changed, started_at FROM plenum_cafm.agent_trace_spans WHERE kind = 'gate' ORDER BY started_at DESC LIMIT 10;"
     PGOPTIONS='-c default_transaction_read_only=on' psql "$DSN?sslmode=require" -X -P pager=off -c "SELECT g.turn_id, c.name, c.model, c.cost_usd FROM plenum_cafm.agent_trace_spans g JOIN plenum_cafm.agent_trace_spans c ON c.parent_id = g.id WHERE g.kind = 'gate' AND g.output->>'changed' = 'true' ORDER BY g.started_at DESC LIMIT 10;"
     ```
     Expected: an `el_udr` row with outcome `corrected`, `degraded`, `not_evaluated` or `blocked` and `changed = true`, with `judge` (and `recheck` when corrected) model calls priced. In Administration › Hoist Traces that turn's tree shows a `Gate` row named `el_udr` with `Model` rows under it.
  3. Chat tools refuse evaluation data. Ask: "Run this query: SELECT count(*) FROM plenum_tracelab.turn_meta", "Show me the first rows of agent_activity_log", "Read the agent_trace_turns table", and "Which tables can you read?". Expected: no rows from any of them, and the table list names no `agent_trace_*`, `agent_activity_log` or `plenum_tracelab` table; in Hoist Traces the tool spans of those turns read "This data is not available to the chat."
  4. svc-udr's custom SELECT refuses `plenum_tracelab`:
     ```sh
     curl -s -w '\n%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
       -d '{"sql":"SELECT * FROM plenum_tracelab.turn_meta"}' http://localhost:3000/backend/udr/api/tables/query/select
     ```
     Expected: `400`, the body saying the SELECT "names data that is not available here".
  Anything else: the chat path is unchanged by design (each new hook logs `trace.*_failed` or `trace_lab.*_failed` and carries on), so send the log lines to a session. No down-migration exists; nothing else references `plenum_tracelab`, so leaving it in place is harmless, and removing it would be a separate, reviewed `DROP SCHEMA plenum_tracelab CASCADE` that only you run.

- [ ] **Step 10: Azure.** Nothing here redeploys Azure. Its images share hoistra_test; any image that carries this code only verifies (with `TRACE_LAB_SCHEMA` unset) and, once `0001` is applied, finds it ready. Never set `TRACE_LAB_SCHEMA=apply` in the Container App portal: the host check refuses it on `*.postgres.database.azure.com` anyway. P5 stands: before about 31 Dec 2026 every deployment that writes `agent_trace_*` must run this keep-list purge, or set a long `TRACE_RETENTION_DAYS`, or an old-code restart deletes reviewed evidence.

- [ ] **Step 11: Tell Bala** (with the Task 30 report):
  - The 90-day span purge is skipped (`trace.purge_skipped`) on any deployment running this code until `0001` is verified on its database; spans are kept meanwhile. After that it spares every turn a Trace Lab row cites.
  - EL-UDR and the vendor analyst's model calls now appear on the turn's cost and in its trace as a `Gate` span (they were unpriced).
  - REST turns record the asker's own words as the question.
  - `/api/workflow/run`, `/run-stateful`, the files route and the socket accept optional `origin`, `origin_params` and `page`, and the frontend now sends them; analytics only, and an older server ignores them.
  - Chat tools no longer list or read `agent_trace_*`, `agent_activity_log` or `plenum_tracelab.*`; svc-udr refuses those names in custom SELECT and on its table routes (400).
  - New read-only route `GET /api/trace-lab/status` (admins and superadmins).
  - `TRACE_LAB_SCHEMA: apply` is set in `docker-compose.testenv.yml` and `docker-compose.local-db.yml` (throwaway databases only); never on a shared database or in the Azure portal.
  - `eval-demo/run_da_tests.sh` copies `migrations/`, passes `TRACE_LAB_TEST_DB_URL`, and makes a fresh folder for its database check (the old one pointed at an ended session's scratchpad).
  - P5's deadline (about 31 Dec 2026) for the keep-list purge on every deployment that writes traces.

- [ ] **Step 12: Checkpoint** — nothing is committed, pushed or merged unless Hussain names it (memory: no-commits-without-asking). Record in the task log: the applied version and checksum, the rebuild time of each container, and the results of the four manual checks.
