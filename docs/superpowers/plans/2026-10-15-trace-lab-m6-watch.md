# Trace Lab M6 — Watch & fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validated checks keep measuring live chat traffic (code checks on every chat answer, frozen judges on a uniform sample, corrected prevalence with a bootstrap interval and a threshold per failure mode, a review batch when a threshold is crossed), confirmed failures become eval cases that run through golden cases (5-run baseline → regression or capability), a release check certifies regression cases k/k with a downloadable report, repeat-n measures one answer's failure rate, and the Watch tab shows the share of answers checked automatically versus by people, week by week.

**Architecture:** Two new svc-deepagents modules, `src/trace_lab/monitor.py` (windows, plan, worker run as a `monitor_batch` job, estimates, threshold action, reads, automation trend) and `src/trace_lab/eval_cases.py` (cases, R9 golden twins in the company's "Regression cases" golden set, baseline / repeat / release runs started through M3's browser-stepped golden runner, rep grading, release report). One migration, `0006_watch.sql`, adds `monitor_batches`, `monitor_verdicts`, `eval_cases` and `eval_case_runs` and alters no M2/M3 table (M3's `0003` already ships the Regression cases set shape and the 20-rep caps, M2's `0002` the `monitor_flagged` strategy). Four helpers are appended to M2's `stats.py` (`case_decision`, `summarize_rollouts`, `iso_week_label`, `REPLAY_BOOTSTRAP_REPS`); the judge store is M5's, review batches are written by M2's `sampling.create_batch`, and "Grade now" is a `golden_grade` job handled through M3's `runner.register_grade_handler`. One router, `src/api/routes/trace_lab_monitor.py`. The frontend adds `logic/traceLabWatch.js` (loaders, actions, pure chart/share helpers) and `screens/traceLab/Watch.jsx` (hand-drawn SVG charts), mounted in M1's Trace Lab tab shell. No scheduler: "Run now" plus a weekly Due marker (R18).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async (asyncpg) `text()` SQL, structlog, slowapi; numpy only through the shared `stats.py` (M2, M5); React 18 function components, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md` (sections: head — Decisions, R1–R37, Milestones M6 row, Prerequisites; "Data model, migrations, jobs and guards" §1.2–1.9, §2.1, §2.9, §3, §5; "Judges, code checks, monitoring, eval cases and release check" — Names table, §1 (stats API), §2 (checks API), §3.6–3.9 (judges in use, cache), §4 monitoring, §5 eval cases / repeat-n / release check, §6 automation metric, §7.1 + §7.3 + §7.4 routes, §8 M6 tests, §9 M6 build order; "Frontend" §1, §2, §3, §4.10 Watch, §5, §6, §7 watch tests)

**Depends on:** M0 (migrate, db helpers and auth, jobs, provenance, scratch fixture), M1 (Trace Lab shell + api client, `tlWrite`, `tlOpenTurnById`), M2 (annotations, `sampling.create_batch`, `stats.py` with `wilson_interval`), M3 (golden sets/cases/runs/results, runner, `golden.regression_set`, `pass_at_k`), M4 (modes, labels), M5 (checks, judges and their store + tables) plans — names imported from them, as listed under **Interfaces**. Prerequisites: P1 before any rebuild, P3 before applying `0006`, P4 before the first monitoring batch or graded eval run.

## Global Constraints

Shared (every Trace Lab plan):
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

M6-specific:
- **Scratch database name.** M0's SQL-test guard (spec §1.8) refuses any database not named `tracelab_scratch`. Start the scratch container as `docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine` and point `TRACE_LAB_TEST_DB_URL` at `postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch`. M0's fixtures for it are `tl_scratch` / `scratch_db` and the helper `fresh_schema` (`tests/trace_lab_scratch.py`, imported as `from trace_lab_scratch import fresh_schema`). Every run command in this plan uses the helper below, which is the shared recipe above with a unique directory per call (its `mkdir` spells every path out: the container's `sh` is dash, which does no `{a,b}` brace expansion). Paste it once per shell, from `/Users/hussain/Desktop/hoist/Hoistra_Plenum`:
  ```sh
  da_test() {
    C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-$(date +%s)-$RANDOM
    docker exec $C sh -lc "mkdir -p $R/apps/backend/cafm-connector-service-final/svc-deepagents $R/apps/backend/cafm-connector-service-final/svc-operations-intelligence $R/apps/frontend"
    for d in src tests skills migrations pyproject.toml; do docker cp apps/backend/cafm-connector-service-final/svc-deepagents/$d $C:$R/apps/backend/cafm-connector-service-final/svc-deepagents/$d; done
    docker cp apps/frontend/src $C:$R/apps/frontend/src
    docker cp apps/backend/cafm-connector-service-final/svc-operations-intelligence/src $C:$R/apps/backend/cafm-connector-service-final/svc-operations-intelligence/src
    N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
    docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy \
      -e TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch $C \
      sh -lc "cd $R/apps/backend/cafm-connector-service-final/svc-deepagents && python -m pytest $* -q -p no:cacheprovider -p no:warnings"
    echo "ran in $C:$R"
  }
  ```
- **No model calls and no golden runs in tests.** `judges.ASK` (M5) and `eval_cases.START_GOLDEN_RUN` (this plan) are replaced in every test that would reach them. A test that forgets fails loudly: `ANTHROPIC_API_KEY=dummy` and the dead DB URL.
- **A GET never spends money.** Reading an eval run or a release report grades reps with code checks and cached judge predictions only (`allow_model_calls=False`); judge calls happen only in a rate-limited, capped POST (`/eval-runs/{id}/grade`), which starts a `golden_grade` job whose handler `eval_cases` runs inside M3's one `golden_grade` worker (`runner.register_grade_handler`, started with `jobs.start`).
- **Apply order.** `0006_watch.sql` is applied only after `0005_judges.sql` is applied and while no Trace Lab job is open: it creates four tables and their foreign keys to `jobs`, `modes`, `judges`, `golden_runs` and `golden_cases` (short locks on those referenced tables under `lock_timeout = 3s`). It alters no M2 or M3 table: M3 owns the Regression cases set shape and the 20-rep caps (`runner.HARD_MAX_K = 20`, `runner.max_k()`, `golden.regression_set(org, *, create=False)`), M2 owns the `monitor_flagged` strategy and `sampling.create_batch`.
- **Cross-milestone files** (`src/trace_lab/db.py` from M0, `src/trace_lab/stats.py` from M2, `src/trace_lab/judges.py` from M5, `tests/test_trace_lab_purge_sql.py` from M0, `apps/frontend/src/api/traceLab.js`, `src/logic/traceLab.js`, `src/logic/session.js`, `src/screens/TraceLab.jsx` from M1) are edited only with exact-string replacements (or, for `db.py` and `stats.py`, statements appended at the end of the file), after the `grep` in that task's Step 0 shows the expected anchor. If an anchor is missing, stop and report; do not improvise a different edit. M6 never edits M3's `trace_lab_golden.py`.
- **One owner per name (R36).** M6 defines no auth helper, no review-batch writer, no judge-store query and no Regression cases set code of its own. Auth and the envelope are M0's (`db.require_reader`, `db.require_reviewer`, `db.scope_for`, `db.http_error`, `db.require_trace_store`; reasons `reviewers_only`, `name_a_company`, `not_your_company`, `bad_company`, `platform_only`, `trace_store_unavailable`, `trace_lab_unavailable`); review batches are written only by M2's `sampling.create_batch` (strategy `monitor_flagged`); judges in use, their frozen test pairs, the prediction cache and its writer are M5's (`judges.in_use_judges`, `judges.test_pairs`, `judges.cached_predictions`, `judges.write_prediction`, `judges.load_judge_turns`); the Regression cases set, its hash and the rep caps are M3's (`golden.regression_set`, `golden.set_hash`, `runner.max_k`, `runner.HARD_MAX_K`); `stats.wilson_interval` is M2's and `stats.pass_at_k` / `stats.pass_hat_k` are M3's. M6 appends only `case_decision`, `summarize_rollouts`, `iso_week_label` and `REPLAY_BOOTSTRAP_REPS` to M2's `stats.py` (Task 4), because nobody else defines them.
- **Copy rules (Frontend §6).** Chrome says "answer" (not "turn") and "conversation" (not "session"); evaluator keys (`judge:<uuid>`, `code:<key>@v1`) never reach the screen — labels and mode names do.
- **R4.** `eval_cases.source_turn_id` joins the purge keep-list at version 6; `monitor_verdicts.turn_id` never does (code checks write a row for every chat answer). Flagged answers are protected once they are opened as a review batch (`sample_items`).

## Review Focus

1. **A capped batch must still estimate from a uniform subset.** Random-sample answers are judged first, in sampled order, and dispatch stops at the cap, so a capped batch holds a prefix of the uniform sample and no risk-only answer — pinned by `test_random_judged_first_so_capped_batch_stays_uniform` (Task 9).
2. **One unverdicted run must never let a regression case pass a release.** A degraded, pending, ungraded or missing rep makes a regression case `incomplete`, and any incomplete regression case makes the release `incomplete` (zero regression cases too) — pinned by `test_case_decision_repo_rule` (Task 4), `test_degraded_regression_incomplete` and `test_release_decision_rules` (Task 12).
3. **Evidence durability follows R4 exactly.** A span cited only by `monitor_verdicts` is purged; a span cited by `eval_cases.source_turn_id` is kept — pinned by `test_purge_keeps_eval_case_turns_but_not_monitor_verdict_turns` and `test_version_6_keeps_eval_case_source_turns` (both Task 3).
4. **Company admins see only their own company.** A platform batch and another company's batch read as 404, flagged answers are filtered to the caller's company, and starting a batch is superadmin-only — pinned by `test_company_scope_and_platform_batch_hidden_from_admin` (Task 10) and `test_company_admin_cannot_start_a_batch_or_read_another_company` (Task 17).
5. **R9: no case without a confirmed failure and a human-written expected answer.** Creating a case from an answer refuses an unconfirmed turn (409) and a missing expected answer (422) before any write, and the twin lands in the company's "Regression cases" set — pinned by `test_live_turn_case_needs_confirmed_failure_and_expected_answer` (Task 13) and `test_case_twin_lands_in_the_regression_set` (Task 13, scratch DB).

## File Structure

svc-deepagents (`apps/backend/cafm-connector-service-final/svc-deepagents`, written `DA/` below):

| File | Create / Modify | One responsibility |
|---|---|---|
| `DA/migrations/trace_lab/0006_watch.sql` | Create | M6 DDL: monitor_batches, monitor_verdicts (+ decided-is-final guard), eval_cases, eval_case_runs; no ALTER of any M2/M3 table |
| `DA/migrations/trace_lab/catalog.json` | Modify (regenerated by M0's `tlsnap.sh`) | expected catalog at version 6 |
| `DA/src/trace_lab/db.py` | Modify (append at the end) | `KEEP_BY_VERSION[6]` (`eval_cases.source_turn_id`) and `OFFBOARD_BY_VERSION[6]` (case runs, cases, verdicts, batches) |
| `DA/tests/test_trace_lab_purge_sql.py` (M0) | Modify (one excused column) | R4: `monitor_verdicts.turn_id` is added to `EXCUSED_TURN_COLUMNS`, not kept |
| `DA/src/trace_lab/stats.py` (M2's) | Modify (append) | `REPLAY_BOOTSTRAP_REPS`, `case_decision`, `summarize_rollouts`, `iso_week_label` |
| `DA/src/trace_lab/judges.py` (M5's) | Modify (one body) | M5's `latest_prevalence` seam reads `monitor.latest_prevalence` |
| `DA/src/trace_lab/monitor.py` | Create | monitoring: windows, plan, worker, estimates, threshold action (review batches through M2's `sampling.create_batch`), reads, automation trend |
| `DA/src/trace_lab/eval_cases.py` | Create | eval cases, golden twins (in M3's Regression cases set), baseline / repeat / release runs, rep grading (handler `eval_cases` of M3's `golden_grade` worker), release report |
| `DA/src/api/routes/trace_lab_monitor.py` | Create | the Watch routes under `/api/trace-lab` (spec §7.3) |
| `DA/src/api/main.py` | Modify (import after `:40`, include after `:255`) | register `trace_lab_monitor_router` |
| `DA/tests/test_trace_lab_m6_interfaces.py` | Create | pins every M0–M5 name this plan consumes |
| `DA/tests/test_trace_lab_stats_watch.py` | Create | `case_decision`, `summarize_rollouts`, `iso_week_label` |
| `DA/tests/test_trace_lab_watch_sql.py` | Create | scratch-DB tests for 0006, the window query, twins, company offboarding (spec's `test_trace_lab_m5_m6_sql.py` M6 entries live here) |
| `DA/tests/test_trace_lab_watch_keep_list.py` | Create | keep-list and offboarding registration at versions 5 and 6 |
| `DA/tests/test_trace_lab_monitor.py` | Create | monitor unit tests (spec §8 list) |
| `DA/tests/test_trace_lab_eval_cases.py` | Create | eval-case unit tests (spec §8 list) |
| `DA/tests/test_trace_lab_monitor_routes.py` | Create | auth matrix, envelope, 503 dependency, rate limits (spec's `test_trace_lab_judge_monitor_routes.py` M6 half) |

Frontend (`apps/frontend`, written `FE/`):

| File | Create / Modify | One responsibility |
|---|---|---|
| `FE/src/api/traceLab.js` | Modify (append one block) | Watch API calls, each with `orgOverride()` |
| `FE/src/logic/traceLabWatch.js` | Create | `traceLabWatchMethods`, `traceLabWatchVals`, `tlWatchResetPatch`, pure `chartGeometry`, `stackShares`, `dueLabel`, `pctText` |
| `FE/src/screens/traceLab/Watch.jsx` | Create | the Watch view (SVG prevalence charts, 100% stacked trend bars) |
| `FE/src/logic/HoistraLogic.js` | Modify (`:20` import, `:383` list) | register the methods |
| `FE/src/logic/renderVals.js` | Modify (`:19` import, `:1993` spread) | register the vals |
| `FE/src/logic/traceLab.js` | Modify (M1's file) | `'watch'` in `TL_VIEWS`, the Watch `TL_TABS` entry, the `tlReadView` branch, and `...tlWatchResetPatch(),` right after `  return Object.assign({}, TL_SCALARS, TP_SCALARS, {` in `freshPatch()` (company switch / sign-out clears every Watch key) |
| `FE/src/logic/session.js` | Modify (M1's `TL_VIEW_KEYS`) | a reload on the Watch tab comes back to it |
| `FE/src/screens/TraceLab.jsx` | Modify (M1's view switch) | mount `Watch` when `tlView === 'watch'`; no `FilterBar` / `UpdateLine` on it |
| `FE/test/traceLabWatch.test.mjs` | Create | y_max geometry, empty state, threshold label, shares sum to 1, Due marker, review-batch action, role gating, refused writes and write keys (M1's `tlWrite` contract), tab registration and visibility, reset coverage |

Outside the repo (the stub-backend recipe, never a database):

| File | Create / Modify | One responsibility |
|---|---|---|
| `~/Desktop/hoist/eval-demo/stub-eval.mjs` | Modify (one inserted block) | canned Watch replies |
| `~/Desktop/hoist/eval-demo/walks/verify-tracelab-watch.mjs` | Create | browser walk of the Watch tab; fails on forbidden writes |

## Interfaces (assumed from M0–M5)

The spec's "Names this section uses from other sections" table predates R36; where it disagrees with the owning section, the owning section's name is used (R36). Task 1 turns every line below into a failing-first test, so a renamed interface is caught before any M6 code depends on it. If Task 1 fails on a name, fix the consuming line in this plan's code (one place each), never the M0–M5 module.

**M0 (`src/trace_lab/`)**
- `db.engine()` → the shared `AsyncEngine`; `db.require_ready` (FastAPI dependency, 503); `db.STATE: migrate.SchemaState | None`; `db.keep_tables() -> tuple[tuple[str, str], ...] | None` (union of a per-version map up to `STATE.applied_version`).
- `migrate.SchemaState(status, expected_version, applied_version, pending, problems, checked_at)`; `migrate.apply_pending(engine=None, *, applied_by, through=None, dry_run=False)`; CLI `python -m src.trace_lab.migrate snapshot --db-url …`.
- `jobs.KINDS` contains `monitor_batch` and `golden_grade` (R6; there is no `eval_grading` kind — the spec §5.3 "eval_grading" job is a `golden_grade` job here); `jobs.STALE_AFTER_S`; `jobs.create(*, kind, scope_key, organization_id, params, usd_cap, usd_estimate, total, actor_id, actor_email) -> dict` (row with `id`); `jobs.claim(job_id, *, worker) -> dict | None`; `jobs.reserve(job_id, usd: Decimal)` raising `jobs.CapReached`; `jobs.checkpoint(conn, job_id, *, usd, released, done_delta=1, progress_patch=None)`; `jobs.finish(job_id, status, error=None)`; `jobs.get(job_id, *, organization_id) -> dict | None`; `jobs.run_in_background(job_id, coro_factory)`; `jobs.JobConflict`; `jobs.start(job_id) -> asyncio.Task | None` (claims the job and runs the one worker registered for its kind); `jobs.JobContext` (`job_id`, `job`, `worker`, `heartbeat()`, `stopping()`).
- Auth and envelope (spec §1; one `http_error`, M0's): `db.http_error(status, reason, error, **extra) -> HTTPException`; `db.Scope(org_id, platform, is_superadmin, is_reviewer, acting)`; `db.scope_for(principal, organization_id) -> Scope` (403 `not_your_company`, 400 `bad_company`); `db.require_reader(principal, organization_id) -> Scope` (403 `reviewers_only`); `db.require_reviewer(principal, organization_id) -> str` (also 400 `name_a_company`); `db.require_platform(principal, organization_id=None) -> Scope` (403 `platform_only`); `db.require_trace_store()` (503 `trace_store_unavailable`).
- Registries (one statement appended at the end of `db.py` per version): `db.KEEP_BY_VERSION[N]`, `db.OFFBOARD_BY_VERSION[N]`, read through `db.keep_tables()` and `db.offboarding_steps(applied_version)`; the excuse registry is `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS` (`db.py` has no excuse list of its own).
- `provenance.inferred_channel_sql(alias: str) -> str` (SQL CASE for turns without `turn_meta`).
- `tests/trace_lab_scratch.py`: `async def fresh_schema(monkeypatch) -> AsyncEngine` performing spec §1.8 steps 1–5 (guarded scratch engine, `plenum_tracelab` dropped and re-applied through the head, trace tables created, `database._get_engine` patched). Imported as a sibling module (`from trace_lab_scratch import fresh_schema`), as `tests/test_loop_answers_get_cards.py:20` imports `test_planner_cards`: `tests/` has no `__init__.py`, and pytest's default prepend mode puts the test directory on `sys.path`.

**M1** — no `normalize` name is consumed: M1's `conversations.load_turn(s)` return timeline dicts, and `normalize.session_kind` needs `thread_exists`; M6 loads the turns it judges and checks through M5's `judges.load_judge_turns` (below) and reads `NormalizedTurn.session_kind`. Frontend: `src/api/traceLab.js` defines module-scope `function orgOverride()` and `export const traceLabApi = {…}` and imports `{ BASES, apiFetch }` from `./client.js`; `src/logic/traceLab.js` exports `TL_VIEWS`, `TL_TABS`, `tlTabsFor(s)`, `tlResetPatch()` (built by the module-private `freshPatch()`) and `traceLabMethods` with `tlSetView(view)`, `tlReadView()` and `tlWrite(fn, { key, retry })`, which never throws and resolves to `{ ok: true, out }`, `{ ok: false, stale: true }` (company switched) or `{ ok: false, error, conflict }` after putting the error in `state.tlWrites[key]` (writes without a key share the key `write`; `retry` is a boolean flag, `tlRetryWrite(key)` re-runs the stored call); `src/logic/traceLabConversations.js` exposes `tlOpenTurn(turnId)` (switches answers inside the open conversation only) and `tlOpenTurnById(turnId)` (opens any answer in its conversation) on the controller; persisted state keys `tlView`, `tlRunId` (Frontend §2); `src/logic/session.js` restores `tlView` only if it is in `TL_VIEW_KEYS`. M1 knows no `watch` view or tab: Task 20 (Watch registration) adds them.

**M2** — tables `annotations` (kind, verdict, tombstone, superseded_by, reviewer_id, organization_id, turn_id, created_at), `sample_batches` (spec §9.1 DDL, `strategy` column with an inline CHECK that already includes `monitor_flagged`), `sample_items` (`UNIQUE (organization_id, turn_id)`); `sampling.create_batch(principal, org, req) -> {"ok", "batch", "items"}` (the one entry point; `req["strategy"] == "monitor_flagged"` records `req["items"]`, 1–200 `{turn_id, session_id, reason}`, with `req["since"]`, `req["until"]`, `req["seed"]`, `req["params"]` and `req["plan_step"]`; raises `db.Invalid("pool_empty")` when none is left and `db.Conflict("pool_changed")` on a race), `sampling.RECORDED_STRATEGIES == ("monitor_flagged",)`; `db.Invalid` / `db.Conflict` (`ReviewError` with `.status`, `.reason`, `.error`) and `db.http_error_for(exc)`; `stats.wilson_interval(successes, total, z=Z95) -> (low, high)` in M2's `src/trace_lab/stats.py`.

**M3** — tables `golden_sets` (the `0003` Regression cases shape: `version = 0`, `status = 'regression'`, `source_kind = 'eval_cases'`, `ck_golden_sets_regression_shape`, `ux_golden_sets_regression`), `golden_cases`, `golden_runs` (`purpose` per R8: `adhoc|baseline|release_check|repeat`; `k` 1–20), `golden_results` (`rep` 1–20); `golden.CaseRow`, `golden.case_version_for(row)`, `golden.parse_expected_values(cell)`, `golden.REGRESSION_SET_NAME == "Regression cases"`, `golden.set_hash(keys_and_versions)`, `async golden.regression_set(org, *, create=False, actor_id=None, actor_email=None, conn=None) -> dict | None` (creating it needs a real actor id and email); `runner.HARD_MAX_K = 20`, `runner.max_k()` (`TRACE_LAB_GOLDEN_MAX_K`, never above 20), `runner.register_grade_handler(name, fn)` (a `golden_grade` job whose `params["handler"]` is `name` runs `await fn(ctx)` in M3's one `golden_grade` worker); `runner.start_run(*, org, principal, authorization, acting_org, body, orchestrator) -> dict` (the run summary, or `{"run": …}`; raises `HTTPException` with the house envelope for `run_in_progress`, caps, `building_selected`, preflight); `GoldenRunStart` / `GoldenEstimate` Pydantic models in `src/api/routes/trace_lab_golden.py` (`k` 1–20 and the fields `set_id, k, case_keys, purpose, usd_cap, context_default, note`; M6 builds them as they are and keeps its per-purpose limits — baseline 5, release up to 10, repeat up to 20 — in its own request models and functions, clamped by `runner.max_k()`); `stats.pass_at_k(n, c, k)` and `stats.pass_hat_k(n, c, k)`; `start_run` accepts `body.set_id` naming any golden set of the company (it does not require `status = 'active'`). The frontend Runs view drives the step loop for the run named by `tlRunId`.

**M4** — tables `modes` (id, name, status, confirmed_at, retired_at), `labels` (turn_id, mode_id, present, tombstone, approval, superseded_by, organization_id, created_at).

**M5** — `stats.corrected_prevalence(sample_preds, test_labels, test_preds, *, confidence=0.95, bootstrap_iterations=…, seed=…) -> dict`; `checks.CHECKS`, `checks.CheckResult(status, findings=(), detail={})` with `.failure_present`, `checks.NormalizedTurn` (`turn_id, session_id, organization_id, question, gates, prior, tool_calls, session_kind, …`), `checks.run_check(check, turn, params)`, `checks.run_live_checks(turn) -> dict[key, CheckResult]`, `CodeCheck.evaluator_key / .label / .scope / .key / .version`; the judge store in `src/trace_lab/judges.py`: `async load_judge_turns(turn_ids) -> dict[turn_id, checks.NormalizedTurn]` (unscoped, missing turns absent), `async in_use_judges() -> dict[mode_id, judge row]` (the row carries `id, mode_id, mode_name, mode_status, judge_key, kind, model, prompt_text, prompt_hash, check_key, check_version, monitor_threshold, version, …`), `async test_pairs(judge_id) -> (labels, preds)` (the frozen J6 decided pairs), `async cached_predictions(prompt_hash, turn_ids) -> dict[turn_id, row]`, `async write_prediction(conn, *, judge, turn_id, organization_id, pred)` (decided rows are never touched; `usd` and `attempts` accumulate), `async fetch_judge(judge_id) -> dict | None`, `ASK(model, prompt_text, judge_text) -> JudgeReply(failure_present, critique, status, usd, error, attempts=1)`, `judge_input(turn) -> JudgeInput(text, chars, truncated, sha)`, `JUDGE_CONCURRENCY`, and the seam `async latest_prevalence(mode_id) -> (dict | None, reason)`, whose body Task 10 replaces; tables `judges` (kind, status, decision, monitor_threshold, prompt_text, prompt_hash, model, check_key, check_version, frozen_at), `judge_runs` (split, status, results `[{turn_id, label, failure_present, status}]`, finished_at), `judge_predictions` (+ `organization_id NOT NULL`, R2).

**Produced for later work** (no later milestone exists; these are the stable names the Watch tab and Hussain's checks use): `stats.case_decision`, `stats.summarize_rollouts`, `stats.iso_week_label`, `stats.REPLAY_BOOTSTRAP_REPS` (Task 4), `monitor.start_batch`, `monitor.list_batches`, `monitor.chart`, `monitor.automation_trend`, `monitor.latest_prevalence`, `eval_cases.grade_rep(result_id)` (reads and "Grade now" grade through `eval_cases.refresh_run`; nothing in M3's runner calls `grade_rep`, so it is there for an explicit after-grade hook if one is ever added), `eval_cases.release_report`, the `eval_cases` handler registered with M3's `runner.register_grade_handler`, route table of Task 17, `traceLabApi.*` Watch calls of Task 18.

---

### Task 1: Pin the M0–M5 interfaces M6 consumes

**Files:**
- Create: `DA/tests/test_trace_lab_m6_interfaces.py`

**Interfaces:**
- Consumes: every name in **Interfaces** above.
- Produces: nothing at runtime; a red test here means "fix this plan's consuming line before going on".

- [ ] **Step 1: Write the failing test**

```python
"""The names M6 (Watch) imports from M0-M5, pinned before any M6 code depends on them. 15 Oct 2026.

Plan: tracelab-work/plans/2026-10-15-trace-lab-m6-watch.md, section Interfaces. A failure here names
the interface that moved; fix the consuming line in monitor.py / eval_cases.py, not the owner module."""
from __future__ import annotations

import inspect


def test_m0_db_migrate_jobs_provenance_and_scratch_helper():
    from src.trace_lab import db, jobs, migrate, provenance
    import trace_lab_scratch

    for name in ("engine", "require_ready", "keep_tables", "STATE", "KEEP_BY_VERSION", "OFFBOARD_BY_VERSION",
                 "offboarding_steps", "http_error", "scope_for", "require_reader", "require_reviewer",
                 "require_trace_store"):
        assert hasattr(db, name), f"db.{name}"
    for name in ("create", "claim", "reserve", "checkpoint", "finish", "get", "run_in_background", "start",
                 "JobContext", "JobConflict", "CapReached", "STALE_AFTER_S", "KINDS"):
        assert hasattr(jobs, name), f"jobs.{name}"
    assert inspect.iscoroutinefunction(jobs.start)
    assert set(inspect.signature(db.require_reviewer).parameters) >= {"principal", "organization_id"}
    assert set(inspect.signature(db.scope_for).parameters) >= {"principal", "organization_id"}
    assert "monitor_batch" in jobs.KINDS and "golden_grade" in jobs.KINDS
    assert set(inspect.signature(jobs.create).parameters) >= {
        "kind", "scope_key", "organization_id", "params", "usd_cap", "usd_estimate", "total", "actor_id", "actor_email"}
    assert set(inspect.signature(jobs.checkpoint).parameters) >= {
        "conn", "job_id", "usd", "released", "done_delta", "progress_patch"}
    assert "organization_id" in inspect.signature(jobs.get).parameters
    sql = provenance.inferred_channel_sql("t")
    assert isinstance(sql, str) and sql.strip()
    assert {"status", "applied_version"} <= set(migrate.SchemaState.__dataclass_fields__)
    assert inspect.iscoroutinefunction(migrate.apply_pending) and inspect.iscoroutinefunction(migrate.verify)
    assert inspect.iscoroutinefunction(trace_lab_scratch.fresh_schema)


def test_m2_sampling_entry_point_and_stats_owner():
    from src.trace_lab import db, sampling, stats

    assert inspect.iscoroutinefunction(sampling.create_batch)
    assert list(inspect.signature(sampling.create_batch).parameters)[:3] == ["principal", "org", "req"]
    assert "monitor_flagged" in sampling.RECORDED_STRATEGIES            # M2 records the flagged batches, M6 asks
    assert issubclass(db.Invalid, db.ReviewError) and issubclass(db.Conflict, db.ReviewError)
    assert callable(stats.wilson_interval)                              # M2's stats.py


def test_m3_golden_runner_regression_set_and_run_model():
    from src.api.routes.trace_lab_golden import GoldenRunStart
    from src.trace_lab import golden, runner, stats

    assert inspect.iscoroutinefunction(runner.start_run)
    assert set(inspect.signature(runner.start_run).parameters) >= {
        "org", "principal", "authorization", "acting_org", "body", "orchestrator"}
    assert callable(golden.case_version_for) and callable(golden.parse_expected_values) and callable(golden.set_hash)
    assert golden.REGRESSION_SET_NAME == "Regression cases"
    assert inspect.iscoroutinefunction(golden.regression_set)
    assert set(inspect.signature(golden.regression_set).parameters) >= {"org", "create", "actor_id", "actor_email", "conn"}
    assert {"case_key", "key_source", "case_version", "question", "followups", "expected_answer", "evaluation",
            "expected_values", "source", "verified_on", "page_context", "ask_as", "requirement_ids", "notes",
            "flags", "row_no"} <= set(golden.CaseRow.__dataclass_fields__)
    for purpose in ("adhoc", "baseline", "release_check", "repeat"):      # R8
        assert GoldenRunStart(purpose=purpose, k=2).purpose == purpose
    assert runner.HARD_MAX_K == 20 and 1 <= runner.max_k() <= runner.HARD_MAX_K
    assert callable(runner.register_grade_handler)                        # M6's "Grade now" handler goes here
    assert callable(stats.pass_at_k) and callable(stats.pass_hat_k)


def test_m5_checks_and_judge_store():
    from src.trace_lab import checks, judges, stats

    assert callable(stats.corrected_prevalence)
    for name in ("load_judge_turns", "in_use_judges", "test_pairs", "cached_predictions", "write_prediction",
                 "fetch_judge"):
        assert inspect.iscoroutinefunction(getattr(judges, name)), f"judges.{name}"
    assert set(inspect.signature(judges.write_prediction).parameters) >= {
        "conn", "judge", "turn_id", "organization_id", "pred"}
    assert inspect.iscoroutinefunction(judges.latest_prevalence)          # the seam Task 10 fills
    live = [c for c in checks.CHECKS.values() if c.scope == "live"]
    assert live and all(c.evaluator_key.startswith("code:") for c in live)
    assert checks.CheckResult("fail").failure_present == 1
    assert checks.CheckResult("undecidable").failure_present is None
    assert callable(checks.run_check) and callable(checks.run_live_checks)
    assert "session_kind" in checks.NormalizedTurn.__dataclass_fields__
    assert callable(judges.ASK) and callable(judges.judge_input) and int(judges.JUDGE_CONCURRENCY) >= 1
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_m6_interfaces.py`
Expected before M0–M5 are in the tree: `ModuleNotFoundError: No module named 'src.trace_lab'` (or a named `AssertionError`). With M0–M5 merged it should already pass; if it does not, the assertion message names the interface to reconcile.

- [ ] **Step 3: Implement**

No production code. When an assertion fails, change the matching consumer in this plan before implementing it (for example a different `judges.in_use_judges` row key or a different `fresh_schema` name), and note the change in the task log.

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_m6_interfaces.py` → `4 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/tests/test_trace_lab_m6_interfaces.py`.

---

### Task 2: `0006_watch.sql`, the regenerated catalog, and its SQL tests

**Files:**
- Create: `DA/migrations/trace_lab/0006_watch.sql`
- Modify: `DA/migrations/trace_lab/catalog.json` (regenerated, never hand-edited)
- Test: `DA/tests/test_trace_lab_watch_sql.py`

**Interfaces:**
- Consumes: M0 `migrate.verify`, `migrate.MIGRATIONS_DIR`, `tests/trace_lab_scratch.fresh_schema` (imported as `from trace_lab_scratch import fresh_schema`, M0's `tracelab_scratch` fixtures); objects M2 and M3 already ship and `0006` therefore never touches: M3's `0003` `golden_sets` with the Regression cases shape (`version = 0`, `status = 'regression'`, `source_kind = 'eval_cases'`, `ck_golden_sets_regression_shape`, `ux_golden_sets_regression`), `golden_cases`, `golden_runs` (`k` 1–20) and `golden_results` (`rep` 1–20); M2's `0002` `sample_batches.strategy` CHECK, which already lists `monitor_flagged`; M5 tables `judges`, `modes`.
- Produces: tables `plenum_tracelab.monitor_batches` (`organization_id` nullable = platform, R2), `monitor_verdicts` (unique `ux_monitor_verdicts_evaluator_turn`, guard `monitor_verdicts_guard`), `eval_cases`, `eval_case_runs`. Nothing else: `0006` alters no M2/M3 table (ledger decisions: M3 owns the Regression cases set and the 20-rep caps, M2 owns `monitor_flagged`).

- [ ] **Step 0: Confirm what M2 and M3 already ship** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/backend/cafm-connector-service-final/svc-deepagents
grep -n "'monitor_flagged'" migrations/trace_lab/0002_*.sql
grep -n "ck_golden_sets_regression_shape\|ux_golden_sets_regression" migrations/trace_lab/0003_*.sql
grep -nE "CHECK \((k|rep) BETWEEN 1 AND 20\)" migrations/trace_lab/0003_*.sql
ls migrations/trace_lab/
```

The first grep prints one line, the second prints the constraint and the index (at least two lines), the third prints two lines (`k` and `rep`). `ls` must show `0001`–`0005` and no `0006`. If any grep prints nothing, M2 or M3 has not landed in this tree: stop and report (never add those objects here).

- [ ] **Step 1: Write the failing test**

```python
"""Watch (M6) on a scratch Postgres: 0006 applies, and its guards, checks and offboarding steps hold. 15 Oct 2026.

Runs only with TRACE_LAB_TEST_DB_URL naming a throwaway postgres:16-alpine database called tracelab_scratch
(M0's guard refuses any other name). Every test starts from a freshly applied plenum_tracelab."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

from src.trace_lab import db, migrate  # noqa: E402
from trace_lab_scratch import fresh_schema  # noqa: E402  (M0 helper, a sibling module)

UTC = timezone.utc
ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"
ACTOR = "cccccccc-0000-4000-8000-000000000003"
WEEK = (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))


async def _batch(conn, org=None, status="queued"):
    bid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.monitor_batches (id, organization_id, period_label, window_start, window_end, sample_rate,
    seed, risk_groups, judges, checks, status)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), '2026-W41', :s, :e, 0.25, 7, CAST(:groups AS text[]),
    CAST('[]' AS jsonb), CAST('[]' AS jsonb), :status)"""),
        {"id": bid, "org": org, "s": WEEK[0], "e": WEEK[1], "groups": ["failed"], "status": status})
    return bid


async def _verdict(conn, batch_id, status, fp, *, ek="code:no_answer@v1", turn="turn-00000000000000a1", org=ORG_A):
    await conn.execute(text("""
INSERT INTO plenum_tracelab.monitor_verdicts (id, evaluator_key, turn_id, organization_id, turn_started_at, status,
    failure_present, first_batch_id)
VALUES (CAST(:id AS uuid), :ek, :tid, CAST(:org AS uuid), :ts, :status, :fp, CAST(:batch AS uuid))"""),
        {"id": str(uuid.uuid4()), "ek": ek, "tid": turn, "org": org, "ts": WEEK[0] + timedelta(hours=9),
         "status": status, "fp": fp, "batch": batch_id})


async def _regression_set(conn, org=ORG_A):
    sid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.golden_sets (id, organization_id, version, status, source_kind, filename, file_sha256,
    header_row, case_count, set_hash, uploaded_by, uploaded_by_email)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), 0, 'regression', 'eval_cases', 'Regression cases', :sha, 0, 1, '',
    CAST(:by AS uuid), 'pytest@example.com')"""), {"id": sid, "org": org, "sha": "0" * 64, "by": ACTOR})
    return sid


async def _golden_case(conn, set_id, key="e-0000000a.v1", org=ORG_A):
    gid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.golden_cases (id, set_id, organization_id, case_key, key_source, case_version, row_no,
    question, expected_answer, evaluation)
VALUES (CAST(:id AS uuid), CAST(:set AS uuid), CAST(:org AS uuid), :key, 'id_column', 'abcdef012345', 1,
    'How many work orders were raised at Bishopsgate Tower in July 2026?', 'There were 786.', 'objective')"""),
        {"id": gid, "set": set_id, "org": org, "key": key})
    return gid


async def _case(conn, *, status="proposed", kind="unclassified", rate=None, twin=None, turn="turn-00000000000000b1"):
    cid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.eval_cases (id, organization_id, case_key, version, status, kind, baseline_pass_rate,
    source_type, source_turn_id, golden_case_id, input, expected)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :key, 1, :status, :kind, :rate, 'live_turn', :turn,
    CAST(:twin AS uuid), CAST(:input AS jsonb), CAST(:expected AS jsonb))"""),
        {"id": cid, "org": ORG_A, "key": "e-" + uuid.uuid4().hex[:8], "status": status, "kind": kind, "rate": rate,
         "turn": turn, "twin": twin, "input": json.dumps({"question": "q", "followups": []}),
         "expected": json.dumps({"assertions": [], "checks": [], "judges": [], "golden_expected": True})})
    return cid


async def _job(conn, kind="golden_run", org=ORG_A):
    jid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.jobs (id, kind, status, organization_id, scope_key, usd_cap)
VALUES (CAST(:id AS uuid), :kind, 'done', CAST(:org AS uuid), :scope, 5)"""),
        {"id": jid, "kind": kind, "org": org, "scope": f"{kind}:{jid}"})
    return jid


async def _golden_run(conn, set_id, *, k, purpose="repeat"):
    rid = uuid.uuid4()
    await conn.execute(text("""
INSERT INTO plenum_tracelab.golden_runs (id, label, job_id, organization_id, set_id, set_hash, case_keys, k, purpose,
    fingerprint, grader_version, estimate_usd, started_by, started_by_email, started_by_role)
VALUES (CAST(:id AS uuid), :label, CAST(:job AS uuid), CAST(:org AS uuid), CAST(:set AS uuid), 'abcdef012345',
    CAST(:keys AS text[]), :k, :purpose, CAST('{}' AS jsonb), 'abcdef012345', 1.0, CAST(:by AS uuid),
    'pytest@example.com', 'admin')"""),
        {"id": str(rid), "label": f"run-20261015T101500Z-{rid.hex[:8]}", "job": await _job(conn), "org": ORG_A,
         "set": set_id, "keys": ["e-0000000a.v1"], "k": k, "purpose": purpose, "by": ACTOR})
    return str(rid)


async def test_migrations_through_0006_apply_with_lock_timeout(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    state = await migrate.verify(engine)
    assert state.status == "ready" and state.applied_version >= 6
    first = (Path(migrate.MIGRATIONS_DIR) / "0006_watch.sql").read_text().splitlines()[0]
    assert first == "SET LOCAL lock_timeout = '3s';"


async def test_monitor_verdict_unique_and_decided_is_final(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        bid = await _batch(conn)
        await _verdict(conn, bid, "decided", 1)
        await _verdict(conn, bid, "error", None, ek="code:refusal@v1")
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await _verdict(conn, bid, "decided", 0)                       # same evaluator and answer
    async with engine.begin() as conn:                                    # an error row may be retried
        await conn.execute(text("UPDATE plenum_tracelab.monitor_verdicts SET status = 'decided', failure_present = 0 "
                                "WHERE evaluator_key = 'code:refusal@v1'"))
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:                                # a decided row never changes
            await conn.execute(text("UPDATE plenum_tracelab.monitor_verdicts SET failure_present = 0 "
                                    "WHERE evaluator_key = 'code:no_answer@v1'"))
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM plenum_tracelab.monitor_verdicts"))
    async with engine.begin() as conn:                                    # only company offboarding deletes
        await conn.execute(text("SET LOCAL plenum_tracelab.allow_purge = 'on'"))
        res = await conn.execute(text("DELETE FROM plenum_tracelab.monitor_verdicts"))
        assert res.rowcount == 2


async def test_one_active_batch_per_scope(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await _batch(conn, ORG_A)
        await _batch(conn, None)                                          # one company and the platform: fine
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await _batch(conn, ORG_A, "running")
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await _batch(conn, None)
    async with engine.begin() as conn:
        await _batch(conn, ORG_A, "done")                                 # finished batches never block
        await _batch(conn, ORG_B)


async def test_eval_case_kind_baseline_checks(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        twin = await _golden_case(conn, await _regression_set(conn))
        await _case(conn, status="proposed")                              # a proposed case has no twin yet
        await _case(conn, status="active", kind="regression", twin=twin)
        await _case(conn, status="active", kind="capability", rate=0.6, twin=twin)
    for bad in (dict(status="draft"),                                     # no twin
                dict(status="active", twin=twin),                         # active but unclassified
                dict(status="active", kind="capability", twin=twin),      # capability without a rate
                dict(status="active", kind="regression", rate=0.4, twin=twin),
                dict(status="draft", kind="capability", rate=0.5, twin=twin)):  # 0.5 is not a 5-run rate
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await _case(conn, **bad)
```

The Regression cases set shape, the 20-rep caps and the `monitor_flagged` strategy are not tested here: M3's `test_golden_store_sql.py` and M2's `test_trace_lab_a_turn_is_never_in_two_batches_sql.py` own those tests, and `0006` no longer touches the objects. The `_regression_set`, `_golden_case`, `_golden_run` and `_job` helpers above are used by this task's eval-case test and by Task 3's offboarding test.

- [ ] **Step 2: Run it and see it fail**

Start the scratch database once (see M6 constraints), then run: `da_test tests/test_trace_lab_watch_sql.py`
Expected: every test fails in `fresh_schema` / `verify` (`applied_version` is 5, or `FileNotFoundError: …0006_watch.sql`).

- [ ] **Step 3: Implement**

Create `DA/migrations/trace_lab/0006_watch.sql` (no apostrophes anywhere, nothing after a `;` on its line, function bodies in `$$`):

```sql
SET LOCAL lock_timeout = '3s';

CREATE TABLE plenum_tracelab.monitor_batches (
    id                UUID PRIMARY KEY,
    organization_id   UUID,
    period_label      TEXT NOT NULL CHECK (length(period_label) BETWEEN 3 AND 40),
    window_start      TIMESTAMPTZ NOT NULL,
    window_end        TIMESTAMPTZ NOT NULL,
    channel           TEXT NOT NULL DEFAULT 'chat' CHECK (channel = 'chat'),
    sample_rate       NUMERIC(5,4) NOT NULL CHECK (sample_rate > 0 AND sample_rate <= 1),
    seed              INTEGER NOT NULL,
    risk_groups       TEXT[] NOT NULL,
    judges            JSONB NOT NULL,
    checks            JSONB NOT NULL,
    plan              JSONB,
    results           JSONB,
    status            TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'capped', 'failed', 'interrupted')),
    job_id            UUID REFERENCES plenum_tracelab.jobs (id),
    usd               NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (usd >= 0),
    usd_cap           NUMERIC(12,4) CHECK (usd_cap IS NULL OR usd_cap >= 0),
    started_by        UUID,
    started_by_email  TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    error             TEXT CHECK (error IS NULL OR length(error) <= 2000),
    CONSTRAINT ck_monitor_batches_window CHECK (window_end > window_start)
);
CREATE UNIQUE INDEX ux_monitor_batches_active ON plenum_tracelab.monitor_batches
    (COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE status IN ('queued', 'running');
CREATE INDEX ix_monitor_batches_period ON plenum_tracelab.monitor_batches (organization_id, period_label, started_at DESC);
CREATE INDEX ix_monitor_batches_started ON plenum_tracelab.monitor_batches (started_at DESC);
COMMENT ON TABLE plenum_tracelab.monitor_batches IS 'One monitoring run over a window of chat answers. organization_id NULL means platform scope (R2).';

CREATE TABLE plenum_tracelab.monitor_verdicts (
    id                UUID PRIMARY KEY,
    evaluator_key     TEXT NOT NULL CHECK (evaluator_key ~ '^(judge:[0-9a-f-]{36}|code:[a-z][a-z0-9_]*@v[0-9]+)$'),
    turn_id           TEXT NOT NULL,
    organization_id   UUID NOT NULL,
    turn_started_at   TIMESTAMPTZ NOT NULL,
    judge_id          UUID REFERENCES plenum_tracelab.judges (id),
    mode_id           UUID REFERENCES plenum_tracelab.modes (id),
    status            TEXT NOT NULL CHECK (status IN ('decided', 'error', 'undecidable', 'not_applicable')),
    failure_present   SMALLINT CHECK (failure_present IN (0, 1)),
    detail            JSONB,
    first_batch_id    UUID NOT NULL REFERENCES plenum_tracelab.monitor_batches (id),
    attempts          INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_monitor_verdicts_evaluator_turn UNIQUE (evaluator_key, turn_id),
    CONSTRAINT ck_monitor_verdicts_decided CHECK ((status = 'decided') = (failure_present IS NOT NULL)),
    CONSTRAINT ck_monitor_verdicts_judge CHECK ((evaluator_key LIKE 'judge:%') = (judge_id IS NOT NULL))
);
CREATE INDEX ix_monitor_verdicts_week ON plenum_tracelab.monitor_verdicts (evaluator_key, turn_started_at);
CREATE INDEX ix_monitor_verdicts_turn ON plenum_tracelab.monitor_verdicts (turn_id);
CREATE INDEX ix_monitor_verdicts_org_time ON plenum_tracelab.monitor_verdicts (organization_id, updated_at);
COMMENT ON TABLE plenum_tracelab.monitor_verdicts IS 'One verdict per evaluator and answer, shared by overlapping batches. Never on the purge keep-list (R4).';

CREATE FUNCTION plenum_tracelab.monitor_verdicts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('plenum_tracelab.allow_purge', true), '') = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'monitor_verdicts: rows are removed only with company data (%)', OLD.turn_id;
    END IF;
    IF OLD.status = 'decided' THEN
        RAISE EXCEPTION 'monitor_verdicts: a decided verdict is never changed (% / %)', OLD.evaluator_key, OLD.turn_id;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER monitor_verdicts_guard BEFORE UPDATE OR DELETE ON plenum_tracelab.monitor_verdicts
    FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.monitor_verdicts_guard();

CREATE TABLE plenum_tracelab.eval_cases (
    id                     UUID PRIMARY KEY,
    organization_id        UUID NOT NULL,
    case_key               TEXT NOT NULL CHECK (case_key ~ '^e-[0-9a-f]{8}$'),
    version                INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status                 TEXT NOT NULL CHECK (status IN ('proposed', 'draft', 'baselining', 'active', 'retired', 'superseded')),
    kind                   TEXT NOT NULL DEFAULT 'unclassified' CHECK (kind IN ('unclassified', 'regression', 'capability')),
    baseline_pass_rate     NUMERIC(3,2) CHECK (baseline_pass_rate IN (0.00, 0.20, 0.40, 0.60, 0.80)),
    baseline_run_id        UUID REFERENCES plenum_tracelab.golden_runs (id),
    mode_id                UUID REFERENCES plenum_tracelab.modes (id),
    source_type            TEXT NOT NULL CHECK (source_type IN ('live_turn', 'monitor', 'golden_case')),
    source_turn_id         TEXT,
    source_batch_id        UUID REFERENCES plenum_tracelab.monitor_batches (id),
    source_golden_case_id  UUID REFERENCES plenum_tracelab.golden_cases (id),
    golden_case_id         UUID REFERENCES plenum_tracelab.golden_cases (id),
    input                  JSONB NOT NULL,
    initial_state          JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected               JSONB NOT NULL,
    supersedes             UUID REFERENCES plenum_tracelab.eval_cases (id),
    retired_reason         TEXT CHECK (retired_reason IS NULL OR length(retired_reason) <= 500),
    rev                    INTEGER NOT NULL DEFAULT 1 CHECK (rev >= 1),
    created_by             UUID,
    created_by_email       TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_eval_cases_key_version UNIQUE (organization_id, case_key, version),
    CONSTRAINT ck_eval_cases_capability_rate CHECK ((kind = 'capability') = (baseline_pass_rate IS NOT NULL)),
    CONSTRAINT ck_eval_cases_active_classified CHECK (status <> 'active' OR kind <> 'unclassified'),
    CONSTRAINT ck_eval_cases_twin CHECK (golden_case_id IS NOT NULL OR status IN ('proposed', 'retired')),
    CONSTRAINT ck_eval_cases_golden_source CHECK ((source_type = 'golden_case') = (source_golden_case_id IS NOT NULL)),
    CONSTRAINT ck_eval_cases_turn_source CHECK (source_type = 'golden_case' OR source_turn_id IS NOT NULL),
    CONSTRAINT ck_eval_cases_monitor_source CHECK (source_type <> 'monitor' OR source_batch_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_eval_cases_current ON plenum_tracelab.eval_cases (organization_id, case_key)
    WHERE status <> 'superseded';
CREATE INDEX ix_eval_cases_org ON plenum_tracelab.eval_cases (organization_id, status, kind);
CREATE INDEX ix_eval_cases_source_turn ON plenum_tracelab.eval_cases (source_turn_id);
COMMENT ON COLUMN plenum_tracelab.eval_cases.golden_case_id IS 'The runnable twin in the company Regression cases golden set (R9). NULL only while proposed or retired.';

CREATE TABLE plenum_tracelab.eval_case_runs (
    id              UUID PRIMARY KEY,
    eval_case_id    UUID NOT NULL REFERENCES plenum_tracelab.eval_cases (id),
    golden_run_id   UUID NOT NULL REFERENCES plenum_tracelab.golden_runs (id),
    purpose         TEXT NOT NULL CHECK (purpose IN ('baseline', 'release_check', 'repeat')),
    k               INTEGER NOT NULL CHECK (k BETWEEN 1 AND 20),
    n               INTEGER NOT NULL DEFAULT 0 CHECK (n >= 0),
    passes          INTEGER NOT NULL DEFAULT 0 CHECK (passes >= 0),
    degraded        INTEGER NOT NULL DEFAULT 0 CHECK (degraded >= 0),
    pending         INTEGER NOT NULL DEFAULT 0 CHECK (pending >= 0),
    reps            JSONB NOT NULL DEFAULT '[]'::jsonb,
    graders         JSONB NOT NULL DEFAULT '{}'::jsonb,
    pass_at_1       NUMERIC(6,4),
    pass_at_3       NUMERIC(6,4),
    pass_at_5       NUMERIC(6,4),
    pass_hat_k      NUMERIC(6,4),
    summary         JSONB,
    decision        TEXT NOT NULL DEFAULT 'incomplete' CHECK (decision IN ('pass', 'block', 'incomplete')),
    reason          TEXT NOT NULL DEFAULT 'running',
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_eval_case_runs_case_run UNIQUE (eval_case_id, golden_run_id),
    CONSTRAINT ck_eval_case_runs_counts CHECK (passes <= n)
);
CREATE INDEX ix_eval_case_runs_run ON plenum_tracelab.eval_case_runs (golden_run_id);
COMMENT ON TABLE plenum_tracelab.eval_case_runs IS 'One row per eval case per golden run. Company scope comes through the case (R2).';
```

Then regenerate `catalog.json` with M0's `tlsnap.sh` (M0 plan, "Before you start"; the only supported way to change `catalog.json`). It applies every file to the guarded `tracelab_scratch` database through `python -m src.trace_lab.migrate snapshot`, at the real repo depth, with every DB URL spelling (`DB_URL`, `db_url`, `DATABASE_URL`, `database_url`) pinned to a dead address. If this session has not saved the script yet, save it to your scratchpad exactly as M0 wrote it:

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

With the scratch Postgres running, run `sh tlsnap.sh` (its full saved path). Expected: M0's `wrote …/catalog.json: version 6, server 16…` line, then `catalog.json copied back to …`. Then check the version: `python3 -c "import json;d=json.load(open('apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/catalog.json'));print(d['version'])"` must print `6`.

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_migrate.py tests/test_trace_lab_watch_sql.py`
Expected: all of M0's file-rule tests (named, contiguous, lock_timeout first, one schema, no `IF NOT EXISTS`/`CONCURRENTLY`/`GRANT`) pass for 0006, and the 4 watch SQL tests pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/migrations/trace_lab/0006_watch.sql`, `DA/migrations/trace_lab/catalog.json`, `DA/tests/test_trace_lab_watch_sql.py`. Do not add 0006 to `SHIPPED_CHECKSUMS` (Hussain adds the line the CLI prints after he applies it).

---

### Task 3: Keep-list and offboarding at version 6 (eval cases kept, monitor verdicts not — R4)

**Files:**
- Modify: `DA/src/trace_lab/db.py` (append `KEEP_BY_VERSION[6]` and `OFFBOARD_BY_VERSION[6]` at the end of the file)
- Modify: `DA/tests/test_trace_lab_purge_sql.py` (M0's `EXCUSED_TURN_COLUMNS`)
- Test: `DA/tests/test_trace_lab_watch_keep_list.py`; append to `DA/tests/test_trace_lab_watch_sql.py`

**Interfaces:**
- Consumes: `db.keep_tables()`, `db.offboarding_steps(applied_version)`, `db.delete_company_rows(organization_id, *, actor_id, dry_run, job_id=None)`, `db.STATE`, `migrate.SchemaState`, `trace.purge(days=None, *, keep)` (M0's replacement of `DA/src/agents/trace.py:133-146`); `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS` (M0; M2 added `annotation_events.turn_id`).
- Produces: `db.keep_tables()` at version ≥ 6 contains `("plenum_tracelab.eval_cases", "source_turn_id")` and never a `plenum_tracelab.monitor_verdicts` entry; `db.OFFBOARD_BY_VERSION[6]` = steps `eval_case_runs`, `eval_cases`, `monitor_verdicts`, `monitor_batches` (in that order, ahead of every earlier version's steps, so a company's monitor batches no longer block version 1's `jobs` step); `("plenum_tracelab.monitor_verdicts", "turn_id")` in `EXCUSED_TURN_COLUMNS`.

- [ ] **Step 0: Locate the anchors** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/backend/cafm-connector-service-final/svc-deepagents
grep -n "^KEEP_BY_VERSION\|^OFFBOARD_BY_VERSION\|^    1: " src/trace_lab/db.py
grep -n "plenum_tracelab.eval_cases\|plenum_tracelab.monitor_verdicts\|plenum_tracelab.monitor_batches" src/trace_lab/db.py
grep -n "EXCUSED_TURN_COLUMNS\|annotation_events" tests/test_trace_lab_purge_sql.py
```

The first grep shows M0's two literals (`{1: ...}`) and the statements M2–M5 appended (`KEEP_BY_VERSION[2]`, `[3]`, `[4]`, `[5]`, and the matching `OFFBOARD_BY_VERSION[N]` lines). The second grep prints nothing: no milestone before M6 names M6's tables. The third shows M0's `EXCUSED_TURN_COLUMNS = {` set and M2's `("plenum_tracelab.annotation_events", "turn_id"),` entry, the line Step 3 extends. Keep entries and offboarding steps are registered by appending a statement at the end of `db.py` (the form M2 and M3 use); there is no literal `5:` line to edit.

- [ ] **Step 1: Write the failing test**

Create `DA/tests/test_trace_lab_watch_keep_list.py`:

```python
"""Span-purge keep-list at version 6 (D5, R4): eval-case source turns are kept, monitor verdicts never.

Code checks write a monitor verdict for every chat answer; keeping them would stop the purge entirely.
Flagged answers are protected once they are opened as a review batch (sample_items). 15 Oct 2026."""
from __future__ import annotations

from datetime import datetime, timezone

from src.trace_lab import db, migrate


def _ready_at(version: int) -> migrate.SchemaState:
    return migrate.SchemaState(status="ready", expected_version=version, applied_version=version, pending=[],
                               problems=[], checked_at=datetime.now(timezone.utc))


def test_version_6_keeps_eval_case_source_turns(monkeypatch):
    monkeypatch.setattr(db, "STATE", _ready_at(6))
    assert ("plenum_tracelab.eval_cases", "source_turn_id") in db.keep_tables()


def test_monitor_verdicts_are_never_on_the_keep_list(monkeypatch):
    monkeypatch.setattr(db, "STATE", _ready_at(6))
    assert all(table != "plenum_tracelab.monitor_verdicts" for table, _ in db.keep_tables())


def test_version_5_does_not_yet_name_eval_cases(monkeypatch):
    monkeypatch.setattr(db, "STATE", _ready_at(5))
    assert ("plenum_tracelab.eval_cases", "source_turn_id") not in db.keep_tables()


def test_monitor_verdict_turns_are_excused_not_kept():
    from test_trace_lab_purge_sql import EXCUSED_TURN_COLUMNS          # M0's excuse registry (a sibling test module)

    assert ("plenum_tracelab.monitor_verdicts", "turn_id") in EXCUSED_TURN_COLUMNS
    assert ("plenum_tracelab.eval_cases", "source_turn_id") not in EXCUSED_TURN_COLUMNS   # kept, not excused


def test_version_6_offboarding_runs_first_and_before_the_jobs_step():
    labels = [label for label, _ in db.offboarding_steps(6)]
    assert labels[:4] == ["eval_case_runs", "eval_cases", "monitor_verdicts", "monitor_batches"]
    assert labels.index("monitor_batches") < labels.index("jobs")        # monitor_batches.job_id references jobs
    assert all(":org" in sql for _, sql in db.OFFBOARD_BY_VERSION[6])
    assert "monitor_batches" not in [label for label, _ in db.offboarding_steps(5)]
    case_runs = dict(db.OFFBOARD_BY_VERSION[6])["eval_case_runs"]
    assert "eval_case_id IN (SELECT id FROM plenum_tracelab.eval_cases" in case_runs   # R2: no organization_id of its own
```

Append to `DA/tests/test_trace_lab_watch_sql.py`:

```python
async def test_purge_keeps_eval_case_turns_but_not_monitor_verdict_turns(monkeypatch):
    from src.agents import trace

    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(db, "STATE", await migrate.verify(engine))
    old = datetime.now(UTC) - timedelta(days=200)
    async with engine.begin() as conn:
        for tid in ("turn-00000000000000c1", "turn-00000000000000c2"):
            await conn.execute(text("INSERT INTO plenum_cafm.agent_trace_turns (turn_id, organization_id, started_at) "
                                    "VALUES (:t, CAST(:o AS uuid), :ts)"), {"t": tid, "o": ORG_A, "ts": old})
            await conn.execute(text("INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, seq, kind, name, started_at) "
                                    "VALUES (CAST(:id AS uuid), :t, 1, 'tool', 'step', :ts)"),
                               {"id": str(uuid.uuid4()), "t": tid, "ts": old})
        bid = await _batch(conn, status="done")
        await _verdict(conn, bid, "decided", 1, turn="turn-00000000000000c1")
        await _case(conn, status="proposed", turn="turn-00000000000000c2")
    assert await trace.purge(keep=db.keep_tables()) == 1
    async with engine.connect() as conn:
        left = (await conn.execute(text("SELECT turn_id FROM plenum_cafm.agent_trace_spans"))).scalars().all()
    assert left == ["turn-00000000000000c2"]


async def test_company_offboarding_deletes_watch_rows_before_the_jobs_step(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(db, "STATE", await migrate.verify(engine))
    async with engine.begin() as conn:
        sid = await _regression_set(conn)
        twin = await _golden_case(conn, sid)
        case = await _case(conn, status="active", kind="regression", twin=twin)
        run = await _golden_run(conn, sid, k=5, purpose="release_check")
        await conn.execute(text("INSERT INTO plenum_tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (CAST(:id AS uuid), CAST(:c AS uuid), CAST(:r AS uuid), 'release_check', 5)"),
                           {"id": str(uuid.uuid4()), "c": case, "r": run})
        mine = await _batch(conn, ORG_A, "done")
        batch_job = await _job(conn, kind="monitor_batch")                # the job a company batch holds
        await conn.execute(text("UPDATE plenum_tracelab.monitor_batches SET job_id = CAST(:j AS uuid) "
                                "WHERE id = CAST(:b AS uuid)"), {"j": batch_job, "b": mine})
        await _verdict(conn, mine, "decided", 1)
        theirs = await _batch(conn, ORG_B, "done")
        await _verdict(conn, theirs, "decided", 0, turn="turn-00000000000000a2", org=ORG_B)
    counts = await db.delete_company_rows(ORG_A, actor_id=ACTOR, dry_run=False)
    assert (counts["eval_case_runs"], counts["eval_cases"], counts["monitor_verdicts"], counts["monitor_batches"]) == (1, 1, 1, 1)
    async with engine.connect() as conn:
        left = {t: (await conn.execute(text(f"SELECT count(*) FROM plenum_tracelab.{t}"))).scalar()
                for t in ("eval_case_runs", "eval_cases", "monitor_verdicts", "monitor_batches")}
    assert left == {"eval_case_runs": 0, "eval_cases": 0, "monitor_verdicts": 1, "monitor_batches": 1}   # the other company's stay
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_watch_keep_list.py tests/test_trace_lab_watch_sql.py`
Expected: `test_version_6_keeps_eval_case_source_turns`, `test_version_6_offboarding_runs_first_and_before_the_jobs_step` (`KeyError: 6`) and `test_monitor_verdict_turns_are_excused_not_kept` fail; the purge test fails with `assert 2 == 1` (nothing is kept yet) or, if the keep entry is missing, M0's `test_keep_tables_cover_every_turn_reference` fails naming `eval_cases.source_turn_id` and `monitor_verdicts.turn_id`; the offboarding test fails with `KeyError: 'eval_case_runs'` (and, without version 6's steps, version 1's `jobs` delete would hit `monitor_batches_job_id_fkey`).

- [ ] **Step 3: Implement**

Append to the end of `DA/src/trace_lab/db.py` (never edit M0's `{1: ...}` literals; M2–M5 registered theirs the same way):

```python


# ── M6: span-purge keep-list, version 6 (0006_watch.sql; spec "Data model" 3, R4) ───────────
# monitor_verdicts.turn_id is excused in M0's tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS.
KEEP_BY_VERSION[6] = (("plenum_tracelab.eval_cases", "source_turn_id"),)

# ── M6: company offboarding, version 6 (spec "Data model" 5.3) ──────────────────────────────
# Runs before every earlier version's steps (offboarding_steps walks the versions downward) and under allow_purge,
# which the monitor_verdicts guard honours. eval_case_runs has no organization_id (R2), so it goes through its
# cases; verdicts go before the batches they cite; monitor_batches.job_id references jobs, so the batches must be
# gone before version 1's jobs step.
OFFBOARD_BY_VERSION[6] = (
    ("eval_case_runs", "DELETE FROM plenum_tracelab.eval_case_runs WHERE eval_case_id IN (SELECT id FROM plenum_tracelab.eval_cases WHERE organization_id = CAST(:org AS uuid))"),
    ("eval_cases", "DELETE FROM plenum_tracelab.eval_cases WHERE organization_id = CAST(:org AS uuid)"),
    ("monitor_verdicts", "DELETE FROM plenum_tracelab.monitor_verdicts WHERE organization_id = CAST(:org AS uuid)"),
    ("monitor_batches", "DELETE FROM plenum_tracelab.monitor_batches WHERE organization_id = CAST(:org AS uuid)"),
)
```

In `DA/tests/test_trace_lab_purge_sql.py`, one exact-string edit (M2's entry is the last one in the set):

old:
```python
    ("plenum_tracelab.annotation_events", "turn_id"),
```
new:
```python
    ("plenum_tracelab.annotation_events", "turn_id"),
    # M6 (R4): code checks write a monitor verdict for every chat answer; keeping them would stop the purge.
    # An answer a verdict cites is protected once it is opened as a review batch (sample_items) or becomes a case.
    ("plenum_tracelab.monitor_verdicts", "turn_id"),
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_watch_keep_list.py tests/test_trace_lab_watch_sql.py tests/test_trace_lab_migrate.py tests/test_trace_lab_purge_sql.py`
Expected: all pass, including M0's `test_keep_tables_cover_every_turn_reference` and `test_purge_spares_every_kept_turn`. If M0's own offboarding tests compared exact step maps they would fail here; they copy only version 1 and compare non-zero counts, so they stay green.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/db.py`, `DA/tests/test_trace_lab_purge_sql.py`, `DA/tests/test_trace_lab_watch_keep_list.py`, `DA/tests/test_trace_lab_watch_sql.py`.

---

### Task 4: `stats.py` additions — `case_decision`, `summarize_rollouts`, `iso_week_label`, `REPLAY_BOOTSTRAP_REPS`

**Files:**
- Modify: `DA/src/trace_lab/stats.py` (M2's file; append at the end)
- Test: `DA/tests/test_trace_lab_stats_watch.py` (create)

**Interfaces:**
- Consumes: M2's `stats.wilson_interval(successes, total, z=Z95) -> (low, high)` (called by that name). M3 appended `pass_at_k` / `pass_hat_k` and M5 appended `corrected_prevalence` and its bootstrap helpers to the same file; this task adds none of their names.
- Produces: `REPLAY_BOOTSTRAP_REPS = 2_000`; `case_decision(kind, passes, n, baseline_pass_rate=None, *, planned=None, not_decided=0) -> {"decision": "pass" | "block" | "incomplete", "reason": str}`; `summarize_rollouts(records, confidence=0.95, bootstrap_iterations=REPLAY_BOOTSTRAP_REPS, seed=7) -> {n, failures, failure_rate, ci_low, ci_high, mode_counts, steps, wilson_high}` over records `{"passed": bool, "failures": [str], "steps": int}`; `iso_week_label(ts) -> "YYYY-Www"`.

Why this task exists: M5's plan says "M6 adds" these names and the monitor and eval-case code below assumes M5 has them, so nobody defined them (an `AttributeError` on the first call). They belong in `stats.py` beside the other estimators, and M6 is the only plan that needs them.

- [ ] **Step 0: Check the anchor** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/backend/cafm-connector-service-final/svc-deepagents
grep -n "^def \|^Z95\|^REPLAY_BOOTSTRAP_REPS" src/trace_lab/stats.py
```

Expected: M2's `Z95 = 1.96`, `def wilson_interval(` and `def cohen_kappa(`, M3's `def pass_at_k(` and `def pass_hat_k(`, M5's `def confusion(` and `def corrected_prevalence(`, and none of `case_decision`, `summarize_rollouts`, `iso_week_label`, `REPLAY_BOOTSTRAP_REPS`. If the file is missing, M2 has not landed: stop and report (never create `stats.py` here).

- [ ] **Step 1: Write the failing test**

Create `DA/tests/test_trace_lab_stats_watch.py`:

```python
"""The stats helpers M6 (Watch) appends to M2's stats.py: the eval-case decision, the replay bootstrap summary and
the ISO week label. 15 Oct 2026. Pure functions: no database, no model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import stats

UTC = timezone.utc


def test_case_decision_repo_rule():
    ok = stats.case_decision("regression", 5, 5, None, planned=5, not_decided=0)
    assert ok == {"decision": "pass", "reason": "regression case passed 5 of 5 runs"}
    blocked = stats.case_decision("regression", 4, 5, None, planned=5, not_decided=0)
    assert blocked == {"decision": "block", "reason": "regression case failed 1 of 5 runs"}
    unsure = stats.case_decision("regression", 4, 4, None, planned=5, not_decided=1)
    assert unsure == {"decision": "incomplete", "reason": "regression case has 1 runs without a verdict of 5; "
                                                          "incomplete evidence never passes"}
    assert stats.case_decision("regression", 3, 3, None, planned=5, not_decided=0)["decision"] == "incomplete"   # n < planned
    assert stats.case_decision("regression", 0, 0, None, planned=5, not_decided=5)["decision"] == "incomplete"   # nothing ran
    assert stats.case_decision("regression", 3, 4, None, planned=5, not_decided=1)["decision"] == "block"        # a failure decides
    cap = stats.case_decision("capability", 2, 5, 0.4, planned=5, not_decided=0)
    assert cap == {"decision": "pass", "reason": "capability case passed 2 of 5, baseline 0.4, not blocking"}
    assert stats.case_decision("capability", 0, 5, 0.2)["decision"] == "pass"       # a capability case never blocks
    with pytest.raises(ValueError):
        stats.case_decision("unclassified", 5, 5)


def test_summarize_rollouts_repo_vector():
    records = [{"passed": True, "failures": [], "steps": 2},
               {"passed": False, "failures": ["judge:a"], "steps": 4},
               {"passed": False, "failures": ["judge:a", "check:b"], "steps": 6}]
    first = stats.summarize_rollouts(records, bootstrap_iterations=100, seed=7)
    assert first == stats.summarize_rollouts(records, bootstrap_iterations=100, seed=7)   # same seed, same interval
    assert first["n"] == 3 and first["failures"] == 2
    assert first["failure_rate"] == pytest.approx(2 / 3)
    assert 0.0 <= first["ci_low"] <= 2 / 3 <= first["ci_high"] <= 1.0
    assert first["mode_counts"] == {"judge:a": 2, "check:b": 1}          # a record listing two modes counts in both
    assert first["steps"] == {"min": 2, "median": 4, "max": 6}
    assert first["wilson_high"] == stats.wilson_interval(2, 3)[1]
    assert stats.REPLAY_BOOTSTRAP_REPS == 2_000
    assert stats.summarize_rollouts([{"passed": True}])["steps"] is None
    with pytest.raises(ValueError):
        stats.summarize_rollouts([])
    with pytest.raises(ValueError):
        stats.summarize_rollouts(records, confidence=1.5)


def test_iso_week_label():
    assert stats.iso_week_label(datetime(2026, 10, 5, tzinfo=UTC)) == "2026-W41"
    assert stats.iso_week_label(datetime(2025, 12, 29, tzinfo=UTC)) == "2026-W01"     # the ISO year, not the calendar year
    assert stats.iso_week_label(datetime(2026, 1, 1, 9, tzinfo=UTC)) == "2026-W01"
    late = datetime(2026, 10, 11, 23, 30, tzinfo=timezone(timedelta(hours=-5)))        # Monday 04:30 UTC
    assert stats.iso_week_label(late) == "2026-W42"
    assert stats.iso_week_label(datetime(2026, 10, 12)) == "2026-W42"                  # a naive time is read as UTC
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_stats_watch.py`
Expected: `AttributeError: module 'src.trace_lab.stats' has no attribute 'case_decision'` (and `summarize_rollouts`, `iso_week_label`, `REPLAY_BOOTSTRAP_REPS`).

- [ ] **Step 3: Implement**

Append to the end of `DA/src/trace_lab/stats.py` (never rewrite the file; M3 and M5 appended theirs the same way):

```python


# ── M6 (Watch): the eval-case decision, the replay bootstrap and the week label ──────────────
import random as _random  # noqa: E402
from collections import Counter as _Counter  # noqa: E402
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402
from statistics import median as _median  # noqa: E402

REPLAY_BOOTSTRAP_REPS = 2_000          # the replay harness default (CW replay/harness.py); seed 7


def case_decision(kind: str, passes: int, n: int, baseline_pass_rate: float | None = None, *,
                  planned: int | None = None, not_decided: int = 0) -> dict:
    """What one eval case decides on one run (spec "Judges" 5.5). A regression case must pass every run that was
    planned: one failed run blocks; a run without a verdict (degraded, pending, ungraded or missing), or fewer runs
    than planned, is incomplete, and incomplete evidence never passes. A capability case never blocks: it reports
    its passes against the baseline it was classified with."""
    if kind == "capability":
        b = "none" if baseline_pass_rate is None else f"{float(baseline_pass_rate):g}"
        return {"decision": "pass", "reason": f"capability case passed {passes} of {n}, baseline {b}, not blocking"}
    if kind != "regression":
        raise ValueError("an eval case is a regression or a capability case")
    if passes < n:
        return {"decision": "block", "reason": f"regression case failed {n - passes} of {n} runs"}
    total = int(planned) if planned else n + int(not_decided)
    missing = max(int(not_decided), total - n)
    if n == 0 or missing > 0:
        return {"decision": "incomplete",
                "reason": f"regression case has {missing} runs without a verdict of {total}; "
                          "incomplete evidence never passes"}
    return {"decision": "pass", "reason": f"regression case passed {n} of {n} runs"}


def summarize_rollouts(records: list[dict], confidence: float = 0.95,
                       bootstrap_iterations: int = REPLAY_BOOTSTRAP_REPS, seed: int = 7) -> dict:
    """The failure-distribution measurement of repeated runs of one answer (repeat-n): a port of CW
    replay/harness.py:104-187. Records are {"passed": bool, "failures": [str], "steps": int}. The interval is a
    percentile bootstrap of the failure rate: random.Random(seed).choices, n records per resample, the sorted
    rates indexed with int(q * (len - 1)). mode_counts counts each failure across failing records (a record listing
    two failures counts in both); steps is {min, median, max} over records that carry steps, else None."""
    if not records:
        raise ValueError("records cannot be empty")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap_iterations must be at least 1")
    n = len(records)
    failures = sum(not bool(r.get("passed")) for r in records)
    rng = _random.Random(seed)
    rates = []
    for _ in range(bootstrap_iterations):
        rates.append(sum(not bool(r.get("passed")) for r in rng.choices(records, k=n)) / n)
    rates.sort()
    alpha = 1 - confidence
    modes: _Counter = _Counter()
    for r in records:
        if not r.get("passed"):
            modes.update(r.get("failures") or r.get("failure_modes") or [])
    step_values = sorted(int(r["steps"]) for r in records if r.get("steps") is not None)
    return {"n": n, "failures": failures, "failure_rate": failures / n,
            "ci_low": rates[int((alpha / 2) * (len(rates) - 1))],
            "ci_high": rates[int((1 - alpha / 2) * (len(rates) - 1))],
            "mode_counts": dict(modes),
            "steps": ({"min": step_values[0], "median": _median(step_values), "max": step_values[-1]}
                      if step_values else None),
            "wilson_high": wilson_interval(failures, n)[1]}


def iso_week_label(ts: _datetime) -> str:
    """'2026-W41': the ISO year and week of ts in UTC (a naive time is read as UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_timezone.utc)
    year, week, _ = ts.astimezone(_timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_stats_watch.py` → `3 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/stats.py`, `DA/tests/test_trace_lab_stats_watch.py`.

---

### Task 5: `monitor.py` — windows, the uniform-plus-risk plan, risk groups and re-asks (pure)

**Files:**
- Create: `DA/src/trace_lab/monitor.py`
- Test: `DA/tests/test_trace_lab_monitor.py`

**Interfaces:**
- Consumes: `stats.iso_week_label(ts) -> str` (Task 4); `trace.DEFAULT_RETENTION_DAYS` (`DA/src/agents/trace.py:49`).
- Produces: `WatchError(status: int, reason: str, message: str, **extra)`; constants `DEFAULT_RATE = 0.25`, `DEFAULT_SEED = 7`, `CHUNK = 50`, `FLAGGED_REVIEW_MAX = 50`, `PROPOSE_CASES_MAX = 5`, `RE_ASK_MINUTES = 10`, `RE_ASK_JACCARD = 0.5`, `CHEAP_GROUPS`, `RISK_GROUPS: dict[str, Callable[[dict], bool]]`, `RISK_LABELS: dict[str, str]`; `monitor_usd_cap() -> float`; `retention_days() -> int`; `_field(obj, key, default=None)`; `_json(value)`; `public(value)`; `_utc(ts)`; `last_complete_week(now) -> (datetime, datetime)`; `period_label(start, end) -> str`; `check_window(start, end, *, now, days=None) -> None`; `weeks_back(now, n) -> list[(label, start, end)]`; `jaccard(a, b) -> float`; `mark_re_asks(turns) -> set[str]`; `select_turns(turns, rate, groups, seed=7) -> {"n_window", "random", "risk_groups", "to_judge"}`.

- [ ] **Step 1: Write the failing test**

```python
"""Monitoring (M6, spec Judges... section 4): plan, estimate, worker, threshold action, reads, trend. 15 Oct 2026.

No database and no model: store functions, judges.ASK and the job helpers are replaced per test."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import monitor

UTC = timezone.utc
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)            # a Thursday; last complete week is 2026-W41
ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _ids(n):
    return [{"turn_id": f"t{i}"} for i in range(1, n + 1)]


def test_select_turns_hw7_vector():
    turns = _ids(10)
    before = copy.deepcopy(turns)
    groups = {"g1": lambda t: t["turn_id"] in ("t1", "t4"), "g2": lambda t: t["turn_id"] in ("t4", "t7")}
    plan = monitor.select_turns(turns, 0.2, groups, seed=7)
    assert plan["n_window"] == 10 and len(plan["random"]) == 2
    assert plan["risk_groups"] == {"g1": ["t1", "t4"], "g2": ["t4", "t7"]}
    assert len(plan["to_judge"]) == len(set(plan["to_judge"]))
    assert {"t1", "t4", "t7"} <= set(plan["to_judge"])
    assert plan["to_judge"][:2] == plan["random"]                     # uniform picks are judged first
    assert turns == before                                             # inputs never mutated


def test_same_seed_same_plan():
    groups = {"failed": lambda t: t["turn_id"] == "t3"}
    assert monitor.select_turns(_ids(40), 0.25, groups, seed=7) == monitor.select_turns(_ids(40), 0.25, groups, seed=7)


def test_rate_outside_0_1_raises():
    for rate in (0, -0.1, 1.5):
        with pytest.raises(ValueError):
            monitor.select_turns(_ids(4), rate, {}, seed=7)
    assert len(monitor.select_turns(_ids(4), 1.0, {}, seed=7)["random"]) == 4


def test_missing_turn_id_raises():
    with pytest.raises(ValueError):
        monitor.select_turns([{"turn_id": "t1"}, {"turn_id": ""}], 0.5, {}, seed=7)


def test_window_never_exceeds_retention():
    monitor.check_window(NOW - timedelta(days=89), NOW - timedelta(days=80), now=NOW, days=90)
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=89, hours=1), NOW - timedelta(days=80), now=NOW, days=90)
    assert e.value.status == 409 and e.value.reason == "window_beyond_retention"
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=1), NOW + timedelta(hours=1), now=NOW, days=90)
    assert e.value.status == 422 and e.value.reason == "window_in_future"
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=1), NOW - timedelta(days=2), now=NOW, days=90)
    assert e.value.reason == "bad_window"


def test_last_complete_week_period_label_and_weeks_back():
    start, end = monitor.last_complete_week(NOW)
    assert (start, end) == (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
    assert monitor.period_label(start, end) == "2026-W41"
    assert monitor.period_label(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)) \
        == "2026-10-01..2026-10-14"
    weeks = monitor.weeks_back(NOW, 3)
    assert [w[0] for w in weeks] == ["2026-W40", "2026-W41", "2026-W42"]
    assert weeks[-1][1] == datetime(2026, 10, 12, tzinfo=UTC)


def _t(tid, minute, q, *, user="u1", session="s1", kind="observed"):
    return {"turn_id": tid, "started_at": datetime(2026, 10, 6, 9, minute, tzinfo=UTC), "question": q,
            "user_id": user, "session_id": session, "session_kind": kind}


def test_re_ask_predicate():
    turns = [
        _t("a", 0, "How many work orders were raised at Bishopsgate in July 2026?"),
        _t("b", 4, "How many work orders were raised at Bishopsgate Tower in July?"),   # same words: a re-asked
        _t("c", 30, "Which certificates expire next month?"),
        _t("d", 33, "try again"),                                                       # retry phrase: c re-asked
        _t("e", 50, "List the vendors on site", user="u2"),
        _t("f", 51, "List the vendors on site", user="u3"),                             # another person
        _t("g", 52, "What is the EUI of B-101?", kind="synthetic"),
        _t("h", 53, "What is the EUI of B-101?", kind="synthetic"),                     # not an observed session
    ]
    assert monitor.mark_re_asks(turns) == {"a", "c"}
    assert monitor.RISK_GROUPS["re_ask"]({"re_ask": True}) is True
    assert monitor.RISK_GROUPS["thumbs_down"]({"feedback_rating": "down"}) is True
    assert monitor.RISK_GROUPS["failed"]({"ok": None}) is False
    assert monitor.RISK_GROUPS["gate_rewrote"]({"gate_outcomes": ("passed", "corrected")}) is True
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `ImportError: cannot import name 'monitor' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

Create `DA/src/trace_lab/monitor.py` (later tasks append to it; all imports are here once):

```python
"""Trace Lab monitoring (M6; spec "Judges, code checks, monitoring, eval cases and release check" §4 and §6).

Code checks run on every chat answer in a window. Frozen judges run on a uniform random sample, random
answers first, so a capped batch still holds a uniform subset. Corrected prevalence comes from each judge's
frozen TEST pairs (J6) through stats.corrected_prevalence. Crossing a mode's threshold opens a review batch
per company and proposes eval cases. V1 runs by hand ("Run now", R18): nothing here schedules itself.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import socket
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..agents import llm_cost
from ..agents import trace as _trace
from ..services.principal import Principal
from . import checks, db, jobs, judges, provenance, sampling, stats

log = structlog.get_logger(__name__)

DEFAULT_RATE = 0.25
DEFAULT_SEED = 7
CHUNK = 50
FLAGGED_REVIEW_MAX = 50
PROPOSE_CASES_MAX = 5
RE_ASK_MINUTES = 10
RE_ASK_JACCARD = 0.5
RETRY_PHRASES = ("try again", "that's wrong", "not what i asked")
GATE_REWRITES = ("corrected", "degraded", "blocked")
CHEAP_GROUPS = ("thumbs_down", "failed")
EST_EVIDENCE_CHARS = 12_000
EST_OUTPUT_TOKENS = 800
DETAIL_MAX = 2_000
WORKER = f"{socket.gethostname()}:{os.getpid()}"


def monitor_usd_cap() -> float:
    return float(os.getenv("TRACE_LAB_MONITOR_USD_CAP", "5"))


def retention_days() -> int:
    """The span purge's own variable and default (agents/trace.py), so a judge never reads purged spans."""
    return int(os.environ.get("TRACE_RETENTION_DAYS") or _trace.DEFAULT_RETENTION_DAYS)


class WatchError(Exception):
    """A refusal the Watch routes turn into the house envelope {ok: false, error, reason, ...extra}."""

    def __init__(self, status: int, reason: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.extra = extra


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """A NormalizedTurn (M1) field, whether the record is a mapping or a dataclass."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def public(value: Any) -> Any:
    """A JSON-safe copy: uuid -> str, datetime/date -> ISO text, Decimal -> float."""
    if isinstance(value, Mapping):
        return {str(k): public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [public(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


# ---- windows (§4.1) ----------------------------------------------------------------------------------

def last_complete_week(now: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00 UTC of last week, and the Monday after it (exclusive)."""
    n = _utc(now)
    monday = datetime.combine(n.date() - timedelta(days=n.weekday()), time(0), tzinfo=timezone.utc)
    return monday - timedelta(days=7), monday


def period_label(start: datetime, end: datetime) -> str:
    s, e = _utc(start), _utc(end)
    if e - s == timedelta(days=7) and s.weekday() == 0 and s.time() == time(0):
        return stats.iso_week_label(s)
    return f"{s.date().isoformat()}..{(e - timedelta(microseconds=1)).date().isoformat()}"


def check_window(start: datetime, end: datetime, *, now: datetime, days: int | None = None) -> None:
    s, e, n = _utc(start), _utc(end), _utc(now)
    if e <= s:
        raise WatchError(422, "bad_window", "The window must end after it starts.")
    if e > n:
        raise WatchError(422, "window_in_future", "The window cannot end in the future.")
    d = days if days is not None else retention_days()
    if s < n - timedelta(days=d - 1):
        raise WatchError(409, "window_beyond_retention",
                         f"Answers older than {d - 1} days have lost their steps; start the window later.")


def weeks_back(now: datetime, n: int) -> list[tuple[str, datetime, datetime]]:
    """The last n ISO weeks in UTC, oldest first, ending with the current (partial) week."""
    current = last_complete_week(now)[1]
    out = []
    for i in range(max(1, int(n)) - 1, -1, -1):
        s = current - timedelta(days=7 * i)
        out.append((stats.iso_week_label(s), s, s + timedelta(days=7)))
    return out


# ---- risk groups (§4.3) ------------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(q: str | None) -> set[str]:
    return set(_TOKEN.findall((q or "").lower()))


def jaccard(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def mark_re_asks(turns: Sequence[Mapping[str, Any]]) -> set[str]:
    """turn_ids the same person asked again, in the same observed conversation, within RE_ASK_MINUTES:
    the later question shares at least RE_ASK_JACCARD of its words, or starts with a retry phrase."""
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for t in turns:
        if t.get("session_kind") != "observed" or not t.get("session_id") or not t.get("user_id"):
            continue
        by_key.setdefault((str(t["session_id"]), str(t["user_id"])), []).append(t)
    window = timedelta(minutes=RE_ASK_MINUTES)
    out: set[str] = set()
    for group in by_key.values():
        ordered = sorted(group, key=lambda t: (_utc(t["started_at"]), t["turn_id"]))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if _utc(b["started_at"]) - _utc(a["started_at"]) > window:
                    break
                later = (b.get("question") or "").strip().lower()
                if later.startswith(RETRY_PHRASES) or jaccard(a.get("question"), b.get("question")) >= RE_ASK_JACCARD:
                    out.add(a["turn_id"])
                    break
    return out


RISK_GROUPS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "thumbs_down": lambda t: t.get("feedback_rating") == "down",
    "failed": lambda t: t.get("ok") is False,
    "gate_rewrote": lambda t: any(o in GATE_REWRITES for o in (t.get("gate_outcomes") or ())),
    "code_flagged": lambda t: bool(t.get("code_failed")),
    "re_ask": lambda t: bool(t.get("re_ask")),
}
RISK_LABELS = {"thumbs_down": "rated down by the asker", "failed": "the answer failed",
               "gate_rewrote": "changed after checking", "code_flagged": "flagged by a code check",
               "re_ask": "asked again soon after"}


def select_turns(turns: Sequence[Mapping[str, Any]], rate: float,
                 groups: Mapping[str, Callable[[Mapping[str, Any]], bool]], seed: int = DEFAULT_SEED) -> dict:
    """Port of the CW monitoring/sample.py contract: a uniform sample of round(rate x N) answers in window
    order, every member of each risk group in input order, and to_judge = random first, then risk-only."""
    if not (0 < rate <= 1):
        raise ValueError("the sample rate must be above 0 and at most 1")
    for t in turns:
        if not t.get("turn_id"):
            raise ValueError("every answer in the window needs a turn_id")
    picked = random.Random(seed).sample(list(turns), round(rate * len(turns)))
    random_ids = [t["turn_id"] for t in picked]
    risk = {name: [t["turn_id"] for t in turns if pred(t)] for name, pred in groups.items()}
    seen: set[str] = set()
    to_judge: list[str] = []
    for tid in random_ids + [x for ids in risk.values() for x in ids]:
        if tid not in seen:
            seen.add(tid)
            to_judge.append(tid)
    return {"n_window": len(turns), "random": random_ids, "risk_groups": risk, "to_judge": to_judge}
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → `7 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`.

---

### Task 6: `monitor.py` — estimate, threshold, review picks, chart points, Due marker, check summary (pure)

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Test: `DA/tests/test_trace_lab_monitor.py` (append)

**Interfaces:**
- Consumes: `stats.corrected_prevalence(sample_preds, test_labels, test_preds, ...) -> dict` (M5); `llm_cost.price_usd(model, usage)` (`DA/src/agents/llm_cost.py:64`).
- Produces: `estimate_mode(*, verdicts, test_labels, test_preds, threshold, n_planned, prevalence=None) -> dict` (keys `n_sample, n_error, n_undecidable, n_not_applicable, threshold, raw, corrected, ci_low, ci_high, confidence, test_tpr, test_tnr, n_test, validity_warning, crossed, clearly_above, reason`); `estimate_ids(kind, plan, window_ids) -> list[str]`; `flagged_for_review(plan, flagged, *, limit=FLAGGED_REVIEW_MAX) -> list[{"turn_id", "group"}]`; `review_reason(judge_key, period, group) -> str`; `chart_points(entries, *, periods=12) -> {"points", "threshold", "y_max"}`; `due_marker(batches, *, now) -> {"period_label", "is_due"}`; `summarise_checks(rows, *, labels, validated, n_window) -> list[dict]`; `call_usd(model, prompt_text, chars) -> Decimal | None`.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_monitor.py`)

```python
from decimal import Decimal  # noqa: E402

ARTIFACT_G_LABELS = [1] * 12 + [0] * 38
ARTIFACT_G_PREDS = [1] * 10 + [0] * 38 + [1] * 2


def test_errors_excluded_from_numerator_and_denominator():
    verdicts = ([{"status": "decided", "failure_present": 1}] * 6 + [{"status": "decided", "failure_present": 0}] * 32
                + [{"status": "error", "failure_present": None}] * 2)
    est = monitor.estimate_mode(verdicts=verdicts, test_labels=ARTIFACT_G_LABELS, test_preds=ARTIFACT_G_PREDS,
                                threshold=0.15, n_planned=40)
    assert est["n_sample"] == 38 and est["n_error"] == 2
    assert est["raw"] == round(6 / 38, 4)
    assert est["test_tpr"] == 0.9474 and est["test_tnr"] == 0.8333 and est["n_test"] == 50


def test_threshold_is_crossed_by_the_corrected_rate_and_clearly_above_by_the_interval():
    def fake(sample, labels, preds):
        return {"raw": 0.2, "corrected": 0.17, "ci_low": 0.06, "ci_high": 0.28, "confidence": 0.95,
                "test_tpr": 0.9, "test_tnr": 0.8, "n_test": 2, "validity_warning": ""}
    one = [{"status": "decided", "failure_present": 1}]
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=0.15, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is True and est["clearly_above"] is False
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=None, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is False
    empty = monitor.estimate_mode(verdicts=[], test_labels=[1], test_preds=[1], threshold=0.15, n_planned=0)
    assert empty["corrected"] is None and empty["reason"] == "uniform sample is empty"
    untested = monitor.estimate_mode(verdicts=one, test_labels=[], test_preds=[], threshold=0.15, n_planned=1)
    assert untested["corrected"] is None and "test" in untested["reason"]


def test_risk_strata_never_enter_the_estimate():
    plan = {"random": ["t1", "t2"], "risk_groups": {"failed": ["t3"]}, "to_judge": ["t1", "t2", "t3"]}
    assert monitor.estimate_ids("llm", plan, ["t1", "t2", "t3", "t4"]) == ["t1", "t2"]
    assert monitor.estimate_ids("code", plan, ["t1", "t2", "t3", "t4"]) == ["t1", "t2", "t3", "t4"]


def test_review_picks_take_uniform_flags_first_then_risk_only():
    plan = {"random": ["t2", "t5", "t9"], "risk_groups": {"thumbs_down": ["t1", "t5"], "failed": ["t7"]},
            "to_judge": ["t2", "t5", "t9", "t1", "t7"]}
    picks = monitor.flagged_for_review(plan, {"t9", "t5", "t1", "t7"}, limit=3)
    assert picks == [{"turn_id": "t5", "group": None}, {"turn_id": "t9", "group": None},
                     {"turn_id": "t1", "group": "thumbs_down"}]
    assert monitor.review_reason("wrong_period-v2", "2026-W41", None) == "flagged by wrong_period-v2 in 2026-W41: uniform sample"
    assert monitor.review_reason("wrong_period-v2", "2026-W41", "failed").endswith(": risk group the answer failed")


def test_chart_y_max_rule_and_empty_state():
    assert monitor.chart_points([]) == {"points": [], "threshold": None, "y_max": None}
    entries = [{"period_label": "2026-W40", "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2,
                "threshold": 0.15},
               {"period_label": "2026-W41", "raw": 0.19, "corrected": 0.17, "ci_low": 0.06, "ci_high": 0.28,
                "threshold": 0.15}]
    out = monitor.chart_points(entries)
    assert [p["period_label"] for p in out["points"]] == ["2026-W40", "2026-W41"]
    assert out["threshold"] == 0.15 and out["y_max"] == 0.35          # 1.25 x max(0.15, 0.28, 0.17)
    assert monitor.chart_points(entries, periods=1)["points"][0]["period_label"] == "2026-W41"
    flat = monitor.chart_points([{"period_label": "2026-W41", "corrected": 0.0, "ci_high": 0.0, "threshold": None}])
    assert flat["y_max"] == 0.1


def test_due_marker_reads_the_last_complete_week():
    week = {"status": "done", "window_start": datetime(2026, 10, 5, tzinfo=UTC),
            "window_end": datetime(2026, 10, 12, tzinfo=UTC)}
    assert monitor.due_marker([week], now=NOW) == {"period_label": "2026-W41", "is_due": False}
    assert monitor.due_marker([dict(week, status="failed")], now=NOW)["is_due"] is True
    assert monitor.due_marker([], now=NOW)["is_due"] is True


def test_code_check_summary_counts_and_rates():
    rows = [{"evaluator_key": "code:no_answer@v1", "status": "decided", "failure_present": 1, "n": 9},
            {"evaluator_key": "code:no_answer@v1", "status": "decided", "failure_present": 0, "n": 403},
            {"evaluator_key": "code:refusal@v1", "status": "undecidable", "failure_present": None, "n": 2}]
    out = monitor.summarise_checks(rows, labels={"code:no_answer@v1": "Gives no answer",
                                                 "code:refusal@v1": "Says it cannot help"},
                                   validated={"code:no_answer@v1"}, n_window=412)
    assert out[0] == {"evaluator_key": "code:no_answer@v1", "label": "Gives no answer", "n": 412, "decided": 412,
                      "flagged": 9, "undecidable": 0, "not_applicable": 0, "error": 0, "rate": 0.0218,
                      "validated": True}
    assert out[1]["rate"] is None and out[1]["undecidable"] == 2 and out[1]["validated"] is False


def test_call_usd_prices_one_judge_call_and_refuses_to_guess():
    usd = monitor.call_usd("claude-sonnet-5", "p" * 3500, 3500)
    assert usd == Decimal(str(round(2000 * 2 / 1e6 + 800 * 10 / 1e6, 6)))   # 2,000 input + 800 output tokens
    assert monitor.call_usd("no-such-model", "p", 10) is None
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `AttributeError: module 'src.trace_lab.monitor' has no attribute 'estimate_mode'` (and the other new names).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- estimate, threshold, review picks, chart, due (§4.2 steps 5-7, §4.4, §4.6) -------------------------

def estimate_mode(*, verdicts: Sequence[Mapping[str, Any]], test_labels: Sequence[int], test_preds: Sequence[int],
                  threshold: float | None, n_planned: int,
                  prevalence: Callable[..., dict] | None = None) -> dict:
    """Corrected prevalence for one mode. Errors, undecidable and not-applicable verdicts are left out of
    both numerator and denominator and counted separately."""
    decided = [int(v["failure_present"]) for v in verdicts if v.get("status") == "decided"]
    out: dict[str, Any] = {
        "n_sample": len(decided),
        "n_error": sum(1 for v in verdicts if v.get("status") == "error"),
        "n_undecidable": sum(1 for v in verdicts if v.get("status") == "undecidable"),
        "n_not_applicable": sum(1 for v in verdicts if v.get("status") == "not_applicable"),
        "threshold": threshold, "raw": None, "corrected": None, "ci_low": None, "ci_high": None,
        "confidence": 0.95, "test_tpr": None, "test_tnr": None, "n_test": len(test_labels),
        "validity_warning": "", "crossed": False, "clearly_above": False, "reason": None,
    }
    if n_planned == 0:
        out["reason"] = "uniform sample is empty"
        return out
    if not decided:
        out["reason"] = "no answer in the sample has a verdict yet"
        return out
    if not test_labels:
        out["reason"] = "the judge has no held-out test result"
        return out
    fn = prevalence or stats.corrected_prevalence
    try:
        est = fn(decided, list(test_labels), list(test_preds))
    except ValueError as exc:
        out["reason"] = f"no estimate: {exc}"
        return out
    for key in ("raw", "corrected", "ci_low", "ci_high", "confidence", "test_tpr", "test_tnr", "n_test",
                "validity_warning"):
        if key in est:
            out[key] = est[key]
    c, lo = out["corrected"], out["ci_low"]
    out["crossed"] = c is not None and threshold is not None and c > threshold
    out["clearly_above"] = lo is not None and threshold is not None and lo > threshold
    return out


def estimate_ids(kind: str, plan: Mapping[str, Any], window_ids: Sequence[str]) -> list[str]:
    """LLM judge: the uniform sample only (risk groups never enter the estimate). Validated code judge:
    every answer in the window (a census)."""
    return list(plan.get("random") or []) if kind == "llm" else list(window_ids)


def flagged_for_review(plan: Mapping[str, Any], flagged: set[str], *, limit: int = FLAGGED_REVIEW_MAX) -> list[dict]:
    """Uniform-sample flags first, in sampled order, then risk-only flags in group order."""
    out: list[dict] = []
    seen: set[str] = set()
    random_ids = list(plan.get("random") or [])
    for tid in random_ids:
        if tid in flagged and tid not in seen:
            seen.add(tid)
            out.append({"turn_id": tid, "group": None})
    in_random = set(random_ids)
    for name, ids in (plan.get("risk_groups") or {}).items():
        for tid in ids:
            if tid in flagged and tid not in in_random and tid not in seen:
                seen.add(tid)
                out.append({"turn_id": tid, "group": name})
    return out[:limit]


def review_reason(judge_key: str, period: str, group: str | None) -> str:
    where = "uniform sample" if group is None else f"risk group {RISK_LABELS.get(group, group)}"
    return f"flagged by {judge_key} in {period}: {where}"[:300]


def chart_points(entries: Sequence[Mapping[str, Any]], *, periods: int = 12) -> dict:
    """entries: one per period (its latest done or capped batch), oldest first. y_max = 1.25 x the largest of
    the thresholds, interval tops and corrected values (CW monitoring/chart.py); 0.1 when all are zero."""
    keys = ("period_label", "window_start", "window_end", "batch_id", "raw", "corrected", "ci_low", "ci_high",
            "n_sample", "threshold", "crossed", "validity_warning")
    pts = [{k: e.get(k) for k in keys} for e in list(entries)[-max(1, int(periods)):]] if entries else []
    if not pts:
        return {"points": [], "threshold": None, "y_max": None}
    values = [v for p in pts for v in (p["ci_high"], p["corrected"], p["threshold"]) if v is not None]
    top = max(values) if values else 0.0
    return {"points": pts, "threshold": pts[-1]["threshold"], "y_max": round(1.25 * top, 4) if top > 0 else 0.1}


def due_marker(batches: Sequence[Mapping[str, Any]], *, now: datetime) -> dict:
    start, end = last_complete_week(now)
    covered = any(b.get("status") in ("done", "capped") and b.get("window_start") and b.get("window_end")
                  and _utc(b["window_start"]) <= start and _utc(b["window_end"]) >= end for b in batches)
    return {"period_label": stats.iso_week_label(start), "is_due": not covered}


def summarise_checks(rows: Sequence[Mapping[str, Any]], *, labels: Mapping[str, str], validated: set[str],
                     n_window: int) -> list[dict]:
    """rows: [{evaluator_key, status, failure_present, n}] grouped counts over the window's answers."""
    out = []
    for ek, label in labels.items():
        mine = [r for r in rows if r["evaluator_key"] == ek]

        def count(pred: Callable[[Mapping[str, Any]], bool]) -> int:
            return sum(int(r["n"]) for r in mine if pred(r))

        decided = count(lambda r: r["status"] == "decided")
        flagged = count(lambda r: r["status"] == "decided" and r["failure_present"] == 1)
        out.append({"evaluator_key": ek, "label": label, "n": n_window, "decided": decided, "flagged": flagged,
                    "undecidable": count(lambda r: r["status"] == "undecidable"),
                    "not_applicable": count(lambda r: r["status"] == "not_applicable"),
                    "error": count(lambda r: r["status"] == "error"),
                    "rate": round(flagged / decided, 4) if decided else None, "validated": ek in validated})
    return out


def call_usd(model: str, prompt_text: str, chars: int) -> Decimal | None:
    """One judge call (spec §3.11): input = ceil((len(prompt) + evidence chars) / 3.5) tokens, output 800.
    None when the model has no price: a cap cannot be enforced, so nothing is guessed."""
    usd = llm_cost.price_usd(model, {"input_tokens": math.ceil((len(prompt_text or "") + int(chars)) / 3.5),
                                     "output_tokens": EST_OUTPUT_TOKENS})
    return None if usd is None else Decimal(str(usd))
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → `15 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`.

---

### Task 7: `monitor.py` — store, judges in use, the plan preview and starting a batch

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Test: `DA/tests/test_trace_lab_monitor.py` (append); `DA/tests/test_trace_lab_watch_sql.py` (append)

**Interfaces:**
- Consumes: `db.engine()`; `provenance.inferred_channel_sql("t")`; `jobs.create`, `jobs.finish`, `jobs.run_in_background`, `jobs.JobConflict` (M0); `checks.CHECKS` (M5); M5's judge store `judges.in_use_judges()`, `judges.test_pairs(judge_id)`, `judges.cached_predictions(prompt_hash, turn_ids)`, `judges.write_prediction(conn, *, judge, turn_id, organization_id, pred)`, `judges.fetch_judge(judge_id)`; tables `judges`, `modes`, `monitor_verdicts`, `monitor_batches`, `turn_meta`, `plenum_cafm.agent_trace_turns` (M6 queries none of M5's judge tables itself).
- Produces: `_window_sql() -> str`; `window_turns(start, end, org) -> list[dict]` (keys `turn_id, session_id, organization_id, user_id, started_at, ok, feedback_rating`); `judges_in_use(mode_ids=None) -> list[dict]` (built from `judges.in_use_judges()` + `judges.test_pairs()`; keys `judge_id, mode_id, mode_name, judge_key, kind, model, prompt_text, prompt_hash, check_key, check_version, threshold, evaluator_key, test_labels, test_preds`); `live_checks() -> list[{"check_key", "version", "evaluator_key", "label"}]`; `_verdicts(evaluator_key, turn_ids) -> dict[turn_id, row]`; `_predictions(prompt_hash, turn_ids) -> dict[turn_id, row]` (M5's cache, decided rows only); `_UPSERT_VERDICT` (SQL); `verdict_row(...) -> dict`; `prediction_args(*, judge, turn_id, organization_id, reply, inp) -> dict` (the keyword arguments of `judges.write_prediction`); `_save(job_id, *, verdicts, prediction=None, usd=None, released=Decimal(0), done_delta=0, progress=None)` (writes the prediction through `judges.write_prediction` in the same transaction); `_judge_row(judge_id) -> dict` (`judges.fetch_judge`); `_verdict_counts(evaluator_keys, turn_ids)`; `_validated_check_keys() -> set[str]`; `_insert_batch(row)`; `_load_batch(batch_id) -> dict | None`; `_update_batch(batch_id, *, finished=False, **fields)`; `batch_public(row, *, with_plan=False) -> dict`; `plan_preview(*, organization_id, since=None, until=None, sample_rate=0.25, seed=7, risk_groups=None, mode_ids=None, now=None) -> dict`; `start_batch(*, organization_id, since, until, sample_rate, seed, risk_groups, mode_ids, usd_cap, actor_id, actor_email, now=None) -> {"batch", "job"}`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_monitor.py`:

```python
ACTOR = "cccccccc-0000-4000-8000-000000000003"
JOB_ID = "11111111-1111-4111-8111-11111111aaaa"


def _window(n=20):
    return [{"turn_id": f"t{i}", "session_id": f"s{i}", "organization_id": ORG_A, "user_id": "u",
             "started_at": datetime(2026, 10, 6, tzinfo=UTC) + timedelta(minutes=i), "ok": i != 3,
             "feedback_rating": "down" if i == 5 else None} for i in range(1, n + 1)]


def _judge(**kw):
    j = {"judge_id": "11111111-1111-4111-8111-111111111111", "judge_key": "wrong_period-v1",
         "mode_id": "22222222-2222-4222-8222-222222222222", "mode_name": "wrong_period", "kind": "llm",
         "model": "claude-sonnet-5", "prompt_text": "p" * 3500, "prompt_hash": "abc123abc123", "threshold": 0.15,
         "evaluator_key": "judge:11111111-1111-4111-8111-111111111111", "test_labels": [1, 0], "test_preds": [1, 0],
         "check_key": None, "check_version": None}
    j.update(kw)
    return j


async def test_window_turns_pages_by_start_time_and_turn_id(monkeypatch):
    rows = _window(120)
    calls = []

    async def page(start, end, org, after, limit=monitor.CHUNK):
        calls.append(after)
        i = 0 if after is None else next(n for n, r in enumerate(rows) if r["turn_id"] == after[1]) + 1
        return rows[i:i + limit]

    monkeypatch.setattr(monitor, "_window_page", page)
    out = await monitor.window_turns(*monitor.last_complete_week(NOW), None)
    assert [r["turn_id"] for r in out] == [r["turn_id"] for r in rows]
    assert calls[0] is None and calls[1] == (rows[49]["started_at"], rows[49]["turn_id"]) and len(calls) == 3


def test_window_query_reads_only_chat_answers_of_a_company():
    sql = monitor._window_sql()
    for needle in ("= 'chat'", "m.golden_run_id IS NULL", "NOT LIKE 'golden-%'", "t.organization_id IS NOT NULL",
                   "'acting' = 'true'", "ORDER BY t.started_at, t.turn_id"):
        assert needle in sql, needle


async def test_plan_preview_counts_cost_and_cache(monkeypatch):
    turns = _window(20)

    async def fake_window(start, end, org):
        assert (start, end) == (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
        return turns

    plan = monitor.select_turns(turns, 0.25, {g: monitor.RISK_GROUPS[g] for g in monitor.CHEAP_GROUPS}, 7)
    cached = plan["to_judge"][0]

    async def fake_verdicts(ek, ids):
        return {cached: {"turn_id": cached, "status": "decided", "failure_present": 0}} if cached in ids else {}

    async def fake_predictions(ph, ids):
        return {}

    async def fake_in_use(mode_ids=None):
        return [_judge()]

    monkeypatch.setattr(monitor, "window_turns", fake_window)
    monkeypatch.setattr(monitor, "_verdicts", fake_verdicts)
    monkeypatch.setattr(monitor, "_predictions", fake_predictions)
    monkeypatch.setattr(monitor, "judges_in_use", fake_in_use)
    out = await monitor.plan_preview(organization_id=None, now=NOW)
    assert out["period_label"] == "2026-W41" and out["n_window"] == 20 and out["n_random"] == 5
    assert out["n_to_judge"] == len(plan["to_judge"]) and out["n_risk_only"] == out["n_to_judge"] - 5
    per = monitor.call_usd("claude-sonnet-5", "p" * 3500, monitor.EST_EVIDENCE_CHARS)
    assert out["usd_estimate"] == float(round(per * (out["n_to_judge"] - 1), 4))   # the cached answer is free
    assert any("risk groups" in w for w in out["warnings"])
    assert "prompt_text" not in out["judges"][0]


async def test_start_batch_refuses_over_cap_and_a_second_open_batch(monkeypatch):
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 10, "n_random": 3, "n_risk_only": 0, "n_to_judge": 3, "judges": [], "checks": [],
               "usd_estimate": 9.0, "sample_rate": 0.25, "seed": 7, "risk_groups": list(monitor.RISK_GROUPS), "warnings": []}

    async def fake_preview(**kw):
        return dict(preview)

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    kw = dict(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7, risk_groups=None, mode_ids=None,
              usd_cap=5.0, actor_id=ACTOR, actor_email="owner@example.com", now=NOW)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(**kw)
    assert e.value.status == 409 and e.value.reason == "over_cap" and e.value.extra["usd_estimate"] == 9.0
    preview["usd_estimate"] = 1.0

    async def conflict(**_):
        raise monitor.jobs.JobConflict()

    monkeypatch.setattr(monitor.jobs, "create", conflict)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(**kw)
    assert e.value.status == 409 and e.value.reason == "batch_running"


async def test_start_batch_creates_the_job_then_the_batch_and_starts_the_worker(monkeypatch):
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 10, "n_random": 3, "n_risk_only": 0, "n_to_judge": 3, "judges": [{"judge_id": "j"}],
               "checks": monitor.live_checks(), "usd_estimate": 1.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"], "warnings": []}
    created, inserted, started = {}, [], []

    async def fake_preview(**kw):
        return dict(preview)

    async def fake_create(**kw):
        created.update(kw)
        return {"id": JOB_ID}

    async def fake_insert(row):
        inserted.append(row)

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor.jobs, "create", fake_create)
    monkeypatch.setattr(monitor, "_insert_batch", fake_insert)
    monkeypatch.setattr(monitor.jobs, "run_in_background", lambda job_id, factory: started.append(job_id))
    out = await monitor.start_batch(organization_id=ORG_A, since=None, until=None, sample_rate=0.25, seed=7,
                                    risk_groups=["failed"], mode_ids=None, usd_cap=None, actor_id=ACTOR,
                                    actor_email="owner@example.com", now=NOW)
    assert created["kind"] == "monitor_batch" and created["scope_key"] == f"monitor_batch:{ORG_A}"
    assert created["usd_cap"] == Decimal(str(monitor.monitor_usd_cap())) and created["total"] == 10
    assert inserted[0]["status"] == "queued" and inserted[0]["organization_id"] == ORG_A
    assert inserted[0]["job_id"] == JOB_ID and inserted[0]["window_start"] == datetime(2026, 10, 5, tzinfo=UTC)
    assert started == [JOB_ID] and out["batch"]["id"] == inserted[0]["id"] and out["job"]["id"] == JOB_ID


async def test_judges_in_use_come_from_m5_with_its_frozen_test_pairs(monkeypatch):
    j, m = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    row = {"id": j, "mode_id": m, "mode_name": "wrong_period", "mode_status": "confirmed", "judge_key": "wrong_period-v2",
           "kind": "llm", "model": "claude-sonnet-5", "prompt_text": "p", "prompt_hash": "abc123abc123",
           "check_key": None, "check_version": None, "monitor_threshold": Decimal("0.15")}
    retired = dict(row, id="33333333-3333-4333-8333-333333333333", mode_status="retired", mode_name="old_mode")

    async def in_use():
        return {m: row, "mode-old": retired}

    async def pairs(judge_id):
        assert judge_id == j                                            # a retired mode's judge is never asked for
        return [1, 0, 1], [1, 0, 0]

    monkeypatch.setattr(monitor.judges, "in_use_judges", in_use)
    monkeypatch.setattr(monitor.judges, "test_pairs", pairs)
    out = await monitor.judges_in_use()
    assert [(x["mode_id"], x["judge_id"], x["threshold"], x["evaluator_key"], x["test_labels"], x["test_preds"])
            for x in out] == [(m, j, 0.15, f"judge:{j}", [1, 0, 1], [1, 0, 0])]
    assert (await monitor.judges_in_use([m]))[0]["prompt_hash"] == "abc123abc123"
    assert await monitor.judges_in_use(["22222222-2222-4222-8222-2222222222ff"]) == []


async def test_predictions_are_m5s_cache_decided_rows_only(monkeypatch):
    async def cached(prompt_hash, ids):
        assert prompt_hash == "h" and ids == ["a", "b"]
        return {"a": {"status": "decided", "failure_present": 1}, "b": {"status": "error", "failure_present": None}}

    monkeypatch.setattr(monitor.judges, "cached_predictions", cached)
    assert list(await monitor._predictions("h", ["a", "b"])) == ["a"]
    assert await monitor._predictions("h", []) == {}


async def test_save_writes_the_prediction_through_m5_in_the_same_transaction(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class Conn:
        async def execute(self, *a, **kw):
            calls.append("verdicts")

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Engine:
        def begin(self):
            return Begin()

    async def write(conn, **kw):
        calls.append(("prediction", sorted(kw)))

    async def checkpoint(conn, job_id, **kw):
        calls.append(("checkpoint", job_id))

    monkeypatch.setattr(monitor.db, "engine", lambda: Engine())
    monkeypatch.setattr(monitor.judges, "write_prediction", write)
    monkeypatch.setattr(monitor.jobs, "checkpoint", checkpoint)
    reply = SimpleNamespace(status="decided", failure_present=1, critique="Quotes June.", usd=0.02, error=None)
    arg = monitor.prediction_args(judge={"judge_id": "j1", "prompt_hash": "ph", "model": "claude-sonnet-5"},
                                  turn_id="t1", organization_id=ORG_A, reply=reply,
                                  inp=SimpleNamespace(text="e", chars=100, truncated=False, sha="sha"))
    assert arg["judge"] == {"id": "j1", "prompt_hash": "ph", "model": "claude-sonnet-5"}
    assert arg["pred"]["failure_present"] == 1 and arg["pred"]["usd"] == Decimal("0.02") and arg["pred"]["input_sha"] == "sha"
    await monitor._save(JOB_ID, verdicts=[{"id": "v"}], prediction=arg, usd=Decimal("0.02"))
    assert calls == [("prediction", ["judge", "organization_id", "pred", "turn_id"]), "verdicts", ("checkpoint", JOB_ID)]
```

Append to `DA/tests/test_trace_lab_watch_sql.py`:

```python
async def test_reads_only_chat_and_excludes_golden_turns(monkeypatch):
    from src.trace_lab import monitor

    engine = await fresh_schema(monkeypatch)
    t0 = datetime(2026, 10, 6, 9, tzinfo=UTC)
    turns = [("turn-0000000000000e01", "s-chat", ORG_A, "chat", None, None),
             ("turn-0000000000000e02", "s-up", ORG_A, "upload", None, None),
             ("turn-0000000000000e03", "golden-1a2b3c4d-21-r1", ORG_A, None, None, None),
             ("turn-0000000000000e04", "s-none", None, "chat", None, None),
             ("turn-0000000000000e05", "s-view-as", ORG_A, "chat", "superadmin", {"acting": True}),
             ("turn-0000000000000e06", "s-b", ORG_B, "chat", None, None),
             ("turn-0000000000000e07", "s-old", ORG_A, "chat", None, None)]
    async with engine.begin() as conn:
        for i, (tid, sid, org, channel, role, scope) in enumerate(turns):
            started = t0 - timedelta(days=3) if tid.endswith("07") else t0 + timedelta(minutes=i)
            await conn.execute(text("INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, organization_id, "
                                    "question, started_at) VALUES (:t, :s, CAST(:o AS uuid), 'q', :ts)"),
                               {"t": tid, "s": sid, "o": org, "ts": started})
            if channel:
                await conn.execute(text("INSERT INTO plenum_tracelab.turn_meta (turn_id, organization_id, channel, "
                                        "channel_source, classifier_version, asker_role, asker_scope) VALUES "
                                        "(:t, CAST(:o AS uuid), :c, 'recorded', 'test', :r, CAST(:sc AS jsonb))"),
                                   {"t": tid, "o": org, "c": channel, "r": role,
                                    "sc": json.dumps(scope) if scope else None})
    rows = await monitor.window_turns(t0 - timedelta(hours=1), t0 + timedelta(days=1), None)
    assert [r["turn_id"] for r in rows] == ["turn-0000000000000e01", "turn-0000000000000e06"]
    rows = await monitor.window_turns(t0 - timedelta(hours=1), t0 + timedelta(days=1), ORG_A)
    assert [r["turn_id"] for r in rows] == ["turn-0000000000000e01"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py tests/test_trace_lab_watch_sql.py`
Expected: `AttributeError: module 'src.trace_lab.monitor' has no attribute '_window_page'` (and `_window_sql`, `plan_preview`, `start_batch`, `live_checks`, `window_turns`).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- store: each call opens its own short connection (tests replace these) ---------------------------

def _window_sql() -> str:
    """Chat answers of a company in [start, end), in pages (§4.1). R27: answers with no company are left
    out. R20: a superadmin's view-as traffic is left out. Golden turns are left out by meta and by session."""
    return f"""
SELECT t.turn_id, t.session_id, t.organization_id, t.user_id, t.started_at, t.ok, t.feedback_rating
  FROM plenum_cafm.agent_trace_turns t
  LEFT JOIN plenum_tracelab.turn_meta m ON m.turn_id = t.turn_id
 WHERE t.started_at >= :start AND t.started_at < :end
   AND t.organization_id IS NOT NULL
   AND (CAST(:org AS uuid) IS NULL OR t.organization_id = CAST(:org AS uuid))
   AND COALESCE(m.channel, {provenance.inferred_channel_sql('t')}) = 'chat'
   AND m.golden_run_id IS NULL
   AND coalesce(t.session_id, '') NOT LIKE 'golden-%'
   AND NOT coalesce(m.asker_role = 'superadmin' AND m.asker_scope ->> 'acting' = 'true', false)
   AND (CAST(:after_ts AS timestamptz) IS NULL
        OR (t.started_at, t.turn_id) > (CAST(:after_ts AS timestamptz), CAST(:after_id AS text)))
 ORDER BY t.started_at, t.turn_id
 LIMIT :limit"""


async def _window_page(start: datetime, end: datetime, org: str | None, after: tuple[datetime, str] | None,
                       limit: int = CHUNK) -> list[dict]:
    async with db.engine().connect() as conn:
        res = await conn.execute(text(_window_sql()), {
            "start": start, "end": end, "org": org, "limit": limit,
            "after_ts": after[0] if after else None, "after_id": after[1] if after else None})
        return [dict(r) for r in res.mappings().all()]


async def window_turns(start: datetime, end: datetime, org: str | None) -> list[dict]:
    out: list[dict] = []
    after: tuple[datetime, str] | None = None
    while True:
        page = await _window_page(start, end, org, after)
        out.extend(page)
        if len(page) < CHUNK:
            return out
        after = (page[-1]["started_at"], page[-1]["turn_id"])


def _evaluator_key(kind: str, judge_id: Any, check_key: str | None, check_version: Any) -> str:
    return f"judge:{judge_id}" if kind == "llm" else f"code:{check_key}@v{check_version}"


async def judges_in_use(mode_ids: Sequence[str] | None = None) -> list[dict]:
    """Per mode, the judge in use (spec §3.1), from M5's store: judges.in_use_judges() (the highest version that is
    frozen, has a finished test run and the decision 'use') plus judges.test_pairs() (its frozen J6 test pairs,
    label 1 = failure present). A retired mode is skipped. M6 has no judge query of its own, so M5's in-use rule
    and M6's can never drift apart."""
    wanted = {str(m) for m in mode_ids} if mode_ids else None
    rows = await judges.in_use_judges()
    out = []
    for mode_id, r in sorted(rows.items(), key=lambda kv: str(kv[1].get("mode_name") or "")):
        if (wanted is not None and str(mode_id) not in wanted) or r.get("mode_status") == "retired":
            continue
        labels, preds = await judges.test_pairs(str(r["id"]))
        thr = r.get("monitor_threshold")
        out.append({"judge_id": str(r["id"]), "mode_id": str(mode_id), "mode_name": r.get("mode_name"),
                    "judge_key": r.get("judge_key"), "kind": r.get("kind"), "model": r.get("model"),
                    "prompt_text": r.get("prompt_text"), "prompt_hash": r.get("prompt_hash"),
                    "check_key": r.get("check_key"), "check_version": r.get("check_version"),
                    "threshold": float(thr) if thr is not None else None,
                    "evaluator_key": _evaluator_key(r.get("kind"), r["id"], r.get("check_key"), r.get("check_version")),
                    "test_labels": list(labels), "test_preds": list(preds)})
    return out


def _snapshot(j: Mapping[str, Any]) -> dict:
    return {k: j.get(k) for k in ("judge_id", "judge_key", "mode_id", "mode_name", "kind", "prompt_hash", "threshold",
                                  "evaluator_key", "check_key", "check_version")}


def live_checks() -> list[dict]:
    return [{"check_key": c.key, "version": c.version, "evaluator_key": c.evaluator_key, "label": c.label}
            for c in checks.CHECKS.values() if c.scope == "live"]


async def _judge_row(judge_id: str) -> dict:
    """The judge's whole row from M5's store (id, model, prompt_text, prompt_hash, ...); {} when it is gone."""
    return await judges.fetch_judge(judge_id) or {}


async def _verdicts(evaluator_key: str, turn_ids: Sequence[str]) -> dict[str, dict]:
    if not turn_ids:
        return {}
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id, status, failure_present, detail, organization_id FROM plenum_tracelab.monitor_verdicts "
            "WHERE evaluator_key = :ek AND turn_id = ANY(CAST(:ids AS text[]))"),
            {"ek": evaluator_key, "ids": list(turn_ids)})).mappings().all()
    return {r["turn_id"]: dict(r, detail=_json(r["detail"])) for r in rows}


async def _predictions(prompt_hash: str, turn_ids: Sequence[str]) -> dict[str, dict]:
    """Decided predictions only, from M5's cache (judges.cached_predictions): what makes a re-run cost nothing
    (spec §3.6). An error row is not a verdict, so it is left out and retried."""
    if not turn_ids:
        return {}
    got = await judges.cached_predictions(prompt_hash, list(turn_ids))
    return {t: p for t, p in got.items() if p.get("status") == "decided"}


_UPSERT_VERDICT = """
INSERT INTO plenum_tracelab.monitor_verdicts (id, evaluator_key, turn_id, organization_id, turn_started_at, judge_id,
    mode_id, status, failure_present, detail, first_batch_id)
VALUES (CAST(:id AS uuid), :ek, :tid, CAST(:org AS uuid), :ts, CAST(:judge_id AS uuid), CAST(:mode_id AS uuid), :status,
    :fp, CAST(:detail AS jsonb), CAST(:batch_id AS uuid))
ON CONFLICT (evaluator_key, turn_id) DO UPDATE
   SET status = EXCLUDED.status, failure_present = EXCLUDED.failure_present, detail = EXCLUDED.detail,
       attempts = plenum_tracelab.monitor_verdicts.attempts + 1, updated_at = now()
 WHERE plenum_tracelab.monitor_verdicts.status <> 'decided'"""


def verdict_row(*, evaluator_key: str, turn: Mapping[str, Any], status: str, failure_present: int | None,
                detail: Mapping[str, Any], batch_id: str, judge_id: str | None = None,
                mode_id: str | None = None) -> dict:
    return {"id": str(uuid.uuid4()), "ek": evaluator_key, "tid": turn["turn_id"], "org": str(turn["organization_id"]),
            "ts": turn["started_at"], "judge_id": judge_id, "mode_id": mode_id, "status": status,
            "fp": failure_present, "detail": json.dumps(public(detail)), "batch_id": batch_id}


def _check_status(status: str) -> str:
    return "decided" if status in ("pass", "fail") else status


def _check_detail(result: Any) -> dict:
    return {"findings": [str(f)[:200] for f in (result.findings or ())][:8]}


def prediction_args(*, judge: Mapping[str, Any], turn_id: str, organization_id: str, reply: Any, inp: Any) -> dict:
    """The keyword arguments of judges.write_prediction for one judge call. M5 owns that upsert (a decided row is
    never touched; usd and attempts accumulate), so M6 only shapes the model's reply into M5's `pred` mapping.
    `judge` needs the judge's id (or judge_id), prompt_hash and model; `inp` is judges.judge_input's result."""
    decided = reply.status == "decided" and reply.failure_present in (0, 1)
    return {"judge": {"id": str(judge.get("id") or judge.get("judge_id")), "prompt_hash": judge["prompt_hash"],
                      "model": judge.get("model")},
            "turn_id": turn_id, "organization_id": organization_id,
            "pred": {"status": "decided" if decided else "error",
                     "failure_present": int(reply.failure_present) if decided else None,
                     "critique": (reply.critique or "")[:4000] or None,
                     "error": None if decided else (reply.error or "no verdict")[:500],
                     "usd": None if reply.usd is None else Decimal(str(reply.usd)),
                     "attempts": int(getattr(reply, "attempts", 1) or 1),
                     "input_sha": _field(inp, "sha"), "evidence_chars": int(_field(inp, "chars", 0) or 0),
                     "evidence_truncated": bool(_field(inp, "truncated", False))}}


async def _save(job_id: str, *, verdicts: Sequence[Mapping[str, Any]], prediction: Mapping[str, Any] | None = None,
                usd: Decimal | None = None, released: Decimal = Decimal(0), done_delta: int = 0,
                progress: Mapping[str, Any] | None = None) -> None:
    """Result rows and the job checkpoint in ONE transaction (jobs contract, spec §2.9). `prediction` is
    prediction_args(...): the cached judge reply goes through M5's judges.write_prediction in the same transaction."""
    async with db.engine().begin() as conn:
        if prediction is not None:
            await judges.write_prediction(conn, **prediction)
        if verdicts:
            await conn.execute(text(_UPSERT_VERDICT), [dict(v) for v in verdicts])
        await jobs.checkpoint(conn, job_id, usd=usd, released=released, done_delta=done_delta,
                              progress_patch=dict(progress) if progress else None)


async def _verdict_counts(evaluator_keys: Sequence[str], turn_ids: Sequence[str]) -> list[dict]:
    if not evaluator_keys or not turn_ids:
        return []
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT evaluator_key, status, failure_present, count(*) AS n FROM plenum_tracelab.monitor_verdicts "
            "WHERE evaluator_key = ANY(CAST(:eks AS text[])) AND turn_id = ANY(CAST(:ids AS text[])) GROUP BY 1, 2, 3"),
            {"eks": list(evaluator_keys), "ids": list(turn_ids)})).mappings().all()
    return [dict(r) for r in rows]


async def _validated_check_keys() -> set[str]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT DISTINCT 'code:' || j.check_key || '@v' || j.check_version AS ek
  FROM plenum_tracelab.judges j
  JOIN plenum_tracelab.judge_runs r ON r.judge_id = j.id AND r.split = 'test' AND r.status = 'done'
 WHERE j.kind = 'code' AND j.status = 'frozen' AND j.decision = 'use'"""))).scalars().all()
    return set(rows)


_BATCH_SET = {"status": ":status", "plan": "CAST(:plan AS jsonb)", "results": "CAST(:results AS jsonb)",
              "usd": ":usd", "error": ":error"}


async def _insert_batch(row: Mapping[str, Any]) -> None:
    async with db.engine().begin() as conn:
        await conn.execute(text("""
INSERT INTO plenum_tracelab.monitor_batches (id, organization_id, period_label, window_start, window_end, channel,
    sample_rate, seed, risk_groups, judges, checks, status, job_id, usd_cap, started_by, started_by_email)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :period, :start, :end, 'chat', :rate, :seed, CAST(:groups AS text[]),
    CAST(:judges AS jsonb), CAST(:checks AS jsonb), 'queued', CAST(:job AS uuid), :cap, CAST(:by AS uuid), :email)"""),
            {"id": row["id"], "org": row["organization_id"], "period": row["period_label"],
             "start": row["window_start"], "end": row["window_end"], "rate": Decimal(str(row["sample_rate"])),
             "seed": int(row["seed"]), "groups": list(row["risk_groups"]), "judges": json.dumps(public(row["judges"])),
             "checks": json.dumps(public(row["checks"])), "job": row["job_id"], "cap": Decimal(str(row["usd_cap"])),
             "by": row["started_by"], "email": row["started_by_email"]})


async def _load_batch(batch_id: str) -> dict | None:
    try:
        uuid.UUID(str(batch_id))
    except ValueError:
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("SELECT * FROM plenum_tracelab.monitor_batches WHERE id = CAST(:id AS uuid)"),
                                  {"id": str(batch_id)})).mappings().first()
    if row is None:
        return None
    out = dict(row)
    for key in ("judges", "checks", "plan", "results"):
        out[key] = _json(out.get(key))
    return out


async def _update_batch(batch_id: str, *, finished: bool = False, **fields: Any) -> None:
    sets: list[str] = []
    params: dict[str, Any] = {"id": str(batch_id)}
    for key, value in fields.items():
        if key not in _BATCH_SET:
            raise ValueError(f"not a batch field: {key}")
        sets.append(f"{key} = {_BATCH_SET[key]}")
        params[key] = json.dumps(public(value)) if key in ("plan", "results") else value
    if finished:
        sets.append("finished_at = now()")
    if not sets:
        return
    async with db.engine().begin() as conn:
        await conn.execute(text(f"UPDATE plenum_tracelab.monitor_batches SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"),
                           params)


def batch_public(row: Mapping[str, Any], *, with_plan: bool = False) -> dict:
    results = _json(row.get("results")) or None
    judges_snap = _json(row.get("judges")) or []
    out = {"id": row.get("id"), "organization_id": row.get("organization_id"),
           "scope": "platform" if row.get("organization_id") is None else "company",
           "period_label": row.get("period_label"), "window_start": row.get("window_start"),
           "window_end": row.get("window_end"), "sample_rate": row.get("sample_rate"), "seed": row.get("seed"),
           "risk_groups": list(row.get("risk_groups") or []),
           "judges": [{k: j.get(k) for k in ("judge_id", "judge_key", "mode_id", "mode_name", "kind", "threshold")}
                      for j in judges_snap],
           "status": row.get("status"), "usd": row.get("usd"), "usd_cap": row.get("usd_cap"),
           "started_by_email": row.get("started_by_email"), "started_at": row.get("started_at"),
           "finished_at": row.get("finished_at"), "error": row.get("error"), "results": results,
           "crossed": any(m.get("crossed") for m in ((results or {}).get("modes") or []))}
    if with_plan:
        out["plan"] = _json(row.get("plan"))
    return public(out)


# ---- plan preview and start (§4.1, §4.6, §7.3) -----------------------------------------------------

def _resolve_window(since: datetime | None, until: datetime | None, now: datetime) -> tuple[datetime, datetime]:
    if since is None and until is None:
        return last_complete_week(now)
    if since is None or until is None:
        raise WatchError(422, "bad_window", "Give both the start and the end of the window, or neither.")
    return _utc(since), _utc(until)


def _resolve_groups(names: Sequence[str] | None) -> list[str]:
    if names is None:
        return list(RISK_GROUPS)
    unknown = [n for n in names if n not in RISK_GROUPS]
    if unknown:
        raise WatchError(422, "unknown_risk_group", f"Unknown risk group: {', '.join(unknown)}.")
    return list(dict.fromkeys(names))


async def plan_preview(*, organization_id: str | None, since: datetime | None = None, until: datetime | None = None,
                       sample_rate: float = DEFAULT_RATE, seed: int = DEFAULT_SEED,
                       risk_groups: Sequence[str] | None = None, mode_ids: Sequence[str] | None = None,
                       now: datetime | None = None) -> dict:
    """What "Run now" would do, before it spends anything (D11): window, sample size and an estimate."""
    now = now or datetime.now(timezone.utc)
    start, end = _resolve_window(since, until, now)
    check_window(start, end, now=now)
    if not (0 < float(sample_rate) <= 1):
        raise WatchError(422, "bad_rate", "The sample rate must be above 0 and at most 1.")
    groups = _resolve_groups(risk_groups)
    turns = await window_turns(start, end, organization_id)
    plan = select_turns(turns, float(sample_rate), {g: RISK_GROUPS[g] for g in groups if g in CHEAP_GROUPS}, int(seed))
    in_use = await judges_in_use(mode_ids)
    warnings: list[str] = []
    usd = Decimal(0)
    for j in in_use:
        if j["kind"] != "llm":
            continue
        have = await _verdicts(j["evaluator_key"], plan["to_judge"])
        done = {t for t, v in have.items() if v["status"] == "decided"}
        done |= set(await _predictions(j["prompt_hash"], [t for t in plan["to_judge"] if t not in done]))
        per = call_usd(j["model"], j["prompt_text"] or "", EST_EVIDENCE_CHARS)
        if per is None:
            warnings.append(f"The judge for {j['mode_name']} has no price, so its cost cannot be estimated.")
            continue
        usd += per * sum(1 for t in plan["to_judge"] if t not in done)
    if not in_use:
        warnings.append("No failure mode has a judge in use yet: only the code checks will run.")
    if any(g not in CHEAP_GROUPS for g in groups):
        warnings.append("Answers in the risk groups that need each answer's steps (changed after checking, flagged by "
                        "a code check, asked again soon after) are added when the batch runs; the cost estimate "
                        "leaves them out.")
    if plan["n_window"] and not plan["random"]:
        warnings.append("The uniform sample is empty at this rate, so no failure rate can be estimated.")
    n_random, n_to_judge = len(plan["random"]), len(plan["to_judge"])
    return {"period_label": period_label(start, end), "window": {"start": start.isoformat(), "end": end.isoformat()},
            "n_window": plan["n_window"], "n_random": n_random, "n_risk_only": n_to_judge - n_random,
            "n_to_judge": n_to_judge, "judges": [_snapshot(j) for j in in_use], "checks": live_checks(),
            "usd_estimate": float(round(usd, 4)), "sample_rate": float(sample_rate), "seed": int(seed),
            "risk_groups": groups, "warnings": warnings}


async def start_batch(*, organization_id: str | None, since: datetime | None, until: datetime | None,
                      sample_rate: float, seed: int, risk_groups: Sequence[str] | None,
                      mode_ids: Sequence[str] | None, usd_cap: float | None, actor_id: str,
                      actor_email: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    preview = await plan_preview(organization_id=organization_id, since=since, until=until, sample_rate=sample_rate,
                                 seed=seed, risk_groups=risk_groups, mode_ids=mode_ids, now=now)
    cap = Decimal(str(usd_cap if usd_cap is not None else monitor_usd_cap()))
    estimate = Decimal(str(preview["usd_estimate"]))
    if estimate > cap:
        raise WatchError(409, "over_cap", f"This batch is estimated at ${float(estimate):.2f}, above its "
                         f"${float(cap):.2f} cap. Lower the sample rate or raise the cap.",
                         usd_estimate=float(estimate), usd_cap=float(cap))
    batch_id = str(uuid.uuid4())
    busy = "A monitoring batch for this scope is running or waiting to be resumed."
    try:
        job = await jobs.create(kind="monitor_batch", scope_key=f"monitor_batch:{organization_id or '*'}",
                                organization_id=organization_id, params={"batch_id": batch_id}, usd_cap=cap,
                                usd_estimate=estimate, total=preview["n_window"], actor_id=actor_id,
                                actor_email=actor_email)
    except jobs.JobConflict:
        raise WatchError(409, "batch_running", busy) from None
    job_id = str(job["id"])
    row = {"id": batch_id, "organization_id": organization_id, "period_label": preview["period_label"],
           "window_start": datetime.fromisoformat(preview["window"]["start"]),
           "window_end": datetime.fromisoformat(preview["window"]["end"]), "sample_rate": preview["sample_rate"],
           "seed": preview["seed"], "risk_groups": preview["risk_groups"], "judges": preview["judges"],
           "checks": preview["checks"], "status": "queued", "job_id": job_id, "usd_cap": cap,
           "started_by": actor_id, "started_by_email": actor_email}
    try:
        await _insert_batch(row)
    except IntegrityError:
        await jobs.finish(job_id, "failed", error="another batch for this scope started first")
        raise WatchError(409, "batch_running", busy) from None
    jobs.run_in_background(job_id, lambda: work_batch(batch_id))
    log.info("trace_lab.monitor.batch_started", batch_id=batch_id, scope=organization_id or "platform",
             n_window=preview["n_window"], usd_estimate=float(estimate))
    return {"batch": batch_public({**row, "usd": 0, "started_at": now}), "job": {"id": job_id, "status": "queued"}}
```

(`work_batch` is defined in Task 9; Python resolves it when the job starts.)

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py tests/test_trace_lab_watch_sql.py`
Expected: all monitor tests pass (23) and `test_reads_only_chat_and_excludes_golden_turns` passes on the scratch DB.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`, `DA/tests/test_trace_lab_watch_sql.py`.

---

### Task 8: `monitor.py` — the threshold action (review batch per company, proposed cases)

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Test: `DA/tests/test_trace_lab_monitor.py` (append)

**Interfaces:**
- Consumes: Task 6 `flagged_for_review`, `review_reason`; Task 7 `_load_batch`, `_verdicts`; M2's `sampling.create_batch(principal, org, req)` (the only writer of review batches) and `db.Invalid` / `db.Conflict`; the service's `services.principal.Principal` (`DA/src/services/principal.py:39-47`); table `sample_batches` (read only, for the once-per-batch check); `eval_cases.propose_from_monitor(*, batch, mode_id, turn_ids) -> list[str]` (Task 13, imported lazily so `monitor` and `eval_cases` never import each other at load).
- Produces: `_act_on_crossings(batch, plan, results) -> None` (fills each crossed mode's `review_batch_ids` and `proposed_case_ids`); `open_review_batches(batch_id, mode_id, *, actor_id, actor_email, batch=None, plan=None) -> list[str]`; `_flagged_ids(snap, plan) -> set[str]`; `_review_batches_for(batch_id, mode_id) -> list[str]`; `_turn_rows(turn_ids) -> dict[turn_id, {"organization_id", "session_id"}]`; `_propose_cases(batch, mode_id, turn_ids) -> list[str]`. There is no SQL writer for review batches here: each company's batch is one call of M2's `sampling.create_batch(principal, org, req)` with `req["strategy"] == "monitor_flagged"`, made as a `Principal` built from the monitor batch's starter.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_monitor.py`)

```python
J1 = "11111111-1111-4111-8111-111111111111"
M1 = "22222222-2222-4222-8222-222222222222"
SNAP = {"judge_id": J1, "judge_key": "wrong_period-v2", "mode_id": M1, "mode_name": "wrong_period", "kind": "llm",
        "evaluator_key": f"judge:{J1}", "threshold": 0.15, "prompt_hash": "abc123abc123"}


def _action_setup(monkeypatch, flagged, org_of):
    plan = {"random": ["t1", "t2", "t3", "t4", "t5", "t6", "t7"], "risk_groups": {"thumbs_down": ["t8"]},
            "to_judge": ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"]}
    batch = {"id": "b1", "judges": [SNAP], "plan": plan, "period_label": "2026-W41", "seed": 7,
             "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
             "started_by": ACTOR, "started_by_email": "owner@example.com"}
    created, proposed, existing = [], [], {}

    async def fake_verdicts(ek, ids):
        return {t: {"status": "decided", "failure_present": 1 if t in flagged else 0} for t in ids}

    async def fake_turns(ids):
        return {t: {"organization_id": org_of(t), "session_id": "s-" + t} for t in ids}

    async def fake_existing(batch_id, mode_id):
        return existing.get((batch_id, mode_id), [])

    async def fake_create_batch(principal, org, req):               # M2's one entry point for review batches
        created.append({"principal": principal, "org": org, "req": req})
        bid = "rb-" + org[:4]
        existing.setdefault(("b1", req["params"]["mode_id"]), []).append(bid)
        return {"ok": True, "batch": {"id": bid}, "items": []}

    async def fake_propose(b, mode_id, ids):
        proposed.append((mode_id, list(ids)))
        return ["case-1"]

    monkeypatch.setattr(monitor, "_verdicts", fake_verdicts)
    monkeypatch.setattr(monitor, "_turn_rows", fake_turns)
    monkeypatch.setattr(monitor, "_review_batches_for", fake_existing)
    monkeypatch.setattr(monitor.sampling, "create_batch", fake_create_batch)
    monkeypatch.setattr(monitor, "_propose_cases", fake_propose)
    return batch, plan, created, proposed


async def test_threshold_crossing_opens_review_batch_per_company_and_proposes_cases(monkeypatch):
    flagged = {"t1", "t2", "t3", "t4", "t5", "t6", "t8"}            # six uniform flags and one risk-only flag
    batch, plan, created, proposed = _action_setup(
        monkeypatch, flagged, lambda t: ORG_A if t in ("t1", "t2", "t8") else ORG_B)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []},
                         {"mode_id": "other", "crossed": False, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)
    assert {c["org"]: [i["turn_id"] for i in c["req"]["items"]] for c in created} == {
        ORG_A: ["t1", "t2", "t8"], ORG_B: ["t3", "t4", "t5", "t6"]}
    reasons = {i["turn_id"]: i["reason"] for c in created for i in c["req"]["items"]}
    assert reasons["t1"] == "flagged by wrong_period-v2 in 2026-W41: uniform sample"
    assert reasons["t8"].endswith(": risk group rated down by the asker")
    for c in created:                                               # recorded by M2, never written by M6
        req, who = c["req"], c["principal"]
        assert req["strategy"] == "monitor_flagged" and req["plan_step"] == "extra" and req["seed"] == 7
        assert (req["since"], req["until"]) == (batch["window_start"], batch["window_end"])
        assert req["params"] == {"monitor_batch_id": "b1", "mode_id": M1, "judge_id": J1}
        assert (str(who.user_id), who.email, who.role, who.organization_id, who.building_ids, who.can_ingest) == (
            ACTOR, "owner@example.com", "superadmin", None, None, False)       # the monitor batch's starter
    assert proposed == [(M1, ["t1", "t2", "t3", "t4", "t5"])]       # at most 5, uniform flags only
    assert results["modes"][0]["review_batch_ids"] == ["rb-aaaa", "rb-bbbb"]
    assert results["modes"][0]["proposed_case_ids"] == ["case-1"]
    assert results["modes"][1]["review_batch_ids"] == [] and len(proposed) == 1


async def test_review_batch_is_opened_once_per_batch_and_mode(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)
    first = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    again = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert first == again == ["rb-aaaa"] and len(created) == 1


async def test_nothing_flagged_is_a_409(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, set(), lambda t: ORG_A)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.status == 409 and e.value.reason == "nothing_flagged" and created == []


async def test_a_company_with_no_answer_left_to_review_is_skipped(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1", "t3"}, lambda t: ORG_A if t == "t1" else ORG_B)

    async def create(principal, org, req):
        if org == ORG_B:
            raise monitor.db.Invalid("pool_empty")                  # all of that company's answers are in batches already
        created.append(org)
        return {"ok": True, "batch": {"id": "rb-a"}, "items": []}

    monkeypatch.setattr(monitor.sampling, "create_batch", create)
    assert await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch) == ["rb-a"]
    assert created == [ORG_A]

    async def race(principal, org, req):
        raise monitor.db.Conflict("pool_changed")                   # another batch took them first

    monkeypatch.setattr(monitor.sampling, "create_batch", race)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.status == 409 and e.value.reason == "nothing_flagged"
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `AttributeError: … has no attribute '_turn_rows'` (monkeypatch refuses to set a missing attribute).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- threshold action (§4.2 step 7) --------------------------------------------------------------------

async def _flagged_ids(snap: Mapping[str, Any], plan: Mapping[str, Any]) -> set[str]:
    rows = await _verdicts(snap["evaluator_key"], list(plan.get("to_judge") or []))
    return {t for t, v in rows.items() if v["status"] == "decided" and v["failure_present"] == 1}


async def _review_batches_for(batch_id: str, mode_id: str) -> list[str]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id FROM plenum_tracelab.sample_batches WHERE strategy = 'monitor_flagged' "
            "AND params ->> 'monitor_batch_id' = :b AND params ->> 'mode_id' = :m ORDER BY created_at"),
            {"b": str(batch_id), "m": str(mode_id)})).scalars().all()
    return [str(r) for r in rows]


async def _turn_rows(turn_ids: Sequence[str]) -> dict[str, dict]:
    if not turn_ids:
        return {}
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id, organization_id, session_id FROM plenum_cafm.agent_trace_turns "
            "WHERE turn_id = ANY(CAST(:ids AS text[]))"), {"ids": list(turn_ids)})).mappings().all()
    return {r["turn_id"]: dict(r) for r in rows}


async def open_review_batches(batch_id: str, mode_id: str, *, actor_id: str, actor_email: str | None,
                              batch: Mapping[str, Any] | None = None,
                              plan: Mapping[str, Any] | None = None) -> list[str]:
    """The answers this judge flagged in the batch (uniform-sample flags first, then risk-only, at most
    FLAGGED_REVIEW_MAX), as one review batch per company, opened at most once per batch and mode. Each company's
    batch is recorded by M2's sampling.create_batch (strategy 'monitor_flagged'): it keeps only that company's
    answers that are not in one of its batches already, never counts toward prevalence, and asks for one review
    each. M6 writes no sample_batches or sample_items row itself."""
    batch = batch or await _load_batch(batch_id)
    if batch is None:
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    snap = next((s for s in batch.get("judges") or [] if str(s["mode_id"]) == str(mode_id)), None)
    if snap is None:
        raise WatchError(404, "not_found", "That failure mode was not part of this batch.")
    existing = await _review_batches_for(str(batch["id"]), str(mode_id))
    if existing:
        return existing
    plan = plan or batch.get("plan") or {}
    picks = flagged_for_review(plan, await _flagged_ids(snap, plan))
    if not picks:
        raise WatchError(409, "nothing_flagged", "No answer was flagged for this failure mode in this batch.")
    rows = await _turn_rows([p["turn_id"] for p in picks])
    by_org: dict[str, list[dict]] = {}
    for p in picks:
        row = rows.get(p["turn_id"])
        if not row or not row.get("organization_id"):
            continue
        by_org.setdefault(str(row["organization_id"]), []).append({
            "turn_id": p["turn_id"], "session_id": row.get("session_id"),
            "reason": review_reason(snap.get("judge_key") or "", batch.get("period_label") or "", p["group"])})
    actor = Principal(user_id=uuid.UUID(str(actor_id)), email=actor_email or "", organization_id=None,
                      role="superadmin", can_ingest=False, building_ids=None)
    created: list[str] = []
    for org, items in by_org.items():
        req = {"strategy": "monitor_flagged", "plan_step": "extra", "items": items, "seed": int(batch["seed"]),
               "since": batch["window_start"], "until": batch["window_end"],
               "params": {"monitor_batch_id": str(batch["id"]), "mode_id": str(snap["mode_id"]),
                          "judge_id": str(snap["judge_id"])}}
        try:
            out = await sampling.create_batch(actor, org, req)
        except (db.Invalid, db.Conflict) as exc:
            if exc.reason in ("pool_empty", "pool_changed"):
                continue                      # this company's flagged answers are all in one of its batches already
            raise
        created.append(str(out["batch"]["id"]))
    if not created:
        raise WatchError(409, "nothing_flagged", "Every flagged answer is already in a review batch.")
    log.info("trace_lab.monitor.review_batches", batch_id=str(batch["id"]), mode_id=str(mode_id), batches=len(created))
    return created


async def _propose_cases(batch: Mapping[str, Any], mode_id: str, turn_ids: Sequence[str]) -> list[str]:
    from . import eval_cases   # lazy: eval_cases imports this module

    return await eval_cases.propose_from_monitor(batch=batch, mode_id=mode_id, turn_ids=turn_ids)


async def _act_on_crossings(batch: Mapping[str, Any], plan: Mapping[str, Any], results: dict) -> None:
    """At most once per batch per mode: a review batch per company, and up to PROPOSE_CASES_MAX uniform-sample
    flags as proposed eval cases (a person confirms each one). No email in V1."""
    actor = str(batch["started_by"]) if batch.get("started_by") else None
    for m in results.get("modes") or []:
        if not m.get("crossed"):
            continue
        snap = next((s for s in batch.get("judges") or [] if str(s["mode_id"]) == str(m["mode_id"])), None)
        if snap is None:
            continue
        if actor:
            try:
                m["review_batch_ids"] = await open_review_batches(str(batch["id"]), str(m["mode_id"]), actor_id=actor,
                                                                  actor_email=batch.get("started_by_email"),
                                                                  batch=batch, plan=plan)
            except (WatchError, db.ReviewError) as exc:
                log.info("trace_lab.monitor.no_review_batch", batch_id=str(batch["id"]), mode_id=str(m["mode_id"]),
                         reason=exc.reason)
        uniform_flags = [p["turn_id"] for p in flagged_for_review(plan, await _flagged_ids(snap, plan))
                         if p["group"] is None][:PROPOSE_CASES_MAX]
        if uniform_flags:
            m["proposed_case_ids"] = await _propose_cases(batch, str(m["mode_id"]), uniform_flags)
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → all pass (27).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`.

---

### Task 9: `monitor.py` — the batch worker (checks on every answer, judges on the sample, estimates, resume)

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Test: `DA/tests/test_trace_lab_monitor.py` (append)

**Interfaces:**
- Consumes: M5's `judges.load_judge_turns(turn_ids) -> dict[turn_id, checks.NormalizedTurn]` (the one turn loader: the worker reads `NormalizedTurn.session_kind` from it and never calls `normalize.session_kind`), `judges.judge_input`, `judges.ASK`, `judges.JUDGE_CONCURRENCY`, `judges.test_pairs`; `checks.run_live_checks`, `checks.CHECKS` (M5); `jobs.claim`, `jobs.reserve`, `jobs.CapReached`, `jobs.finish`, `jobs.get`, `jobs.run_in_background`, `jobs.STALE_AFTER_S` (M0); Tasks 5–8.
- Produces: `work_batch(batch_id) -> None` (the `monitor_batch` job body); `_run(batch, job_id) -> "done" | "capped"`; `_check_all(batch, job_id, turns) -> list[fact]` (fact = window row + `session_kind, question, gate_outcomes, code_failed, re_ask`); `_judge_sample(batch, job_id, plan, by_id) -> bool` (capped?); `_call_judge(batch, job_id, snap, judge, todo, by_id) -> bool`; `_estimate_all(batch, plan, facts) -> results` (spec §4.2 `results` shape); `_settle(rows) -> rows`; `resume_batch(batch_id, *, org) -> {"batch"}`.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_monitor.py`)

```python
from types import SimpleNamespace  # noqa: E402

from src.trace_lab import checks, jobs, judges  # noqa: E402

LIVE = [k for k, c in checks.CHECKS.items() if c.scope == "live"]


class FakeStore:
    """In-memory stand-ins for monitor's store, the normalizer, the checks, the judge and the job helpers."""

    def __init__(self, n, *, fails=(), code_fail=()):
        self.turns = [{"turn_id": f"t{i:03d}", "session_id": f"s{i}", "organization_id": ORG_A, "user_id": "u",
                       "started_at": datetime(2026, 10, 6, tzinfo=UTC) + timedelta(minutes=i), "ok": True,
                       "feedback_rating": "down" if i % 10 == 0 else None} for i in range(1, n + 1)]
        self.fails, self.code_fail = set(fails), set(code_fail)
        self.verdicts, self.predictions = {}, {}
        self.asked, self.loaded_chunks, self.batch_updates = [], [], []
        self.reserved, self.cap_after, self.acted = 0, None, None

    def install(self, monkeypatch):
        store = self

        async def window_turns(start, end, org):
            return list(store.turns)

        async def verdicts(ek, ids):
            return {t: store.verdicts[(ek, t)] for t in ids if (ek, t) in store.verdicts}

        async def predictions(ph, ids):
            return {t: store.predictions[(ph, t)] for t in ids
                    if (ph, t) in store.predictions and store.predictions[(ph, t)]["status"] == "decided"}

        async def save(job_id, *, verdicts, prediction=None, usd=None, released=Decimal(0), done_delta=0, progress=None):
            for v in verdicts:
                key = (v["ek"], v["tid"])
                if key in store.verdicts and store.verdicts[key]["status"] == "decided":
                    continue
                store.verdicts[key] = {"turn_id": v["tid"], "status": v["status"], "failure_present": v["fp"],
                                       "organization_id": v["org"]}
            if prediction is not None:                      # judges.write_prediction's keyword arguments
                pred = prediction["pred"]
                store.predictions[(prediction["judge"]["prompt_hash"], prediction["turn_id"])] = {
                    "status": pred["status"], "failure_present": pred["failure_present"], "critique": pred["critique"]}

        async def counts(eks, ids):
            agg, wanted = {}, set(ids)
            for (ek, t), v in store.verdicts.items():
                if ek in eks and t in wanted:
                    k = (ek, v["status"], v["failure_present"])
                    agg[k] = agg.get(k, 0) + 1
            return [{"evaluator_key": k[0], "status": k[1], "failure_present": k[2], "n": n} for k, n in agg.items()]

        async def load_judge_turns(ids):                    # M5's loader: {turn_id: NormalizedTurn}
            store.loaded_chunks.append(len(ids))
            return {t: SimpleNamespace(turn_id=t, question=f"question {t}", gates=[], tool_calls=1,
                                       session_kind="observed") for t in ids}

        def run_live_checks(nt):
            return {k: checks.CheckResult("fail" if (k == LIVE[0] and nt.turn_id in store.code_fail) else "pass")
                    for k in LIVE}

        async def ask(model, prompt_text, judge_text):
            tid = judge_text.split()[-1]
            store.asked.append(tid)
            return SimpleNamespace(failure_present=1 if tid in store.fails else 0, critique="because", status="decided",
                                   usd=0.01, error=None)

        async def reserve(job_id, usd):
            if store.cap_after is not None and store.reserved >= store.cap_after:
                raise jobs.CapReached()
            store.reserved += 1

        async def judge_row(judge_id):
            return {"id": SNAP["judge_id"], "model": "claude-sonnet-5", "prompt_text": "prompt",
                    "prompt_hash": SNAP["prompt_hash"]}

        async def update_batch(batch_id, *, finished=False, **fields):
            store.batch_updates.append(dict(fields, finished=finished))

        async def in_use(mode_ids=None):
            return [dict(SNAP, test_labels=ARTIFACT_G_LABELS, test_preds=ARTIFACT_G_PREDS)]

        async def validated():
            return set()

        async def act(batch, plan, results):
            store.acted = results

        async def job_get(job_id, *, organization_id):
            return {"status": "running", "usd_spent": Decimal("0.05")}

        for name, fn in {"window_turns": window_turns, "_verdicts": verdicts, "_predictions": predictions,
                         "_save": save, "_verdict_counts": counts, "_judge_row": judge_row,
                         "_update_batch": update_batch, "judges_in_use": in_use, "_validated_check_keys": validated,
                         "_act_on_crossings": act}.items():
            monkeypatch.setattr(monitor, name, fn)
        monkeypatch.setattr(judges, "load_judge_turns", load_judge_turns)
        monkeypatch.setattr(checks, "run_live_checks", run_live_checks)
        monkeypatch.setattr(judges, "judge_input",
                            lambda nt: SimpleNamespace(text=f"evidence {nt.turn_id}", chars=100, truncated=False, sha="sha"))
        monkeypatch.setattr(judges, "ASK", ask)
        monkeypatch.setattr(judges, "JUDGE_CONCURRENCY", 1)
        monkeypatch.setattr(jobs, "reserve", reserve)
        monkeypatch.setattr(jobs, "get", job_get)
        return self


def _batch(**kw):
    b = {"id": "b1", "organization_id": None, "window_start": datetime(2026, 10, 5, tzinfo=UTC),
         "window_end": datetime(2026, 10, 12, tzinfo=UTC), "sample_rate": 0.25, "seed": 7,
         "risk_groups": ["thumbs_down"], "judges": [SNAP], "checks": monitor.live_checks(), "plan": None,
         "period_label": "2026-W41", "started_by": ACTOR, "started_by_email": "owner@example.com"}
    b.update(kw)
    return b


def _plan_of(store):
    return next(u["plan"] for u in store.batch_updates if "plan" in u)


async def test_code_checks_cover_every_window_turn(monkeypatch):
    store = FakeStore(120).install(monkeypatch)
    assert await monitor._run(_batch(judges=[]), JOB_ID) == "done"
    assert store.loaded_chunks == [50, 50, 20]
    for c in LIVE:
        ek = checks.CHECKS[c].evaluator_key
        assert all((ek, t["turn_id"]) in store.verdicts for t in store.turns), ek
    assert store.asked == []


async def test_judges_run_only_on_planned_turns(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    assert await monitor._run(_batch(), JOB_ID) == "done"
    plan = _plan_of(store)
    assert sorted(store.asked) == sorted(plan["to_judge"]) and len(store.asked) == len(plan["to_judge"])
    assert store.asked[:len(plan["random"])] == plan["random"]


async def test_overlapping_batches_reuse_verdicts(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    await monitor._run(_batch(), JOB_ID)
    store.asked.clear()
    await monitor._run(_batch(id="b2"), JOB_ID)
    assert store.asked == []                                          # decided verdicts are reused
    for key in [k for k in store.verdicts if k[0] == SNAP["evaluator_key"]]:
        del store.verdicts[key]
    await monitor._run(_batch(id="b3"), JOB_ID)
    assert store.asked == []                                          # cached predictions are copied, never re-asked
    assert any(k[0] == SNAP["evaluator_key"] for k in store.verdicts)


async def test_random_judged_first_so_capped_batch_stays_uniform(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    store.cap_after = 3
    assert await monitor._run(_batch(), JOB_ID) == "capped"
    plan = _plan_of(store)
    assert store.asked == plan["random"][:3]
    assert not set(store.asked) & (set(plan["to_judge"]) - set(plan["random"]))


async def test_risk_only_failures_never_lift_the_estimate(monkeypatch):
    probe = FakeStore(40)
    plan = monitor.select_turns(probe.turns, 0.25, {"thumbs_down": monitor.RISK_GROUPS["thumbs_down"]}, 7)
    risk_only = set(plan["risk_groups"]["thumbs_down"]) - set(plan["random"])
    store = FakeStore(40, fails=risk_only).install(monkeypatch)
    await monitor._run(_batch(), JOB_ID)
    mode = store.acted["modes"][0]
    assert mode["raw"] == 0.0 and mode["n_sample"] == len(plan["random"])
    assert mode["flagged"] == len(risk_only) and mode["risk_groups"]["thumbs_down"]["flagged"] == len(risk_only)
    assert store.acted["n_window"] == 40 and store.acted["code_checks"][0]["n"] == 40


async def test_work_batch_records_a_failure_and_finishes_the_job(monkeypatch):
    updates, finished = [], []

    async def load(batch_id):
        return _batch(job_id=JOB_ID)

    async def claim(job_id, *, worker):
        return {"id": job_id}

    async def boom(batch, job_id):
        raise RuntimeError("database went away")

    async def update(batch_id, *, finished=False, **fields):
        updates.append(dict(fields, finished=finished))

    async def finish(job_id, status, error=None):
        finished.append((status, error))

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_run", boom)
    monkeypatch.setattr(monitor, "_update_batch", update)
    monkeypatch.setattr(jobs, "claim", claim)
    monkeypatch.setattr(jobs, "finish", finish)
    await monitor.work_batch("b1")
    assert updates[0] == {"status": "running", "finished": False}
    assert updates[-1]["status"] == "failed" and updates[-1]["finished"] is True
    assert finished == [("failed", "database went away")]


async def test_settle_marks_a_batch_interrupted_when_its_job_was_and_resume_requeues_it(monkeypatch):
    updates, started = [], []

    async def job_get(job_id, *, organization_id):
        return {"status": "interrupted", "created_at": datetime.now(UTC)}

    async def update(batch_id, *, finished=False, **fields):
        updates.append(fields)

    async def load(batch_id):
        return _batch(id=batch_id, status="running", job_id=JOB_ID, organization_id=None)

    monkeypatch.setattr(jobs, "get", job_get)
    monkeypatch.setattr(monitor, "_update_batch", update)
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(jobs, "run_in_background", lambda job_id, factory: started.append(job_id))
    rows = await monitor._settle([_batch(status="running", job_id=JOB_ID)])
    assert rows[0]["status"] == "interrupted" and updates[0] == {"status": "interrupted"}
    out = await monitor.resume_batch("b9", org=None)
    assert out["batch"]["status"] == "queued" and started == [JOB_ID]
    with pytest.raises(monitor.WatchError) as e:
        await monitor.resume_batch("b9", org=ORG_A)                  # another scope reads as not found
    assert e.value.status == 404
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `AttributeError: module 'src.trace_lab.monitor' has no attribute '_run'` (and `work_batch`, `_settle`, `resume_batch`).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- the batch worker (§4.2; job kind monitor_batch) ---------------------------------------------------

async def work_batch(batch_id: str) -> None:
    """The monitor_batch job body. Never raises into the event loop: a failure is recorded on the batch and
    the job. A resumed batch re-runs the free code checks and reuses its stored plan and decided verdicts."""
    batch = await _load_batch(batch_id)
    if batch is None:
        return
    job_id = str(batch["job_id"])
    if await jobs.claim(job_id, worker=WORKER) is None:
        return
    await _update_batch(batch_id, status="running")
    try:
        status = await _run(batch, job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.monitor.batch_failed", batch_id=batch_id, error=str(exc)[:300])
        await _update_batch(batch_id, status="failed", error=str(exc)[:2000], finished=True)
        await jobs.finish(job_id, "failed", error=str(exc)[:2000])
        return
    await _update_batch(batch_id, status=status, finished=True)
    await jobs.finish(job_id, status)
    log.info("trace_lab.monitor.batch_finished", batch_id=batch_id, status=status)


async def _run(batch: Mapping[str, Any], job_id: str) -> str:
    org = str(batch["organization_id"]) if batch.get("organization_id") else None
    turns = await window_turns(batch["window_start"], batch["window_end"], org)
    facts = await _check_all(batch, job_id, turns)
    plan = batch.get("plan")
    if not plan:
        groups = {g: RISK_GROUPS[g] for g in batch.get("risk_groups") or [] if g in RISK_GROUPS}
        plan = select_turns(facts, float(batch["sample_rate"]), groups, int(batch["seed"]))
        await _update_batch(str(batch["id"]), plan=plan)
    by_id = {f["turn_id"]: f for f in facts}
    capped = await _judge_sample(batch, job_id, plan, by_id)
    results = await _estimate_all(batch, plan, facts)
    await _act_on_crossings(batch, plan, results)
    job = await jobs.get(job_id, organization_id=org)
    await _update_batch(str(batch["id"]), results=results, usd=Decimal(str((job or {}).get("usd_spent") or 0)))
    return "capped" if capped else "done"


async def _check_all(batch: Mapping[str, Any], job_id: str, turns: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Every live code check on every answer in the window: free, one checkpoint per chunk of CHUNK."""
    live = [c for c in checks.CHECKS.values() if c.scope == "live"]
    batch_id = str(batch["id"])
    facts: list[dict] = []
    for i in range(0, len(turns), CHUNK):
        chunk = list(turns[i:i + CHUNK])
        loaded = await judges.load_judge_turns([t["turn_id"] for t in chunk])     # M5's loader: {turn_id: NormalizedTurn}
        rows: list[dict] = []
        for t in chunk:
            fact = dict(t)
            nt = loaded.get(t["turn_id"])
            fact["session_kind"] = _field(nt, "session_kind", "inferred") if nt is not None else "inferred"
            if nt is None:
                fact.update(question=None, gate_outcomes=(), code_failed=False)
                rows += [verdict_row(evaluator_key=c.evaluator_key, turn=t, status="undecidable", failure_present=None,
                                     detail={"findings": ["the answer could not be read"]}, batch_id=batch_id)
                         for c in live]
            else:
                results = checks.run_live_checks(nt)
                fact.update(question=_field(nt, "question"),
                            gate_outcomes=tuple(_field(g, "outcome") for g in (_field(nt, "gates") or ())),
                            code_failed=any(r.status == "fail" for r in results.values()))
                rows += [verdict_row(evaluator_key=checks.CHECKS[key].evaluator_key, turn=t,
                                     status=_check_status(r.status), failure_present=r.failure_present,
                                     detail=_check_detail(r), batch_id=batch_id) for key, r in results.items()]
            facts.append(fact)
        await _save(job_id, verdicts=rows, done_delta=len(chunk),
                    progress={"phase": "checks", "checked": i + len(chunk), "n_window": len(turns)})
    re_asked = mark_re_asks(facts)
    for f in facts:
        f["re_ask"] = f["turn_id"] in re_asked
    return facts


async def _judge_sample(batch: Mapping[str, Any], job_id: str, plan: Mapping[str, Any],
                        by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """Each LLM judge in use, over to_judge in order: reuse a decided verdict, else copy a decided prediction,
    else call the judge. True when the cap stopped it."""
    for snap in batch.get("judges") or []:
        if snap.get("kind") != "llm":
            continue
        judge = await _judge_row(str(snap["judge_id"]))
        order = [t for t in plan.get("to_judge") or [] if t in by_id]
        have = await _verdicts(snap["evaluator_key"], order)
        decided = {t for t, v in have.items() if v["status"] == "decided"}
        cached = await _predictions(judge["prompt_hash"], [t for t in order if t not in decided])
        copies = [verdict_row(evaluator_key=snap["evaluator_key"], turn=by_id[t], status="decided",
                              failure_present=int(p["failure_present"]),
                              detail={"critique": (p.get("critique") or "")[:1800]}, batch_id=str(batch["id"]),
                              judge_id=str(snap["judge_id"]), mode_id=str(snap["mode_id"])) for t, p in cached.items()]
        if copies:
            await _save(job_id, verdicts=copies, progress={"phase": "judging", "judge": snap.get("judge_key")})
        todo = [t for t in order if t not in decided and t not in cached]
        if await _call_judge(batch, job_id, snap, judge, todo, by_id):
            return True
    return False


async def _call_judge(batch: Mapping[str, Any], job_id: str, snap: Mapping[str, Any], judge: Mapping[str, Any],
                      todo: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """Calls the frozen judge on todo in order, at most JUDGE_CONCURRENCY in flight. Dispatch stops at the
    cap, so what was judged is a prefix of the order: the uniform sample first."""
    sem = asyncio.Semaphore(max(1, int(judges.JUDGE_CONCURRENCY)))
    state = {"capped": False}

    async def one(tid: str) -> None:
        async with sem:
            if state["capped"]:
                return
            fact = by_id[tid]
            common = {"evaluator_key": snap["evaluator_key"], "turn": fact, "batch_id": str(batch["id"]),
                      "judge_id": str(snap["judge_id"]), "mode_id": str(snap["mode_id"])}
            turn = (await judges.load_judge_turns([tid])).get(tid)          # M5's loader: {turn_id: NormalizedTurn}
            if turn is None:
                await _save(job_id, verdicts=[verdict_row(status="error", failure_present=None,
                                                          detail={"error": "the answer could not be read"}, **common)])
                return
            inp = judges.judge_input(turn)
            chars = int(_field(inp, "chars", 0) or 0)
            est = call_usd(judge["model"], judge["prompt_text"] or "", chars) or Decimal(0)
            try:
                await jobs.reserve(job_id, est)
            except jobs.CapReached:
                state["capped"] = True
                return
            try:
                reply = await judges.ASK(judge["model"], judge["prompt_text"], _field(inp, "text"))
            except Exception as exc:  # noqa: BLE001 - a failed call is an error verdict, never a pass
                reply = SimpleNamespace(status="error", failure_present=None, critique="", usd=None,
                                        error=type(exc).__name__)
            prediction = prediction_args(judge=judge, turn_id=tid, organization_id=str(fact["organization_id"]),
                                         reply=reply, inp=inp)                  # written through judges.write_prediction
            pred = prediction["pred"]
            detail = ({"critique": (reply.critique or "")[:1800]} if pred["status"] == "decided"
                      else {"error": pred["error"]})
            await _save(job_id, verdicts=[verdict_row(status=pred["status"], failure_present=pred["failure_present"],
                                                      detail=detail, **common)],
                        prediction=prediction, usd=pred["usd"], released=est,
                        progress={"phase": "judging", "judge": snap.get("judge_key")})

    await asyncio.gather(*(one(t) for t in todo))
    return state["capped"]


async def _estimate_all(batch: Mapping[str, Any], plan: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> dict:
    window_ids = [f["turn_id"] for f in facts]
    to_judge = list(plan.get("to_judge") or [])
    snaps = list(batch.get("judges") or [])
    in_use = {j["judge_id"]: j for j in await judges_in_use([s["mode_id"] for s in snaps] or None)}
    modes = []
    for snap in snaps:
        j = in_use.get(str(snap["judge_id"]))
        labels, preds = (j["test_labels"], j["test_preds"]) if j else await judges.test_pairs(str(snap["judge_id"]))
        ids = estimate_ids(snap["kind"], plan, window_ids)
        rows = await _verdicts(snap["evaluator_key"], ids)
        sample = [rows[t] for t in ids if t in rows]
        est = await asyncio.to_thread(estimate_mode, verdicts=sample, test_labels=labels, test_preds=preds,
                                      threshold=snap.get("threshold"), n_planned=len(ids))
        scope = to_judge if snap["kind"] == "llm" else window_ids
        flagged = {t for t, v in (await _verdicts(snap["evaluator_key"], scope)).items()
                   if v["status"] == "decided" and v["failure_present"] == 1}
        risk = {g: {"n": len(members), "flagged": sum(1 for t in members if t in flagged)}
                for g, members in (plan.get("risk_groups") or {}).items()}
        modes.append({"mode_id": str(snap["mode_id"]), "mode_name": snap.get("mode_name"),
                      "judge_id": str(snap["judge_id"]), "judge_key": snap.get("judge_key"), "kind": snap["kind"],
                      "evaluator_key": snap["evaluator_key"], **est, "flagged": len(flagged), "risk_groups": risk,
                      "review_batch_ids": [], "proposed_case_ids": []})
    labels_by_key = {c["evaluator_key"]: c["label"] for c in batch.get("checks") or []}
    counts = await _verdict_counts(list(labels_by_key), window_ids)
    n_random = len(plan.get("random") or [])
    return {"n_window": len(window_ids), "n_random": n_random, "n_risk_only": len(to_judge) - n_random,
            "modes": modes,
            "code_checks": summarise_checks(counts, labels=labels_by_key, validated=await _validated_check_keys(),
                                            n_window=len(window_ids))}


async def _settle(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Settle on read (instead of a startup hook): a queued or running batch whose job was interrupted,
    failed or cancelled, or never claimed for STALE_AFTER_S, reads as interrupted or failed."""
    out: list[dict] = []
    for r in rows:
        r = dict(r)
        if r.get("status") in ("queued", "running") and r.get("job_id"):
            job = await jobs.get(str(r["job_id"]),
                                 organization_id=str(r["organization_id"]) if r.get("organization_id") else None)
            js = (job or {}).get("status")
            created = (job or {}).get("created_at")
            stale_queue = (js == "queued" and created is not None
                           and _utc(created) < datetime.now(timezone.utc) - timedelta(seconds=int(jobs.STALE_AFTER_S)))
            new = "interrupted" if js == "interrupted" or stale_queue else ("failed" if js in ("failed", "cancelled") else None)
            if new:
                await _update_batch(str(r["id"]), status=new)
                r["status"] = new
        out.append(r)
    return out


async def resume_batch(batch_id: str, *, org: str | None) -> dict:
    row = await _load_batch(batch_id)
    if row is None or str(row.get("organization_id") or "") != str(org or ""):
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    row = (await _settle([row]))[0]
    if row["status"] != "interrupted":
        raise WatchError(409, "not_interrupted", "Only an interrupted batch can be resumed.")
    await _update_batch(str(row["id"]), status="queued")
    jobs.run_in_background(str(row["job_id"]), lambda: work_batch(str(row["id"])))
    return {"batch": batch_public(dict(row, status="queued"))}
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → all pass (34).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`.

---

### Task 10: `monitor.py` — reads: batches with the Due marker, batch detail, chart, code-check trend, prevalence for M5's report

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Modify: `DA/src/trace_lab/judges.py` (M5's file: the body of the `latest_prevalence` seam, one exact-string edit)
- Test: `DA/tests/test_trace_lab_monitor.py` (append)

**Interfaces:**
- Consumes: Tasks 5, 6, 7, 9 (`weeks_back`, `chart_points`, `due_marker`, `batch_public`, `_load_batch`, `_settle`, `_validated_check_keys`); M5's seam `judges.latest_prevalence(mode_id) -> (dict | None, reason)`; tables `monitor_batches`, `monitor_verdicts`, `modes`, `turn_meta`, `plenum_cafm.agent_trace_turns`.
- Produces: `latest_prevalence(mode_id) -> (dict | None, str)` (the corrected prevalence of one mode from the latest done or capped platform-wide batch that estimated it, keys `period, batch_id, n_sample, raw, corrected, ci_low, ci_high, confidence, validity_warning, threshold`; M5's failure report shows it, because M5's seam body now calls this); `list_batches(*, org, limit=20, before=None, now=None) -> {"batches", "due"}` (org None = platform owner, sees every batch; Due is computed for exactly the viewed scope); `batch_detail(batch_id, *, org, mode_id=None, offset=0, limit=50) -> {"batch", "flagged": [{turn_id, question, mode_name, evaluator_key, critique, group}]}`; `chart(*, org, mode_id, periods=12) -> {"mode", "points", "threshold", "y_max"}`; `code_check_trend(*, org, periods=12, now=None) -> {"checks": [{evaluator_key, label, validated, weeks: [{period_label, n, flagged, rate}]}]}`; store `_batch_rows`, `_scope_batches`, `_flagged_rows`, `_mode`, `_period_batches`, `_check_week_rows`.

- [ ] **Step 0: Locate M5's seam** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/backend/cafm-connector-service-final/svc-deepagents
grep -n "async def latest_prevalence" -A 3 src/trace_lab/judges.py
```

Expected: the seam exactly as M5 wrote it: a docstring line `The seam M6 fills with its latest monitor batch for the mode. M5 has no monitoring yet.` and `return None, "Monitoring has not run for this mode yet."`. If it already calls `monitor.latest_prevalence`, skip the `judges.py` edit in Step 3; if the body differs otherwise, stop and report.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_monitor.py`)

```python
import uuid  # noqa: E402


def _row(bid, org, **kw):
    r = {"id": bid, "organization_id": uuid.UUID(org) if org else None, "period_label": "2026-W41",
         "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
         "sample_rate": Decimal("0.25"), "seed": 7, "risk_groups": ["thumbs_down"], "judges": [SNAP], "checks": [],
         "plan": {"random": ["t1", "t2"], "risk_groups": {"thumbs_down": ["t3"]}, "to_judge": ["t1", "t2", "t3"]},
         "results": None, "status": "done", "job_id": None, "usd": Decimal("0.4"), "usd_cap": Decimal("5"),
         "started_by_email": "owner@example.com", "started_at": NOW, "finished_at": NOW, "error": None}
    r.update(kw)
    return r


async def _same(rows):
    return [dict(r) for r in rows]


async def test_company_scope_and_platform_batch_hidden_from_admin(monkeypatch):
    platform, company = _row("b-p", None), _row("b-a", ORG_A)
    seen, flagged_orgs = {}, []

    async def rows(org, limit, before):
        seen["list"] = org
        return [company] if org else [platform, company]

    async def scope_rows(org):
        seen["scope"] = org
        return [company] if org else [platform]

    async def load(bid):
        return {"b-p": platform, "b-a": company}.get(bid)

    async def flagged(eks, ids, *, org, offset, limit):
        flagged_orgs.append(org)
        return []

    monkeypatch.setattr(monitor, "_batch_rows", rows)
    monkeypatch.setattr(monitor, "_scope_batches", scope_rows)
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    out = await monitor.list_batches(org=ORG_A, now=NOW)
    assert seen == {"list": ORG_A, "scope": ORG_A}
    assert [b["id"] for b in out["batches"]] == ["b-a"] and out["batches"][0]["scope"] == "company"
    assert out["due"] == {"period_label": "2026-W41", "is_due": False}
    for bid, org in (("b-p", ORG_A), ("b-a", ORG_B)):
        with pytest.raises(monitor.WatchError) as e:
            await monitor.batch_detail(bid, org=org)
        assert e.value.status == 404
    await monitor.batch_detail("b-a", org=ORG_A)
    await monitor.batch_detail("b-p", org=None)
    assert flagged_orgs == [ORG_A, None]


async def test_batch_detail_names_the_group_of_each_flagged_answer(monkeypatch):
    row = _row("b-a", ORG_A)

    async def load(bid):
        return row

    async def flagged(eks, ids, *, org, offset, limit):
        assert eks == [SNAP["evaluator_key"]] and ids == ["t1", "t2", "t3"] and org == ORG_A
        return [{"turn_id": "t2", "evaluator_key": SNAP["evaluator_key"], "critique": "Quotes June, not July.",
                 "question": "How many work orders in July?"},
                {"turn_id": "t3", "evaluator_key": SNAP["evaluator_key"], "critique": "x", "question": "q"}]

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    out = await monitor.batch_detail("b-a", org=ORG_A)
    assert [(f["turn_id"], f["group"], f["mode_name"]) for f in out["flagged"]] == [
        ("t2", "uniform sample", "wrong_period"), ("t3", "rated down by the asker", "wrong_period")]
    assert "plan" not in out["batch"]


async def test_chart_reads_the_latest_batch_per_period(monkeypatch):
    async def mode(mode_id):
        return {"id": M1, "name": "wrong_period"} if mode_id == M1 else None

    async def periods(org):
        assert org is None
        week = {"window_start": datetime(2026, 9, 28, tzinfo=UTC), "window_end": datetime(2026, 10, 5, tzinfo=UTC)}
        return [dict(week, id="b40", period_label="2026-W40", results={"modes": [
                    {"mode_id": M1, "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2, "threshold": 0.15,
                     "crossed": False, "n_sample": 100, "validity_warning": ""}]}),
                dict(week, id="b41", period_label="2026-W41", results={"modes": [{"mode_id": "other"}]})]

    monkeypatch.setattr(monitor, "_mode", mode)
    monkeypatch.setattr(monitor, "_period_batches", periods)
    out = await monitor.chart(org=None, mode_id=M1)
    assert out["mode"] == {"id": M1, "name": "wrong_period"}
    assert [p["batch_id"] for p in out["points"]] == ["b40"] and out["y_max"] == 0.25 and out["threshold"] == 0.15
    with pytest.raises(monitor.WatchError) as e:
        await monitor.chart(org=None, mode_id="not-a-mode")
    assert e.value.status == 404


async def test_code_check_trend_fills_every_week(monkeypatch):
    ek = checks.CHECKS[LIVE[0]].evaluator_key

    async def rows(org, since):
        assert org == ORG_A and since == datetime(2026, 10, 5, tzinfo=UTC)
        return [{"evaluator_key": ek, "week_start": datetime(2026, 10, 5), "n": 412, "decided": 412, "flagged": 9}]

    async def validated():
        return {ek}

    monkeypatch.setattr(monitor, "_check_week_rows", rows)
    monkeypatch.setattr(monitor, "_validated_check_keys", validated)
    out = await monitor.code_check_trend(org=ORG_A, periods=2, now=NOW)
    first = next(c for c in out["checks"] if c["evaluator_key"] == ek)
    assert first["validated"] is True and first["label"] == checks.CHECKS[LIVE[0]].label
    assert first["weeks"] == [{"period_label": "2026-W41", "n": 412, "flagged": 9, "rate": 0.0218},
                              {"period_label": "2026-W42", "n": 0, "flagged": 0, "rate": None}]
    assert len(out["checks"]) == len(LIVE)


async def test_latest_prevalence_reads_the_latest_platform_batch_that_estimated_the_mode(monkeypatch):
    week = {"window_start": datetime(2026, 9, 28, tzinfo=UTC), "window_end": datetime(2026, 10, 5, tzinfo=UTC)}
    estimated = {"mode_id": M1, "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2, "confidence": 0.95,
                 "n_sample": 100, "threshold": 0.15, "validity_warning": "", "reason": None}

    async def periods(org):
        assert org is None                                            # M5's failure report is the platform owner's view
        return [dict(week, id="b40", period_label="2026-W40", results={"modes": [estimated]}),
                dict(week, id="b41", period_label="2026-W41", results={"modes": [{"mode_id": "other"}]})]

    monkeypatch.setattr(monitor, "_period_batches", periods)
    found, reason = await monitor.latest_prevalence(M1)
    assert reason == "" and found["period"] == "2026-W40" and found["batch_id"] == "b40"
    assert (found["corrected"], found["ci_low"], found["ci_high"], found["n_sample"]) == (0.1, 0.02, 0.2, 100)
    none, why = await monitor.latest_prevalence("a-mode-no-batch-estimated")
    assert none is None and why == "Monitoring has not produced an estimate for this mode yet."
    estimated["corrected"], estimated["reason"] = None, "the judge has no held-out test result"
    assert await monitor.latest_prevalence(M1) == (None, "the judge has no held-out test result")


async def test_m5_failure_report_seam_reads_the_latest_prevalence(monkeypatch):
    async def fake(mode_id):
        return {"period": "2026-W41", "corrected": 0.17}, ""

    monkeypatch.setattr(monitor, "latest_prevalence", fake)
    assert await judges.latest_prevalence(M1) == ({"period": "2026-W41", "corrected": 0.17}, "")
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `AttributeError: … has no attribute '_batch_rows'`.

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- reads (§4.4, §7.3) ---------------------------------------------------------------------------------

def _decode(row: Mapping[str, Any]) -> dict:
    out = dict(row)
    for key in ("judges", "checks", "plan", "results"):
        if key in out:
            out[key] = _json(out[key])
    return out


async def _batch_rows(org: str | None, limit: int, before: datetime | None) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT * FROM plenum_tracelab.monitor_batches
 WHERE (CAST(:org AS uuid) IS NULL OR organization_id = CAST(:org AS uuid))
   AND (CAST(:before AS timestamptz) IS NULL OR started_at < CAST(:before AS timestamptz))
 ORDER BY started_at DESC LIMIT :limit"""), {"org": org, "before": before, "limit": int(limit)})).mappings().all()
    return [_decode(r) for r in rows]


async def _scope_batches(org: str | None) -> list[dict]:
    """The latest batches of exactly this scope (the platform when org is None), for the Due marker."""
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT * FROM plenum_tracelab.monitor_batches
 WHERE organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)
 ORDER BY started_at DESC LIMIT 20"""), {"org": org})).mappings().all()
    return [_decode(r) for r in rows]


async def list_batches(*, org: str | None, limit: int = 20, before: datetime | None = None,
                       now: datetime | None = None) -> dict:
    """R19: a company reads its own batches; the platform owner (org None) reads every batch."""
    rows = await _settle(await _batch_rows(org, limit, before))
    scope_rows = await _settle(await _scope_batches(org))
    return {"batches": [batch_public(r) for r in rows],
            "due": due_marker(scope_rows, now=now or datetime.now(timezone.utc))}


async def _flagged_rows(evaluator_keys: Sequence[str], turn_ids: Sequence[str], *, org: str | None, offset: int,
                        limit: int) -> list[dict]:
    if not evaluator_keys or not turn_ids:
        return []
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT v.turn_id, v.evaluator_key, v.detail ->> 'critique' AS critique, v.organization_id,
       COALESCE(m.asker_question, t.question) AS question, t.started_at
  FROM plenum_tracelab.monitor_verdicts v
  JOIN plenum_cafm.agent_trace_turns t ON t.turn_id = v.turn_id
  LEFT JOIN plenum_tracelab.turn_meta m ON m.turn_id = v.turn_id
 WHERE v.evaluator_key = ANY(CAST(:eks AS text[])) AND v.turn_id = ANY(CAST(:ids AS text[]))
   AND v.status = 'decided' AND v.failure_present = 1
   AND (CAST(:org AS uuid) IS NULL OR v.organization_id = CAST(:org AS uuid))
 ORDER BY t.started_at, v.turn_id, v.evaluator_key
 OFFSET :offset LIMIT :limit"""), {"eks": list(evaluator_keys), "ids": list(turn_ids), "org": org,
                                   "offset": int(offset), "limit": int(limit)})).mappings().all()
    return [dict(r) for r in rows]


async def batch_detail(batch_id: str, *, org: str | None, mode_id: str | None = None, offset: int = 0,
                       limit: int = 50) -> dict:
    row = await _load_batch(batch_id)
    if row is None or (org is not None and str(row.get("organization_id") or "") != str(org)):
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    row = (await _settle([row]))[0]
    plan = _json(row.get("plan")) or {}
    snaps = [s for s in (_json(row.get("judges")) or []) if mode_id is None or str(s["mode_id"]) == str(mode_id)]
    in_random = set(plan.get("random") or [])
    group_of: dict[str, str] = {}
    for name, ids in (plan.get("risk_groups") or {}).items():
        for tid in ids:
            group_of.setdefault(tid, name)
    names = {s["evaluator_key"]: s.get("mode_name") for s in snaps}
    found = await _flagged_rows([s["evaluator_key"] for s in snaps], list(plan.get("to_judge") or []), org=org,
                                offset=offset, limit=limit)
    flagged = [{"turn_id": f["turn_id"], "question": (f.get("question") or "")[:300],
                "mode_name": names.get(f["evaluator_key"]), "evaluator_key": f["evaluator_key"],
                "critique": (f.get("critique") or "")[:600],
                "group": "uniform sample" if f["turn_id"] in in_random
                else RISK_LABELS.get(group_of.get(f["turn_id"], ""), "risk group")} for f in found]
    return {"batch": batch_public(row), "flagged": public(flagged)}


async def _mode(mode_id: str) -> dict | None:
    try:
        uuid.UUID(str(mode_id))
    except ValueError:
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("SELECT id, name FROM plenum_tracelab.modes WHERE id = CAST(:m AS uuid)"),
                                  {"m": str(mode_id)})).mappings().first()
    return {"id": str(row["id"]), "name": row["name"]} if row else None


async def _period_batches(org: str | None) -> list[dict]:
    """The latest done or capped batch per period for exactly this scope, oldest window first."""
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT * FROM (
    SELECT DISTINCT ON (period_label) id, period_label, window_start, window_end, results, started_at
      FROM plenum_tracelab.monitor_batches
     WHERE status IN ('done', 'capped') AND organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)
     ORDER BY period_label, started_at DESC) latest
 ORDER BY window_start"""), {"org": org})).mappings().all()
    return [_decode(r) for r in rows]


async def chart(*, org: str | None, mode_id: str, periods: int = 12) -> dict:
    mode = await _mode(mode_id)
    if mode is None:
        raise WatchError(404, "not_found", "That failure mode was not found.")
    entries = []
    for r in await _period_batches(org):
        found = next((m for m in ((r.get("results") or {}).get("modes") or []) if str(m.get("mode_id")) == str(mode_id)), None)
        if found:
            entries.append({**found, "period_label": r["period_label"], "window_start": r["window_start"],
                            "window_end": r["window_end"], "batch_id": r["id"]})
    return public({"mode": mode, **chart_points(entries, periods=periods)})


async def _check_week_rows(org: str | None, since: datetime) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT v.evaluator_key, date_trunc('week', v.turn_started_at AT TIME ZONE 'UTC') AS week_start,
       count(*) AS n, count(*) FILTER (WHERE v.status = 'decided') AS decided,
       count(*) FILTER (WHERE v.status = 'decided' AND v.failure_present = 1) AS flagged
  FROM plenum_tracelab.monitor_verdicts v
 WHERE v.evaluator_key LIKE 'code:%' AND v.turn_started_at >= :since
   AND (CAST(:org AS uuid) IS NULL OR v.organization_id = CAST(:org AS uuid))
 GROUP BY 1, 2"""), {"org": org, "since": since})).mappings().all()
    return [dict(r) for r in rows]


async def code_check_trend(*, org: str | None, periods: int = 12, now: datetime | None = None) -> dict:
    """Weekly raw rates per live check, by the ISO week of the answer (spec §4.4). Unvalidated checks are
    shown as raw rates only (J3); `validated` says which is which."""
    spans = weeks_back(now or datetime.now(timezone.utc), min(max(int(periods), 1), 52))
    rows = await _check_week_rows(org, spans[0][1])
    validated = await _validated_check_keys()
    by_key = {(r["evaluator_key"], stats.iso_week_label(_utc(r["week_start"]))): r for r in rows}
    out = []
    for c in checks.CHECKS.values():
        if c.scope != "live":
            continue
        weeks = []
        for label, _, _ in spans:
            r = by_key.get((c.evaluator_key, label))
            decided, flagged = (int(r["decided"]), int(r["flagged"])) if r else (0, 0)
            weeks.append({"period_label": label, "n": int(r["n"]) if r else 0, "flagged": flagged,
                          "rate": round(flagged / decided, 4) if decided else None})
        out.append({"evaluator_key": c.evaluator_key, "label": c.label, "validated": c.evaluator_key in validated,
                    "weeks": weeks})
    return {"checks": out}


async def latest_prevalence(mode_id: str) -> tuple[dict | None, str]:
    """What M5's failure report shows as a mode's prevalence (the seam judges.latest_prevalence calls this): the
    corrected prevalence from the latest done or capped platform-wide batch that estimated the mode. The report is
    the platform owner's, so company batches are not mixed in. (None, reason) says why there is no figure."""
    for batch in reversed(await _period_batches(None)):
        found = next((m for m in ((batch.get("results") or {}).get("modes") or [])
                      if str(m.get("mode_id")) == str(mode_id)), None)
        if found is None:
            continue
        if found.get("corrected") is None:
            return None, found.get("reason") or "The latest batch could not estimate this mode."
        return public({"period": batch["period_label"], "batch_id": batch["id"], "n_sample": found.get("n_sample"),
                       "raw": found.get("raw"), "corrected": found.get("corrected"), "ci_low": found.get("ci_low"),
                       "ci_high": found.get("ci_high"), "confidence": found.get("confidence"),
                       "validity_warning": found.get("validity_warning") or "", "threshold": found.get("threshold")}), ""
    return None, "Monitoring has not produced an estimate for this mode yet."
```

In `DA/src/trace_lab/judges.py` (M5), one exact-string replacement of the seam body:

old:
```python
async def latest_prevalence(mode_id: str) -> tuple[dict[str, Any] | None, str]:
    """The seam M6 fills with its latest monitor batch for the mode. M5 has no monitoring yet."""
    return None, "Monitoring has not run for this mode yet."
```
new:
```python
async def latest_prevalence(mode_id: str) -> tuple[dict[str, Any] | None, str]:
    """The corrected prevalence of a mode from M6's latest monitoring batch that estimated it."""
    from . import monitor    # lazy: monitor imports this module

    return await monitor.latest_prevalence(mode_id)
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → all pass (40).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/src/trace_lab/judges.py` (the seam body), `DA/tests/test_trace_lab_monitor.py`.

---

### Task 11: `monitor.py` — the "checked automatically vs by people" trend

**Files:**
- Modify: `DA/src/trace_lab/monitor.py` (append)
- Test: `DA/tests/test_trace_lab_monitor.py` (append)

**Interfaces:**
- Consumes: `stats.wilson_interval(successes, total)` (M2); `weeks_back` (Task 5); tables `monitor_verdicts`, `judges`, `judge_runs`, `golden_results` (M3 columns `is_final, outcome, graded_at, grader_validated, det, grade, turn_id`), `eval_case_runs.reps` (this plan), `annotations` (M2), `labels` (M4), `sample_items` / `sample_batches` (M2), `modes` (M4).
- Produces: `bucket_automation(*, weeks, auto, human, golden_acc, live_acc, modes, automated) -> list[week]` (spec §6 week shape); `automation_trend(*, org, weeks=12, now=None) -> {"weeks", "definitions"}`; `AUTOMATION_DEFINITIONS: dict[str, str]`; store `_automation_rows(org, start, end) -> dict`.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_monitor.py`)

```python
from src.trace_lab import stats  # noqa: E402


def test_automation_buckets_and_validated_sources_only():
    weeks = monitor.weeks_back(NOW, 2)                                # 2026-W41 (complete), 2026-W42 (this week)
    w41, w42 = datetime(2026, 10, 6, 10, tzinfo=UTC), datetime(2026, 10, 13, 10, tzinfo=UTC)
    auto = [{"turn_id": "a1", "at": w41, "source": "code_validated", "validated": True, "is_golden": False},
            {"turn_id": "a2", "at": w41, "source": "judge_frozen", "validated": True, "is_golden": False},
            {"turn_id": "g1", "at": w41, "source": "golden_auto", "validated": True, "is_golden": True},
            {"turn_id": "x1", "at": w41, "source": "code_validated", "validated": False, "is_golden": False},
            {"turn_id": "a3", "at": w42, "source": "code_validated", "validated": True, "is_golden": False}]
    human = [{"turn_id": "a2", "at": w41, "source": "open_coding", "is_golden": False},
             {"turn_id": "h1", "at": w41, "source": "labels", "is_golden": False},
             {"turn_id": "g2", "at": w41, "source": "golden_person", "is_golden": True}]
    out = monitor.bucket_automation(
        weeks=weeks, auto=auto, human=human,
        golden_acc=[{"at": w41, "correct": True}, {"at": w41, "correct": False}],
        live_acc=[{"at": w41, "right": True}],
        modes=[{"mode_id": "m1", "confirmed_at": w41 - timedelta(days=3), "retired_at": None},
               {"mode_id": "m2", "confirmed_at": w42, "retired_at": None}],
        automated=[{"mode_id": "m1", "since": w41}])
    w = out[0]
    assert w["week"] == "2026-W41"
    assert (w["checked_any"], w["auto_only"], w["people_only"], w["both"]) == (5, 2, 2, 1)
    assert w["auto_share"] == 0.4 and w["people_share"] == 0.6 and w["auto_share"] + w["people_share"] == 1
    assert w["auto_by_source"] == {"golden_auto": 1, "code_validated": 1, "judge_frozen": 1}   # x1 left out
    assert w["people_by_source"] == {"open_coding": 1, "labels": 1, "golden_person": 1}
    assert (w["answers_live"], w["answers_golden"]) == (3, 2)
    assert (w["modes_confirmed"], w["modes_automated"]) == (1, 1)
    assert w["golden_accuracy"]["decided"] == 2 and w["golden_accuracy"]["rate"] == 0.5
    lo, hi = stats.wilson_interval(1, 1)
    assert w["live_accuracy"] == {"reviewed": 1, "right": 1, "rate": 1.0, "low": lo, "high": hi}
    assert (out[1]["checked_any"], out[1]["auto_share"], out[1]["modes_confirmed"]) == (1, 1.0, 2)
    empty = monitor.bucket_automation(weeks=weeks[:1], auto=[], human=[], golden_acc=[], live_acc=[], modes=[],
                                      automated=[])[0]
    assert empty["auto_share"] is None and empty["people_share"] is None and empty["golden_accuracy"]["rate"] is None


async def test_automation_trend_reads_the_whole_span_once(monkeypatch):
    seen = {}

    async def rows(org, start, end):
        seen.update(org=org, start=start, end=end)
        return {"auto": [], "human": [], "golden_acc": [], "live_acc": [], "modes": [], "automated": []}

    monkeypatch.setattr(monitor, "_automation_rows", rows)
    out = await monitor.automation_trend(org=ORG_A, weeks=3, now=NOW)
    assert seen == {"org": ORG_A, "start": datetime(2026, 9, 28, tzinfo=UTC), "end": datetime(2026, 10, 19, tzinfo=UTC)}
    assert [w["week"] for w in out["weeks"]] == ["2026-W40", "2026-W41", "2026-W42"]
    assert set(out["definitions"]) >= {"auto_share", "people_share", "golden_accuracy", "live_accuracy"}
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor.py`
Expected: `AttributeError: module 'src.trace_lab.monitor' has no attribute 'bucket_automation'`.

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/monitor.py`)

```python
# ---- "less manual checking over time" (§6) -----------------------------------------------------------

AUTOMATION_DEFINITIONS = {
    "auto_share": "Answers checked with no person needed that week: a validated code check, a judge in use, or a "
                  "golden answer graded automatically, and no person.",
    "people_share": "Answers a person checked that week (review notes, labels, golden answers decided by a person), "
                    "with or without an automatic check as well.",
    "auto_by_source": "A validated code check covers every chat answer, so read the sources and the automated "
                      "failure modes beside the headline: full coverage is not full correctness checking.",
    "golden_accuracy": "Golden answers graded that week that matched the expected answer (degraded runs left out).",
    "live_accuracy": "Fair-sample share right (sample fraction): verdicts that week on answers from uniform review "
                     "batches only.",
    "modes_automated": "Confirmed failure modes with a judge in use at the end of the week.",
}


def bucket_automation(*, weeks: Sequence[tuple[str, datetime, datetime]], auto: Sequence[Mapping[str, Any]],
                      human: Sequence[Mapping[str, Any]], golden_acc: Sequence[Mapping[str, Any]],
                      live_acc: Sequence[Mapping[str, Any]], modes: Sequence[Mapping[str, Any]],
                      automated: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Per ISO week, by when the check was made: A = answers with a validated automatic verdict, H = answers a
    person decided. auto_share = |A minus H| / |A or H|; people_share = 1 - auto_share (None when nothing)."""
    out = []
    for label, ws, we in weeks:
        def inside(r: Mapping[str, Any]) -> bool:
            return r.get("at") is not None and ws <= _utc(r["at"]) < we

        a_rows = [r for r in auto if r.get("validated") and inside(r)]
        h_rows = [r for r in human if inside(r)]
        a_ids, h_ids = {r["turn_id"] for r in a_rows}, {r["turn_id"] for r in h_rows}
        checked = a_ids | h_ids
        golden_ids = {r["turn_id"] for r in a_rows + h_rows if r.get("is_golden")} & checked
        n = len(checked)
        auto_share = round(len(a_ids - h_ids) / n, 4) if n else None
        ga = [r for r in golden_acc if inside(r)]
        la = [r for r in live_acc if inside(r)]
        g_ok, l_ok = sum(1 for r in ga if r["correct"]), sum(1 for r in la if r["right"])
        g_lo, g_hi = stats.wilson_interval(g_ok, len(ga))
        l_lo, l_hi = stats.wilson_interval(l_ok, len(la))
        confirmed = {str(m["mode_id"]) for m in modes if m.get("confirmed_at") and _utc(m["confirmed_at"]) < we
                     and not (m.get("retired_at") and _utc(m["retired_at"]) < we)}
        automated_ids = {str(m["mode_id"]) for m in automated if m.get("since") and _utc(m["since"]) < we} & confirmed
        out.append({
            "week": label, "week_start": ws.isoformat(), "answers_live": n - len(golden_ids),
            "answers_golden": len(golden_ids), "checked_any": n, "auto_only": len(a_ids - h_ids),
            "people_only": len(h_ids - a_ids), "both": len(a_ids & h_ids), "auto_share": auto_share,
            "people_share": round(1 - auto_share, 4) if auto_share is not None else None,
            "auto_by_source": {s: len({r["turn_id"] for r in a_rows if r["source"] == s})
                               for s in ("golden_auto", "code_validated", "judge_frozen")},
            "people_by_source": {s: len({r["turn_id"] for r in h_rows if r["source"] == s})
                                 for s in ("open_coding", "labels", "golden_person")},
            "modes_confirmed": len(confirmed), "modes_automated": len(automated_ids),
            "golden_accuracy": {"decided": len(ga), "correct": g_ok, "rate": round(g_ok / len(ga), 4) if ga else None,
                                "low": g_lo, "high": g_hi},
            "live_accuracy": {"reviewed": len(la), "right": l_ok, "rate": round(l_ok / len(la), 4) if la else None,
                              "low": l_lo, "high": l_hi}})
    return out


_ORG = "(CAST(:org AS uuid) IS NULL OR {alias}.organization_id = CAST(:org AS uuid))"
_IS_GOLDEN = "EXISTS (SELECT 1 FROM plenum_tracelab.golden_results g WHERE g.turn_id = {alias}.turn_id)"

_AUTO_SQL = [f"""
SELECT v.turn_id, v.updated_at AS at,
       CASE WHEN v.evaluator_key LIKE 'judge:%' THEN 'judge_frozen' ELSE 'code_validated' END AS source,
       (v.evaluator_key LIKE 'judge:%' OR EXISTS (
            SELECT 1 FROM plenum_tracelab.judges j
             WHERE j.kind = 'code' AND j.status = 'frozen' AND j.decision = 'use' AND j.frozen_at <= v.updated_at
               AND v.evaluator_key = 'code:' || j.check_key || '@v' || j.check_version)) AS validated,
       false AS is_golden
  FROM plenum_tracelab.monitor_verdicts v
 WHERE v.status = 'decided' AND v.updated_at >= :start AND v.updated_at < :end AND {_ORG.format(alias='v')}""",
             f"""
SELECT r.turn_id, r.graded_at AS at, 'golden_auto' AS source,
       (r.grader_validated IS TRUE OR r.det ->> 'status' IN ('all_found', 'mismatch')
        OR r.grade ->> 'source' = 'runner') AS validated,
       true AS is_golden
  FROM plenum_tracelab.golden_results r
 WHERE r.is_final AND r.outcome IN ('pass', 'fail') AND r.graded_at >= :start AND r.graded_at < :end
   AND {_ORG.format(alias='r')}""",
             f"""
SELECT rep ->> 'turn_id' AS turn_id, CAST(rep ->> 'graded_at' AS timestamptz) AS at, 'golden_auto' AS source,
       true AS validated, true AS is_golden
  FROM plenum_tracelab.eval_case_runs cr
  JOIN plenum_tracelab.eval_cases c ON c.id = cr.eval_case_id
 CROSS JOIN LATERAL jsonb_array_elements(cr.reps) AS rep
 WHERE rep ->> 'status' IN ('passed', 'failed') AND rep ->> 'graded_at' IS NOT NULL
   AND CAST(rep ->> 'graded_at' AS timestamptz) >= :start AND CAST(rep ->> 'graded_at' AS timestamptz) < :end
   AND {_ORG.format(alias='c')}"""]

_HUMAN_SQL = [f"""
SELECT a.turn_id, a.created_at AS at,
       CASE WHEN {_IS_GOLDEN.format(alias='a')} THEN 'golden_person' ELSE 'open_coding' END AS source,
       {_IS_GOLDEN.format(alias='a')} AS is_golden
  FROM plenum_tracelab.annotations a
 WHERE a.kind = 'verdict' AND NOT a.tombstone AND a.created_at >= :start AND a.created_at < :end
   AND {_ORG.format(alias='a')}""",
              f"""
SELECT l.turn_id, l.created_at AS at, 'labels' AS source, {_IS_GOLDEN.format(alias='l')} AS is_golden
  FROM plenum_tracelab.labels l
 WHERE l.approval = 'single' AND NOT l.tombstone AND l.created_at >= :start AND l.created_at < :end
   AND {_ORG.format(alias='l')}"""]

_GOLDEN_ACC_SQL = f"""
SELECT r.graded_at AS at, (r.outcome = 'pass') AS correct
  FROM plenum_tracelab.golden_results r
 WHERE r.is_final AND r.outcome IN ('pass', 'fail') AND r.graded_at >= :start AND r.graded_at < :end
   AND {_ORG.format(alias='r')}"""

_LIVE_ACC_SQL = f"""
SELECT DISTINCT ON (a.turn_id, a.reviewer_id, date_trunc('week', a.created_at AT TIME ZONE 'UTC'))
       a.created_at AS at, (a.verdict = 'right') AS is_right
  FROM plenum_tracelab.annotations a
  JOIN plenum_tracelab.sample_items si ON si.turn_id = a.turn_id AND si.organization_id = a.organization_id
  JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id AND b.strategy = 'uniform' AND b.withdrawn_at IS NULL
 WHERE a.kind = 'verdict' AND NOT a.tombstone AND a.created_at >= :start AND a.created_at < :end
   AND {_ORG.format(alias='a')}
 ORDER BY a.turn_id, a.reviewer_id, date_trunc('week', a.created_at AT TIME ZONE 'UTC'), a.created_at DESC"""

_MODES_SQL = "SELECT id AS mode_id, confirmed_at, retired_at FROM plenum_tracelab.modes WHERE confirmed_at IS NOT NULL"

_AUTOMATED_SQL = """
SELECT j.mode_id, min(r.finished_at) AS since
  FROM plenum_tracelab.judges j
  JOIN plenum_tracelab.judge_runs r ON r.judge_id = j.id AND r.split = 'test' AND r.status = 'done'
 WHERE j.status = 'frozen' AND j.decision = 'use' AND r.finished_at IS NOT NULL
 GROUP BY j.mode_id"""


async def _automation_rows(org: str | None, start: datetime, end: datetime) -> dict:
    p = {"org": org, "start": start, "end": end}
    async with db.engine().connect() as conn:
        async def rows(sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
            return [dict(r) for r in (await conn.execute(text(sql), dict(params or {}))).mappings().all()]

        auto = [r for sql in _AUTO_SQL for r in await rows(sql, p)]
        human = [r for sql in _HUMAN_SQL for r in await rows(sql, p)]
        golden_acc = await rows(_GOLDEN_ACC_SQL, p)
        live_acc = [{"at": r["at"], "right": r["is_right"]} for r in await rows(_LIVE_ACC_SQL, p)]
        modes = await rows(_MODES_SQL)
        automated = await rows(_AUTOMATED_SQL)
    return {"auto": auto, "human": human, "golden_acc": golden_acc, "live_acc": live_acc, "modes": modes,
            "automated": automated}


async def automation_trend(*, org: str | None, weeks: int = 12, now: datetime | None = None) -> dict:
    spans = weeks_back(now or datetime.now(timezone.utc), min(max(int(weeks), 1), 52))
    rows = await _automation_rows(org, spans[0][1], spans[-1][2])
    return {"weeks": public(bucket_automation(weeks=spans, **rows)), "definitions": AUTOMATION_DEFINITIONS}
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor.py` → all pass (42).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/monitor.py`, `DA/tests/test_trace_lab_monitor.py`.

---

### Task 12: `eval_cases.py` — case shape, rep verdicts, baselines, decisions, repeat summary, report text (pure)

**Files:**
- Create: `DA/src/trace_lab/eval_cases.py`
- Test: `DA/tests/test_trace_lab_eval_cases.py`

**Interfaces:**
- Consumes: `stats.pass_at_k`, `stats.pass_hat_k` (M3); `stats.case_decision`, `stats.summarize_rollouts` (Task 4); `checks.CHECKS` (M5); `golden.parse_expected_values`, `golden.REGRESSION_SET_NAME` (M3); `runner.HARD_MAX_K` (M3); `skill_lab.WRITE_INTENT` (`DA/src/agents/skill_lab.py:97-99`); `monitor.WatchError`, `_field`, `_json`, `public`, `judges_in_use` (Tasks 5, 7).
- Produces: constants `REGRESSION_SET_NAME` (M3's `golden.REGRESSION_SET_NAME`, re-exported, never redefined), `BASELINE_K = 5`, `REPEAT_DEFAULT_N = 10`, `REPEAT_MIN_N = 2`, `REPEAT_MAX_N = runner.HARD_MAX_K` (20), `RELEASE_DEFAULT_K = 5`, `RELEASE_MAX_K = 10`, `MAX_FOLLOWUPS = 4`, `LIVE_STATUSES`, `REPLAY_GUIDANCE`; `eval_grading_usd_cap()`; `new_case_key()`; `twin_key(case_key, version)`; `replayable(question, channel, session_id) -> bool`; `case_from_turn(turn, meta) -> input`; `merge_input(base, override) -> input`; `normalise_expected(expected, *, modes_in_use) -> expected`; `parse_values(text) -> list[dict]`; `rep_verdict(*, rep, turn_id, outcome, status, check_results, judge_results, golden_expected, usd=0.0, graded_at=None, steps=None) -> dict` (status `passed|failed|degraded|ungraded|pending|missing`); `classify_baseline(statuses) -> {"kind", "baseline_pass_rate"} | None`; `case_run_summary(reps, *, k, purpose, kind, baseline_pass_rate) -> dict`; `release_decision(regression, *, k) -> (decision, reason)`; `rollout_records(reps)`; `repeat_summary(reps) -> dict`; `next_version(old, *, input_override=None, initial_state=None, expected=None) -> dict`; `case_public(row) -> dict`; `render_release_md(report, *, company) -> str`.

- [ ] **Step 1: Write the failing test**

```python
"""Eval cases (M6, spec Judges... section 5): case shape, rep verdicts, baselines, release decision, report.
15 Oct 2026. No database, no model, no golden run: store functions and START_GOLDEN_RUN are replaced."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.trace_lab import eval_cases
from src.trace_lab.monitor import WatchError

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ACTOR = "cccccccc-0000-4000-8000-000000000003"
M1 = "22222222-2222-4222-8222-222222222222"


def _rv(**kw):
    base = dict(rep=1, turn_id="turn-1", outcome="pass", status="completed", check_results=[], judge_results=[],
                golden_expected=True)
    base.update(kw)
    return eval_cases.rep_verdict(**base)


def _reps(*statuses):
    return [{"rep": i, "status": s, "failures": ["check:expected_values"] if s == "failed" else []}
            for i, s in enumerate(statuses, start=1)]


def _summary(statuses, *, k=5, purpose="release_check", kind="regression", rate=None):
    return eval_cases.case_run_summary(_reps(*statuses), k=k, purpose=purpose, kind=kind, baseline_pass_rate=rate)


def test_rep_verdict_precedence():
    fail_check = [{"key": "expected_values", "status": "fail"}]
    na_check = [{"key": "invented_identifiers", "status": "not_applicable"}]
    judge_fail = [{"mode": "wrong_period", "status": "decided", "failure_present": 1}]
    judge_error = [{"mode": "wrong_period", "status": "error", "failure_present": None}]
    judge_wait = [{"mode": "wrong_period", "status": "ungraded", "failure_present": None}]
    assert _rv()["status"] == "passed"
    assert _rv(turn_id=None, outcome=None, status=None)["status"] == "missing"
    assert _rv(outcome="degraded")["status"] == "degraded"
    assert _rv(outcome="infra_failed")["status"] == "degraded"
    v = _rv(check_results=fail_check + na_check, judge_results=judge_fail, outcome="fail")
    assert v["status"] == "failed"                                     # a failure wins over a degraded part
    assert v["failures"] == ["check:expected_values", "judge:wrong_period", "golden:expected_answer"]
    assert _rv(check_results=na_check, outcome="needs_person")["status"] == "degraded"
    assert _rv(judge_results=judge_error)["status"] == "degraded"
    assert _rv(judge_results=judge_wait, outcome="unsure")["status"] == "ungraded"
    assert _rv(outcome="needs_person")["status"] == "pending"
    assert _rv(outcome="fail", golden_expected=False)["status"] == "passed"   # the golden grade is not used


def test_baseline_5_of_5_regression():
    s = _summary(["passed"] * 5, purpose="baseline", kind="unclassified")
    assert s["classification"] == {"kind": "regression", "baseline_pass_rate": None}
    assert s["decision"] == "pass" and s["pass_hat_k"] == 1.0 and s["pass_at_1"] == 1.0


def test_baseline_3_of_5_capability_0_6():
    s = _summary(["passed", "failed", "passed", "failed", "passed"], purpose="baseline", kind="unclassified")
    assert s["classification"] == {"kind": "capability", "baseline_pass_rate": 0.6}
    assert (s["pass_at_1"], s["pass_at_3"], s["pass_at_5"], s["pass_hat_k"]) == (0.6, 1.0, 1.0, 0.0)


def test_baseline_with_degraded_rep_not_classified():
    s = _summary(["passed", "passed", "degraded", "passed", "passed"], purpose="baseline", kind="unclassified")
    assert s["classification"] is None and s["decision"] == "incomplete" and s["reason"] == "Re-run the missing reps"
    s = _summary(["passed"] * 4, purpose="baseline", kind="unclassified")          # one run never happened
    assert s["classification"] is None and s["missing"] == 1


def test_regression_blocks_on_any_failed_rep():
    s = _summary(["passed", "passed", "failed", "passed", "passed"])
    assert s["decision"] == "block" and "failed 1 of 5" in s["reason"]


def test_capability_never_blocks():
    s = _summary(["failed"] * 5, kind="capability", rate=0.4)
    assert s["decision"] == "pass" and s["pass_at_1"] == 0.0


def test_degraded_regression_incomplete():
    assert _summary(["passed", "passed", "passed", "passed", "degraded"])["decision"] == "incomplete"
    s = _summary(["passed", "passed", "passed", "passed", "ungraded"])
    assert s["decision"] == "incomplete" and s["reason"].startswith("1 ungraded")
    assert _summary(["passed"] * 3)["decision"] == "incomplete"                     # n < planned


def test_release_decision_rules():
    p, b, i = {"decision": "pass"}, {"decision": "block"}, {"decision": "incomplete"}
    assert eval_cases.release_decision([], k=5)[0] == "incomplete"                  # nothing can be certified
    assert eval_cases.release_decision([p, i, b], k=5)[0] == "block"
    assert eval_cases.release_decision([p, i], k=5)[0] == "incomplete"
    assert eval_cases.release_decision([p, p], k=5) == ("pass", "all 2 regression cases passed 5 of 5 runs")


REPORT = {
    "golden_run_id": "9b2f0c1e-0000-4000-8000-00000000abcd", "run_key": "run-20261015T101500Z-9b2f0c1e",
    "organization_id": ORG_A, "decision": "block", "reason": "1 of 2 regression cases failed at least one run",
    "k": 5, "started_at": "2026-10-15T10:15:00+00:00", "finished_at": "2026-10-15T11:02:00+00:00", "usd": 4.8,
    "fingerprint": {"image": "f5-1a2b3c4", "openai_model": "gpt-5.6-terra", "grader_model": "claude-sonnet-5"},
    "graders": {"checks": ["code:invented_identifiers@v1"],
                "judges": [{"mode": "wrong_period", "judge_key": "wrong_period-v2", "prompt_hash": "f85bce23a878"}]},
    "regression": [{"case_key": "e-7f3a19c2", "version": 1, "mode": "wrong_period", "passes": 5, "n": 5,
                    "not_decided": 0, "decision": "pass", "reason": "regression case passed 5 of 5 runs",
                    "turn_ids": [], "judge_changed_since_baseline": False},
                   {"case_key": "e-0b1c2d3e", "version": 2, "mode": "invented_code", "passes": 4, "n": 5,
                    "not_decided": 0, "decision": "block", "reason": "regression case failed 1 of 5 runs",
                    "turn_ids": [], "judge_changed_since_baseline": True}],
    "capability": [{"case_key": "e-11223344", "version": 1, "mode": "wrong_period", "passes": 2, "n": 5,
                    "pass_at_1": 0.4, "pass_at_3": 0.9, "pass_at_5": 1.0, "pass_hat_5": 0.0,
                    "baseline_pass_rate": 0.4, "decision": "pass", "reason": "not blocking"}],
    "runs_without_verdict": [{"case_key": "e-11223344", "rep": 3, "status": "degraded",
                              "reason": "a step was refused or needed approval"}],
    "changes_since_last": [{"case_key": "e-0b1c2d3e", "before": "pass", "after": "block"}]}


def test_release_report_md_has_decision_cases_fingerprint_and_no_internal_names():
    md = eval_cases.render_release_md(REPORT, company="Northbridge Estates Ltd")
    assert md.splitlines()[0] == "# Release check — Northbridge Estates Ltd — run-20261015T101500Z-9b2f0c1e"
    assert "**Decision: BLOCK** — 1 of 2 regression cases failed at least one run" in md
    for part in ("## Run", "## Configuration", "## Regression cases", "## Capability cases",
                 "## Runs without a verdict", "## Changes since the last check"):
        assert part in md
    assert md.index("## Regression cases") < md.index("## Capability cases") < md.index("## Changes since the last check")
    assert "| e-0b1c2d3e v2 | invented_code | 4 of 5 | block | regression case failed 1 of 5 runs |" in md
    assert "| e-11223344 v1 | wrong_period | 0.40 | 0.90 | 1.00 | 0.00 | 0.40 |" in md
    assert "f5-1a2b3c4" in md and "gpt-5.6-terra" in md and "wrong_period-v2" in md
    assert "- e-11223344 run 3: degraded (a step was refused or needed approval)" in md
    assert "- e-0b1c2d3e: pass → block" in md
    assert "?tl=runs&run=9b2f0c1e-0000-4000-8000-00000000abcd" in md
    for internal in ("svc-", "plenum_", "deepagents", "deep-agents", "UDR", "judge:", "code:", "@v1", "golden:"):
        assert internal not in md, internal


def test_repeat_summary_uses_replay_bootstrap(monkeypatch):
    seen = {}

    def fake(records, **kw):
        seen.update(records=records, kw=kw)
        return {"failure_rate": 0.5, "ci_low": 0.0, "ci_high": 1.0, "mode_counts": {"judge:wrong_period": 1},
                "steps": {"min": 2, "median": 3, "max": 4}, "wilson_high": 0.9}

    monkeypatch.setattr(eval_cases.stats, "summarize_rollouts", fake)
    reps = [{"rep": 1, "status": "passed", "failures": [], "steps": 2},
            {"rep": 2, "status": "failed", "failures": ["judge:wrong_period"], "steps": 4},
            {"rep": 3, "status": "degraded", "failures": [], "steps": 3}]
    out = eval_cases.repeat_summary(reps)
    assert seen["records"] == [{"passed": True, "failures": [], "steps": 2},
                               {"passed": False, "failures": ["judge:wrong_period"], "steps": 4}]
    assert seen["kw"] == {}                                           # the replay defaults: 2,000 resamples, seed 7
    assert (out["n"], out["excluded"], out["failure_rate"]) == (2, 1, 0.5)
    assert out["guidance"] == eval_cases.REPLAY_GUIDANCE
    assert eval_cases.repeat_summary([{"rep": 1, "status": "degraded"}])["failure_rate"] is None


def test_case_from_turn_copies_context_and_scope_never_answer():
    turn = SimpleNamespace(question="And in August?", answer="There were 786 work orders.", prior=[
        {"turn_id": "turn-a", "question": "How many work orders were raised at Bishopsgate Tower in July 2026?",
         "answer": "There were 786."}])
    meta = {"context_text": "Page: Maintenance", "asker_role": "admin",
            "asker_scope": json.dumps({"building_ids": None, "acting": False})}
    out = eval_cases.case_from_turn(turn, meta)
    assert out == {"question": "How many work orders were raised at Bishopsgate Tower in July 2026?",
                   "followups": ["And in August?"], "page_context": "Page: Maintenance",
                   "scope": {"asker_role": "admin", "building_ids": None}}
    assert "786" not in json.dumps(out)
    long = SimpleNamespace(question="q6", prior=[{"question": f"q{i}"} for i in range(1, 6)])
    assert eval_cases.case_from_turn(long, None)["question"] == "q2"
    assert eval_cases.case_from_turn(long, None)["followups"] == ["q3", "q4", "q5", "q6"]


def test_judges_in_expected_must_be_in_use():
    in_use = {M1: {"mode_id": M1}}
    out = eval_cases.normalise_expected(
        {"checks": [{"check": "expected_values", "values": [{"label": "July", "value": "786", "required": True}]}],
         "judges": [{"mode_id": M1}, {"mode_id": M1}]}, modes_in_use=in_use)
    assert out["judges"] == [{"mode_id": M1}] and out["checks"][0]["check"] == "expected_values"
    assert out["golden_expected"] is True
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"judges": [{"mode_id": "other"}]}, modes_in_use=in_use)
    assert e.value.status == 409 and e.value.reason == "judge_not_frozen"
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"checks": [{"check": "nope"}]}, modes_in_use=in_use)
    assert e.value.status == 422
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"golden_expected": False}, modes_in_use=in_use)
    assert e.value.reason == "nothing_decides"


def test_edit_creates_version_and_resets_kind():
    old = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "active",
           "kind": "regression", "baseline_pass_rate": None, "baseline_run_id": "r1", "mode_id": M1,
           "source_type": "live_turn", "source_turn_id": "turn-1", "source_batch_id": None,
           "source_golden_case_id": None, "golden_case_id": "g1",
           "input": json.dumps({"question": "How many?", "followups": [], "page_context": "", "scope": {}}),
           "initial_state": {}, "expected": {"assertions": [], "checks": [], "judges": [], "golden_expected": True},
           "rev": 3}
    new = eval_cases.next_version(old, input_override={"question": "How many work orders in July 2026?"})
    assert (new["case_key"], new["version"], new["status"]) == ("e-7f3a19c2", 2, "draft")
    assert new["kind"] == "unclassified" and new["baseline_pass_rate"] is None and new["baseline_run_id"] is None
    assert new["supersedes"] == "c1" and new["golden_case_id"] is None and new["rev"] == 1 and new["id"] != "c1"
    assert new["input"]["question"] == "How many work orders in July 2026?"
    assert eval_cases.twin_key(new["case_key"], new["version"]) == "e-7f3a19c2.v2"


def test_only_chat_answers_that_ask_for_no_change_replay():
    assert eval_cases.replayable("How many work orders in July?", "chat", "s1")
    assert not eval_cases.replayable("Raise a work order for the boiler", "chat", "s1")
    assert not eval_cases.replayable("How many work orders in July?", "upload", "s1")
    assert not eval_cases.replayable("How many work orders in July?", "chat", "golden-1a2b3c4d-21-r1")
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_eval_cases.py`
Expected: `ImportError: cannot import name 'eval_cases' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

Create `DA/src/trace_lab/eval_cases.py` (later tasks append to it; all imports are here once):

```python
"""Trace Lab eval cases (M6; spec "Judges, code checks, monitoring, eval cases and release check" §5).

A confirmed failure, a monitoring flag or a golden case becomes an eval case. Every case runs through a golden
case in the company's "Regression cases" golden set (R9), on M3's browser-stepped golden runner (J8): a 5-run
baseline classifies it (5 of 5 = regression, else capability), a release check runs every active case k times,
and repeat-n measures one answer's failure rate. Only automatic, validated evidence decides a rep.
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..agents.skill_lab import WRITE_INTENT
from . import checks, db, golden, jobs, judges, monitor, provenance, runner, stats
from .monitor import WatchError, _field, _json, judges_in_use, public

log = structlog.get_logger(__name__)

REGRESSION_SET_NAME = golden.REGRESSION_SET_NAME      # M3 owns the company's one "Regression cases" set
REGRESSION_SET_MAX = 2000
BASELINE_K = 5
REPEAT_DEFAULT_N = 10
REPEAT_MIN_N = 2
REPEAT_MAX_N = runner.HARD_MAX_K                      # 20; runner.max_k() may lower it per deployment
RELEASE_DEFAULT_K = 5
RELEASE_MAX_K = 10
MAX_FOLLOWUPS = 4
LIVE_STATUSES = ("proposed", "draft", "baselining", "active")
DECIDED_REPS = ("passed", "failed")
REPLAY_GUIDANCE = ("A high, consistent failure rate is a regression to fix and block in CI. "
                   "A rare failure should become an evaluation case.")


def eval_grading_usd_cap() -> float:
    return float(os.getenv("TRACE_LAB_EVAL_GRADING_USD_CAP", "2"))


def new_case_key() -> str:
    return "e-" + secrets.token_hex(4)


def twin_key(case_key: str, version: int) -> str:
    """The golden case key of one case version's twin (golden_cases is unique per set and key)."""
    return f"{case_key}.v{int(version)}"


def replayable(question: str | None, channel: str | None, session_id: str | None) -> bool:
    """Only chat answers whose question asks for no change can be replayed (spec §5.7)."""
    return (channel == "chat" and not (session_id or "").startswith("golden-")
            and not WRITE_INTENT.search(question or ""))


def case_from_turn(turn: Any, meta: Mapping[str, Any] | None) -> dict:
    """The case input from an answer (spec §5.1). A follow-up in an observed conversation starts at the first
    question, with the later questions up to this one as follow-ups (at most MAX_FOLLOWUPS). Page context and
    scope come from turn_meta. The answer is never copied."""
    asked = [str(_field(p, "question") or "").strip() for p in (_field(turn, "prior") or [])]
    asked.append(str(_field(turn, "question") or "").strip())
    asked = [q for q in asked if q][-(MAX_FOLLOWUPS + 1):]
    m = dict(meta or {})
    scope = _json(m.get("asker_scope")) or {}
    return {"question": asked[0] if asked else "", "followups": asked[1:], "page_context": m.get("context_text") or "",
            "scope": {"asker_role": m.get("asker_role"), "building_ids": scope.get("building_ids")}}


def merge_input(base: Mapping[str, Any], override: Mapping[str, Any] | None) -> dict:
    out = {"question": str(base.get("question") or ""), "followups": list(base.get("followups") or []),
           "page_context": base.get("page_context"), "scope": dict(base.get("scope") or {})}
    o = dict(override or {})
    if "question" in o:
        out["question"] = str(o["question"] or "").strip()
    if "followups" in o:
        out["followups"] = [str(f).strip() for f in (o["followups"] or []) if str(f).strip()]
    if "page_context" in o:
        out["page_context"] = None if o["page_context"] is None else str(o["page_context"])
    if not 1 <= len(out["question"]) <= 2000:
        raise WatchError(422, "bad_input", "The question must be 1 to 2,000 characters.")
    if len(out["followups"]) > MAX_FOLLOWUPS or any(len(f) > 2000 for f in out["followups"]):
        raise WatchError(422, "bad_input", f"At most {MAX_FOLLOWUPS} follow-ups, each up to 2,000 characters.")
    if out["page_context"] is not None and len(out["page_context"]) > 4000:
        raise WatchError(422, "bad_input", "The page context can be up to 4,000 characters.")
    return out


def normalise_expected(expected: Mapping[str, Any] | None, *, modes_in_use: Mapping[str, Any]) -> dict:
    """Spec §5.2: assertions are prose and decide nothing; checks are keys of checks.CHECKS; judges name modes
    that have a judge in use ("never a new judge in CI")."""
    e = dict(expected or {})
    out_checks = []
    for c in e.get("checks") or []:
        key = str((c or {}).get("check") or "")
        if key not in checks.CHECKS:
            raise WatchError(422, "unknown_check", f"There is no code check called {key or '(blank)'}.")
        out_checks.append({**{k: v for k, v in dict(c).items() if k != "check"}, "check": key})
    out_judges: list[dict] = []
    for jd in e.get("judges") or []:
        mode_id = str((jd or {}).get("mode_id") or "")
        if mode_id not in modes_in_use:
            raise WatchError(409, "judge_not_frozen",
                             "That failure mode has no judge in use yet, so it cannot decide a case.")
        if all(j["mode_id"] != mode_id for j in out_judges):
            out_judges.append({"mode_id": mode_id})
    golden_expected = bool(e.get("golden_expected", True))
    if not out_checks and not out_judges and not golden_expected:
        raise WatchError(422, "nothing_decides",
                         "Name a code check, a judged failure mode, or grade against the expected answer.")
    return {"assertions": [str(a)[:500] for a in (e.get("assertions") or [])][:20], "checks": out_checks,
            "judges": out_judges, "golden_expected": golden_expected}


def parse_values(cell: str | None) -> list[dict]:
    """The golden Expected values grammar (M3 golden.parse_expected_values)."""
    if not (cell or "").strip():
        return []
    try:
        return list(golden.parse_expected_values(cell))
    except ValueError as exc:
        raise WatchError(422, "bad_expected_values", f"Could not read this expected value: {exc}") from None


def rep_verdict(*, rep: int, turn_id: str | None, outcome: str | None, status: str | None,
                check_results: Sequence[Mapping[str, Any]], judge_results: Sequence[Mapping[str, Any]],
                golden_expected: bool, usd: float = 0.0, graded_at: str | None = None,
                steps: int | None = None) -> dict:
    """Spec §5.3, in order. Not run yet -> missing. No answer, or a degraded / infra_failed M3 result -> degraded.
    A failing check, a Fail from a judge in use, or (with golden_expected) an automatic 'fail' grade -> failed,
    which wins over everything; then degraded, then ungraded (a judge call not made yet), then pending (the
    golden grade needs a person), then passed."""
    base = {"rep": rep, "turn_id": turn_id, "failures": [], "degraded_reason": None,
            "checks": [dict(c) for c in check_results], "judges": [dict(j) for j in judge_results],
            "golden": outcome, "usd": round(float(usd or 0), 6), "graded_at": graded_at, "steps": steps}
    if outcome is None and status in (None, "running", "infra"):
        return {**base, "status": "missing"}
    if turn_id is None:
        return {**base, "status": "degraded", "degraded_reason": "no answer was recorded"}
    if outcome in ("degraded", "infra_failed"):
        reason = ("a step was refused or needed approval" if outcome == "degraded"
                  else "the service failed three times in a row")
        return {**base, "status": "degraded", "degraded_reason": reason}
    failures: list[str] = []
    degraded: str | None = None
    ungraded = pending = False
    for c in check_results:
        if c["status"] == "fail":
            failures.append(f"check:{c['key']}")
        elif c["status"] in ("undecidable", "not_applicable"):
            degraded = degraded or f"the check {c['key']} could not decide"
    for j in judge_results:
        if j["status"] == "decided" and j.get("failure_present") == 1:
            failures.append(f"judge:{j['mode']}")
        elif j["status"] == "error":
            degraded = degraded or f"the judge for {j['mode']} returned no verdict"
        elif j["status"] == "ungraded":
            ungraded = True
    if golden_expected:
        if outcome == "fail":
            failures.append("golden:expected_answer")
        elif outcome != "pass":
            pending = True
    if failures:
        state = "failed"
    elif degraded:
        state = "degraded"
    elif ungraded:
        state = "ungraded"
    elif pending:
        state = "pending"
    else:
        state = "passed"
    return {**base, "status": state, "failures": failures, "degraded_reason": degraded if state == "degraded" else None}


def classify_baseline(statuses: Sequence[str]) -> dict | None:
    """Spec §5.4: classify only with 5 decided reps."""
    if len(statuses) != BASELINE_K or any(s not in DECIDED_REPS for s in statuses):
        return None
    passes = list(statuses).count("passed")
    if passes == BASELINE_K:
        return {"kind": "regression", "baseline_pass_rate": None}
    return {"kind": "capability", "baseline_pass_rate": round(passes / BASELINE_K, 2)}


def case_run_summary(reps: Sequence[Mapping[str, Any]], *, k: int, purpose: str, kind: str,
                     baseline_pass_rate: float | None) -> dict:
    by_rep = {int(r["rep"]): r for r in reps}
    statuses = [by_rep.get(i, {"status": "missing"})["status"] for i in range(1, int(k) + 1)]
    passes, failed = statuses.count("passed"), statuses.count("failed")
    n = passes + failed
    ungraded = statuses.count("ungraded")
    degraded, pending, missing = statuses.count("degraded"), statuses.count("pending") + ungraded, statuses.count("missing")
    out: dict[str, Any] = {
        "k": int(k), "n": n, "passes": passes, "degraded": degraded, "pending": pending, "missing": missing,
        **{f"pass_at_{x}": (round(stats.pass_at_k(n, passes, x), 4) if n >= x else None) for x in (1, 3, 5)},
        "pass_hat_k": round(stats.pass_hat_k(n, passes, int(k)), 4) if n >= int(k) else None,
        "classification": None}
    if purpose == "baseline":
        cls = classify_baseline(statuses)
        out["classification"] = cls
        if cls is None:
            decision, reason = "incomplete", "Re-run the missing reps"
        elif cls["kind"] == "regression":
            decision, reason = "pass", f"baseline: a regression case, passed {passes} of {k}"
        else:
            decision, reason = "pass", f"baseline: a capability case, passed {passes} of {k}"
    elif kind in ("regression", "capability"):
        d = stats.case_decision(kind, passes, n, baseline_pass_rate, planned=int(k),
                                not_decided=degraded + pending + missing)
        decision, reason = d["decision"], d["reason"]
    else:
        decision, reason = "incomplete", "the case has no baseline yet, so this run decides nothing"
    if decision == "incomplete" and ungraded:
        reason = f"{ungraded} ungraded reps; grade them to decide. {reason}"
    out.update(decision=decision, reason=reason)
    return out


def release_decision(regression: Sequence[Mapping[str, Any]], *, k: int) -> tuple[str, str]:
    """Spec §5.6. Capability cases never affect it."""
    if not regression:
        return "incomplete", "No regression cases yet: nothing can be certified."
    blocked = [r for r in regression if r["decision"] == "block"]
    if blocked:
        return "block", f"{len(blocked)} of {len(regression)} regression cases failed at least one run"
    unsure = [r for r in regression if r["decision"] != "pass"]
    if unsure:
        return "incomplete", (f"{len(unsure)} of {len(regression)} regression cases have runs without a verdict; "
                              "incomplete evidence never passes")
    return "pass", f"all {len(regression)} regression cases passed {k} of {k} runs"


def rollout_records(reps: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    records, excluded = [], 0
    for r in reps:
        if r.get("status") in DECIDED_REPS:
            records.append({"passed": r["status"] == "passed", "failures": list(r.get("failures") or []),
                            "steps": int(r.get("steps") or 0)})
        else:
            excluded += 1
    return records, excluded


def repeat_summary(reps: Sequence[Mapping[str, Any]]) -> dict:
    """Spec §5.7: failure rate with the replay bootstrap (2,000 resamples, seed 7); degraded reps left out."""
    records, excluded = rollout_records(reps)
    if not records:
        return {"n": 0, "excluded": excluded, "failure_rate": None, "ci_low": None, "ci_high": None,
                "mode_counts": {}, "steps": None, "wilson_high": None, "guidance": REPLAY_GUIDANCE}
    return {**stats.summarize_rollouts(records), "n": len(records), "excluded": excluded, "guidance": REPLAY_GUIDANCE}


_CASE_COPY = ("organization_id", "case_key", "mode_id", "source_type", "source_turn_id", "source_batch_id",
              "source_golden_case_id", "input", "initial_state", "expected")


def next_version(old: Mapping[str, Any], *, input_override: Mapping[str, Any] | None = None,
                 initial_state: Mapping[str, Any] | None = None, expected: Mapping[str, Any] | None = None) -> dict:
    """An edit is a new version with the same key, unclassified again (the repo never re-labels a case by hand)."""
    new = {k: (_json(old.get(k)) if k in ("input", "initial_state", "expected") else old.get(k)) for k in _CASE_COPY}
    for key in ("organization_id", "mode_id", "source_batch_id", "source_golden_case_id"):
        if new.get(key) is not None:
            new[key] = str(new[key])
    new.update(id=str(uuid.uuid4()), version=int(old["version"]) + 1, status="draft", kind="unclassified",
               baseline_pass_rate=None, baseline_run_id=None, golden_case_id=None, supersedes=str(old["id"]),
               retired_reason=None, rev=1)
    if input_override:
        new["input"] = merge_input(new["input"] or {}, input_override)
    if initial_state is not None:
        new["initial_state"] = dict(initial_state)
    if expected is not None:
        new["expected"] = dict(expected)
    return new


_PUBLIC_CASE = ("id", "organization_id", "case_key", "version", "status", "kind", "baseline_pass_rate",
                "baseline_run_id", "mode_id", "mode_name", "source_type", "source_turn_id", "source_batch_id",
                "source_golden_case_id", "golden_case_id", "input", "initial_state", "expected", "supersedes",
                "retired_reason", "rev", "created_by_email", "created_at", "updated_at")


def case_public(row: Mapping[str, Any]) -> dict:
    out = {k: row.get(k) for k in _PUBLIC_CASE}
    for key in ("input", "initial_state", "expected"):
        out[key] = _json(out[key])
    out["latest_run"] = ({"golden_run_id": row.get("run_golden_run_id"), "purpose": row.get("run_purpose"),
                          "decision": row.get("run_decision"), "reason": row.get("run_reason"),
                          "passes": row.get("run_passes"), "n": row.get("run_n"), "k": row.get("run_k"),
                          "pass_at_1": row.get("run_pass_at_1"), "pass_at_3": row.get("run_pass_at_3"),
                          "pass_at_5": row.get("run_pass_at_5"), "pass_hat_k": row.get("run_pass_hat_k"),
                          "computed_at": row.get("run_computed_at")}
                         if row.get("run_golden_run_id") else None)
    return public(out)


def _fmt(x: Any) -> str:
    return "—" if x is None else f"{float(x):.2f}"


def render_release_md(report: Mapping[str, Any], *, company: str) -> str:
    """Spec §5.6 Markdown, in order. No internal names: checks by label, judges by version name."""
    fp = report.get("fingerprint") or {}
    graders = report.get("graders") or {}
    labels = {c.evaluator_key: c.label for c in checks.CHECKS.values()}
    k = report.get("k")
    lines = [f"# Release check — {company} — {report.get('run_key')}", "",
             f"**Decision: {str(report.get('decision') or '').upper()}** — {report.get('reason')}", "",
             "## Run", f"- Runs per case: {k}", f"- Started: {report.get('started_at') or '—'}",
             f"- Finished: {report.get('finished_at') or 'not finished'}",
             f"- Cost: ${float(report.get('usd') or 0):.2f}", "",
             "## Configuration", f"- Build: {fp.get('image') or 'not recorded'}",
             f"- Chat model: {fp.get('openai_model') or 'not recorded'}",
             f"- Judge model: {fp.get('grader_model') or 'not recorded'}",
             "- Judge versions: " + (", ".join(j.get("judge_key") or "" for j in graders.get("judges") or []) or "none"),
             "- Code checks: " + (", ".join(labels.get(key, "a retired check") for key in graders.get("checks") or [])
                                  or "none"), "",
             "## Regression cases"]
    regression = report.get("regression") or []
    if regression:
        lines += ["| Case | Failure mode | Runs passed | Decision | Why |", "|---|---|---|---|---|"]
        lines += [f"| {r['case_key']} v{r['version']} | {r.get('mode') or '—'} | {r['passes']} of {k} | "
                  f"{r['decision']} | {r['reason']} |" for r in regression]
    else:
        lines.append("No regression cases yet.")
    lines += ["", "## Capability cases"]
    capability = report.get("capability") or []
    if capability:
        lines += ["| Case | Failure mode | pass@1 | pass@3 | pass@5 | pass^5 | Baseline |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {c['case_key']} v{c['version']} | {c.get('mode') or '—'} | {_fmt(c.get('pass_at_1'))} | "
                  f"{_fmt(c.get('pass_at_3'))} | {_fmt(c.get('pass_at_5'))} | {_fmt(c.get('pass_hat_5'))} | "
                  f"{_fmt(c.get('baseline_pass_rate'))} |" for c in capability]
    else:
        lines.append("None in this check.")
    lines += ["", "## Runs without a verdict"]
    gaps = report.get("runs_without_verdict") or []
    lines += [f"- {g['case_key']} run {g['rep']}: {g['status']}" + (f" ({g['reason']})" if g.get("reason") else "")
              for g in gaps] or ["None."]
    lines += ["", "## Changes since the last check"]
    changes = report.get("changes_since_last")
    if changes is None:
        lines.append("No earlier release check.")
    elif not changes:
        lines.append("No changes.")
    else:
        lines += [f"- {c['case_key']}: {c['before']} → {c['after']}" for c in changes]
    lines += ["", f"Open in the Trace Lab: ?tl=runs&run={report.get('golden_run_id')}", ""]
    return "\n".join(lines)
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_eval_cases.py` → `14 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/eval_cases.py`, `DA/tests/test_trace_lab_eval_cases.py`.

---

### Task 13: `eval_cases.py` — cases and their golden twins in the "Regression cases" set (R9)

**Files:**
- Modify: `DA/src/trace_lab/eval_cases.py` (append)
- Test: `DA/tests/test_trace_lab_eval_cases.py` (append); `DA/tests/test_trace_lab_watch_sql.py` (append)

**Interfaces:**
- Consumes: `golden.CaseRow`, `golden.case_version_for(row)`, `golden.set_hash(keys_and_versions)`, `golden.regression_set(org, *, create=False, actor_id=None, actor_email=None, conn=None) -> dict | None` (M3: the one Regression cases set; M6 never creates it itself); M5's `judges.load_judge_turns(turn_ids) -> dict[turn_id, NormalizedTurn]`; `provenance.inferred_channel_sql` (M0); tables `labels` (M4), `annotations` (M2), `golden_sets`, `golden_cases` (M3's `0003`), `eval_cases`, `eval_case_runs`, `modes`, `turn_meta`, `plenum_cafm.agent_trace_turns`; Task 12 pure functions.
- Produces: `create_case(org, *, source, mode_id=None, input_override=None, initial_state=None, expected=None, expected_answer=None, evaluation="objective", expected_values=None, actor_id, actor_email) -> case`; `confirm_case(org, case_id, *, base, expected_answer=None, evaluation="objective", expected_values=None, actor_id, actor_email) -> case` (the expected answer is required unless the case already has a twin); `edit_case(org, case_id, *, base, input_override=None, initial_state=None, expected=None, expected_answer=None, evaluation=None, expected_values=None, actor_id, actor_email) -> case`; `retire_case(org, case_id, *, base, reason) -> case`; `list_cases(org, *, status=None, kind=None, mode_id=None) -> list[case]`; `get_case(org, case_id) -> {"case", "runs", "twin"}`; `propose_from_monitor(*, batch, mode_id, turn_ids) -> list[str]`; store `_turn_for_case(org | None, turn_id)`, `_failure_confirmed(org, turn_id, mode_id)`, `_case(org, case_id)`, `_live_case_for_turn(org, turn_id)`, `_golden_case(org, golden_case_id)`, `_twin(golden_case_id)`, `_actor_for_twin(actor_id, actor_email) -> (id, email)` (422 `actor_required`: M3's `golden.regression_set` records who created the set, so a real id and email are required), `_add_twin(conn, *, set_id, org, case_key, question, followups, expected_answer, evaluation, expected_values, page_context, source) -> golden_case_id`, `_insert_case(case, twin, *, actor_id, actor_email, supersede=None)`, `_with_twin(twin, *, actor_id, actor_email, update_sql, params) -> golden_case_id`, `_new_case(...)`, `_twin_from(case, *, expected_answer, evaluation, values, source)`.

- [ ] **Step 1: Write the failing test**

Append to `DA/tests/test_trace_lab_eval_cases.py`:

```python
def _turn_row(tid, question="How many work orders were raised at Bishopsgate Tower in July 2026?", channel="chat"):
    return {"turn_id": tid, "organization_id": ORG_A, "session_id": "s-1", "question": question, "channel": channel,
            "context_text": "Page: Maintenance", "asker_role": "admin", "asker_scope": None}


async def test_live_turn_case_needs_confirmed_failure_and_expected_answer(monkeypatch):
    inserted, confirmed = [], {"value": False}

    async def turn(org, tid):
        return _turn_row(tid)

    async def is_confirmed(org, tid, mode):
        return confirmed["value"]

    async def no_case(org, tid):
        return None

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def load(ids):                                              # M5's loader: {turn_id: NormalizedTurn}
        return {t: SimpleNamespace(question="How many work orders were raised at Bishopsgate Tower in July 2026?",
                                   prior=[]) for t in ids}

    async def insert(case, twin, **kw):
        inserted.append((case, twin))
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", is_confirmed)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", no_case)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    body = dict(source={"type": "live_turn", "turn_id": "turn-0000000000000001"}, mode_id=M1,
                expected={"judges": [{"mode_id": M1}]}, actor_id=ACTOR, actor_email="reviewer@example.com")
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, expected_answer="There were 786.", **body)
    assert e.value.status == 409 and e.value.reason == "failure_not_confirmed" and inserted == []
    confirmed["value"] = True
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, expected_answer="   ", **body)
    assert e.value.status == 422 and e.value.reason == "expected_answer_required" and inserted == []
    out = await eval_cases.create_case(ORG_A, expected_answer="There were 786 work orders in July 2026.", **body)
    case, twin = inserted[0]
    assert case["status"] == "draft" and case["source_type"] == "live_turn" and case["source_turn_id"] == "turn-0000000000000001"
    assert twin["case_key"] == eval_cases.twin_key(case["case_key"], 1)
    assert twin["expected_answer"] == "There were 786 work orders in July 2026." and twin["evaluation"] == "objective"
    assert twin["question"].startswith("How many work orders") and twin["page_context"] == "Page: Maintenance"
    assert case["expected"]["judges"] == [{"mode_id": M1}] and out["case_key"] == case["case_key"]


async def test_golden_case_source_copies_the_golden_case_as_its_twin(monkeypatch):
    inserted = []

    async def golden_case(org, gid):
        return {"id": gid, "question": "How many floors does Bishopsgate Tower have?", "followups": "[]",
                "expected_answer": "22 floors.", "evaluation": "objective",
                "expected_values": json.dumps([{"label": "floors", "kind": "number", "value": 22}]),
                "page_context": None}

    async def in_use(mode_ids=None):
        return []

    async def insert(case, twin, **kw):
        inserted.append((case, twin))
        return case

    monkeypatch.setattr(eval_cases, "_golden_case", golden_case)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    await eval_cases.create_case(ORG_A, source={"type": "golden_case", "golden_case_id": "g-1"}, actor_id=ACTOR,
                                 actor_email=None)
    case, twin = inserted[0]
    assert case["status"] == "draft" and case["source_golden_case_id"] == "g-1" and case["source_turn_id"] is None
    assert twin["expected_answer"] == "22 floors." and twin["expected_values"][0]["value"] == 22
    assert twin["page_context"] is None and case["expected"]["golden_expected"] is True


async def test_confirm_needs_a_proposed_case_a_fresh_base_and_an_expected_answer(monkeypatch):
    row = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "proposed",
           "rev": 2, "mode_id": M1, "source_turn_id": "turn-1",
           "input": {"question": "How many work orders in July?", "followups": [], "page_context": ""}}
    calls = []

    async def case(org, cid):
        return dict(row)

    async def confirmed(org, tid, mode):
        return True

    async def with_twin(twin, **kw):
        calls.append((twin, kw))
        return "g1"

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", confirmed)
    monkeypatch.setattr(eval_cases, "_with_twin", with_twin)
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, "c1", base=1, expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.status == 409 and e.value.reason == "stale_base"
    await eval_cases.confirm_case(ORG_A, "c1", base=2, expected_answer="There were 786.", actor_id=ACTOR,
                                  actor_email=None)
    twin, kw = calls[0]
    assert twin["case_key"] == "e-7f3a19c2.v1" and twin["expected_answer"] == "There were 786."
    assert kw["params"]["base"] == 2 and "status = 'proposed'" in kw["update_sql"]
    row["status"] = "draft"
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, "c1", base=2, expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.reason == "not_proposed"


async def test_propose_from_monitor_skips_turns_that_already_have_a_case(monkeypatch):
    inserted = []

    async def turn(org, tid):
        return dict(_turn_row(tid, question=f"How many certificates expire in {tid}?"), context_text=None)

    async def live(org, tid):
        return {"id": "existing"} if tid == "t2" else None

    async def load(ids):
        return {t: SimpleNamespace(question=f"How many certificates expire in {t}?", prior=[]) for t in ids}

    async def insert(case, twin, **kw):
        inserted.append((case, twin, kw))
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", live)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    ids = await eval_cases.propose_from_monitor(batch={"id": "b1", "started_by": ACTOR, "started_by_email": "o@x"},
                                                mode_id=M1, turn_ids=["t1", "t2", "t3"])
    assert len(ids) == 2 and [c["source_turn_id"] for c, _, _ in inserted] == ["t1", "t3"]
    assert all(c["status"] == "proposed" and c["source_type"] == "monitor" and c["source_batch_id"] == "b1"
               and twin is None for c, twin, _ in inserted)
    assert inserted[0][0]["expected"] == {"assertions": [], "checks": [], "judges": [{"mode_id": M1}],
                                          "golden_expected": True}


def test_a_twin_is_recorded_under_a_real_actor_id_and_email():
    with pytest.raises(WatchError) as e:                              # M3's golden.regression_set(create=True) needs both
        eval_cases._actor_for_twin(ACTOR, None)
    assert e.value.status == 422 and e.value.reason == "actor_required"
    with pytest.raises(WatchError):
        eval_cases._actor_for_twin(None, "reviewer@example.com")
    assert eval_cases._actor_for_twin(ACTOR, "reviewer@example.com") == (ACTOR, "reviewer@example.com")
```

Append to `DA/tests/test_trace_lab_watch_sql.py`:

```python
async def _wrong_answer(conn, tid, org=ORG_A):
    await conn.execute(text("INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, organization_id, question, "
                            "answer, started_at) VALUES (:t, 's-1', CAST(:o AS uuid), :q, 'There were 758.', :ts)"),
                       {"t": tid, "o": org, "q": "How many work orders were raised at Bishopsgate Tower in July 2026?",
                        "ts": datetime.now(UTC) - timedelta(days=2)})
    await conn.execute(text("INSERT INTO plenum_tracelab.turn_meta (turn_id, organization_id, channel, channel_source, "
                            "classifier_version, context_text, asker_role) VALUES (:t, CAST(:o AS uuid), 'chat', "
                            "'recorded', 'test', 'Page: Maintenance', 'admin')"), {"t": tid, "o": org})
    aid = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id,
    reviewer_id, reviewer_email, verdict, note, field, quote, quote_occurrence, quote_start, blind)
VALUES (CAST(:id AS uuid), CAST(:id AS uuid), 1, 'verdict', CAST(:o AS uuid), 's-1', :t, CAST(:by AS uuid),
    'reviewer@example.com', 'wrong', 'Counts June, not July.', 'answer', '758', 1, 10, true)"""),
                       {"id": aid, "o": org, "t": tid, "by": ACTOR})


async def test_case_twin_lands_in_the_regression_set(monkeypatch):
    from types import SimpleNamespace

    from src.trace_lab import eval_cases, golden

    engine = await fresh_schema(monkeypatch)
    async with engine.begin() as conn:
        await _wrong_answer(conn, "turn-00000000000000f1")
        await _wrong_answer(conn, "turn-00000000000000f2")

    async def none_in_use(mode_ids=None):
        return []

    async def load(ids):
        return {t: SimpleNamespace(question="How many work orders were raised at Bishopsgate Tower in July 2026?",
                                   prior=[]) for t in ids}

    monkeypatch.setattr(eval_cases, "judges_in_use", none_in_use)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    first = await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": "turn-00000000000000f1"},
                                         expected_answer="There were 786 work orders in July 2026.",
                                         actor_id=ACTOR, actor_email="reviewer@example.com")
    await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": "turn-00000000000000f2"},
                                 expected_answer="There were 786 work orders in July 2026.",
                                 actor_id=ACTOR, actor_email="reviewer@example.com")
    async with engine.connect() as conn:
        sets = (await conn.execute(text("SELECT id, version, status, source_kind, filename, case_count FROM "
                                        "plenum_tracelab.golden_sets WHERE organization_id = CAST(:o AS uuid)"),
                                   {"o": ORG_A})).mappings().all()
        twin = (await conn.execute(text("SELECT g.case_key, g.expected_answer, g.page_context, g.set_id FROM "
                                        "plenum_tracelab.eval_cases c JOIN plenum_tracelab.golden_cases g ON "
                                        "g.id = c.golden_case_id WHERE c.case_key = :k"),
                                   {"k": first["case_key"]})).mappings().one()
    assert len(sets) == 1 and sets[0]["version"] == 0 and sets[0]["status"] == "regression"
    assert sets[0]["source_kind"] == "eval_cases" and sets[0]["filename"] == "Regression cases"
    assert sets[0]["case_count"] == 2 and twin["set_id"] == sets[0]["id"]
    assert twin["case_key"] == f"{first['case_key']}.v1" and twin["expected_answer"].startswith("There were 786")
    assert twin["page_context"] == "Page: Maintenance" and first["status"] == "draft"
    assert str(sets[0]["id"]) == str((await golden.regression_set(ORG_A))["id"])   # the set is M3's, found by its own reader
    with pytest.raises(eval_cases.WatchError) as e:                   # one live case per answer
        await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": "turn-00000000000000f1"},
                                     expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.reason == "case_exists"
    edited = await eval_cases.edit_case(ORG_A, first["id"], base=first["rev"], expected_answer="786 work orders.",
                                        actor_id=ACTOR, actor_email="reviewer@example.com")
    assert edited["version"] == 2 and edited["status"] == "draft" and edited["supersedes"] == first["id"]
    async with engine.connect() as conn:
        old = (await conn.execute(text("SELECT status FROM plenum_tracelab.eval_cases WHERE id = CAST(:i AS uuid)"),
                                  {"i": first["id"]})).scalar()
        keys = (await conn.execute(text("SELECT case_key FROM plenum_tracelab.golden_cases ORDER BY row_no"))).scalars().all()
    assert old == "superseded" and f"{first['case_key']}.v2" in keys
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_eval_cases.py tests/test_trace_lab_watch_sql.py`
Expected: `AttributeError: … has no attribute '_turn_for_case'` (and `create_case`, `confirm_case`, `edit_case`, `propose_from_monitor`).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/eval_cases.py`)

```python
# ---- store: cases and their golden twins (R9) ----------------------------------------------------------

def _turn_sql() -> str:
    return f"""
SELECT t.turn_id, t.organization_id, t.session_id, COALESCE(m.asker_question, t.question) AS question,
       COALESCE(m.channel, {provenance.inferred_channel_sql('t')}) AS channel,
       m.context_text, m.asker_role, m.asker_scope
  FROM plenum_cafm.agent_trace_turns t
  LEFT JOIN plenum_tracelab.turn_meta m ON m.turn_id = t.turn_id
 WHERE t.turn_id = :tid AND t.organization_id IS NOT NULL
   AND (CAST(:org AS uuid) IS NULL OR t.organization_id = CAST(:org AS uuid))"""


async def _turn_for_case(org: str | None, turn_id: str) -> dict | None:
    async with db.engine().connect() as conn:
        row = (await conn.execute(text(_turn_sql()), {"tid": turn_id, "org": org})).mappings().first()
    return dict(row) if row else None


async def _failure_confirmed(org: str, turn_id: str, mode_id: str | None) -> bool:
    """Spec §5.1: a person recorded the failure - a live label 'present' decided one at a time (R10) for the
    mode, or a live 'wrong' verdict on the answer."""
    async with db.engine().connect() as conn:
        return bool((await conn.execute(text("""
SELECT EXISTS (
    SELECT 1 FROM plenum_tracelab.labels l
     WHERE l.turn_id = :tid AND l.organization_id = CAST(:org AS uuid) AND l.superseded_by IS NULL
       AND NOT l.tombstone AND l.present AND l.approval = 'single'
       AND (CAST(:mode AS uuid) IS NULL OR l.mode_id = CAST(:mode AS uuid)))
 OR EXISTS (
    SELECT 1 FROM plenum_tracelab.annotations a
     WHERE a.turn_id = :tid AND a.organization_id = CAST(:org AS uuid) AND a.kind = 'verdict'
       AND a.superseded_by IS NULL AND NOT a.tombstone AND a.verdict = 'wrong')"""),
            {"tid": turn_id, "org": org, "mode": mode_id})).scalar())


_CASE_SELECT = """
SELECT c.*, m.name AS mode_name, lr.golden_run_id AS run_golden_run_id, lr.purpose AS run_purpose,
       lr.decision AS run_decision, lr.reason AS run_reason, lr.passes AS run_passes, lr.n AS run_n, lr.k AS run_k,
       lr.pass_at_1 AS run_pass_at_1, lr.pass_at_3 AS run_pass_at_3, lr.pass_at_5 AS run_pass_at_5,
       lr.pass_hat_k AS run_pass_hat_k, lr.computed_at AS run_computed_at
  FROM plenum_tracelab.eval_cases c
  LEFT JOIN plenum_tracelab.modes m ON m.id = c.mode_id
  LEFT JOIN LATERAL (SELECT * FROM plenum_tracelab.eval_case_runs r WHERE r.eval_case_id = c.id
                      ORDER BY r.computed_at DESC LIMIT 1) lr ON true"""


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


async def _case(org: str, case_id: str) -> dict | None:
    if not _is_uuid(case_id):
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text(_CASE_SELECT + " WHERE c.id = CAST(:id AS uuid) AND c.organization_id = CAST(:org AS uuid)"),
                                  {"id": str(case_id), "org": org})).mappings().first()
    return dict(row) if row else None


async def _live_case_for_turn(org: str, turn_id: str) -> dict | None:
    async with db.engine().connect() as conn:
        row = (await conn.execute(text(_CASE_SELECT + """
 WHERE c.organization_id = CAST(:org AS uuid) AND c.source_turn_id = :tid
   AND c.status IN ('proposed', 'draft', 'baselining', 'active')
 ORDER BY c.created_at DESC LIMIT 1"""), {"org": org, "tid": turn_id})).mappings().first()
    return dict(row) if row else None


async def _golden_case(org: str, golden_case_id: str) -> dict | None:
    if not _is_uuid(golden_case_id):
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("SELECT * FROM plenum_tracelab.golden_cases WHERE id = CAST(:id AS uuid) "
                                       "AND organization_id = CAST(:org AS uuid)"),
                                  {"id": str(golden_case_id), "org": org})).mappings().first()
    return dict(row) if row else None


async def _twin(golden_case_id: Any) -> dict | None:
    if not golden_case_id:
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("SELECT id, set_id, case_key, expected_answer, evaluation, expected_values, "
                                       "page_context FROM plenum_tracelab.golden_cases WHERE id = CAST(:id AS uuid)"),
                                  {"id": str(golden_case_id)})).mappings().first()
    return dict(row) if row else None


def _actor_for_twin(actor_id: str | None, actor_email: str | None) -> tuple[str, str]:
    """M3's golden.regression_set(create=True) records who made the company's Regression cases set, so a twin is
    written only under a real actor id and email: no placeholder, no company-id fallback."""
    if not (actor_id and actor_email):
        raise WatchError(422, "actor_required",
                         "Your account needs an email address before a case can be recorded under it.")
    return str(actor_id), str(actor_email)


async def _add_twin(conn: Any, *, set_id: str, org: str, case_key: str, question: str, followups: Sequence[str],
                    expected_answer: str, evaluation: str, expected_values: Sequence[Mapping[str, Any]],
                    page_context: str | None, source: str) -> str:
    count = int((await conn.execute(text("SELECT count(*) FROM plenum_tracelab.golden_cases WHERE set_id = CAST(:s AS uuid)"),
                                    {"s": set_id})).scalar() or 0)
    if count >= REGRESSION_SET_MAX:
        raise WatchError(409, "regression_set_full", "The Regression cases set holds 2,000 cases; retire some first.")
    flags = ("write_intent",) if WRITE_INTENT.search(" ".join([question, *followups])) else ()
    row = golden.CaseRow(row_no=count + 1, case_key=case_key, key_source="id_column", case_version="",
                         question=question, followups=tuple(followups), expected_answer=expected_answer,
                         evaluation=evaluation, expected_values=tuple(expected_values), source=source,
                         verified_on=datetime.now(timezone.utc).date(), page_context=page_context, ask_as="company",
                         requirement_ids=(), notes=None, flags=flags)
    golden_id = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO plenum_tracelab.golden_cases (id, set_id, organization_id, case_key, key_source, case_version, row_no,
    question, followups, expected_answer, evaluation, expected_values, source, verified_on, page_context, ask_as,
    requirement_ids, notes, flags)
VALUES (CAST(:id AS uuid), CAST(:set AS uuid), CAST(:org AS uuid), :key, 'id_column', :version, :row_no, :q,
    CAST(:followups AS jsonb), :expected, :evaluation, CAST(:values AS jsonb), :source, :verified, :ctx, 'company',
    CAST(:reqs AS text[]), NULL, CAST(:flags AS text[]))"""),
        {"id": golden_id, "set": set_id, "org": org, "key": case_key, "version": golden.case_version_for(row),
         "row_no": count + 1, "q": question, "followups": json.dumps(list(followups)),
         "expected": expected_answer, "evaluation": evaluation, "values": json.dumps(public(list(expected_values))),
         "source": source[:500], "verified": row.verified_on, "ctx": page_context, "reqs": [], "flags": list(flags)})
    pairs = [(k, v) for k, v in (await conn.execute(text(
        "SELECT case_key, case_version FROM plenum_tracelab.golden_cases WHERE set_id = CAST(:s AS uuid)"),
        {"s": set_id})).all()]
    await conn.execute(text("UPDATE plenum_tracelab.golden_sets SET case_count = :n, set_hash = :h "
                            "WHERE id = CAST(:s AS uuid)"), {"n": len(pairs), "h": golden.set_hash(pairs), "s": set_id})
    return golden_id


_INSERT_CASE = """
INSERT INTO plenum_tracelab.eval_cases (id, organization_id, case_key, version, status, kind, baseline_pass_rate,
    mode_id, source_type, source_turn_id, source_batch_id, source_golden_case_id, golden_case_id, input,
    initial_state, expected, supersedes, created_by, created_by_email)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :key, :version, :status, :kind, :rate, CAST(:mode AS uuid), :stype,
    :sturn, CAST(:sbatch AS uuid), CAST(:sgolden AS uuid), CAST(:golden AS uuid), CAST(:input AS jsonb),
    CAST(:state AS jsonb), CAST(:expected AS jsonb), CAST(:supersedes AS uuid), CAST(:by AS uuid), :email)"""


def _case_params(case: Mapping[str, Any], actor_id: str | None, actor_email: str | None) -> dict:
    rate = case.get("baseline_pass_rate")
    return {"id": case["id"], "org": case["organization_id"], "key": case["case_key"], "version": int(case["version"]),
            "status": case["status"], "kind": case["kind"], "rate": None if rate is None else Decimal(str(rate)),
            "mode": case.get("mode_id"), "stype": case["source_type"], "sturn": case.get("source_turn_id"),
            "sbatch": case.get("source_batch_id"), "sgolden": case.get("source_golden_case_id"),
            "golden": case.get("golden_case_id"), "input": json.dumps(public(case["input"])),
            "state": json.dumps(public(case.get("initial_state") or {})), "expected": json.dumps(public(case["expected"])),
            "supersedes": case.get("supersedes"), "by": actor_id, "email": actor_email}


async def _insert_case(case: dict, twin: Mapping[str, Any] | None, *, actor_id: str | None, actor_email: str | None,
                       supersede: tuple[str, int] | None = None) -> dict:
    """One transaction: supersede the old version (edit), create the twin, insert the case."""
    try:
        async with db.engine().begin() as conn:
            if supersede is not None:
                res = await conn.execute(text(
                    "UPDATE plenum_tracelab.eval_cases SET status = 'superseded', rev = rev + 1, updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND rev = :base AND status IN ('draft', 'baselining', 'active')"),
                    {"id": supersede[0], "base": int(supersede[1])})
                if res.rowcount == 0:
                    raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
            if twin is not None:
                who, email = _actor_for_twin(actor_id, actor_email)
                set_id = str((await golden.regression_set(case["organization_id"], create=True, actor_id=who,
                                                          actor_email=email, conn=conn))["id"])
                case["golden_case_id"] = await _add_twin(conn, set_id=set_id, **twin)
            await conn.execute(text(_INSERT_CASE), _case_params(case, actor_id, actor_email))
    except IntegrityError:
        raise WatchError(409, "case_exists", "A case with this key already exists; reload and try again.") from None
    return case


async def _with_twin(twin: Mapping[str, Any], *, actor_id: str | None, actor_email: str | None, update_sql: str,
                     params: Mapping[str, Any]) -> str:
    """Creates the twin and runs one guarded UPDATE of the case in one transaction; 0 rows rolls the twin back."""
    who, email = _actor_for_twin(actor_id, actor_email)
    async with db.engine().begin() as conn:
        set_id = str((await golden.regression_set(str(twin["org"]), create=True, actor_id=who, actor_email=email,
                                                  conn=conn))["id"])
        golden_id = await _add_twin(conn, set_id=set_id, **twin)
        res = await conn.execute(text(update_sql), {**params, "g": golden_id})
        if res.rowcount == 0:
            raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    return golden_id


def _new_case(org: str, key: str, *, status: str, source_type: str, inp: Mapping[str, Any],
              expected: Mapping[str, Any], mode_id: str | None = None, initial_state: Mapping[str, Any] | None = None,
              source_turn_id: str | None = None, source_batch_id: str | None = None,
              source_golden_case_id: str | None = None) -> dict:
    return {"id": str(uuid.uuid4()), "organization_id": org, "case_key": key, "version": 1, "status": status,
            "kind": "unclassified", "baseline_pass_rate": None, "baseline_run_id": None,
            "mode_id": str(mode_id) if mode_id else None, "source_type": source_type,
            "source_turn_id": source_turn_id, "source_batch_id": source_batch_id,
            "source_golden_case_id": source_golden_case_id, "golden_case_id": None, "input": dict(inp),
            "initial_state": dict(initial_state or {"as_of": datetime.now(timezone.utc).date().isoformat()}),
            "expected": dict(expected), "supersedes": None, "retired_reason": None, "rev": 1}


def _twin_from(case: Mapping[str, Any], *, expected_answer: str, evaluation: str,
               values: Sequence[Mapping[str, Any]], source: str) -> dict:
    inp = case["input"]
    return {"org": str(case["organization_id"]), "case_key": twin_key(case["case_key"], int(case["version"])),
            "question": inp["question"], "followups": list(inp.get("followups") or []),
            "expected_answer": expected_answer.strip()[:6000], "evaluation": evaluation,
            "expected_values": list(values), "page_context": inp.get("page_context"), "source": source}


def _require_answer(expected_answer: str | None) -> str:
    if not (expected_answer or "").strip():
        raise WatchError(422, "expected_answer_required",
                         "Write what a right answer says before the case can run (R9).")
    return expected_answer.strip()


# ---- cases API (§5.1, §5.2, §7.3) -------------------------------------------------------------------------

async def create_case(org: str, *, source: Mapping[str, Any], mode_id: str | None = None,
                      input_override: Mapping[str, Any] | None = None, initial_state: Mapping[str, Any] | None = None,
                      expected: Mapping[str, Any] | None = None, expected_answer: str | None = None,
                      evaluation: str = "objective", expected_values: str | None = None,
                      actor_id: str | None, actor_email: str | None) -> dict:
    in_use = {j["mode_id"]: j for j in await judges_in_use(None)}
    exp = normalise_expected(expected, modes_in_use=in_use)
    kind = (source or {}).get("type")
    key = new_case_key()
    if kind == "live_turn":
        turn_id = str(source.get("turn_id") or "")
        turn = await _turn_for_case(org, turn_id)
        if turn is None:
            raise WatchError(404, "turn_not_in_scope", "That answer is not in this company.")
        if not replayable(turn["question"], turn["channel"], turn["session_id"]):
            raise WatchError(409, "turn_not_replayable", "Only chat answers that ask for no change can become cases.")
        if not await _failure_confirmed(org, turn_id, mode_id):
            raise WatchError(409, "failure_not_confirmed",
                             "A person must first mark this answer wrong, or label the failure present.")
        if await _live_case_for_turn(org, turn_id):
            raise WatchError(409, "case_exists", "This answer already has a case.")
        answer = _require_answer(expected_answer)
        values = parse_values(expected_values)
        loaded = (await judges.load_judge_turns([turn_id])).get(turn_id)
        inp = case_from_turn(loaded if loaded is not None else {"question": turn["question"], "prior": []}, turn)
        if input_override:
            inp = merge_input(inp, input_override)
        case = _new_case(org, key, status="draft", source_type="live_turn", inp=inp, expected=exp, mode_id=mode_id,
                         initial_state=initial_state, source_turn_id=turn_id)
        twin = _twin_from(case, expected_answer=answer, evaluation=evaluation, values=values,
                          source=f"Eval case {key} v1, from a reviewed answer")
    elif kind == "golden_case":
        g = await _golden_case(org, str(source.get("golden_case_id") or ""))
        if g is None:
            raise WatchError(404, "not_found", "That golden case was not found.")
        inp = {"question": g["question"], "followups": list(_json(g["followups"]) or []),
               "page_context": g["page_context"], "scope": {"asker_role": None, "building_ids": None}}
        case = _new_case(org, key, status="draft", source_type="golden_case", inp=inp, expected=exp, mode_id=mode_id,
                         initial_state=initial_state, source_golden_case_id=str(g["id"]))
        twin = _twin_from(case, expected_answer=g["expected_answer"], evaluation=g["evaluation"],
                          values=list(_json(g["expected_values"]) or []), source=f"Eval case {key} v1, from a golden case")
    else:
        raise WatchError(422, "bad_source", "A case comes from a reviewed answer or a golden case.")
    saved = await _insert_case(case, twin, actor_id=actor_id, actor_email=actor_email)
    log.info("trace_lab.eval_cases.created", case_key=key, source=kind)
    return case_public(saved)


async def confirm_case(org: str, case_id: str, *, base: int, expected_answer: str | None = None,
                       evaluation: str = "objective", expected_values: str | None = None, actor_id: str | None,
                       actor_email: str | None) -> dict:
    """A proposed case becomes a draft once a person has confirmed the failure. Its twin is made here, from the
    expected answer the person writes (R9) - unless a repeat-n request already made one with a written answer."""
    case = await _case(org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] != "proposed":
        raise WatchError(409, "not_proposed", "Only a proposed case is confirmed.")
    if int(case["rev"]) != int(base):
        raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    if not await _failure_confirmed(org, case["source_turn_id"], str(case["mode_id"]) if case.get("mode_id") else None):
        raise WatchError(409, "failure_not_confirmed",
                         "A person must first mark this answer wrong, or label the failure present.")
    if case.get("golden_case_id"):
        async with db.engine().begin() as conn:
            res = await conn.execute(text(
                "UPDATE plenum_tracelab.eval_cases SET status = 'draft', rev = rev + 1, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND rev = :base AND status = 'proposed'"),
                {"id": str(case["id"]), "base": int(base)})
            if res.rowcount == 0:
                raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
        return case_public(await _case(org, case_id) or case)
    answer = _require_answer(expected_answer)
    case = dict(case, input=_json(case["input"]))
    twin = _twin_from(case, expected_answer=answer, evaluation=evaluation, values=parse_values(expected_values),
                      source=f"Eval case {case['case_key']} v{case['version']}, from a monitoring flag")
    await _with_twin(twin, actor_id=actor_id, actor_email=actor_email,
                     update_sql="UPDATE plenum_tracelab.eval_cases SET status = 'draft', golden_case_id = CAST(:g AS uuid), "
                                "rev = rev + 1, updated_at = now() WHERE id = CAST(:id AS uuid) AND rev = :base "
                                "AND status = 'proposed'",
                     params={"id": str(case["id"]), "base": int(base)})
    return case_public(await _case(org, case_id) or case)


async def edit_case(org: str, case_id: str, *, base: int, input_override: Mapping[str, Any] | None = None,
                    initial_state: Mapping[str, Any] | None = None, expected: Mapping[str, Any] | None = None,
                    expected_answer: str | None = None, evaluation: str | None = None,
                    expected_values: str | None = None, actor_id: str | None, actor_email: str | None) -> dict:
    old = await _case(org, case_id)
    if old is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if old["status"] in ("retired", "superseded"):
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    if old["status"] == "proposed":
        raise WatchError(409, "not_confirmed", "Confirm the case first; that is where its expected answer is written.")
    if int(old["rev"]) != int(base):
        raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    exp = None
    if expected is not None:
        exp = normalise_expected(expected, modes_in_use={j["mode_id"]: j for j in await judges_in_use(None)})
    new = next_version(old, input_override=input_override, initial_state=initial_state, expected=exp)
    prev = await _twin(old.get("golden_case_id")) or {}
    twin = _twin_from(new, expected_answer=(expected_answer or prev.get("expected_answer") or ""),
                      evaluation=evaluation or prev.get("evaluation") or "objective",
                      values=(parse_values(expected_values) if expected_values is not None
                              else list(_json(prev.get("expected_values")) or [])),
                      source=f"Eval case {new['case_key']} v{new['version']}")
    _require_answer(twin["expected_answer"])
    saved = await _insert_case(new, twin, actor_id=actor_id, actor_email=actor_email, supersede=(str(old["id"]), int(base)))
    return case_public(saved)


async def retire_case(org: str, case_id: str, *, base: int, reason: str) -> dict:
    async with db.engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.eval_cases SET status = 'retired', retired_reason = :reason, rev = rev + 1, "
            "updated_at = now() WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) AND rev = :base "
            "AND status IN ('proposed', 'draft', 'baselining', 'active') RETURNING id"),
            {"reason": reason.strip()[:500], "id": str(case_id), "org": org, "base": int(base)})).first()
    if row is None:
        if await _case(org, case_id) is None:
            raise WatchError(404, "not_found", "That case was not found.")
        raise WatchError(409, "stale_base", "This case changed since you opened it, or is already retired.")
    return case_public(await _case(org, case_id) or {})


async def list_cases(org: str, *, status: str | None = None, kind: str | None = None,
                     mode_id: str | None = None) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(_CASE_SELECT + """
 WHERE c.organization_id = CAST(:org AS uuid)
   AND ((CAST(:status AS text) IS NULL AND c.status <> 'superseded') OR c.status = CAST(:status AS text))
   AND (CAST(:kind AS text) IS NULL OR c.kind = CAST(:kind AS text))
   AND (CAST(:mode AS text) IS NULL OR c.mode_id::text = CAST(:mode AS text))
 ORDER BY c.updated_at DESC LIMIT 500"""), {"org": org, "status": status, "kind": kind, "mode": mode_id})).mappings().all()
    return [case_public(r) for r in rows]


async def get_case(org: str, case_id: str) -> dict:
    case = await _case(org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    async with db.engine().connect() as conn:
        runs = (await conn.execute(text(
            "SELECT * FROM plenum_tracelab.eval_case_runs WHERE eval_case_id = CAST(:id AS uuid) ORDER BY computed_at DESC"),
            {"id": str(case_id)})).mappings().all()
    twin = await _twin(case.get("golden_case_id"))
    return {"case": case_public(case), "runs": public([dict(r, reps=_json(r["reps"]), graders=_json(r["graders"]),
                                                            summary=_json(r["summary"])) for r in runs]),
            "twin": public({"case_key": twin["case_key"], "expected_answer": twin["expected_answer"],
                            "evaluation": twin["evaluation"]}) if twin else None}


async def propose_from_monitor(*, batch: Mapping[str, Any], mode_id: str, turn_ids: Sequence[str]) -> list[str]:
    """Up to PROPOSE_CASES_MAX flagged answers become proposed cases (spec §4.2 step 7). No twin yet: confirming
    is where a person writes the expected answer (R9). Answers that already have a live case are skipped."""
    ids: list[str] = []
    for turn_id in list(turn_ids)[:monitor.PROPOSE_CASES_MAX]:
        turn = await _turn_for_case(None, turn_id)
        if turn is None or not replayable(turn["question"], turn["channel"], turn["session_id"]):
            continue
        org = str(turn["organization_id"])
        if await _live_case_for_turn(org, turn_id):
            continue
        loaded = (await judges.load_judge_turns([turn_id])).get(turn_id)
        inp = case_from_turn(loaded if loaded is not None else {"question": turn["question"], "prior": []}, turn)
        case = _new_case(org, new_case_key(), status="proposed", source_type="monitor", inp=inp, mode_id=mode_id,
                         source_turn_id=turn_id, source_batch_id=str(batch["id"]),
                         expected={"assertions": [], "checks": [], "judges": [{"mode_id": str(mode_id)}],
                                   "golden_expected": True})
        await _insert_case(case, None, actor_id=str(batch["started_by"]) if batch.get("started_by") else None,
                           actor_email=batch.get("started_by_email"))
        ids.append(case["id"])
    return ids
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_eval_cases.py tests/test_trace_lab_watch_sql.py`
Expected: eval-case unit tests pass (19) and `test_case_twin_lands_in_the_regression_set` passes on the scratch DB.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/eval_cases.py`, `DA/tests/test_trace_lab_eval_cases.py`, `DA/tests/test_trace_lab_watch_sql.py`.

---

### Task 14: Eval runs through M3's golden runner — baseline, repeat-n, repeat from an answer, release check

**Files:**
- Modify: `DA/src/trace_lab/eval_cases.py` (append)
- Test: `DA/tests/test_trace_lab_eval_cases.py` (append)

M3's `src/api/routes/trace_lab_golden.py` is not edited: its `GoldenEstimate` / `GoldenRunStart` already take `k` 1–20 and M6 builds `GoldenRunStart(set_id, k, case_keys, purpose, usd_cap, context_default, note)` as it stands.

**Interfaces:**
- Consumes: `runner.start_run(*, org, principal, authorization, acting_org, body, orchestrator)`, `runner.max_k()`, `runner.HARD_MAX_K` and `GoldenRunStart` (M3); Task 13 store and API.
- Produces: `RunContext(org, principal, authorization, acting_org, orchestrator)` (frozen dataclass); `START_GOLDEN_RUN: Callable[..., Awaitable[dict]]` (= `_start_golden_run(*, ctx, set_id, case_keys, k, purpose, usd_cap, note) -> run dict with "id"`; tests replace it); `start_baseline(ctx, case_id, *, usd_cap=None) -> {"golden_run"}`; `start_repeat(ctx, case_id, *, n, usd_cap=None) -> {"golden_run", "case"}`; `repeat_turn(ctx, turn_id, *, n, mode_ids, expected_answer=None, evaluation="objective", usd_cap=None, actor_id, actor_email) -> {"golden_run", "case"}`; `start_release_check(ctx, *, k=5, include_capability=True, usd_cap=None) -> {"golden_run", "cases": {"regression", "capability"}}`; store `_twin_ref(case) -> (set_id, twin case_key)`, `_run_open(golden_run_id) -> bool`, `_add_case_runs(rows)`, `_begin_baseline(case_id, golden_run_id)`, `_active_cases(org)`, `_check_reps(k)` (422 `too_many_reps` when `k > runner.max_k()`). Per-purpose limits stay in M6's own code and request models, clamped by M3's cap: baseline `BASELINE_K = 5`, release `RELEASE_MAX_K = 10`, repeat `REPEAT_MAX_N = runner.HARD_MAX_K` (20); each `start_*` refuses `k > runner.max_k()` before it calls `START_GOLDEN_RUN`.

- [ ] **Step 0: Confirm the M3 anchors** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/backend/cafm-connector-service-final/svc-deepagents
grep -n "k: int = Field(1, ge=1, le=20)" src/api/routes/trace_lab_golden.py
grep -n "class GoldenRunStart\|class GoldenEstimate" src/api/routes/trace_lab_golden.py
grep -n "^HARD_MAX_K = 20\|^def max_k\|^def register_grade_handler" src/trace_lab/runner.py
```

Expected: one `k:` line (inside `GoldenEstimate`, already 1–20), both class lines, and the three runner lines. If `le=5` is still there, M3 has not landed its caps: stop and report; never widen M3's model from here.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_eval_cases.py`)

```python
CTX = eval_cases.RunContext(org=ORG_A, principal=SimpleNamespace(role="admin"), authorization="Bearer token",
                            acting_org=None, orchestrator=object())


def _case_row(cid, *, status="draft", kind="unclassified", key=None, version=1, twin="g-1", rate=None):
    return {"id": cid, "organization_id": ORG_A, "case_key": key or "e-7f3a19c2", "version": version,
            "status": status, "kind": kind, "baseline_pass_rate": rate, "baseline_run_id": None, "mode_id": M1,
            "golden_case_id": twin, "rev": 1, "source_turn_id": "turn-1",
            "input": {"question": "How many work orders in July 2026?", "followups": [], "page_context": ""}}


def _stub_runs(monkeypatch, cases, *, run_open=False):
    log = SimpleNamespace(started=[], case_runs=[], baselines=[])

    async def start(**kw):
        log.started.append(kw)
        return {"id": f"run-{len(log.started)}", "label": "run-20261015T101500Z-0000000a", "status": "running"}

    async def case(org, cid):
        return dict(cases[cid]) if cid in cases else None

    async def twin_ref(c):
        if not c.get("golden_case_id"):
            raise WatchError(409, "expected_answer_required", "no twin")
        return "set-regression", eval_cases.twin_key(c["case_key"], c["version"])

    async def is_open(run_id):
        return run_open

    async def add_runs(rows):
        log.case_runs.extend(rows)

    async def begin(case_id, run_id):
        log.baselines.append((case_id, run_id))

    async def active(org):
        return [dict(c) for c in cases.values() if c["status"] == "active"]

    monkeypatch.setattr(eval_cases, "START_GOLDEN_RUN", start)
    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_twin_ref", twin_ref)
    monkeypatch.setattr(eval_cases, "_run_open", is_open)
    monkeypatch.setattr(eval_cases, "_add_case_runs", add_runs)
    monkeypatch.setattr(eval_cases, "_begin_baseline", begin)
    monkeypatch.setattr(eval_cases, "_active_cases", active)
    return log


async def test_baseline_runs_five_reps_on_the_twin(monkeypatch):
    log = _stub_runs(monkeypatch, {"c1": _case_row("c1")})
    out = await eval_cases.start_baseline(CTX, "c1")
    kw = log.started[0]
    assert (kw["k"], kw["purpose"], kw["case_keys"], kw["set_id"]) == (5, "baseline", ["e-7f3a19c2.v1"], "set-regression")
    assert kw["ctx"] is CTX and log.baselines == [("c1", "run-1")] and out["golden_run"]["id"] == "run-1"


async def test_baseline_refuses_proposed_classified_and_running_cases(monkeypatch):
    cases = {"p": _case_row("p", status="proposed", twin=None), "a": _case_row("a", status="active", kind="regression"),
             "b": _case_row("b", status="baselining")}
    log = _stub_runs(monkeypatch, cases, run_open=True)
    for cid, reason in (("p", "not_confirmed"), ("a", "already_classified"), ("b", "baseline_running")):
        with pytest.raises(WatchError) as e:
            await eval_cases.start_baseline(CTX, cid)
        assert e.value.status == 409 and e.value.reason == reason
    assert log.started == []


async def test_write_intent_turn_not_replayable(monkeypatch):
    log = _stub_runs(monkeypatch, {})
    turns = {"t-write": _turn_row("t-write", question="Raise a work order for the boiler in Plant Room 2"),
             "t-upload": _turn_row("t-upload", channel="upload"), "t-ok": _turn_row("t-ok")}

    async def turn(org, tid):
        return turns.get(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    for tid in ("t-write", "t-upload"):
        with pytest.raises(WatchError) as e:
            await eval_cases.repeat_turn(CTX, tid, n=10, mode_ids=[M1], expected_answer="x", actor_id=ACTOR,
                                         actor_email=None)
        assert e.value.status == 409 and e.value.reason == "turn_not_replayable"
    with pytest.raises(WatchError) as e:
        await eval_cases.repeat_turn(CTX, "t-ok", n=10, mode_ids=["not-in-use"], expected_answer="x", actor_id=ACTOR,
                                     actor_email=None)
    assert e.value.reason == "judge_not_frozen"
    assert log.started == []


async def test_repeat_turn_creates_a_case_with_a_twin_then_repeats_it(monkeypatch):
    cases = {}
    log = _stub_runs(monkeypatch, cases)

    async def turn(org, tid):
        return _turn_row(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def no_case(org, tid):
        return None

    async def unconfirmed(org, tid, mode):
        return False

    async def load(ids):
        return {t: SimpleNamespace(question="How many work orders were raised at Bishopsgate Tower in July 2026?",
                                   prior=[]) for t in ids}

    async def insert(case, twin, **kw):
        assert twin["expected_answer"] == "There were 786."
        cases[case["id"]] = dict(case, golden_case_id="g-new")
        return case

    for name, fn in {"_turn_for_case": turn, "judges_in_use": in_use, "_live_case_for_turn": no_case,
                     "_failure_confirmed": unconfirmed, "_insert_case": insert}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    out = await eval_cases.repeat_turn(CTX, "turn-9", n=10, mode_ids=[M1], expected_answer="There were 786.",
                                       actor_id=ACTOR, actor_email="reviewer@example.com")
    (case,) = cases.values()
    assert case["status"] == "proposed" and case["expected"]["judges"] == [{"mode_id": M1}]
    assert log.started[0]["k"] == 10 and log.started[0]["purpose"] == "repeat"
    assert log.case_runs == [{"case_id": case["id"], "run_id": "run-1", "purpose": "repeat", "k": 10}]
    assert out["golden_run"]["id"] == "run-1" and out["case"]["case_key"] == case["case_key"]
    with pytest.raises(WatchError) as e:
        await eval_cases.start_repeat(CTX, case["id"], n=21)
    assert e.value.status == 422


async def test_release_check_runs_every_active_case_once_with_k(monkeypatch):
    cases = {"r1": _case_row("r1", status="active", kind="regression", key="e-00000001"),
             "r2": _case_row("r2", status="active", kind="regression", key="e-00000002"),
             "c1": _case_row("c1", status="active", kind="capability", key="e-00000003", rate=0.4),
             "d1": _case_row("d1", status="draft", key="e-00000004")}
    log = _stub_runs(monkeypatch, cases)
    out = await eval_cases.start_release_check(CTX, k=5, include_capability=True)
    kw = log.started[0]
    assert kw["purpose"] == "release_check" and kw["k"] == 5
    assert kw["case_keys"] == ["e-00000001.v1", "e-00000002.v1", "e-00000003.v1"]
    assert [r["case_id"] for r in log.case_runs] == ["r1", "r2", "c1"] and out["cases"] == {"regression": 2, "capability": 1}
    await eval_cases.start_release_check(CTX, k=3, include_capability=False)
    assert log.started[1]["case_keys"] == ["e-00000001.v1", "e-00000002.v1"]
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=11)
    assert e.value.status == 422
    for c in cases.values():
        c["status"] = "draft"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5)
    assert e.value.status == 409 and e.value.reason == "no_active_cases"


def test_m6_limits_per_purpose_sit_inside_m3s_model_and_its_cap():
    from pydantic import ValidationError

    from src.api.routes.trace_lab_golden import GoldenRunStart

    assert GoldenRunStart(purpose="repeat", k=eval_cases.REPEAT_MAX_N).k == 20    # M3's model already takes 1-20
    with pytest.raises(ValidationError):
        GoldenRunStart(purpose="repeat", k=21)
    assert (eval_cases.BASELINE_K, eval_cases.RELEASE_MAX_K, eval_cases.REPEAT_MAX_N) == (5, 10, eval_cases.runner.HARD_MAX_K)


async def test_reps_above_the_deployment_cap_are_refused_before_any_run_starts(monkeypatch):
    cases = {"c1": _case_row("c1"), "r1": _case_row("r1", status="active", kind="regression", key="e-00000001")}
    log = _stub_runs(monkeypatch, cases)
    monkeypatch.setattr(eval_cases.runner, "max_k", lambda: 3)            # TRACE_LAB_GOLDEN_MAX_K=3 on this deployment
    with pytest.raises(WatchError) as e:
        await eval_cases.start_baseline(CTX, "c1")                        # a baseline is 5 runs
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_repeat(CTX, "c1", n=10)
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5)
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    assert log.started == []
    out = await eval_cases.start_release_check(CTX, k=3)                  # inside the cap
    assert log.started[0]["k"] == 3 and out["cases"]["regression"] == 1
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_eval_cases.py`
Expected: `AttributeError: module 'src.trace_lab.eval_cases' has no attribute 'RunContext'` at import of the new block.

- [ ] **Step 3: Implement**

Append to `DA/src/trace_lab/eval_cases.py`:

```python
# ---- runs through M3's golden runner (J8; spec §5.3-§5.7) -----------------------------------------------

@dataclass(frozen=True)
class RunContext:
    """What M3's start_run needs from the request: the caller's bearer (runs are browser-stepped, D9) and the
    app's orchestrator. acting_org is the company for a superadmin acting for it, else None."""
    org: str
    principal: Any
    authorization: str | None
    acting_org: str | None
    orchestrator: Any


async def _start_golden_run(*, ctx: RunContext, set_id: str, case_keys: Sequence[str], k: int, purpose: str,
                            usd_cap: float | None, note: str) -> dict:
    from ..api.routes.trace_lab_golden import GoldenRunStart
    from . import runner

    body = GoldenRunStart(set_id=set_id, k=int(k), case_keys=list(case_keys), purpose=purpose, usd_cap=usd_cap,
                          context_default="", note=note[:500])
    out = await runner.start_run(org=ctx.org, principal=ctx.principal, authorization=ctx.authorization,
                                 acting_org=ctx.acting_org, body=body, orchestrator=ctx.orchestrator)
    run = out.get("run", out) if isinstance(out, Mapping) else out
    return dict(run)


START_GOLDEN_RUN: Callable[..., Awaitable[dict]] = _start_golden_run   # tests replace this: never a real run in tests


def _check_reps(k: int) -> None:
    """M3 owns the rep cap (TRACE_LAB_GOLDEN_MAX_K, never above runner.HARD_MAX_K): refuse before a run starts.
    M6's per-purpose limits (baseline 5, release up to 10, repeat up to 20) are checked by each start_* below."""
    cap = runner.max_k()
    if int(k) > cap:
        raise WatchError(422, "too_many_reps", f"This deployment allows at most {cap} runs per case.")


async def _twin_ref(case: Mapping[str, Any]) -> tuple[str, str]:
    twin = await _twin(case.get("golden_case_id"))
    if twin is None:
        raise WatchError(409, "expected_answer_required",
                         "Confirm the case and write its expected answer before it can run.")
    return str(twin["set_id"]), str(twin["case_key"])


async def _run_open(golden_run_id: Any) -> bool:
    if not golden_run_id:
        return False
    async with db.engine().connect() as conn:
        status = (await conn.execute(text("SELECT j.status FROM plenum_tracelab.golden_runs r JOIN plenum_tracelab.jobs j "
                                          "ON j.id = r.job_id WHERE r.id = CAST(:id AS uuid)"),
                                     {"id": str(golden_run_id)})).scalar()
    return status in ("queued", "running", "paused", "interrupted")


async def _add_case_runs(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    async with db.engine().begin() as conn:
        await conn.execute(text("INSERT INTO plenum_tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (CAST(:id AS uuid), CAST(:case_id AS uuid), CAST(:run_id AS uuid), :purpose, :k)"),
                           [{"id": str(uuid.uuid4()), **dict(r)} for r in rows])


async def _begin_baseline(case_id: str, golden_run_id: str) -> None:
    async with db.engine().begin() as conn:
        await conn.execute(text("INSERT INTO plenum_tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (CAST(:id AS uuid), CAST(:case_id AS uuid), CAST(:run_id AS uuid), 'baseline', :k)"),
                           {"id": str(uuid.uuid4()), "case_id": case_id, "run_id": golden_run_id, "k": BASELINE_K})
        await conn.execute(text("UPDATE plenum_tracelab.eval_cases SET status = 'baselining', "
                                "baseline_run_id = CAST(:run_id AS uuid), rev = rev + 1, updated_at = now() "
                                "WHERE id = CAST(:case_id AS uuid) AND status IN ('draft', 'baselining')"),
                           {"case_id": case_id, "run_id": golden_run_id})


async def _active_cases(org: str) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text(_CASE_SELECT + " WHERE c.organization_id = CAST(:org AS uuid) "
                                        "AND c.status = 'active' ORDER BY c.case_key"), {"org": org})).mappings().all()
    return [dict(r) for r in rows]


async def start_baseline(ctx: RunContext, case_id: str, *, usd_cap: float | None = None) -> dict:
    """Spec §5.4: a golden run with purpose 'baseline' and k = 5 on the case's twin."""
    case = await _case(ctx.org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] == "proposed":
        raise WatchError(409, "not_confirmed", "Confirm the failure and write the expected answer first.")
    if case["status"] in ("retired", "superseded"):
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    if case["status"] == "active" or case["kind"] != "unclassified":
        raise WatchError(409, "already_classified", "This case already has a baseline. Edit it to start a new version.")
    if case["status"] == "baselining" and await _run_open(case.get("baseline_run_id")):
        raise WatchError(409, "baseline_running", "The baseline for this case is still running; resume it from Runs.")
    _check_reps(BASELINE_K)
    set_id, key = await _twin_ref(case)
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_id, case_keys=[key], k=BASELINE_K, purpose="baseline",
                                 usd_cap=usd_cap, note=f"Baseline for case {case['case_key']} v{case['version']}")
    await _begin_baseline(str(case["id"]), str(run["id"]))
    return {"golden_run": public(run)}


async def start_repeat(ctx: RunContext, case_id: str, *, n: int, usd_cap: float | None = None) -> dict:
    """Spec §5.7: one golden run with purpose 'repeat' and k = n on the case's twin."""
    if not REPEAT_MIN_N <= int(n) <= REPEAT_MAX_N:
        raise WatchError(422, "bad_n", f"Repeat an answer between {REPEAT_MIN_N} and {REPEAT_MAX_N} times.")
    _check_reps(n)
    case = await _case(ctx.org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] not in LIVE_STATUSES:
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    set_id, key = await _twin_ref(case)
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_id, case_keys=[key], k=int(n), purpose="repeat", usd_cap=usd_cap,
                                 note=f"Repeat {int(n)} times: case {case['case_key']} v{case['version']}")
    await _add_case_runs([{"case_id": str(case["id"]), "run_id": str(run["id"]), "purpose": "repeat", "k": int(n)}])
    return {"golden_run": public(run), "case": case_public(case)}


async def repeat_turn(ctx: RunContext, turn_id: str, *, n: int, mode_ids: Sequence[str],
                      expected_answer: str | None = None, evaluation: str = "objective", usd_cap: float | None = None,
                      actor_id: str | None, actor_email: str | None) -> dict:
    """Repeat-n of one answer: reuse its live case, or make one (draft when the failure is confirmed, else
    proposed) with a twin from the written expected answer (R9), then start the repeat run."""
    turn = await _turn_for_case(ctx.org, turn_id)
    if turn is None:
        raise WatchError(404, "turn_not_in_scope", "That answer is not in this company.")
    if not replayable(turn["question"], turn["channel"], turn["session_id"]):
        raise WatchError(409, "turn_not_replayable", "Only chat answers that ask for no change can be repeated.")
    in_use = {j["mode_id"]: j for j in await judges_in_use(None)}
    modes = list(dict.fromkeys(str(m) for m in mode_ids or []))
    if not modes or any(m not in in_use for m in modes):
        raise WatchError(409, "judge_not_frozen", "Every failure mode named must have a judge in use.")
    case = await _live_case_for_turn(ctx.org, turn_id)
    if case is None:
        answer = _require_answer(expected_answer)
        confirmed = await _failure_confirmed(ctx.org, turn_id, None)
        loaded = (await judges.load_judge_turns([turn_id])).get(turn_id)
        inp = case_from_turn(loaded if loaded is not None else {"question": turn["question"], "prior": []}, turn)
        new = _new_case(ctx.org, new_case_key(), status="draft" if confirmed else "proposed", source_type="live_turn",
                        inp=inp, mode_id=modes[0], source_turn_id=turn_id,
                        expected={"assertions": [], "checks": [], "judges": [{"mode_id": m} for m in modes],
                                  "golden_expected": True})
        await _insert_case(new, _twin_from(new, expected_answer=answer, evaluation=evaluation, values=[],
                                           source=f"Eval case {new['case_key']} v1, repeated from an answer"),
                           actor_id=actor_id, actor_email=actor_email)
        case_id = new["id"]
    else:
        case_id = str(case["id"])
        if not case.get("golden_case_id"):
            answer = _require_answer(expected_answer)
            current = dict(case, input=_json(case["input"]))
            await _with_twin(_twin_from(current, expected_answer=answer, evaluation=evaluation, values=[],
                                        source=f"Eval case {case['case_key']} v{case['version']}, repeated from an answer"),
                             actor_id=actor_id, actor_email=actor_email,
                             update_sql="UPDATE plenum_tracelab.eval_cases SET golden_case_id = CAST(:g AS uuid), "
                                        "rev = rev + 1, updated_at = now() WHERE id = CAST(:id AS uuid) "
                                        "AND golden_case_id IS NULL",
                             params={"id": case_id})
    return await start_repeat(ctx, case_id, n=n, usd_cap=usd_cap)


async def start_release_check(ctx: RunContext, *, k: int = RELEASE_DEFAULT_K, include_capability: bool = True,
                              usd_cap: float | None = None) -> dict:
    """Spec §5.6: one golden run, purpose 'release_check', over the company's active cases (all twins live in its
    Regression cases set). It measures the deployment serving this page, read-only."""
    if not 1 <= int(k) <= RELEASE_MAX_K:
        raise WatchError(422, "bad_k", f"A release check runs each case 1 to {RELEASE_MAX_K} times.")
    _check_reps(k)
    cases = await _active_cases(ctx.org)
    regression = [c for c in cases if c["kind"] == "regression"]
    chosen = regression + ([c for c in cases if c["kind"] == "capability"] if include_capability else [])
    if not chosen:
        raise WatchError(409, "no_active_cases", "No case has a baseline yet, so there is nothing to run.")
    refs = [await _twin_ref(c) for c in chosen]
    set_ids = {s for s, _ in refs}
    if len(set_ids) != 1:
        raise WatchError(409, "cases_in_several_sets", "These cases do not share one Regression cases set.")
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_ids.pop(), case_keys=[key for _, key in refs], k=int(k),
                                 purpose="release_check", usd_cap=usd_cap,
                                 note=f"Release check: {len(regression)} regression and "
                                      f"{len(chosen) - len(regression)} capability cases")
    await _add_case_runs([{"case_id": str(c["id"]), "run_id": str(run["id"]), "purpose": "release_check", "k": int(k)}
                          for c in chosen])
    return {"golden_run": public(run), "cases": {"regression": len(regression), "capability": len(chosen) - len(regression)}}
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_eval_cases.py tests/test_trace_lab_m6_interfaces.py`
Expected: all pass (26 eval-case tests; the interface tests still pass).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/eval_cases.py`, `DA/tests/test_trace_lab_eval_cases.py` (no change to M3's route module).

---

### Task 15: Grading eval reps — on read (free) and by "Grade now" (a capped job through M3's grading worker)

**Files:**
- Modify: `DA/src/trace_lab/eval_cases.py` (append)
- Test: `DA/tests/test_trace_lab_eval_cases.py` (append)

**Interfaces:**
- Consumes: `checks.run_check`, `checks.CHECKS` (M5); `judges.load_judge_turns`, `judges.judge_input`, `judges.ASK` (M5); `monitor._predictions` (M5's cache), `monitor._save`, `monitor.prediction_args` (the prediction goes through `judges.write_prediction`), `monitor.call_usd` (Tasks 5, 7); `jobs.create`, `jobs.start`, `jobs.reserve`, `jobs.CapReached`, `jobs.JobContext` (M0); M3's `runner.register_grade_handler(name, fn)` (R6: one worker per job kind, so "Grade now" is a `golden_grade` job handled inside M3's worker through its `params["handler"]` table); tables `golden_runs`, `golden_results`, `golden_cases`, `eval_case_runs`, `eval_cases`, `modes`, `jobs`.
- Produces: `refresh_run(org, golden_run_id, *, allow_model_calls, job_id=None, only=None) -> {"run", "case_runs"}`; `grade_rep(result_id) -> dict | None` (grades one rep; never raises; reads and "Grade now" grade through `refresh_run` and nothing in M3's runner calls it); `start_grading(org, golden_run_id, *, usd_cap, actor_id, actor_email) -> {"job"}` (a `golden_grade` job, scope `golden_grade:eval:<run>`, params `{"handler": "eval_cases", "golden_run_id", "organization_id"}`, cap `TRACE_LAB_EVAL_GRADING_USD_CAP` = $2, started with `jobs.start`); `async grade_job(ctx) -> "done" | "capped"` (the handler, registered at import as `runner.register_grade_handler("eval_cases", grade_job)`); `eval_run(org, golden_run_id)` (read: `allow_model_calls=False`); store `_golden_run`, `_case_runs`, `_finals`, `_result_row`, `_save_case_run`, `_classify`.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_eval_cases.py`)

```python
from decimal import Decimal  # noqa: E402


def _grading_setup(monkeypatch, *, purpose="release_check", k=1, finals=None, expected=None, kind="regression",
                   case_status="active"):
    run = {"id": "run-1", "label": "run-20261015T101500Z-0000000a", "purpose": purpose, "k": k, "job_id": "job-run",
           "job_status": "running", "usd_spent": Decimal("0.5"), "created_at": None, "finished_at": None,
           "fingerprint": {}, "organization_id": ORG_A}
    cr = {"id": "cr-1", "eval_case_id": "c1", "golden_run_id": "run-1", "purpose": purpose, "k": k, "reps": [],
          "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "case_status": case_status, "kind": kind,
          "baseline_pass_rate": None, "baseline_run_id": "run-1", "mode_id": M1, "mode_name": "wrong_period",
          "twin_key": "e-7f3a19c2.v1",
          "expected": expected or {"assertions": [], "checks": [], "judges": [{"mode_id": M1}], "golden_expected": False}}
    s = SimpleNamespace(saved=[], classified=[], asked=[], reserved=[], preds=[])
    finals = finals if finals is not None else {
        ("e-7f3a19c2.v1", 1): {"turn_id": "turn-g1", "status": "completed", "outcome": "pass", "graded_at": None}}

    async def golden_run(org, rid):
        return dict(run)

    async def case_runs(rid):
        return [dict(cr)]

    async def get_finals(rid):
        return finals

    async def in_use(mode_ids=None):
        return [{"mode_id": M1, "mode_name": "wrong_period", "judge_id": "j1", "judge_key": "wrong_period-v2",
                 "kind": "llm", "model": "claude-sonnet-5", "prompt_text": "prompt", "prompt_hash": "abc123abc123",
                 "check_key": None, "check_version": None}]

    async def load(ids):                                              # M5's loader: {turn_id: NormalizedTurn}
        return {t: SimpleNamespace(turn_id=t, question="q", tool_calls=3) for t in ids}

    async def no_predictions(ph, ids):
        return {}

    async def save_run(case_run_id, reps, summary, graders, repeat):
        s.saved.append({"reps": reps, **summary, "graders": graders, "repeat": repeat})

    async def classify(case_id, cls, rid):
        s.classified.append((case_id, cls, rid))

    async def ask(model, prompt, judge_text):
        s.asked.append(judge_text)
        return SimpleNamespace(status="decided", failure_present=1, critique="Quotes June.", usd=0.02, error=None)

    async def reserve(job_id, usd):
        s.reserved.append(job_id)

    async def save(job_id, *, verdicts, prediction=None, usd=None, released=Decimal(0), done_delta=0, progress=None):
        s.preds.append((job_id, prediction, usd))

    for name, fn in {"_golden_run": golden_run, "_case_runs": case_runs, "_finals": get_finals,
                     "judges_in_use": in_use, "_save_case_run": save_run, "_classify": classify}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases.monitor, "_predictions", no_predictions)
    monkeypatch.setattr(eval_cases.monitor, "_save", save)
    monkeypatch.setattr(eval_cases.judges, "ASK", ask)
    monkeypatch.setattr(eval_cases.judges, "judge_input",
                        lambda t: SimpleNamespace(text="evidence", chars=100, truncated=False, sha="sha"))
    monkeypatch.setattr(eval_cases.jobs, "reserve", reserve)
    return s


async def test_reading_an_eval_run_never_calls_a_judge(monkeypatch):
    s = _grading_setup(monkeypatch)
    out = await eval_cases.eval_run(ORG_A, "run-1")
    assert s.asked == [] and s.reserved == [] and s.preds == []
    assert s.saved[0]["reps"][0]["status"] == "ungraded"
    assert s.saved[0]["decision"] == "incomplete" and s.saved[0]["reason"].startswith("1 ungraded")
    assert out["case_runs"][0]["reps"][0]["status"] == "ungraded"


async def test_grading_calls_the_judge_and_charges_the_job_it_runs_under(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    assert s.asked == ["evidence"] and s.reserved == ["job-grade"]
    job_id, prediction, usd = s.preds[0]
    assert job_id == "job-grade" and prediction["judge"]["prompt_hash"] == "abc123abc123"
    assert prediction["organization_id"] == ORG_A and prediction["turn_id"] == "turn-g1"     # judges.write_prediction's arguments
    assert prediction["pred"]["failure_present"] == 1 and usd == Decimal("0.02")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "failed" and rep["failures"] == ["judge:wrong_period"] and s.saved[0]["decision"] == "block"
    assert s.saved[0]["graders"]["judges"] == [{"mode_id": M1, "judge_id": "j1", "judge_key": "wrong_period-v2",
                                                "prompt_hash": "abc123abc123"}]


async def test_baseline_refresh_classifies_the_case(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "status": "completed", "outcome": "pass",
                                     "graded_at": None} for r in range(1, 6)}
    s = _grading_setup(monkeypatch, purpose="baseline", k=5, finals=finals, kind="unclassified",
                       case_status="baselining",
                       expected={"assertions": [], "checks": [{"check": "must_contain", "text": "786"}], "judges": [],
                                 "golden_expected": True})
    monkeypatch.setattr(eval_cases.checks, "run_check", lambda check, turn, params: eval_cases.checks.CheckResult("pass"))
    await eval_cases.eval_run(ORG_A, "run-1")
    assert [r["status"] for r in s.saved[0]["reps"]] == ["passed"] * 5 and s.asked == []
    assert s.classified == [("c1", {"kind": "regression", "baseline_pass_rate": None}, "run-1")]


async def test_grade_rep_refreshes_only_its_rep(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "status": "completed", "outcome": "pass",
                                     "graded_at": None} for r in (1, 2)}
    s = _grading_setup(monkeypatch, k=2, finals=finals,
                       expected={"assertions": [], "checks": [], "judges": [], "golden_expected": True})

    async def result_row(result_id):
        return {"run_id": "run-1", "organization_id": ORG_A, "case_key": "e-7f3a19c2.v1", "rep": 2}

    monkeypatch.setattr(eval_cases, "_result_row", result_row)
    await eval_cases.grade_rep("11111111-1111-4111-8111-000000000002")
    assert [r["status"] for r in s.saved[0]["reps"]] == ["missing", "passed"]


async def test_grade_now_is_a_golden_grade_job_handled_by_the_eval_cases_handler(monkeypatch):
    created, started = {}, []

    async def golden_run(org, rid):
        return {"id": rid}

    async def create(**kw):
        created.update(kw)
        return {"id": "job-grade"}

    async def start(job_id):
        started.append(job_id)

    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases.jobs, "create", create)
    monkeypatch.setattr(eval_cases.jobs, "start", start)
    out = await eval_cases.start_grading(ORG_A, "run-1", usd_cap=None, actor_id=ACTOR,
                                         actor_email="reviewer@example.com")
    assert created["kind"] == "golden_grade" and created["scope_key"] == "golden_grade:eval:run-1"
    assert created["organization_id"] == ORG_A and created["usd_cap"] == Decimal(str(eval_cases.eval_grading_usd_cap()))
    assert created["params"] == {"handler": "eval_cases", "golden_run_id": "run-1", "organization_id": ORG_A}
    assert started == ["job-grade"] and out["job"]["id"] == "job-grade"      # run by M3's one golden_grade worker
    assert eval_cases.runner.GRADE_HANDLERS["eval_cases"] is eval_cases.grade_job


async def test_the_grade_handler_returns_capped_while_reps_stay_ungraded_and_done_after(monkeypatch):
    seen = {}

    async def refresh(org, rid, *, allow_model_calls, job_id=None, only=None):
        seen.update(org=org, rid=rid, allow=allow_model_calls, job=job_id)
        return {"run": {}, "case_runs": [{"reps": [{"status": "ungraded"}, {"status": "passed"}]}]}

    async def clean(org, rid, **kw):
        return {"run": {}, "case_runs": [{"reps": [{"status": "passed"}]}]}

    monkeypatch.setattr(eval_cases, "refresh_run", refresh)
    params = {"handler": "eval_cases", "golden_run_id": "run-1", "organization_id": ORG_A}
    ctx = SimpleNamespace(job_id="job-grade", job={"params": params}, stopping=lambda: False)
    assert await eval_cases.grade_job(ctx) == "capped"
    assert seen == {"org": ORG_A, "rid": "run-1", "allow": True, "job": "job-grade"}   # charged to the job it runs under
    monkeypatch.setattr(eval_cases, "refresh_run", clean)
    ctx.job["params"] = json.dumps(params)                                              # a stored job may carry params as text
    assert await eval_cases.grade_job(ctx) == "done"
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_eval_cases.py`
Expected: `AttributeError: … has no attribute '_golden_run'` (monkeypatch refuses a missing attribute).

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/eval_cases.py`)

```python
# ---- grading reps (spec §5.3) ----------------------------------------------------------------------------

async def _golden_run(org: str, golden_run_id: str) -> dict | None:
    if not _is_uuid(golden_run_id):
        return None
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("""
SELECT r.id, r.label, r.purpose, r.k, r.set_id, r.case_keys, r.job_id, r.fingerprint, r.created_at, r.finished_at,
       r.organization_id, j.status AS job_status, j.usd_spent
  FROM plenum_tracelab.golden_runs r JOIN plenum_tracelab.jobs j ON j.id = r.job_id
 WHERE r.id = CAST(:id AS uuid) AND r.organization_id = CAST(:org AS uuid)"""),
            {"id": str(golden_run_id), "org": org})).mappings().first()
    return dict(row, fingerprint=_json(row["fingerprint"])) if row else None


async def _case_runs(golden_run_id: str) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.*, c.organization_id, c.case_key, c.version, c.status AS case_status, c.kind, c.baseline_pass_rate,
       c.baseline_run_id, c.expected, c.mode_id, m.name AS mode_name, gc.case_key AS twin_key
  FROM plenum_tracelab.eval_case_runs cr
  JOIN plenum_tracelab.eval_cases c ON c.id = cr.eval_case_id
  LEFT JOIN plenum_tracelab.modes m ON m.id = c.mode_id
  JOIN plenum_tracelab.golden_cases gc ON gc.id = c.golden_case_id
 WHERE cr.golden_run_id = CAST(:run AS uuid)
 ORDER BY c.case_key"""), {"run": str(golden_run_id)})).mappings().all()
    return [dict(r) for r in rows]


async def _finals(golden_run_id: str) -> dict[tuple[str, int], dict]:
    """The final row of each (case, rep): the highest attempt (M3 §3.10)."""
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT DISTINCT ON (case_key, rep) id, case_key, rep, attempt, turn_id, status, outcome, graded_at
  FROM plenum_tracelab.golden_results
 WHERE run_id = CAST(:run AS uuid) AND is_final
 ORDER BY case_key, rep, attempt DESC"""), {"run": str(golden_run_id)})).mappings().all()
    return {(r["case_key"], int(r["rep"])): dict(r) for r in rows}


async def _result_row(result_id: str) -> dict | None:
    async with db.engine().connect() as conn:
        row = (await conn.execute(text("SELECT run_id, organization_id, case_key, rep FROM plenum_tracelab.golden_results "
                                       "WHERE id = CAST(:id AS uuid)"), {"id": result_id})).mappings().first()
    return dict(row) if row else None


async def _save_case_run(case_run_id: str, reps: Sequence[Mapping[str, Any]], summary: Mapping[str, Any],
                         graders: Mapping[str, Any], repeat: Mapping[str, Any] | None) -> None:
    def num(v: Any) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    async with db.engine().begin() as conn:
        await conn.execute(text("""
UPDATE plenum_tracelab.eval_case_runs
   SET reps = CAST(:reps AS jsonb), n = :n, passes = :passes, degraded = :degraded, pending = :pending,
       graders = CAST(:graders AS jsonb), pass_at_1 = :p1, pass_at_3 = :p3, pass_at_5 = :p5, pass_hat_k = :phk,
       summary = CAST(:summary AS jsonb), decision = :decision, reason = :reason, computed_at = now()
 WHERE id = CAST(:id AS uuid)"""),
            {"id": case_run_id, "reps": json.dumps(public(list(reps))), "n": summary["n"], "passes": summary["passes"],
             "degraded": summary["degraded"], "pending": summary["pending"] + summary["missing"],
             "graders": json.dumps(public(dict(graders))), "p1": num(summary["pass_at_1"]),
             "p3": num(summary["pass_at_3"]), "p5": num(summary["pass_at_5"]), "phk": num(summary["pass_hat_k"]),
             "summary": json.dumps(public(dict(repeat))) if repeat is not None else None,
             "decision": summary["decision"], "reason": summary["reason"][:1000]})


async def _classify(case_id: str, classification: Mapping[str, Any], golden_run_id: str) -> None:
    rate = classification.get("baseline_pass_rate")
    async with db.engine().begin() as conn:
        await conn.execute(text("""
UPDATE plenum_tracelab.eval_cases
   SET status = 'active', kind = :kind, baseline_pass_rate = :rate, rev = rev + 1, updated_at = now()
 WHERE id = CAST(:id AS uuid) AND status = 'baselining' AND baseline_run_id = CAST(:run AS uuid)"""),
            {"id": case_id, "kind": classification["kind"], "rate": None if rate is None else Decimal(str(rate)),
             "run": golden_run_id})
    log.info("trace_lab.eval_cases.classified", case_id=case_id, kind=classification["kind"])


async def _judge_turn(j: Mapping[str, Any], turn: Any, *, org: str, allow_model_calls: bool,
                      job_id: str | None) -> tuple[dict, Decimal]:
    """A judge in use on one rep's answer: the prediction cache first (free), a code judge runs its check (free),
    and an LLM call only when allowed - charged to job_id under its cap."""
    head = {"mode": j["mode_name"], "mode_id": j["mode_id"], "judge_key": j["judge_key"], "prompt_hash": j["prompt_hash"]}
    if j["kind"] == "code":
        r = checks.run_check(checks.CHECKS[j["check_key"]], turn, {})
        decided = r.status in ("pass", "fail")
        return {**head, "status": "decided" if decided else "error", "failure_present": r.failure_present}, Decimal(0)
    tid = str(_field(turn, "turn_id"))
    cached = (await monitor._predictions(j["prompt_hash"], [tid])).get(tid)
    if cached is not None:
        return {**head, "status": "decided", "failure_present": int(cached["failure_present"])}, Decimal(0)
    if not allow_model_calls or not job_id:
        return {**head, "status": "ungraded", "failure_present": None}, Decimal(0)
    inp = judges.judge_input(turn)
    chars = int(_field(inp, "chars", 0) or 0)
    est = monitor.call_usd(j["model"], j["prompt_text"] or "", chars) or Decimal(0)
    try:
        await jobs.reserve(job_id, est)
    except jobs.CapReached:
        return {**head, "status": "ungraded", "failure_present": None}, Decimal(0)
    try:
        reply = await judges.ASK(j["model"], j["prompt_text"], _field(inp, "text"))
    except Exception as exc:  # noqa: BLE001 - a failed call is an error, never a pass
        reply = SimpleNamespace(status="error", failure_present=None, critique="", usd=None, error=type(exc).__name__)
    prediction = monitor.prediction_args(judge=j, turn_id=tid, organization_id=org, reply=reply, inp=inp)
    pred = prediction["pred"]                                         # written through M5's judges.write_prediction
    await monitor._save(job_id, verdicts=[], usd=pred["usd"], released=est, prediction=prediction)
    return {**head, "status": pred["status"], "failure_present": pred["failure_present"]}, pred["usd"] or Decimal(0)


async def _grade(cr: Mapping[str, Any], rep: int, row: Mapping[str, Any] | None, expected: Mapping[str, Any],
                 in_use: Mapping[str, Mapping[str, Any]], *, allow_model_calls: bool, job_id: str | None) -> dict:
    golden_expected = bool(expected.get("golden_expected"))
    if row is None or row.get("outcome") is None:
        return rep_verdict(rep=rep, turn_id=None, outcome=None, status=(row or {}).get("status"), check_results=[],
                           judge_results=[], golden_expected=golden_expected)
    if row["outcome"] in ("degraded", "infra_failed"):
        return rep_verdict(rep=rep, turn_id=row["turn_id"], outcome=row["outcome"], status=row["status"],
                           check_results=[], judge_results=[], golden_expected=golden_expected)
    turn = (await judges.load_judge_turns([row["turn_id"]])).get(row["turn_id"])
    if turn is None:
        return rep_verdict(rep=rep, turn_id=None, outcome=row["outcome"], status=row["status"], check_results=[],
                           judge_results=[], golden_expected=golden_expected)
    check_results = []
    for c in expected.get("checks") or []:
        key = c["check"]
        result = checks.run_check(checks.CHECKS[key], turn, {k: v for k, v in c.items() if k != "check"})
        check_results.append({"key": key, "status": result.status, "findings": list(result.findings or ())[:3]})
    judge_results, usd = [], Decimal(0)
    for jd in expected.get("judges") or []:
        j = in_use.get(str(jd["mode_id"]))
        if j is None:
            judge_results.append({"mode": cr.get("mode_name") or str(jd["mode_id"]), "mode_id": str(jd["mode_id"]),
                                  "status": "error", "failure_present": None})
            continue
        result, spent = await _judge_turn(j, turn, org=str(cr["organization_id"]), allow_model_calls=allow_model_calls,
                                          job_id=job_id)
        judge_results.append(result)
        usd += spent
    return rep_verdict(rep=rep, turn_id=row["turn_id"], outcome=row["outcome"], status=row["status"],
                       check_results=check_results, judge_results=judge_results, golden_expected=golden_expected,
                       usd=float(usd), graded_at=datetime.now(timezone.utc).isoformat(),
                       steps=int(_field(turn, "tool_calls", 0) or 0))


def _graders(expected: Mapping[str, Any], in_use: Mapping[str, Mapping[str, Any]]) -> dict:
    return {"checks": sorted({checks.CHECKS[c["check"]].evaluator_key for c in expected.get("checks") or []
                              if c.get("check") in checks.CHECKS}),
            "judges": [{"mode_id": j["mode_id"], "judge_id": j["judge_id"], "judge_key": j["judge_key"],
                        "prompt_hash": j["prompt_hash"]}
                       for jd in expected.get("judges") or [] if (j := in_use.get(str(jd["mode_id"])))]}


def _run_public(run: Mapping[str, Any]) -> dict:
    return public({"id": run["id"], "label": run.get("label"), "purpose": run.get("purpose"), "k": run.get("k"),
                   "status": run.get("job_status"), "created_at": run.get("created_at"),
                   "finished_at": run.get("finished_at"), "usd_spent": run.get("usd_spent")})


async def refresh_run(org: str, golden_run_id: str, *, allow_model_calls: bool, job_id: str | None = None,
                      only: tuple[str, int] | None = None) -> dict:
    """Grades every rep of every eval case on this golden run that has no final verdict yet, and stores each
    case run's summary and decision. passed/failed reps are final; the rest are recomputed. Without
    allow_model_calls nothing is spent: uncached judge calls leave the rep 'ungraded'."""
    run = await _golden_run(org, golden_run_id)
    if run is None:
        raise WatchError(404, "not_found", "That run was not found.")
    case_runs = await _case_runs(golden_run_id)
    if not case_runs:
        return {"run": _run_public(run), "case_runs": []}
    finals = await _finals(golden_run_id)
    in_use = {j["mode_id"]: j for j in await judges_in_use(None)}
    spend_job = job_id or (str(run["job_id"]) if allow_model_calls else None)
    out = []
    for cr in case_runs:
        expected = _json(cr["expected"]) or {}
        previous = {int(r["rep"]): r for r in (_json(cr["reps"]) or [])}
        reps = []
        for rep in range(1, int(cr["k"]) + 1):
            old = previous.get(rep)
            if old and old.get("status") in DECIDED_REPS:
                reps.append(old)
            elif only is not None and (cr["twin_key"], rep) != only:
                reps.append(old or {"rep": rep, "status": "missing"})
            else:
                reps.append(await _grade(cr, rep, finals.get((cr["twin_key"], rep)), expected, in_use,
                                         allow_model_calls=allow_model_calls, job_id=spend_job))
        rate = float(cr["baseline_pass_rate"]) if cr.get("baseline_pass_rate") is not None else None
        summary = case_run_summary(reps, k=int(cr["k"]), purpose=cr["purpose"], kind=cr["kind"], baseline_pass_rate=rate)
        graders = _graders(expected, in_use)
        repeat = repeat_summary(reps) if cr["purpose"] == "repeat" else None
        await _save_case_run(str(cr["id"]), reps, summary, graders, repeat)
        cls = summary.get("classification")
        if (cr["purpose"] == "baseline" and cls and cr.get("case_status") == "baselining"
                and str(cr.get("baseline_run_id")) == str(golden_run_id)):
            await _classify(str(cr["eval_case_id"]), cls, str(golden_run_id))
        out.append(public({"eval_case_id": cr["eval_case_id"], "case_key": cr["case_key"], "version": cr["version"],
                           "kind": cr["kind"], "mode": cr.get("mode_name"), "purpose": cr["purpose"],
                           "baseline_pass_rate": rate, "reps": reps, **summary, "graders": graders, "summary": repeat}))
    return {"run": _run_public(run), "case_runs": out}


async def grade_rep(result_id: str) -> dict | None:
    """Grades the one eval rep a golden result belongs to (judge calls allowed, charged to the run's job). Nothing
    in M3's runner calls this: reads grade for free through eval_run and "Grade now" through grade_job. It is kept
    for an explicit after-grade hook, should one be added. It never breaks a run: every error is logged and swallowed."""
    if not _is_uuid(result_id):
        return None
    try:
        row = await _result_row(str(result_id))
        if row is None:
            return None
        return await refresh_run(str(row["organization_id"]), str(row["run_id"]), allow_model_calls=True,
                                 only=(row["case_key"], int(row["rep"])))
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.eval_cases.grade_rep_failed", result_id=str(result_id), error=str(exc)[:300])
        return None


async def eval_run(org: str, golden_run_id: str) -> dict:
    """The read behind GET /eval-runs/{id}: checks and cached predictions only - a GET never spends money."""
    return await refresh_run(org, golden_run_id, allow_model_calls=False)


async def start_grading(org: str, golden_run_id: str, *, usd_cap: float | None, actor_id: str,
                        actor_email: str | None) -> dict:
    """'Grade now' (spec §5.3): a golden_grade job (R6) that makes the judge calls reads left ungraded. M0 allows one
    worker per job kind and M3's runner.startup restarts every queued or interrupted golden_grade job with M3's
    worker, so the job names its handler (params["handler"] == "eval_cases", registered below) and is started with
    jobs.start: the same worker runs it now and after a restart."""
    if await _golden_run(org, golden_run_id) is None:
        raise WatchError(404, "not_found", "That run was not found.")
    cap = Decimal(str(usd_cap if usd_cap is not None else eval_grading_usd_cap()))
    try:
        job = await jobs.create(kind="golden_grade", scope_key=f"golden_grade:eval:{golden_run_id}", organization_id=org,
                                params={"handler": "eval_cases", "golden_run_id": str(golden_run_id),
                                        "organization_id": org}, usd_cap=cap, usd_estimate=None, total=None,
                                actor_id=actor_id, actor_email=actor_email)
    except jobs.JobConflict:
        raise WatchError(409, "grading_running", "These runs are already being graded.") from None
    job_id = str(job["id"])
    await jobs.start(job_id)
    return {"job": {"id": job_id, "status": "queued", "usd_cap": float(cap)}}


async def grade_job(ctx: Any) -> str:
    """The `eval_cases` handler of M3's golden_grade worker (ctx is M0's JobContext). The worker has already claimed
    the job and finishes it with the status returned here, so there is no claim and no finish in this function:
    'capped' while reps stay ungraded (the cost cap stopped the judge calls), else 'done'. A failure raises: M0's
    job runner records the job as failed."""
    params = ctx.job.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    out = await refresh_run(str(params["organization_id"]), str(params["golden_run_id"]), allow_model_calls=True,
                            job_id=str(ctx.job_id))
    left = sum(1 for cr in out["case_runs"] for r in cr["reps"] if r.get("status") == "ungraded")
    return "capped" if left else "done"


runner.register_grade_handler("eval_cases", grade_job)     # at import: M3's startup() restarts jobs that name it
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_eval_cases.py` → all pass (32).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/eval_cases.py`, `DA/tests/test_trace_lab_eval_cases.py`.

---

### Task 16: The release check — list and downloadable report

**Files:**
- Modify: `DA/src/trace_lab/eval_cases.py` (append)
- Test: `DA/tests/test_trace_lab_eval_cases.py` (append)

**Interfaces:**
- Consumes: Task 15 `refresh_run`, `_golden_run`; Task 12 `release_decision`, `render_release_md`; `stats.pass_hat_k` (M3).
- Produces: `list_release_checks(org, *, limit=20) -> list[{golden_run_id, run_key, decision, reason, k, status, started_at, finished_at}]` (stored decisions); `release_report(org, golden_run_id) -> dict` (spec §5.6 JSON plus `runs_without_verdict`; read-only, refreshes free); store `_release_runs`, `_stored_case_runs`, `_baseline_graders`, `_previous_release`.

- [ ] **Step 1: Write the failing test** (append to `DA/tests/test_trace_lab_eval_cases.py`)

```python
from datetime import datetime, timezone  # noqa: E402


def _cr(key, kind, decision, *, passes=5, n=5, degraded=0, pending=0, missing=0, judges=None, reps=None, rate=None):
    return {"eval_case_id": f"id-{key}", "case_key": key, "version": 1, "kind": kind, "mode": "wrong_period",
            "purpose": "release_check", "baseline_pass_rate": rate, "passes": passes, "n": n, "degraded": degraded,
            "pending": pending, "missing": missing, "decision": decision, "reason": f"{kind} {decision}",
            "pass_at_1": passes / 5 if n else None, "pass_at_3": None, "pass_at_5": None, "pass_hat_k": None,
            "graders": {"checks": ["code:invented_identifiers@v1"],
                        "judges": judges or [{"mode_id": M1, "judge_id": "j2", "judge_key": "wrong_period-v2",
                                              "prompt_hash": "bbbbbbbbbbbb"}]},
            "reps": reps or [{"rep": i, "status": "passed", "turn_id": f"turn-{key}-{i}"} for i in range(1, 6)]}


async def test_release_report_counts_regression_only_and_lists_gaps_and_changes(monkeypatch):
    regression_ok = _cr("e-00000001", "regression", "pass")
    regression_gap = _cr("e-00000002", "regression", "incomplete", passes=4, n=4, degraded=1,
                         reps=[{"rep": 1, "status": "passed", "turn_id": "t"}] * 4 +
                              [{"rep": 5, "status": "degraded", "degraded_reason": "a step was refused or needed approval"}])
    capability = _cr("e-00000003", "capability", "pass", passes=2, n=5, rate=0.4,
                     reps=[{"rep": i, "status": "passed" if i <= 2 else "failed"} for i in range(1, 6)])

    async def refresh(org, rid, *, allow_model_calls, job_id=None, only=None):
        assert allow_model_calls is False
        return {"run": {"id": rid, "label": "run-20261015T101500Z-0000000a", "purpose": "release_check", "k": 5,
                        "status": "done", "created_at": "2026-10-15T10:15:00+00:00", "finished_at": None, "usd_spent": 4.8},
                "case_runs": [regression_ok, regression_gap, capability]}

    async def golden_run(org, rid):
        return {"fingerprint": {"image": "f5-1a2b3c4", "openai_model": "gpt-5.6-terra"},
                "created_at": datetime(2026, 10, 15, 10, 15, tzinfo=timezone.utc)}

    async def baselines(case_ids):
        return {"id-e-00000001": {"judges": [{"mode_id": M1, "prompt_hash": "aaaaaaaaaaaa"}]},
                "id-e-00000002": {"judges": [{"mode_id": M1, "prompt_hash": "bbbbbbbbbbbb"}]}}

    async def previous(org, rid, created_at):
        return {"e-00000001": "pass", "e-00000002": "pass"}

    monkeypatch.setattr(eval_cases, "refresh_run", refresh)
    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases, "_baseline_graders", baselines)
    monkeypatch.setattr(eval_cases, "_previous_release", previous)
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert report["decision"] == "incomplete"                          # the capability case never decides
    assert [r["case_key"] for r in report["regression"]] == ["e-00000001", "e-00000002"]
    assert [c["case_key"] for c in report["capability"]] == ["e-00000003"]
    assert report["regression"][0]["judge_changed_since_baseline"] is True
    assert report["regression"][1]["judge_changed_since_baseline"] is False
    assert report["regression"][1]["not_decided"] == 1
    assert report["runs_without_verdict"][0] == {"case_key": "e-00000002", "rep": 5, "status": "degraded",
                                                 "reason": "a step was refused or needed approval"}
    assert report["changes_since_last"] == [{"case_key": "e-00000002", "before": "pass", "after": "incomplete"}]
    assert report["fingerprint"]["image"] == "f5-1a2b3c4" and report["usd"] == 4.8
    assert report["graders"]["checks"] == ["code:invented_identifiers@v1"]
    assert report["capability"][0]["pass_hat_5"] == 0.0


async def test_release_list_reads_the_stored_decisions(monkeypatch):
    async def runs(org, limit):
        return [{"id": "r2", "label": "run-b", "k": 5, "created_at": None, "finished_at": None, "job_status": "done"},
                {"id": "r1", "label": "run-a", "k": 5, "created_at": None, "finished_at": None, "job_status": "done"}]

    async def stored(run_ids):
        return [{"golden_run_id": "r2", "decision": "block", "kind": "regression", "case_key": "e-1"},
                {"golden_run_id": "r1", "decision": "pass", "kind": "regression", "case_key": "e-1"},
                {"golden_run_id": "r1", "decision": "pass", "kind": "capability", "case_key": "e-2"}]

    monkeypatch.setattr(eval_cases, "_release_runs", runs)
    monkeypatch.setattr(eval_cases, "_stored_case_runs", stored)
    out = await eval_cases.list_release_checks(ORG_A)
    assert [(c["golden_run_id"], c["decision"]) for c in out] == [("r2", "block"), ("r1", "pass")]
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_eval_cases.py`
Expected: `AttributeError: … has no attribute '_baseline_graders'`.

- [ ] **Step 3: Implement** (append to `DA/src/trace_lab/eval_cases.py`)

```python
# ---- the release check: list and report (spec §5.6) -------------------------------------------------------

async def _release_runs(org: str, limit: int) -> list[dict]:
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT r.id, r.label, r.k, r.created_at, r.finished_at, j.status AS job_status
  FROM plenum_tracelab.golden_runs r JOIN plenum_tracelab.jobs j ON j.id = r.job_id
 WHERE r.organization_id = CAST(:org AS uuid) AND r.purpose = 'release_check'
 ORDER BY r.created_at DESC LIMIT :limit"""), {"org": org, "limit": int(limit)})).mappings().all()
    return [dict(r) for r in rows]


async def _stored_case_runs(run_ids: Sequence[str]) -> list[dict]:
    if not run_ids:
        return []
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.golden_run_id, cr.decision, c.kind, c.case_key
  FROM plenum_tracelab.eval_case_runs cr JOIN plenum_tracelab.eval_cases c ON c.id = cr.eval_case_id
 WHERE cr.golden_run_id::text = ANY(CAST(:ids AS text[]))"""), {"ids": [str(r) for r in run_ids]})).mappings().all()
    return [dict(r) for r in rows]


async def _baseline_graders(case_ids: Sequence[str]) -> dict[str, dict]:
    if not case_ids:
        return {}
    async with db.engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.eval_case_id, cr.graders
  FROM plenum_tracelab.eval_case_runs cr
  JOIN plenum_tracelab.eval_cases c ON c.id = cr.eval_case_id AND cr.golden_run_id = c.baseline_run_id
 WHERE c.id::text = ANY(CAST(:ids AS text[]))"""), {"ids": [str(c) for c in case_ids]})).mappings().all()
    return {str(r["eval_case_id"]): _json(r["graders"]) or {} for r in rows}


async def _previous_release(org: str, golden_run_id: str, created_at: datetime | None) -> dict[str, str] | None:
    if created_at is None:
        return None
    async with db.engine().connect() as conn:
        prev = (await conn.execute(text("""
SELECT id FROM plenum_tracelab.golden_runs
 WHERE organization_id = CAST(:org AS uuid) AND purpose = 'release_check' AND created_at < :at
   AND id <> CAST(:id AS uuid)
 ORDER BY created_at DESC LIMIT 1"""), {"org": org, "at": created_at, "id": str(golden_run_id)})).scalar()
        if prev is None:
            return None
        rows = (await conn.execute(text("""
SELECT c.case_key, cr.decision FROM plenum_tracelab.eval_case_runs cr
  JOIN plenum_tracelab.eval_cases c ON c.id = cr.eval_case_id
 WHERE cr.golden_run_id = CAST(:prev AS uuid)"""), {"prev": str(prev)})).mappings().all()
    return {r["case_key"]: r["decision"] for r in rows}


async def list_release_checks(org: str, *, limit: int = 20) -> list[dict]:
    """Stored decisions: refreshed whenever a run is read (free) or graded."""
    runs = await _release_runs(org, limit)
    stored = await _stored_case_runs([str(r["id"]) for r in runs])
    out = []
    for r in runs:
        regression = [x for x in stored if str(x["golden_run_id"]) == str(r["id"]) and x["kind"] == "regression"]
        decision, reason = release_decision(regression, k=int(r["k"]))
        out.append({"golden_run_id": str(r["id"]), "run_key": r["label"], "decision": decision, "reason": reason,
                    "k": r["k"], "status": r["job_status"], "started_at": r["created_at"], "finished_at": r["finished_at"]})
    return public(out)


async def release_report(org: str, golden_run_id: str) -> dict:
    """Spec §5.6 JSON (plus runs_without_verdict). Read-only: grades with checks and cached predictions only."""
    data = await refresh_run(org, golden_run_id, allow_model_calls=False)
    run = data["run"]
    if run.get("purpose") != "release_check":
        raise WatchError(404, "not_found", "That run is not a release check.")
    full = await _golden_run(org, golden_run_id) or {}
    k = int(run["k"])
    baselines = await _baseline_graders([str(c["eval_case_id"]) for c in data["case_runs"] if c["kind"] == "regression"])
    regression, capability, gaps = [], [], []
    for cr in data["case_runs"]:
        for rep in cr["reps"]:
            if rep.get("status") not in DECIDED_REPS:
                gaps.append({"case_key": cr["case_key"], "rep": rep["rep"], "status": rep.get("status"),
                             "reason": rep.get("degraded_reason")})
        if cr["kind"] == "regression":
            then = {j["mode_id"]: j["prompt_hash"] for j in (baselines.get(str(cr["eval_case_id"])) or {}).get("judges", [])}
            now = {j["mode_id"]: j["prompt_hash"] for j in (cr.get("graders") or {}).get("judges", [])}
            regression.append({"case_key": cr["case_key"], "version": cr["version"], "mode": cr.get("mode"),
                               "passes": cr["passes"], "n": cr["n"],
                               "not_decided": int(cr["degraded"]) + int(cr["pending"]) + int(cr["missing"]),
                               "decision": cr["decision"], "reason": cr["reason"],
                               "turn_ids": [r["turn_id"] for r in cr["reps"] if r.get("turn_id")],
                               "judge_changed_since_baseline": any(then.get(m) != h for m, h in now.items())})
        elif cr["kind"] == "capability":
            n, passes = int(cr["n"]), int(cr["passes"])
            capability.append({"case_key": cr["case_key"], "version": cr["version"], "mode": cr.get("mode"),
                               "passes": passes, "n": n, "pass_at_1": cr.get("pass_at_1"), "pass_at_3": cr.get("pass_at_3"),
                               "pass_at_5": cr.get("pass_at_5"),
                               "pass_hat_5": round(stats.pass_hat_k(n, passes, 5), 4) if n >= 5 else None,
                               "baseline_pass_rate": cr.get("baseline_pass_rate"), "decision": cr["decision"],
                               "reason": cr["reason"]})
    decision, reason = release_decision(regression, k=k)
    checks_used = sorted({c for cr in data["case_runs"] for c in (cr.get("graders") or {}).get("checks", [])})
    judges_used: dict[str, dict] = {}
    for cr in data["case_runs"]:
        for j in (cr.get("graders") or {}).get("judges", []):
            judges_used[j["prompt_hash"]] = {"mode": cr.get("mode"), "judge_key": j["judge_key"], "prompt_hash": j["prompt_hash"]}
    previous = await _previous_release(org, golden_run_id, full.get("created_at"))
    changes = None if previous is None else [
        {"case_key": r["case_key"], "before": previous[r["case_key"]], "after": r["decision"]}
        for r in regression + capability if r["case_key"] in previous and previous[r["case_key"]] != r["decision"]]
    return public({"golden_run_id": str(run["id"]), "run_key": run.get("label"), "organization_id": org,
                   "decision": decision, "reason": reason, "k": k, "started_at": run.get("created_at"),
                   "finished_at": run.get("finished_at"), "usd": run.get("usd_spent"),
                   "fingerprint": full.get("fingerprint") or {},
                   "graders": {"checks": checks_used, "judges": list(judges_used.values())},
                   "regression": regression, "capability": capability, "runs_without_verdict": gaps,
                   "changes_since_last": changes})
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_eval_cases.py` → all pass (34).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/trace_lab/eval_cases.py`, `DA/tests/test_trace_lab_eval_cases.py`.

---

### Task 17: Routes `trace_lab_monitor.py` and their registration

**Files:**
- Create: `DA/src/api/routes/trace_lab_monitor.py`
- Modify: `DA/src/api/main.py` (import after `:40` `traces_router`; include after `:255`)
- Test: `DA/tests/test_trace_lab_monitor_routes.py`

**Interfaces:**
- Consumes: `current_principal`, `Principal` (`DA/src/services/principal.py:39-47,130-132`); `limiter` (`DA/src/limiter.py`); M0's `db.require_ready` (503 `trace_lab_unavailable`), `db.require_trace_store` (503 `trace_store_unavailable`), `db.require_reader(principal, organization_id) -> Scope` (403 `reviewers_only`), `db.require_reviewer(principal, organization_id) -> str` (400 `name_a_company`), `db.scope_for(principal, organization_id) -> Scope` (403 `not_your_company`, 400 `bad_company`), `db.http_error(status, reason, error, **extra)`; M2's `db.ReviewError` and `db.http_error_for(exc)`; every public function of `monitor` and `eval_cases` (Tasks 4–16). The route module has no auth or envelope helper of its own beyond one-line delegations to those.
- Produces: `router` (prefix `/api/trace-lab`, router-level `Depends(db.require_ready)` and `Depends(db.require_trace_store)`), registered in `main.py` as `trace_lab_monitor_router`; the route table below (spec §7.3, plus `POST /monitor/batches/{batch_id}/resume`, which the spec lacks: without it an interrupted batch would block its scope forever, because `ux_jobs_one_open_per_scope` counts `interrupted`).

| Method | Path | Who (§7.1) | Calls |
|---|---|---|---|
| GET | `/monitor/plan` | superadmin | `monitor.plan_preview` |
| POST | `/monitor/batches` (202, 10/min) | superadmin | `monitor.start_batch` |
| POST | `/monitor/batches/{batch_id}/resume` (202, 10/min) | superadmin | `monitor.resume_batch` |
| GET | `/monitor/batches` | admin (own company), superadmin (scope) | `monitor.list_batches` |
| GET | `/monitor/batches/{batch_id}` | same | `monitor.batch_detail` |
| GET | `/monitor/chart` | same | `monitor.chart` |
| GET | `/monitor/code-checks` | same | `monitor.code_check_trend` |
| GET | `/monitor/automation` | same | `monitor.automation_trend` |
| POST | `/monitor/batches/{batch_id}/review-batch` (201) | superadmin | `monitor.open_review_batches` |
| GET / POST | `/eval-cases` | admin or acting superadmin (a company) | `eval_cases.list_cases` / `create_case` |
| GET / PATCH | `/eval-cases/{case_id}` | same | `get_case` / `edit_case` |
| POST | `/eval-cases/{case_id}/confirm`, `/retire` | same | `confirm_case`, `retire_case` |
| POST | `/eval-cases/{case_id}/baseline`, `/repeat` (202, 10/min) | same | `start_baseline`, `start_repeat` |
| POST | `/turns/{turn_id}/repeat` (202, 10/min) | same | `repeat_turn` |
| POST | `/eval-runs/{golden_run_id}/grade` (202, 10/min) | same | `start_grading` |
| GET | `/eval-runs/{golden_run_id}` | same | `eval_run` |
| POST | `/release-checks` (202, 10/min) | same | `start_release_check` |
| GET | `/release-checks`, `/release-checks/{golden_run_id}?format=json\|md` | same | `list_release_checks`, `release_report` (+ `render_release_md`) |

- [ ] **Step 1: Write the failing test**

```python
"""Watch routes (M6; spec section Judges... 7.1 auth matrix, 7.3 route table): who may do what, the error
envelope, the 503 dependency, the rate limits, and the registration. 15 Oct 2026. No database: the monitor and
eval-case functions are replaced per test."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import UUID

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import trace_lab_monitor as routes
    from src.limiter import limiter
    from src.services.principal import Principal, current_principal
    from src.trace_lab import db, eval_cases, monitor
    from src.trace_lab.monitor import WatchError
except Exception as _exc:  # noqa: BLE001 - a bare dev environment cannot import the router
    routes = None

pytestmark = pytest.mark.skipif(routes is None, reason="the Trace Lab monitor router could not be imported here")

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"
PLATFORM = "dddddddd-0000-4000-8000-000000000004"
SPENDING = {"/monitor/batches", "/monitor/batches/{batch_id}/resume", "/eval-cases/{case_id}/baseline",
            "/eval-cases/{case_id}/repeat", "/turns/{turn_id}/repeat", "/eval-runs/{golden_run_id}/grade",
            "/release-checks"}


def _p(role, org):
    return Principal(user_id=UUID("cccccccc-0000-4000-8000-000000000003"), email=f"{role}@example.com",
                     organization_id=UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def _client(principal, monkeypatch, patches=(), *, trace_store=True):
    app = FastAPI()
    app.state.limiter = limiter
    app.state.orchestrator = object()
    app.include_router(routes.router)
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[db.require_ready] = lambda: None
    if trace_store:                                                   # the 503 test leaves M0's real dependency in place
        app.dependency_overrides[db.require_trace_store] = lambda: None
    monkeypatch.setattr(limiter, "enabled", False)
    for target, name, fn in patches:
        monkeypatch.setattr(target, name, fn)
    return TestClient(app)


OWNER, ADMIN, USER = _p("superadmin", PLATFORM), _p("admin", ORG_A), _p("user", ORG_A)


def test_platform_owner_starts_a_platform_batch_and_acting_starts_a_company_batch(monkeypatch):
    seen = []

    async def start(**kw):
        seen.append(kw["organization_id"])
        return {"batch": {"id": "b1"}, "job": {"id": "j1", "status": "queued"}}

    c = _client(OWNER, monkeypatch, [(monitor, "start_batch", start)])
    assert c.post("/api/trace-lab/monitor/batches", json={}).status_code == 202
    assert c.post(f"/api/trace-lab/monitor/batches?organization_id={ORG_A}", json={"sample_rate": 0.5}).status_code == 202
    assert seen == [None, ORG_A]


def test_company_admin_cannot_start_a_batch_or_read_another_company(monkeypatch):
    seen = []

    async def listing(**kw):
        seen.append(kw["org"])
        return {"batches": [], "due": {"period_label": "2026-W41", "is_due": True}}

    c = _client(ADMIN, monkeypatch, [(monitor, "list_batches", listing)])
    r = c.post("/api/trace-lab/monitor/batches", json={})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"       # R19: reading is open, starting is not
    assert c.get("/api/trace-lab/monitor/plan").status_code == 403
    r = c.post("/api/trace-lab/monitor/batches/b1/review-batch", json={"mode_id": "m"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    assert c.get("/api/trace-lab/monitor/batches").status_code == 200 and seen == [ORG_A]
    r = c.get(f"/api/trace-lab/monitor/batches?organization_id={ORG_B}")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "not_your_company"    # M0's scope_for


def test_users_get_403_everywhere(monkeypatch):
    c = _client(USER, monkeypatch)
    for method, path in (("get", "/api/trace-lab/monitor/batches"), ("get", "/api/trace-lab/monitor/automation"),
                         ("get", "/api/trace-lab/eval-cases"), ("get", "/api/trace-lab/release-checks"),
                         ("post", "/api/trace-lab/release-checks")):
        r = c.post(path, json={}) if method == "post" else c.get(path)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only", path


def test_an_admin_with_no_company_never_reads_the_platform_batches(monkeypatch):
    async def listing(**kw):
        raise AssertionError("an admin without a company must not reach the platform view")

    c = _client(_p("admin", None), monkeypatch, [(monitor, "list_batches", listing)])
    r = c.get("/api/trace-lab/monitor/batches")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"


def test_eval_cases_need_a_company(monkeypatch):
    seen = []

    async def listing(org, **kw):
        seen.append(org)
        return []

    c = _client(OWNER, monkeypatch, [(eval_cases, "list_cases", listing)])
    for r in (c.get("/api/trace-lab/eval-cases"), c.post("/api/trace-lab/release-checks", json={})):
        assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"   # M0's require_reviewer
    assert c.get(f"/api/trace-lab/eval-cases?organization_id={ORG_A}").status_code == 200 and seen == [ORG_A]


def test_watch_errors_use_the_house_envelope(monkeypatch):
    async def busy(**kw):
        raise WatchError(409, "over_cap", "This batch is estimated above its cap.", usd_estimate=9.0, usd_cap=5.0)

    c = _client(OWNER, monkeypatch, [(monitor, "start_batch", busy)])
    r = c.post("/api/trace-lab/monitor/batches", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"ok": False, "error": "This batch is estimated above its cap.", "reason": "over_cap",
                                  "usd_estimate": 9.0, "usd_cap": 5.0}


def test_every_route_waits_for_a_verified_schema_and_the_trace_store():
    for dep in (db.require_ready, db.require_trace_store):
        assert any(d.dependency is dep for d in routes.router.dependencies), dep.__name__
        for route in routes.router.routes:
            assert any(d.dependency is dep for d in route.dependencies), (dep.__name__, route.path)


def test_the_routes_answer_503_while_the_trace_store_is_down(monkeypatch):
    c = _client(ADMIN, monkeypatch, trace_store=False)
    monkeypatch.setattr("src.agents.trace.ready", lambda: False)              # M0's require_trace_store reads trace.ready()
    r = c.get("/api/trace-lab/monitor/batches")
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_store_unavailable"


def test_a_review_batch_refusal_from_m2_keeps_its_own_envelope(monkeypatch):
    async def detail(batch_id, **kw):
        return {}

    async def refuse(*args, **kw):
        raise db.Invalid("window_outside_retention")

    c = _client(OWNER, monkeypatch, [(monitor, "batch_detail", detail), (monitor, "open_review_batches", refuse)])
    r = c.post("/api/trace-lab/monitor/batches/b1/review-batch", json={"mode_id": "m"})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "window_outside_retention"


def test_every_spending_route_is_rate_limited():
    tree = ast.parse(inspect.getsource(routes))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        posts = [d.args[0].value for d in node.decorator_list
                 if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "post" and d.args
                 and isinstance(d.args[0], ast.Constant)]
        if not posts or posts[0] not in SPENDING:
            continue
        found.add(posts[0])
        assert any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "limit" for d in node.decorator_list), posts[0]
        assert "request" in [a.arg for a in node.args.args], posts[0]
    assert found == SPENDING


def test_baseline_runs_as_the_caller_with_their_bearer(monkeypatch):
    seen = {}

    async def baseline(ctx, case_id, *, usd_cap=None):
        seen.update(ctx=ctx, case_id=case_id, usd_cap=usd_cap)
        return {"golden_run": {"id": "run-1"}}

    c = _client(OWNER, monkeypatch, [(eval_cases, "start_baseline", baseline)])
    r = c.post(f"/api/trace-lab/eval-cases/c1/baseline?organization_id={ORG_A}", json={"usd_cap": 3},
               headers={"Authorization": "Bearer abc"})
    assert r.status_code == 202 and r.json()["golden_run"]["id"] == "run-1"
    assert (seen["ctx"].org, seen["ctx"].acting_org, seen["ctx"].authorization) == (ORG_A, ORG_A, "Bearer abc")
    assert seen["case_id"] == "c1" and seen["usd_cap"] == 3


def test_release_report_downloads_as_markdown_or_json(monkeypatch):
    async def report(org, rid):
        return {"golden_run_id": rid, "run_key": "run-20261015T101500Z-0000000a", "decision": "pass",
                "reason": "all 1 regression cases passed 5 of 5 runs", "k": 5, "regression": [], "capability": [],
                "runs_without_verdict": [], "changes_since_last": None, "fingerprint": {}, "graders": {}}

    c = _client(ADMIN, monkeypatch, [(eval_cases, "release_report", report)])
    r = c.get("/api/trace-lab/release-checks/run-x?format=md&company_name=Northbridge")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("# Release check — Northbridge — run-20261015T101500Z-0000000a")
    j = c.get("/api/trace-lab/release-checks/run-x")
    assert j.status_code == 200 and j.json()["decision"] == "pass" and "attachment" in j.headers["content-disposition"]


def test_main_registers_the_watch_router():
    src = (Path(routes.__file__).resolve().parents[1] / "main.py").read_text()
    assert "from .routes.trace_lab_monitor import router as trace_lab_monitor_router" in src
    assert "app.include_router(trace_lab_monitor_router)" in src
```

- [ ] **Step 2: Run it and see it fail**

Run: `da_test tests/test_trace_lab_monitor_routes.py`
Expected: every test is skipped with "the Trace Lab monitor router could not be imported here" (the module does not exist). Treat the skip as the failure; it must turn into passes.

- [ ] **Step 3: Implement**

Create `DA/src/api/routes/trace_lab_monitor.py`:

```python
"""Trace Lab Watch routes (M6): monitoring batches, prevalence charts, code-check rates, the automation trend,
eval cases, repeat-n and release checks. Spec "Judges, code checks, monitoring, eval cases and release check"
section 7.1 (who may do what) and 7.3 (routes). Every route answers 503 while the Trace Lab schema is not
verified (the chat is unaffected). Routes that spend model money are rate-limited.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ...limiter import limiter
from ...services.principal import Principal, current_principal
from ...trace_lab import db, eval_cases, monitor
from ...trace_lab.monitor import WatchError

router = APIRouter(prefix="/api/trace-lab", tags=["Trace Lab"],
                   dependencies=[Depends(db.require_ready), Depends(db.require_trace_store)])

UUID_LEN = 36


def _watch(exc: WatchError) -> HTTPException:
    return db.http_error(exc.status, exc.reason, exc.message, **exc.extra)


def _reader(principal: Principal, organization_id: str | None) -> str | None:
    """R19: a company admin reads their own company's batches; a superadmin reads the company they act for, or
    (None) the platform. M0's db.require_reader owns the rules and the reasons (reviewers_only, not_your_company,
    bad_company); an admin with no company is refused, never shown the platform view."""
    sc = db.require_reader(principal, organization_id)
    if sc.org_id is None and not sc.is_superadmin:
        raise db.http_error(400, "name_a_company", "Choose a company to view as first.")
    return sc.org_id


def _starter(principal: Principal, organization_id: str | None) -> str | None:
    """R19: only platform superadmins start (and resume) monitoring; None = platform-wide."""
    sc = db.scope_for(principal, organization_id)
    if not sc.is_superadmin:
        raise db.http_error(403, "platform_only", "Only the platform team starts monitoring.")
    return sc.org_id


def _company(principal: Principal, organization_id: str | None) -> str:
    """Eval cases, repeat-n and release checks belong to one company: an admin's own, or the one a superadmin acts
    for (M0's db.require_reviewer: 403 reviewers_only, 400 name_a_company)."""
    return db.require_reviewer(principal, organization_id)


def _actor(principal: Principal) -> tuple[str, str | None]:
    return str(principal.user_id), (principal.email or None)


def _ctx(request: Request, principal: Principal, org: str) -> eval_cases.RunContext:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise db.http_error(503, "orchestrator_unavailable", "The chat is still starting; try again in a minute.")
    return eval_cases.RunContext(org=org, principal=principal, authorization=request.headers.get("authorization"),
                                 acting_org=org if principal.role == "superadmin" else None, orchestrator=orchestrator)


class BatchStart(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    sample_rate: float = Field(monitor.DEFAULT_RATE, gt=0, le=1)
    seed: int = Field(monitor.DEFAULT_SEED, ge=0, le=2_147_483_647)
    risk_groups: list[str] | None = Field(None, max_length=10)
    mode_ids: list[str] | None = Field(None, max_length=50)
    usd_cap: float | None = Field(None, gt=0, le=100)


class ReviewBatch(BaseModel):
    mode_id: str = Field(..., min_length=1, max_length=UUID_LEN)


class CaseSource(BaseModel):
    type: str = Field(..., pattern="^(live_turn|golden_case)$")
    turn_id: str | None = Field(None, max_length=80)
    golden_case_id: str | None = Field(None, max_length=UUID_LEN)


class CaseCreate(BaseModel):
    source: CaseSource
    mode_id: str | None = Field(None, max_length=UUID_LEN)
    input: dict[str, Any] | None = None
    initial_state: dict[str, Any] | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern="^(objective|judgment)$")
    expected_values: str | None = Field(None, max_length=4000)


class CaseEdit(BaseModel):
    base: int = Field(..., ge=1)
    input: dict[str, Any] | None = None
    initial_state: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str | None = Field(None, pattern="^(objective|judgment)$")
    expected_values: str | None = Field(None, max_length=4000)


class CaseConfirm(BaseModel):
    base: int = Field(..., ge=1)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern="^(objective|judgment)$")
    expected_values: str | None = Field(None, max_length=4000)


class CaseRetire(BaseModel):
    base: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=500)


class RunCap(BaseModel):
    usd_cap: float | None = Field(None, gt=0, le=100)


class CaseRepeat(RunCap):
    n: int = Field(eval_cases.REPEAT_DEFAULT_N, ge=eval_cases.REPEAT_MIN_N, le=eval_cases.REPEAT_MAX_N)


class TurnRepeat(CaseRepeat):
    mode_ids: list[str] = Field(..., min_length=1, max_length=10)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern="^(objective|judgment)$")


class ReleaseStart(RunCap):
    k: int = Field(eval_cases.RELEASE_DEFAULT_K, ge=1, le=eval_cases.RELEASE_MAX_K)
    include_capability: bool = True


# ---- monitoring --------------------------------------------------------------------------------------------

@router.get("/monitor/plan")
async def monitor_plan(since: datetime | None = Query(None), until: datetime | None = Query(None),
                       rate: float = Query(monitor.DEFAULT_RATE, gt=0, le=1),
                       seed: int = Query(monitor.DEFAULT_SEED, ge=0, le=2_147_483_647),
                       risk_groups: str | None = Query(None, max_length=200),
                       organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _starter(principal, organization_id)
    groups = [g.strip() for g in risk_groups.split(",") if g.strip()] if risk_groups else None
    try:
        out = await monitor.plan_preview(organization_id=org, since=since, until=until, sample_rate=rate, seed=seed,
                                         risk_groups=groups)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.post("/monitor/batches", status_code=202)
@limiter.limit("10/minute")
async def start_monitor_batch(request: Request, body: BatchStart, organization_id: str | None = Query(None),
                              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _starter(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        out = await monitor.start_batch(organization_id=org, since=body.since, until=body.until,
                                        sample_rate=body.sample_rate, seed=body.seed, risk_groups=body.risk_groups,
                                        mode_ids=body.mode_ids, usd_cap=body.usd_cap, actor_id=actor_id,
                                        actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.post("/monitor/batches/{batch_id}/resume", status_code=202)
@limiter.limit("10/minute")
async def resume_monitor_batch(request: Request, batch_id: str, organization_id: str | None = Query(None),
                               principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _starter(principal, organization_id)
    try:
        out = await monitor.resume_batch(batch_id, org=org)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.get("/monitor/batches")
async def monitor_batches(limit: int = Query(20, ge=1, le=100), before: datetime | None = Query(None),
                          organization_id: str | None = Query(None),
                          principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return {"ok": True, **await monitor.list_batches(org=org, limit=limit, before=before)}


@router.get("/monitor/batches/{batch_id}")
async def monitor_batch(batch_id: str, mode_id: str | None = Query(None, max_length=UUID_LEN),
                        offset: int = Query(0, ge=0), organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    try:
        out = await monitor.batch_detail(batch_id, org=org, mode_id=mode_id, offset=offset)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.get("/monitor/chart")
async def monitor_chart(mode_id: str = Query(..., max_length=UUID_LEN), periods: int = Query(12, ge=1, le=52),
                        organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    try:
        out = await monitor.chart(org=org, mode_id=mode_id, periods=periods)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.get("/monitor/code-checks")
async def monitor_code_checks(periods: int = Query(12, ge=1, le=52), organization_id: str | None = Query(None),
                              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return {"ok": True, **await monitor.code_check_trend(org=org, periods=periods)}


@router.get("/monitor/automation")
async def monitor_automation(weeks: int = Query(12, ge=1, le=52), organization_id: str | None = Query(None),
                             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return {"ok": True, **await monitor.automation_trend(org=org, weeks=weeks)}


@router.post("/monitor/batches/{batch_id}/review-batch", status_code=201)
async def open_review_batch(batch_id: str, body: ReviewBatch, organization_id: str | None = Query(None),
                            principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _starter(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        await monitor.batch_detail(batch_id, org=org, limit=1)            # 404 outside the caller's scope
        ids = await monitor.open_review_batches(batch_id, body.mode_id, actor_id=actor_id, actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    except db.ReviewError as exc:                                         # M2's own refusals keep their envelope
        raise db.http_error_for(exc) from None
    return {"ok": True, "review_batch_ids": ids}


# ---- eval cases -------------------------------------------------------------------------------------------

@router.get("/eval-cases")
async def list_eval_cases(status: str | None = Query(None, pattern="^(proposed|draft|baselining|active|retired|superseded)$"),
                          kind: str | None = Query(None, pattern="^(unclassified|regression|capability)$"),
                          mode_id: str | None = Query(None, max_length=UUID_LEN),
                          organization_id: str | None = Query(None),
                          principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    return {"ok": True, "cases": await eval_cases.list_cases(org, status=status, kind=kind, mode_id=mode_id)}


@router.post("/eval-cases", status_code=201)
async def create_eval_case(body: CaseCreate, organization_id: str | None = Query(None),
                           principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        case = await eval_cases.create_case(org, source=body.source.model_dump(), mode_id=body.mode_id,
                                            input_override=body.input, initial_state=body.initial_state,
                                            expected=body.expected, expected_answer=body.expected_answer,
                                            evaluation=body.evaluation, expected_values=body.expected_values,
                                            actor_id=actor_id, actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, "case": case}


@router.get("/eval-cases/{case_id}")
async def eval_case(case_id: str, organization_id: str | None = Query(None),
                    principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        out = await eval_cases.get_case(org, case_id)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.patch("/eval-cases/{case_id}")
async def edit_eval_case(case_id: str, body: CaseEdit, organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        case = await eval_cases.edit_case(org, case_id, base=body.base, input_override=body.input,
                                          initial_state=body.initial_state, expected=body.expected,
                                          expected_answer=body.expected_answer, evaluation=body.evaluation,
                                          expected_values=body.expected_values, actor_id=actor_id,
                                          actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, "case": case}


@router.post("/eval-cases/{case_id}/confirm")
async def confirm_eval_case(case_id: str, body: CaseConfirm, organization_id: str | None = Query(None),
                            principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        case = await eval_cases.confirm_case(org, case_id, base=body.base, expected_answer=body.expected_answer,
                                             evaluation=body.evaluation, expected_values=body.expected_values,
                                             actor_id=actor_id, actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, "case": case}


@router.post("/eval-cases/{case_id}/retire")
async def retire_eval_case(case_id: str, body: CaseRetire, organization_id: str | None = Query(None),
                           principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        case = await eval_cases.retire_case(org, case_id, base=body.base, reason=body.reason)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, "case": case}


@router.post("/eval-cases/{case_id}/baseline", status_code=202)
@limiter.limit("10/minute")
async def baseline_eval_case(request: Request, case_id: str, body: RunCap, organization_id: str | None = Query(None),
                             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        out = await eval_cases.start_baseline(_ctx(request, principal, org), case_id, usd_cap=body.usd_cap)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.post("/eval-cases/{case_id}/repeat", status_code=202)
@limiter.limit("10/minute")
async def repeat_eval_case(request: Request, case_id: str, body: CaseRepeat, organization_id: str | None = Query(None),
                           principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        out = await eval_cases.start_repeat(_ctx(request, principal, org), case_id, n=body.n, usd_cap=body.usd_cap)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.post("/turns/{turn_id}/repeat", status_code=202)
@limiter.limit("10/minute")
async def repeat_turn(request: Request, turn_id: str, body: TurnRepeat, organization_id: str | None = Query(None),
                      principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        out = await eval_cases.repeat_turn(_ctx(request, principal, org), turn_id, n=body.n, mode_ids=body.mode_ids,
                                           expected_answer=body.expected_answer, evaluation=body.evaluation,
                                           usd_cap=body.usd_cap, actor_id=actor_id, actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.post("/eval-runs/{golden_run_id}/grade", status_code=202)
@limiter.limit("10/minute")
async def grade_eval_run(request: Request, golden_run_id: str, body: RunCap, organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    actor_id, actor_email = _actor(principal)
    try:
        out = await eval_cases.start_grading(org, golden_run_id, usd_cap=body.usd_cap, actor_id=actor_id,
                                             actor_email=actor_email)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.get("/eval-runs/{golden_run_id}")
async def eval_run(golden_run_id: str, organization_id: str | None = Query(None),
                   principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        out = await eval_cases.eval_run(org, golden_run_id)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


# ---- release checks ----------------------------------------------------------------------------------------

@router.post("/release-checks", status_code=202)
@limiter.limit("10/minute")
async def start_release_check(request: Request, body: ReleaseStart, organization_id: str | None = Query(None),
                              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    try:
        out = await eval_cases.start_release_check(_ctx(request, principal, org), k=body.k,
                                                   include_capability=body.include_capability, usd_cap=body.usd_cap)
    except WatchError as exc:
        raise _watch(exc) from None
    return {"ok": True, **out}


@router.get("/release-checks")
async def release_checks(limit: int = Query(20, ge=1, le=100), organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _company(principal, organization_id)
    return {"ok": True, "checks": await eval_cases.list_release_checks(org, limit=limit)}


@router.get("/release-checks/{golden_run_id}")
async def release_check(golden_run_id: str, format: str = Query("json", pattern="^(json|md)$"),
                        company_name: str | None = Query(None, max_length=120),
                        organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> Response:
    """The report to attach to a deploy: JSON, or Markdown with the company's name in the heading."""
    org = _company(principal, organization_id)
    try:
        report = await eval_cases.release_report(org, golden_run_id)
    except WatchError as exc:
        raise _watch(exc) from None
    name = f"release-check-{report.get('run_key') or golden_run_id}"
    if format == "md":
        md = eval_cases.render_release_md(report, company=(company_name or "").strip() or "your company")
        return Response(content=md, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    return JSONResponse(content={"ok": True, **report}, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
```

In `DA/src/api/main.py`, two exact-string edits (shared file; replace only these lines):

Old:
```python
from .routes.traces import router as traces_router
```
New:
```python
from .routes.traces import router as traces_router
from .routes.trace_lab_monitor import router as trace_lab_monitor_router
```

Old:
```python
app.include_router(traces_router)
```
New:
```python
app.include_router(traces_router)
app.include_router(trace_lab_monitor_router)
```

- [ ] **Step 4: Run it and see it pass**

Run: `da_test tests/test_trace_lab_monitor_routes.py` → `13 passed` (no skips).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `DA/src/api/routes/trace_lab_monitor.py`, `DA/src/api/main.py`, `DA/tests/test_trace_lab_monitor_routes.py`.

---

### Task 18: Frontend API client — the Watch calls

**Files:**
- Modify: `FE/src/api/traceLab.js` (append one block at the end of the file)
- Test: `FE/test/traceLabWatch.test.mjs` (create; Task 19 appends to it)

**Interfaces:**
- Consumes: M1's `src/api/traceLab.js` module scope: `apiFetch` and `BASES` (imported from `./client.js`), `orgOverride` (imported from `./deepAgents.js`), `export const traceLabApi`.
- Produces on `traceLabApi`: `monitorPlan(q)`, `startMonitorBatch(body)`, `resumeMonitorBatch(id)`, `monitorBatches(q)`, `monitorBatch(id, q)`, `monitorChart(modeId, periods)`, `monitorCodeChecks(periods)`, `automationTrend(weeks)`, `openReviewBatch(batchId, modeId)`, `evalCases(q)`, `createEvalCase(body)`, `evalCase(id)`, `editEvalCase(id, body)`, `confirmEvalCase(id, body)`, `retireEvalCase(id, body)`, `baselineEvalCase(id, body)`, `repeatEvalCase(id, body)`, `repeatTurn(turnId, body)`, `gradeEvalRun(runId, body)`, `evalRun(runId)`, `startReleaseCheck(body)`, `releaseChecks(q)`, `releaseCheck(runId)`, `releaseCheckFile(runId, format, companyName) -> {blob, filename, type}` (spec §7.4 names, plus `resumeMonitorBatch` and `releaseCheckFile`).

- [ ] **Step 0: Confirm M1's names** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend
grep -n "export const traceLabApi\|orgOverride\|from './client.js'\|from './deepAgents.js'" src/api/traceLab.js
```

Expected: `export const traceLabApi = {`, an import line from `./deepAgents.js` that brings in `orgOverride`, and an import line from `./client.js` that brings in `apiFetch` and `BASES`. If `BASES` is not imported there, add it to that import line (exact-string edit) in Step 3.

- [ ] **Step 1: Write the failing test**

Create `FE/test/traceLabWatch.test.mjs`:

```js
// traceLabWatch — the Trace Lab's Watch tab (logic/traceLabWatch.js, api/traceLab.js): every call carries the
// view-as company, chart geometry from the server's points, the automatic-vs-people shares, the Due marker,
// role gating, the review-batch action, case confirmation, and the reset a company switch relies on.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { traceLabApi } from '../src/api/traceLab.js';
import { setActingOrg } from '../src/api/client.js';

test('every Watch call carries the view-as company and goes to the Trace Lab routes', async () => {
  const urls = [];
  const savedFetch = globalThis.fetch;
  const savedWindow = globalThis.window;
  globalThis.window = { location: { origin: 'http://localhost' } };
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json' } });
  };
  setActingOrg('org-x');
  try {
    await traceLabApi.monitorPlan({ rate: 0.25 });
    await traceLabApi.startMonitorBatch({});
    await traceLabApi.resumeMonitorBatch('b1');
    await traceLabApi.monitorBatches({ limit: 20 });
    await traceLabApi.monitorBatch('b1');
    await traceLabApi.monitorChart('m1', 12);
    await traceLabApi.monitorCodeChecks(12);
    await traceLabApi.automationTrend(12);
    await traceLabApi.openReviewBatch('b1', 'm1');
    await traceLabApi.evalCases({});
    await traceLabApi.createEvalCase({});
    await traceLabApi.evalCase('c1');
    await traceLabApi.editEvalCase('c1', { base: 1 });
    await traceLabApi.confirmEvalCase('c1', { base: 1 });
    await traceLabApi.retireEvalCase('c1', { base: 1, reason: 'duplicate' });
    await traceLabApi.baselineEvalCase('c1', {});
    await traceLabApi.repeatEvalCase('c1', { n: 10 });
    await traceLabApi.repeatTurn('t1', { n: 10, mode_ids: ['m1'] });
    await traceLabApi.gradeEvalRun('r1', {});
    await traceLabApi.evalRun('r1');
    await traceLabApi.startReleaseCheck({ k: 5 });
    await traceLabApi.releaseChecks({});
    await traceLabApi.releaseCheck('r1');
    await traceLabApi.releaseCheckFile('r1', 'md', 'Northbridge Estates Ltd');
  } finally {
    setActingOrg(null);
    globalThis.fetch = savedFetch;
    globalThis.window = savedWindow;
  }
  assert.equal(urls.length, 24);
  for (const u of urls) {
    const url = new URL(u);
    assert.equal(url.searchParams.get('organization_id'), 'org-x', u);
    assert.ok(url.pathname.startsWith('/backend/deep-agents/api/trace-lab/'), u);
  }
  assert.equal(new URL(urls[23]).searchParams.get('format'), 'md');
  assert.equal(new URL(urls[23]).searchParams.get('company_name'), 'Northbridge Estates Ltd');
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs`
Expected: `TypeError: traceLabApi.monitorPlan is not a function`.

- [ ] **Step 3: Implement**

Append to the end of `FE/src/api/traceLab.js`:

```js
// ---- Watch (M6): monitoring, eval cases, release checks and the automation trend ------------------------------
// Every call sends orgOverride() as its query: a superadmin's view-as company only, never currentOrgId().
const TLW = '/api/trace-lab';
Object.assign(traceLabApi, {
  monitorPlan: (q) => apiFetch(BASES.deepAgents, TLW + '/monitor/plan', { query: Object.assign({}, q || {}, orgOverride()), timeoutMs: 60000 }),
  startMonitorBatch: (body) => apiFetch(BASES.deepAgents, TLW + '/monitor/batches', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 60000 }),
  resumeMonitorBatch: (id) => apiFetch(BASES.deepAgents, TLW + '/monitor/batches/' + encodeURIComponent(id) + '/resume', { method: 'POST', body: {}, query: orgOverride(), timeoutMs: 20000 }),
  monitorBatches: (q) => apiFetch(BASES.deepAgents, TLW + '/monitor/batches', { query: Object.assign({}, q || {}, orgOverride()), timeoutMs: 20000 }),
  monitorBatch: (id, q) => apiFetch(BASES.deepAgents, TLW + '/monitor/batches/' + encodeURIComponent(id), { query: Object.assign({}, q || {}, orgOverride()), timeoutMs: 20000 }),
  monitorChart: (modeId, periods) => apiFetch(BASES.deepAgents, TLW + '/monitor/chart', { query: Object.assign({ mode_id: modeId, periods: periods || 12 }, orgOverride()), timeoutMs: 20000 }),
  monitorCodeChecks: (periods) => apiFetch(BASES.deepAgents, TLW + '/monitor/code-checks', { query: Object.assign({ periods: periods || 12 }, orgOverride()), timeoutMs: 20000 }),
  automationTrend: (weeks) => apiFetch(BASES.deepAgents, TLW + '/monitor/automation', { query: Object.assign({ weeks: weeks || 12 }, orgOverride()), timeoutMs: 30000 }),
  openReviewBatch: (batchId, modeId) => apiFetch(BASES.deepAgents, TLW + '/monitor/batches/' + encodeURIComponent(batchId) + '/review-batch', { method: 'POST', body: { mode_id: modeId }, query: orgOverride(), timeoutMs: 20000 }),
  evalCases: (q) => apiFetch(BASES.deepAgents, TLW + '/eval-cases', { query: Object.assign({}, q || {}, orgOverride()), timeoutMs: 20000 }),
  createEvalCase: (body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 20000 }),
  evalCase: (id) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id), { query: orgOverride(), timeoutMs: 20000 }),
  editEvalCase: (id, body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id), { method: 'PATCH', body: body || {}, query: orgOverride(), timeoutMs: 20000 }),
  confirmEvalCase: (id, body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id) + '/confirm', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 20000 }),
  retireEvalCase: (id, body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id) + '/retire', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 20000 }),
  baselineEvalCase: (id, body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id) + '/baseline', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 60000 }),
  repeatEvalCase: (id, body) => apiFetch(BASES.deepAgents, TLW + '/eval-cases/' + encodeURIComponent(id) + '/repeat', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 60000 }),
  repeatTurn: (turnId, body) => apiFetch(BASES.deepAgents, TLW + '/turns/' + encodeURIComponent(turnId) + '/repeat', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 60000 }),
  gradeEvalRun: (runId, body) => apiFetch(BASES.deepAgents, TLW + '/eval-runs/' + encodeURIComponent(runId) + '/grade', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 20000 }),
  evalRun: (runId) => apiFetch(BASES.deepAgents, TLW + '/eval-runs/' + encodeURIComponent(runId), { query: orgOverride(), timeoutMs: 60000 }),
  startReleaseCheck: (body) => apiFetch(BASES.deepAgents, TLW + '/release-checks', { method: 'POST', body: body || {}, query: orgOverride(), timeoutMs: 60000 }),
  releaseChecks: (q) => apiFetch(BASES.deepAgents, TLW + '/release-checks', { query: Object.assign({}, q || {}, orgOverride()), timeoutMs: 20000 }),
  releaseCheck: (runId) => apiFetch(BASES.deepAgents, TLW + '/release-checks/' + encodeURIComponent(runId), { query: Object.assign({ format: 'json' }, orgOverride()), timeoutMs: 60000 }),
  // The report as a file to attach to a deploy: {blob, filename, type}.
  releaseCheckFile: (runId, format, companyName) => apiFetch(BASES.deepAgents, TLW + '/release-checks/' + encodeURIComponent(runId), { query: Object.assign({ format: format === 'md' ? 'md' : 'json', company_name: companyName || undefined }, orgOverride()), raw: true, timeoutMs: 60000 })
});
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs test/traceLabCompanyScope.test.mjs` → both pass (M1's company-scope guard covers the new calls too).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/api/traceLab.js`, `FE/test/traceLabWatch.test.mjs`.

---

### Task 19: Frontend logic `traceLabWatch.js` — loaders, actions and the pure chart and share helpers

**Files:**
- Create: `FE/src/logic/traceLabWatch.js`
- Test: `FE/test/traceLabWatch.test.mjs` (append)

**Interfaces:**
- Consumes: Task 18 `traceLabApi.*`; `isStaleScope` (`FE/src/api/client.js:47`); controller methods from M1: `this.tlWrite(fn, { key, retry })` (never throws: it resolves `{ ok: true, out }`, `{ ok: false, error, conflict }` after putting the error in `state.tlWrites[key]`, or `{ ok: false, stale: true }` when the company changed; a call without a key shares the key `write`, so every Watch write names its own), `this.tlRetryWrite(key)`, `this.tlSetView(view)`, `this.tlOpenTurnById(turnId)` (opens any answer in its conversation; `tlOpenTurn` only switches answers inside the conversation already open); state keys `tlView`, `tlRunId`, `tlWrites`, `account`, `viewOrgId`, `viewOrgName`.
- Produces: `CHART`, `pctText(v)`, `chartGeometry(chart, dims?) -> {empty, points: [{x, corrected, raw, low, high, label, crossed, warning, title}], thresholdY, thresholdLabel, ticks, width, height, padL, padR}`, `stackShares(weeks) -> [{week, n, empty, autoShare, peopleShare, autoPct, peoplePct, autoText, peopleText, sources, people, modesAutomated, modesConfirmed, golden, live}]`, `dueLabel(due) -> {text, isDue} | null`, `latestModes(batches)`, `traceLabWatchMethods` (`tlWatchEnsure`, `tlWatchLoad`, `tlWatchLoadAuto`, `tlWatchLoadChart`, `tlWatchLoadCases`, `tlWatchLoadReleases`, `tlWatchPlan`, `tlWatchSetPlanDraft`, `tlWatchClosePlan`, `tlWatchStart`, `tlWatchResume`, `tlWatchOpenBatch`, `tlWatchCloseBatch`, `tlWatchOpenReviewBatch`, `tlWatchOpenAnswer`, `tlWatchOpenRun`, `tlWatchSetCaseDraft`, `tlWatchConfirmCase`, `tlWatchBaseline`, `tlWatchRepeat`, `tlWatchRetire`, `tlWatchSetReleaseDraft`, `tlWatchStartRelease`, `tlWatchDownloadRelease`), `traceLabWatchVals(c, now)` (every `tlw*` val used by `Watch.jsx`), `tlWatchResetPatch()` (owns every `tlMonitor*`, `tlCases*`, `tlRelease*`, `tlAuto*` key).

- [ ] **Step 1: Write the failing test** (append to `FE/test/traceLabWatch.test.mjs`)

```js
import { readFileSync } from 'node:fs';
import {
  CHART, chartGeometry, dueLabel, stackShares, traceLabWatchMethods, traceLabWatchVals, tlWatchResetPatch
} from '../src/logic/traceLabWatch.js';

const CHART_DATA = { mode: { id: 'm1', name: 'wrong_period' }, threshold: 0.15, y_max: 0.35, points: [
  { period_label: '2026-W40', raw: 0.12, corrected: 0.1, ci_low: 0.02, ci_high: 0.2, threshold: 0.15, crossed: false },
  { period_label: '2026-W41', raw: 0.19, corrected: 0.17, ci_low: 0.06, ci_high: 0.28, threshold: 0.15, crossed: true }] };

function ctl(state) {
  const calls = [];
  const c = {
    calls,
    state: Object.assign({ tlView: 'watch', account: { role: 'admin', organization_name: 'Northbridge Estates Ltd' } }, state || {}),
    setState(u) { Object.assign(this.state, typeof u === 'function' ? u(this.state) : u); },
    // M1's tlWrite contract: never throws, resolves { ok: true, out } or { ok: false, error } (always truthy).
    async tlWrite(fn, opts) {
      calls.push(['writeKey', opts && opts.key]);
      try { return { ok: true, out: await fn() }; } catch (e) { calls.push(['writeError', e.message]); return { ok: false, error: e.message }; }
    },
    tlSetView(v) { calls.push(['view', v]); },
    tlOpenTurnById(t) { calls.push(['turn', t]); }
  };
  Object.keys(traceLabWatchMethods).forEach((k) => { c[k] = traceLabWatchMethods[k].bind(c); });
  return c;
}

test('the chart scale tops out at y_max and the threshold line is labelled with its value', () => {
  const g = chartGeometry(CHART_DATA);
  const innerH = CHART.height - CHART.padT - CHART.padB;
  assert.equal(g.empty, false);
  assert.equal(g.points.length, 2);
  assert.equal(g.thresholdLabel, 'threshold 0.15');
  assert.equal(g.ticks[2].y, CHART.padT, 'the top tick is y_max');
  assert.equal(g.points[1].high, Math.round((CHART.padT + innerH - 0.8 * innerH) * 10) / 10, 'ci_high = y_max / 1.25 sits at 80%');
  assert.ok(g.thresholdY > g.points[1].corrected, 'a point above the threshold is drawn above its line');
  assert.equal(g.points[1].crossed, true);
});

test('points carry a whisker for the interval and a hollow raw rate', () => {
  const p = chartGeometry(CHART_DATA).points[0];
  assert.ok(p.low > p.corrected && p.corrected > p.high, 'the whisker spans the interval around the estimate');
  assert.equal(typeof p.raw, 'number');
  assert.equal(p.title, '2026-W40: 10% corrected (2% to 20%), 12% flagged in the sample');
});

test('with no batches the tab says so and draws nothing', () => {
  const v = traceLabWatchVals(ctl({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } }), new Date());
  assert.equal(v.tlwBatchesEmpty, true);
  assert.equal(v.tlwEmptyText, 'No batches yet. Monitoring has not run for this scope.');
  assert.deepEqual(v.tlwCharts, []);
  assert.equal(chartGeometry({ points: [], y_max: null }).empty, true);
  assert.equal(chartGeometry(null).empty, true);
});

test('the Due marker names the week that still needs a batch', () => {
  assert.deepEqual(dueLabel({ period_label: '2026-W41', is_due: true }), { text: 'Due: 2026-W41 has not been checked yet', isDue: true });
  assert.deepEqual(dueLabel({ period_label: '2026-W41', is_due: false }), { text: 'Up to date: 2026-W41 checked', isDue: false });
  assert.equal(dueLabel(null), null);
  const v = traceLabWatchVals(ctl({ tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } }), new Date());
  assert.equal(v.tlwDue.isDue, true);
});

test('automatic and people shares add up to one, and an empty week reads as a dash', () => {
  const weeks = stackShares([
    { week: '2026-W41', checked_any: 5, auto_share: 0.4, people_share: 0.6, auto_by_source: { golden_auto: 1 }, people_by_source: {},
      golden_accuracy: { decided: 2, correct: 1, rate: 0.5, low: 0.1, high: 0.9 }, live_accuracy: { reviewed: 0 } },
    { week: '2026-W42', checked_any: 0, auto_share: null, people_share: null }]);
  assert.ok(Math.abs(weeks[0].autoShare + weeks[0].peopleShare - 1) < 1e-9);
  assert.equal(weeks[0].autoPct + weeks[0].peoplePct, 100);
  assert.equal(weeks[0].autoText, '40%');
  assert.equal(weeks[0].peopleText, '60%');
  assert.equal(weeks[0].golden, '50% of 2 (10% to 90%)');
  assert.equal(weeks[0].live, '—');
  assert.equal(weeks[1].empty, true);
  assert.equal(weeks[1].autoText, '—');
});

test('opening flagged answers as a review batch calls the route with the batch and the mode', async () => {
  const sent = [];
  const saved = traceLabApi.openReviewBatch;
  traceLabApi.openReviewBatch = async (b, m) => { sent.push([b, m]); return { ok: true, review_batch_ids: ['rb-1', 'rb-2'] }; };
  try {
    const c = ctl({ account: { role: 'superadmin' }, tlMonitorBatchId: 'b1', tlMonitorBatch: {
      batch: { id: 'b1', period_label: '2026-W41', results: { modes: [{ mode_id: 'm1', mode_name: 'wrong_period', crossed: true, flagged: 3, corrected: 0.17 }] } },
      flagged: [{ turn_id: 't1', question: 'How many work orders in July?', mode_name: 'wrong_period', group: 'uniform sample' }] } });
    const v = traceLabWatchVals(c, new Date());
    assert.equal(v.tlwBatch.modes[0].canReview, true);
    await v.tlwBatch.modes[0].review();
    assert.deepEqual(sent, [['b1', 'm1']]);
    assert.deepEqual(traceLabWatchVals(c, new Date()).tlwBatch.modes[0].reviewIds, ['rb-1', 'rb-2']);
    traceLabWatchVals(c, new Date()).tlwBatch.flagged[0].openAnswer();
    assert.deepEqual(c.calls.slice(-2), [['view', 'conversations'], ['turn', 't1']]);
  } finally {
    traceLabApi.openReviewBatch = saved;
  }
});

test('a company admin sees no Run now, and the platform owner sees no eval-case actions', () => {
  const admin = traceLabWatchVals(ctl({ account: { role: 'admin' } }), new Date());
  assert.equal(admin.tlwCanStart, false);
  assert.equal(admin.tlwCanCases, true);
  const owner = traceLabWatchVals(ctl({ account: { role: 'superadmin' }, viewOrgId: null }), new Date());
  assert.equal(owner.tlwCanStart, true);
  assert.equal(owner.tlwCanCases, false);
  assert.match(owner.tlwCasesHint, /view as a company/);
  assert.equal(owner.tlwScope, 'All companies');
  const acting = traceLabWatchVals(ctl({ account: { role: 'superadmin' }, viewOrgId: 'org-a', viewOrgName: 'Northbridge' }), new Date());
  assert.equal(acting.tlwCanStart, true);
  assert.equal(acting.tlwCanCases, true);
  assert.equal(acting.tlwScope, 'Company: Northbridge');
});

test('a proposed case is never confirmed without a written expected answer', async () => {
  const sent = [];
  const savedConfirm = traceLabApi.confirmEvalCase;
  const savedList = traceLabApi.evalCases;
  traceLabApi.confirmEvalCase = async (id, body) => { sent.push([id, body]); return { ok: true }; };
  traceLabApi.evalCases = async () => ({ ok: true, cases: [] });
  try {
    const c = ctl({ tlCases: [{ id: 'c1', case_key: 'e-7f3a19c2', version: 1, status: 'proposed', kind: 'unclassified', rev: 2,
      golden_case_id: null, input: { question: 'How many work orders in July?' } }] });
    await c.tlWatchConfirmCase('c1');
    assert.deepEqual(sent, []);
    assert.match(c.state.tlCasesErr, /right answer/);
    c.tlWatchSetCaseDraft('c1', 'expected_answer', 'There were 786.');
    await c.tlWatchConfirmCase('c1');
    assert.deepEqual(sent, [['c1', { base: 2, expected_answer: 'There were 786.', evaluation: 'objective' }]]);
  } finally {
    traceLabApi.confirmEvalCase = savedConfirm;
    traceLabApi.evalCases = savedList;
  }
});

test('a refused write leaves the plan open, and a start that succeeds closes it', async () => {
  const saved = traceLabApi.startMonitorBatch;
  const c = ctl({ tlMonitorPlanOpen: true, tlMonitorPlanDraft: { rate: '0.5' } });
  c.tlWatchLoad = async () => { c.calls.push(['reload']); };            // the reload after a start is not under test
  try {
    traceLabApi.startMonitorBatch = async () => { throw new Error('over the cost cap'); };
    await c.tlWatchStart();
    assert.equal(c.state.tlMonitorPlanOpen, true, 'a failed start must not read as a success');
    assert.equal(c.state.tlMonitorStarting, false);
    assert.deepEqual(c.calls.filter((x) => x[0] === 'writeError'), [['writeError', 'over the cost cap']]);
    assert.ok(!c.calls.some((x) => x[0] === 'reload'));
    let sent = null;
    traceLabApi.startMonitorBatch = async (body) => { sent = body; return { ok: true, batch: { id: 'b1' } }; };
    await c.tlWatchStart();
    assert.deepEqual(sent, { sample_rate: 0.5 });
    assert.equal(c.state.tlMonitorPlanOpen, false);
    assert.ok(c.calls.some((x) => x[0] === 'reload'));
    assert.deepEqual(c.calls.filter((x) => x[0] === 'writeKey').map((x) => x[1]), ['watch:start', 'watch:start']);
  } finally {
    traceLabApi.startMonitorBatch = saved;
  }
});

test('every Watch write names its own key, and the errors of those keys show on the tab', () => {
  const src = readFileSync(new URL('../src/logic/traceLabWatch.js', import.meta.url), 'utf8');
  const calls = src.match(/this\.tlWrite\(/g) || [];
  const keyed = src.match(/this\.tlWrite\([^;]*?\{ key: 'watch:/g) || [];
  assert.ok(calls.length >= 8 && keyed.length === calls.length, 'a write without a key would share the key "write"');
  const writes = {
    'watch:start': { busy: false, error: 'Could not save — over the cost cap', conflict: false, canRetry: true },
    'verdict:turn-1': { busy: false, error: 'not a Watch write', conflict: false, canRetry: true } };
  const v = traceLabWatchVals(ctl({ tlWrites: writes }), new Date());
  assert.deepEqual(v.tlwWrites.map((w) => [w.key, w.error, typeof w.retry]),
    [['watch:start', 'Could not save — over the cost cap', 'function']]);
});

test('the reset clears every Watch key the module writes', () => {
  const src = readFileSync(new URL('../src/logic/traceLabWatch.js', import.meta.url), 'utf8');
  const used = new Set(src.match(/\btl(?:Monitor|Cases|Release|Auto)[A-Za-z]*\b/g));
  const patch = tlWatchResetPatch();
  for (const key of used) assert.ok(Object.prototype.hasOwnProperty.call(patch, key), key);
  assert.notEqual(tlWatchResetPatch().tlMonitorCharts, patch.tlMonitorCharts, 'each reset gets fresh objects');
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs`
Expected: `ERR_MODULE_NOT_FOUND` for `../src/logic/traceLabWatch.js`.

- [ ] **Step 3: Implement**

Create `FE/src/logic/traceLabWatch.js`:

```js
// traceLabWatch — the Trace Lab's Watch tab (M6): monitoring batches with the corrected failure rate per week and a
// threshold per failure mode, code-check rates, eval cases (regression / capability), release checks, and the
// "checked automatically vs by people" trend. Methods are mixed into HoistraLogic.prototype (`this` is the
// controller). The pure helpers and traceLabWatchVals are tested in test/traceLabWatch.test.mjs.
// Every number comes from the server; nothing here estimates anything, and a missing number reads "—".
import { traceLabApi } from '../api/traceLab.js';
import { isStaleScope } from '../api/client.js';

export const CHART = { width: 560, height: 200, padL: 44, padR: 16, padT: 14, padB: 30 };

const STATUS_TEXT = { queued: 'Waiting', running: 'Running', done: 'Done', capped: 'Stopped at its cost cap',
  failed: 'Failed', interrupted: 'Interrupted, resume it' };
const CASE_STATUS = { proposed: 'Proposed: confirm the failure', draft: 'Ready for a baseline', baselining: 'Baseline running',
  active: 'Active', retired: 'Retired', superseded: 'Replaced by a newer version' };
const DECISION_TEXT = { pass: '✓ Pass', block: '✗ Blocked', incomplete: '… Incomplete' };
const LIVE = ['proposed', 'draft', 'baselining', 'active'];

const errText = (e) => (e && e.message) || String(e);
const round1 = (v) => Math.round(v * 10) / 10;
const has = (v) => v !== null && v !== undefined && !Number.isNaN(Number(v));
export const pctText = (v) => (has(v) ? (Math.round(Number(v) * 1000) / 10) + '%' : '—');
const usdText = (v) => (has(v) ? '$' + Number(v).toFixed(2) : '—');
const companyName = (s) => (s.viewOrgId ? (s.viewOrgName || '') : ((s.account && s.account.organization_name) || ''));
const findCase = (s, id) => (s.tlCases || []).find((k) => k.id === id) || null;

export function chartGeometry(chart, dims) {
  const d = Object.assign({}, CHART, dims || {});
  const pts = (chart && chart.points) || [];
  const yMax = chart && Number(chart.y_max) > 0 ? Number(chart.y_max) : null;
  if (!pts.length || !yMax) return { empty: true, points: [], thresholdY: null, thresholdLabel: '', ticks: [], width: d.width, height: d.height, padL: d.padL, padR: d.padR };
  const innerW = d.width - d.padL - d.padR;
  const innerH = d.height - d.padT - d.padB;
  const y = (v) => (has(v) ? round1(d.padT + innerH - (Math.min(Number(v), yMax) / yMax) * innerH) : null);
  const x = (i) => round1(d.padL + (pts.length > 1 ? (i * innerW) / (pts.length - 1) : innerW / 2));
  const points = pts.map((p, i) => ({
    x: x(i), corrected: y(p.corrected), raw: y(p.raw), low: y(p.ci_low), high: y(p.ci_high),
    label: p.period_label || '', crossed: !!p.crossed, warning: p.validity_warning || '',
    title: (p.period_label || '') + ': ' + pctText(p.corrected) + ' corrected (' + pctText(p.ci_low) + ' to ' + pctText(p.ci_high) + '), '
      + pctText(p.raw) + ' flagged in the sample' + (p.crossed ? ', above the threshold' : '')
  }));
  const thr = has(chart.threshold) ? Number(chart.threshold) : null;
  return {
    empty: false, points, width: d.width, height: d.height, padL: d.padL, padR: d.padR,
    thresholdY: thr === null ? null : y(thr), thresholdLabel: thr === null ? '' : 'threshold ' + String(thr),
    ticks: [0, 0.5, 1].map((f) => ({ y: y(f * yMax), label: pctText(f * yMax) }))
  };
}

function accText(a, nKey) {
  if (!a || !Number(a[nKey])) return '—';
  return pctText(a.rate) + ' of ' + a[nKey] + ' (' + pctText(a.low) + ' to ' + pctText(a.high) + ')';
}

export function stackShares(weeks) {
  return (weeks || []).map((w) => {
    const n = Number(w.checked_any) || 0;
    const auto = n && has(w.auto_share) ? Number(w.auto_share) : null;
    const autoPct = auto === null ? 0 : Math.round(auto * 1000) / 10;
    const peoplePct = auto === null ? 0 : Math.round((100 - autoPct) * 10) / 10;
    return {
      week: w.week, n, empty: auto === null, autoShare: auto, peopleShare: auto === null ? null : Math.round((1 - auto) * 10000) / 10000,
      autoPct, peoplePct, autoText: auto === null ? '—' : autoPct + '%', peopleText: auto === null ? '—' : peoplePct + '%',
      sources: w.auto_by_source || {}, people: w.people_by_source || {},
      modesAutomated: Number(w.modes_automated) || 0, modesConfirmed: Number(w.modes_confirmed) || 0,
      golden: accText(w.golden_accuracy, 'decided'), live: accText(w.live_accuracy, 'reviewed')
    };
  });
}

export function dueLabel(due) {
  if (!due || !due.period_label) return null;
  return due.is_due ? { text: 'Due: ' + due.period_label + ' has not been checked yet', isDue: true }
    : { text: 'Up to date: ' + due.period_label + ' checked', isDue: false };
}

export function latestModes(batches) {
  const out = [];
  const seen = new Set();
  (batches || []).forEach((b) => ((b.results && b.results.modes) || []).forEach((m) => {
    if (!m.mode_id || seen.has(m.mode_id)) return;
    seen.add(m.mode_id);
    out.push({ modeId: m.mode_id, name: m.mode_name || 'failure mode', crossed: !!m.crossed, batchId: b.id });
  }));
  return out;
}

function saveFile(file, fallbackName) {
  if (!file || !file.blob || typeof document === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return false;
  const href = URL.createObjectURL(file.blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = file.filename || fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 0);
  return true;
}

export function tlWatchResetPatch() {
  return {
    tlMonitorBatches: null, tlMonitorLoading: false, tlMonitorErr: '', tlMonitorLoadedAt: 0, tlMonitorChecks: null,
    tlMonitorCharts: {}, tlMonitorPlanOpen: false, tlMonitorPlanLoading: false, tlMonitorPlanErr: '', tlMonitorPlan: null,
    tlMonitorPlanDraft: {}, tlMonitorStarting: false, tlMonitorBatchId: null, tlMonitorBatch: null,
    tlMonitorBatchLoading: false, tlMonitorBatchErr: '', tlMonitorReviewing: null, tlMonitorReviewIds: {},
    tlMonitorResuming: null,
    tlCases: null, tlCasesLoading: false, tlCasesErr: '', tlCasesLoadedAt: 0, tlCasesDrafts: {}, tlCasesBusy: null,
    tlRelease: null, tlReleaseLoading: false, tlReleaseErr: '', tlReleaseLoadedAt: 0,
    tlReleaseDraft: { k: 5, include_capability: true }, tlReleaseStarting: false,
    tlAuto: null, tlAutoLoading: false, tlAutoErr: '', tlAutoLoadedAt: 0
  };
}

export const traceLabWatchMethods = {
  tlWatchEnsure() {
    const s = this.state;
    if (s.tlMonitorLoading || s.tlMonitorLoadedAt) return;
    this.tlWatchLoad();
  },
  async tlWatchLoad() {
    const seq = (this._tlwSeq = (this._tlwSeq || 0) + 1);
    const s = this.state;
    const acct = s.account || {};
    const company = !(acct.role === 'superadmin' && !s.viewOrgId);
    this.setState({ tlMonitorLoading: true, tlMonitorErr: '' });
    this.tlWatchLoadAuto(seq);
    if (company) { this.tlWatchLoadCases(); this.tlWatchLoadReleases(); }
    try {
      const [batches, checks] = await Promise.all([traceLabApi.monitorBatches({ limit: 20 }), traceLabApi.monitorCodeChecks(12)]);
      if (seq !== this._tlwSeq) return;
      this.setState({ tlMonitorLoading: false, tlMonitorBatches: batches || null, tlMonitorChecks: (checks && checks.checks) || [], tlMonitorLoadedAt: Date.now() });
      latestModes((batches && batches.batches) || []).forEach((m) => this.tlWatchLoadChart(m.modeId));
    } catch (e) {
      if (isStaleScope(e) || seq !== this._tlwSeq) return;
      this.setState({ tlMonitorLoading: false, tlMonitorErr: errText(e) });
    }
  },
  async tlWatchLoadAuto(seq) {
    this.setState({ tlAutoLoading: true, tlAutoErr: '' });
    try {
      const out = await traceLabApi.automationTrend(12);
      if (seq !== undefined && seq !== this._tlwSeq) return;
      this.setState({ tlAutoLoading: false, tlAuto: out || null, tlAutoLoadedAt: Date.now() });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tlAutoLoading: false, tlAutoErr: errText(e) });
    }
  },
  async tlWatchLoadChart(modeId) {
    const put = (patch) => this.setState((p) => ({ tlMonitorCharts: Object.assign({}, p.tlMonitorCharts, { [modeId]: Object.assign({}, (p.tlMonitorCharts || {})[modeId], patch) }) }));
    put({ loading: true, error: '' });
    try {
      put({ loading: false, error: '', chart: await traceLabApi.monitorChart(modeId, 12) });
    } catch (e) {
      if (isStaleScope(e)) return;
      put({ loading: false, error: errText(e) });
    }
  },
  async tlWatchLoadCases() {
    this.setState({ tlCasesLoading: true, tlCasesErr: '' });
    try {
      const out = await traceLabApi.evalCases({});
      this.setState({ tlCasesLoading: false, tlCases: (out && out.cases) || [], tlCasesLoadedAt: Date.now() });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tlCasesLoading: false, tlCasesErr: errText(e) });
    }
  },
  async tlWatchLoadReleases() {
    this.setState({ tlReleaseLoading: true, tlReleaseErr: '' });
    try {
      const out = await traceLabApi.releaseChecks({ limit: 20 });
      this.setState({ tlReleaseLoading: false, tlRelease: out || null, tlReleaseLoadedAt: Date.now() });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tlReleaseLoading: false, tlReleaseErr: errText(e) });
    }
  },
  async tlWatchPlan() {
    const d = this.state.tlMonitorPlanDraft || {};
    this.setState({ tlMonitorPlanOpen: true, tlMonitorPlanLoading: true, tlMonitorPlanErr: '' });
    try {
      const out = await traceLabApi.monitorPlan(d.rate ? { rate: Number(d.rate) } : {});
      this.setState({ tlMonitorPlanLoading: false, tlMonitorPlan: out || null });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tlMonitorPlanLoading: false, tlMonitorPlanErr: errText(e) });
    }
  },
  tlWatchSetPlanDraft(field, value) { this.setState((p) => ({ tlMonitorPlanDraft: Object.assign({}, p.tlMonitorPlanDraft, { [field]: value }) })); },
  tlWatchClosePlan() { this.setState({ tlMonitorPlanOpen: false }); },
  async tlWatchStart() {
    const d = this.state.tlMonitorPlanDraft || {};
    const body = {};
    if (d.rate) body.sample_rate = Number(d.rate);
    if (d.cap) body.usd_cap = Number(d.cap);
    this.setState({ tlMonitorStarting: true });
    // M1's tlWrite never throws and always resolves an object: { ok: true, out } or { ok: false, ... }.
    const r = await this.tlWrite(() => traceLabApi.startMonitorBatch(body), { key: 'watch:start' });
    this.setState({ tlMonitorStarting: false });
    if (!r.ok) return;
    this.setState({ tlMonitorPlanOpen: false });
    this.tlWatchLoad();
  },
  async tlWatchResume(batchId) {
    this.setState({ tlMonitorResuming: batchId });
    const r = await this.tlWrite(() => traceLabApi.resumeMonitorBatch(batchId), { key: 'watch:resume:' + batchId });
    this.setState({ tlMonitorResuming: null });
    if (r.ok) this.tlWatchLoad();
  },
  async tlWatchOpenBatch(batchId) {
    this.setState({ tlMonitorBatchId: batchId, tlMonitorBatch: null, tlMonitorBatchLoading: true, tlMonitorBatchErr: '' });
    try {
      const out = await traceLabApi.monitorBatch(batchId);
      this.setState({ tlMonitorBatchLoading: false, tlMonitorBatch: out || null });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tlMonitorBatchLoading: false, tlMonitorBatchErr: errText(e) });
    }
  },
  tlWatchCloseBatch() { this.setState({ tlMonitorBatchId: null, tlMonitorBatch: null }); },
  async tlWatchOpenReviewBatch(batchId, modeId) {
    this.setState({ tlMonitorReviewing: modeId });
    const r = await this.tlWrite(() => traceLabApi.openReviewBatch(batchId, modeId), { key: 'watch:review:' + batchId + ':' + modeId });
    this.setState((p) => ({ tlMonitorReviewing: null,
      tlMonitorReviewIds: r.ok ? Object.assign({}, p.tlMonitorReviewIds, { [batchId + ':' + modeId]: (r.out && r.out.review_batch_ids) || [] }) : p.tlMonitorReviewIds }));
  },
  // M1's tlOpenTurn only switches answers inside the conversation already open; a flagged answer needs tlOpenTurnById.
  tlWatchOpenAnswer(turnId) { this.tlSetView('conversations'); this.tlOpenTurnById(turnId); },
  tlWatchOpenRun(runId) { this.setState({ tlRunId: runId }); this.tlSetView('runs'); },
  tlWatchSetCaseDraft(caseId, field, value) {
    this.setState((p) => ({ tlCasesDrafts: Object.assign({}, p.tlCasesDrafts, { [caseId]: Object.assign({}, (p.tlCasesDrafts || {})[caseId], { [field]: value }) }) }));
  },
  async tlWatchConfirmCase(caseId) {
    const k = findCase(this.state, caseId);
    if (!k) return;
    const d = (this.state.tlCasesDrafts || {})[caseId] || {};
    if (!k.golden_case_id && !String(d.expected_answer || '').trim()) {
      this.setState({ tlCasesErr: 'Write what a right answer says before confirming.' });
      return;
    }
    this.setState({ tlCasesBusy: caseId, tlCasesErr: '' });
    const r = await this.tlWrite(() => traceLabApi.confirmEvalCase(caseId, { base: k.rev, expected_answer: String(d.expected_answer || '').trim() || null, evaluation: d.evaluation || 'objective' }),
      { key: 'watch:confirm:' + caseId });
    this.setState({ tlCasesBusy: null });
    if (r.ok) this.tlWatchLoadCases();
  },
  async tlWatchBaseline(caseId) {
    this.setState({ tlCasesBusy: caseId, tlCasesErr: '' });
    const r = await this.tlWrite(() => traceLabApi.baselineEvalCase(caseId, {}), { key: 'watch:baseline:' + caseId });
    this.setState({ tlCasesBusy: null });
    if (r.ok && r.out && r.out.golden_run) this.tlWatchOpenRun(r.out.golden_run.id);
  },
  async tlWatchRepeat(caseId) {
    const d = (this.state.tlCasesDrafts || {})[caseId] || {};
    const n = Math.round(Number(d.n === undefined ? 10 : d.n));
    if (!(n >= 2 && n <= 20)) { this.setState({ tlCasesErr: 'Repeat an answer between 2 and 20 times.' }); return; }
    this.setState({ tlCasesBusy: caseId, tlCasesErr: '' });
    const r = await this.tlWrite(() => traceLabApi.repeatEvalCase(caseId, { n }), { key: 'watch:repeat:' + caseId });
    this.setState({ tlCasesBusy: null });
    if (r.ok && r.out && r.out.golden_run) this.tlWatchOpenRun(r.out.golden_run.id);
  },
  async tlWatchRetire(caseId) {
    const k = findCase(this.state, caseId);
    if (!k) return;
    const reason = String((((this.state.tlCasesDrafts || {})[caseId]) || {}).reason || '').trim();
    if (reason.length < 3) { this.setState({ tlCasesErr: 'Say why the case is retired.' }); return; }
    this.setState({ tlCasesBusy: caseId, tlCasesErr: '' });
    const r = await this.tlWrite(() => traceLabApi.retireEvalCase(caseId, { base: k.rev, reason }), { key: 'watch:retire:' + caseId });
    this.setState({ tlCasesBusy: null });
    if (r.ok) this.tlWatchLoadCases();
  },
  tlWatchSetReleaseDraft(field, value) { this.setState((p) => ({ tlReleaseDraft: Object.assign({ k: 5, include_capability: true }, p.tlReleaseDraft, { [field]: value }) })); },
  async tlWatchStartRelease() {
    const d = this.state.tlReleaseDraft || {};
    const k = Math.round(Number(d.k === undefined ? 5 : d.k));
    if (!(k >= 1 && k <= 10)) { this.setState({ tlReleaseErr: 'A release check runs each case 1 to 10 times.' }); return; }
    this.setState({ tlReleaseStarting: true, tlReleaseErr: '' });
    const r = await this.tlWrite(() => traceLabApi.startReleaseCheck({ k, include_capability: d.include_capability !== false }), { key: 'watch:release' });
    this.setState({ tlReleaseStarting: false });
    if (r.ok && r.out && r.out.golden_run) this.tlWatchOpenRun(r.out.golden_run.id);
  },
  async tlWatchDownloadRelease(runId, format) {
    try {
      const file = await traceLabApi.releaseCheckFile(runId, format, companyName(this.state));
      saveFile(file, 'release-check-' + runId + (format === 'md' ? '.md' : '.json'));
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ tlReleaseErr: errText(e) });
    }
  }
};

function batchVals(c, s, superadmin) {
  const detail = s.tlMonitorBatch;
  const b = detail && detail.batch;
  const reviewIds = s.tlMonitorReviewIds || {};
  return {
    loading: !!s.tlMonitorBatchLoading, error: s.tlMonitorBatchErr || '', period: b ? b.period_label : '',
    flagged: ((detail && detail.flagged) || []).map((f) => ({
      turnId: f.turn_id, question: f.question || '(no question recorded)', mode: f.mode_name || 'failure mode',
      critique: f.critique || '', group: f.group || '', openAnswer: () => c.tlWatchOpenAnswer(f.turn_id) })),
    modes: ((b && b.results && b.results.modes) || []).map((m) => ({
      name: m.mode_name || 'failure mode', crossed: !!m.crossed, corrected: pctText(m.corrected),
      interval: pctText(m.ci_low) + ' to ' + pctText(m.ci_high), raw: pctText(m.raw),
      nSample: has(m.n_sample) ? String(m.n_sample) : '—', threshold: has(m.threshold) ? 'threshold ' + String(m.threshold) : '—',
      warning: m.validity_warning || '', reason: m.reason || '', canReview: superadmin && Number(m.flagged) > 0,
      review: () => c.tlWatchOpenReviewBatch(b.id, m.mode_id), reviewing: s.tlMonitorReviewing === m.mode_id,
      reviewIds: reviewIds[b.id + ':' + m.mode_id] || m.review_batch_ids || [] })),
    checks: ((b && b.results && b.results.code_checks) || []).map((x) => ({
      label: x.label, rate: pctText(x.rate), flagged: x.flagged, decided: x.decided, tag: x.validated ? 'validated' : 'raw rate only' })),
    close: () => c.tlWatchCloseBatch()
  };
}

export function traceLabWatchVals(c, now) {
  const s = c.state;
  const acct = s.account || {};
  const superadmin = acct.role === 'superadmin';
  const platform = superadmin && !s.viewOrgId;
  const admin = acct.role === 'admin' || superadmin;
  const body = s.tlMonitorBatches || null;
  const batches = (body && body.batches) || [];
  const charts = s.tlMonitorCharts || {};
  const plan = s.tlMonitorPlan;
  const draft = s.tlMonitorPlanDraft || {};
  const cases = s.tlCases || [];
  const drafts = s.tlCasesDrafts || {};
  const releases = (s.tlRelease && s.tlRelease.checks) || [];
  const rdraft = Object.assign({ k: 5, include_capability: true }, s.tlReleaseDraft || {});
  const weeks = stackShares((s.tlAuto && s.tlAuto.weeks) || []);
  return {
    tlwOn: s.tlView === 'watch',
    tlwEnsure: () => c.tlWatchEnsure(),
    tlwReload: () => c.tlWatchLoad(),
    tlwLoading: !!s.tlMonitorLoading && !s.tlMonitorLoadedAt,
    tlwError: s.tlMonitorErr || '',
    // The inline errors of Watch writes (M1's tlWrite keeps them in tlWrites[key]); a 409 has no Retry, like M1's.
    tlwWrites: Object.keys(s.tlWrites || {}).filter((k) => k.indexOf('watch:') === 0 && s.tlWrites[k].error)
      .map((k) => ({ key: k, error: s.tlWrites[k].error, retry: s.tlWrites[k].canRetry ? () => c.tlRetryWrite(k) : null })),
    tlwScope: platform ? 'All companies' : 'Company: ' + (companyName(s) || 'this company'),
    tlwCanStart: superadmin,
    tlwCanCases: admin && !platform,
    tlwCasesHint: platform ? 'Eval cases and release checks belong to one company: view as a company to work on them.' : '',
    tlwDue: dueLabel(body && body.due),
    tlwRunNow: () => c.tlWatchPlan(),
    tlwPlan: s.tlMonitorPlanOpen ? {
      loading: !!s.tlMonitorPlanLoading, error: s.tlMonitorPlanErr || '',
      data: plan ? { period: plan.period_label, nWindow: plan.n_window, nRandom: plan.n_random, nRiskOnly: plan.n_risk_only,
        nToJudge: plan.n_to_judge, cost: usdText(plan.usd_estimate), judges: (plan.judges || []).map((j) => j.mode_name || j.judge_key),
        warnings: plan.warnings || [] } : null,
      rate: draft.rate === undefined ? '' : String(draft.rate), cap: draft.cap === undefined ? '' : String(draft.cap),
      setRate: (e) => c.tlWatchSetPlanDraft('rate', e.target.value), setCap: (e) => c.tlWatchSetPlanDraft('cap', e.target.value),
      refresh: () => c.tlWatchPlan(), start: () => c.tlWatchStart(), starting: !!s.tlMonitorStarting, close: () => c.tlWatchClosePlan()
    } : null,
    tlwBatches: batches.map((b) => ({
      id: b.id, period: b.period_label, status: b.status, statusText: STATUS_TEXT[b.status] || b.status,
      scope: b.scope === 'platform' ? 'All companies' : 'One company',
      nWindow: b.results && has(b.results.n_window) ? b.results.n_window : null,
      nRandom: b.results && has(b.results.n_random) ? b.results.n_random : null,
      cost: usdText(b.usd), crossed: !!b.crossed, open: () => c.tlWatchOpenBatch(b.id),
      resume: superadmin && b.status === 'interrupted' ? () => c.tlWatchResume(b.id) : null, resuming: s.tlMonitorResuming === b.id })),
    tlwBatchesEmpty: !!s.tlMonitorLoadedAt && !batches.length,
    tlwEmptyText: 'No batches yet. Monitoring has not run for this scope.',
    tlwBatch: s.tlMonitorBatchId ? batchVals(c, s, superadmin) : null,
    tlwCharts: latestModes(batches).map((m) => {
      const ch = charts[m.modeId] || {};
      return { modeId: m.modeId, name: m.name, crossed: m.crossed, loading: !!ch.loading && !ch.chart, error: ch.error || '', geometry: chartGeometry(ch.chart) };
    }),
    tlwChecks: (s.tlMonitorChecks || []).map((x) => ({
      label: x.label, tag: x.validated ? 'validated' : 'raw rate only',
      weeks: (x.weeks || []).slice(-6).map((w) => ({ period: w.period_label, rate: pctText(w.rate), n: w.n })) })),
    tlwCases: cases.map((k) => {
      const d = drafts[k.id] || {};
      const lr = k.latest_run || {};
      return {
        id: k.id, key: k.case_key + ' v' + k.version, mode: k.mode_name || '—', kind: k.kind,
        kindText: k.kind === 'regression' ? 'Regression' : k.kind === 'capability' ? 'Capability (baseline ' + pctText(k.baseline_pass_rate) + ')' : 'Not baselined',
        status: k.status, statusText: CASE_STATUS[k.status] || k.status, question: (k.input && k.input.question) || '',
        passAt1: pctText(lr.pass_at_1), passAt3: pctText(lr.pass_at_3), passAt5: pctText(lr.pass_at_5), passHat: pctText(lr.pass_hat_k),
        canConfirm: k.status === 'proposed', canBaseline: k.status === 'draft',
        canRepeat: LIVE.includes(k.status) && !!k.golden_case_id, canRetire: LIVE.includes(k.status),
        answer: d.expected_answer || '', setAnswer: (e) => c.tlWatchSetCaseDraft(k.id, 'expected_answer', e.target.value),
        evaluation: d.evaluation || 'objective', setEvaluation: (e) => c.tlWatchSetCaseDraft(k.id, 'evaluation', e.target.value),
        n: d.n === undefined ? '10' : String(d.n), setN: (e) => c.tlWatchSetCaseDraft(k.id, 'n', e.target.value),
        reason: d.reason || '', setReason: (e) => c.tlWatchSetCaseDraft(k.id, 'reason', e.target.value),
        confirm: () => c.tlWatchConfirmCase(k.id), baseline: () => c.tlWatchBaseline(k.id),
        repeat: () => c.tlWatchRepeat(k.id), retire: () => c.tlWatchRetire(k.id), busy: s.tlCasesBusy === k.id
      };
    }),
    tlwCasesLoading: !!s.tlCasesLoading && !s.tlCasesLoadedAt,
    tlwCasesError: s.tlCasesErr || '',
    tlwCasesEmpty: !!s.tlCasesLoadedAt && !cases.length,
    tlwRelease: {
      list: releases.map((r) => ({
        id: r.golden_run_id, label: r.run_key, decision: r.decision, decisionText: DECISION_TEXT[r.decision] || r.decision,
        tone: r.decision === 'pass' ? 'ok' : r.decision === 'block' ? 'risk' : 'warn', reason: r.reason || '', k: r.k,
        when: r.started_at || '', downloadJson: () => c.tlWatchDownloadRelease(r.golden_run_id, 'json'),
        downloadMd: () => c.tlWatchDownloadRelease(r.golden_run_id, 'md'), openRun: () => c.tlWatchOpenRun(r.golden_run_id) })),
      loading: !!s.tlReleaseLoading && !s.tlReleaseLoadedAt, error: s.tlReleaseErr || '',
      empty: !!s.tlReleaseLoadedAt && !releases.length,
      k: String(rdraft.k), setK: (e) => c.tlWatchSetReleaseDraft('k', e.target.value),
      includeCapability: rdraft.include_capability !== false,
      toggleCapability: () => c.tlWatchSetReleaseDraft('include_capability', rdraft.include_capability === false),
      start: () => c.tlWatchStartRelease(), starting: !!s.tlReleaseStarting
    },
    tlwAuto: {
      loading: !!s.tlAutoLoading && !s.tlAutoLoadedAt, error: s.tlAutoErr || '', weeks,
      empty: !!s.tlAutoLoadedAt && !weeks.some((w) => !w.empty), definitions: (s.tlAuto && s.tlAuto.definitions) || {}
    }
  };
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs` → `12 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/logic/traceLabWatch.js`, `FE/test/traceLabWatch.test.mjs`.

---

### Task 20: `Watch.jsx`, mounted in the Trace Lab shell and registered

**Files:**
- Create: `FE/src/screens/traceLab/Watch.jsx`
- Modify: `FE/src/logic/HoistraLogic.js` (`:20` import, `:383` method list)
- Modify: `FE/src/logic/renderVals.js` (`:19` import, `:1993` vals)
- Modify: `FE/src/logic/traceLab.js` (M1's file: the `watch` view and tab, the `tlReadView` branch, and the reset patch inside `freshPatch()`)
- Modify: `FE/src/logic/session.js` (M1's `TL_VIEW_KEYS`)
- Modify: `FE/src/screens/TraceLab.jsx` (M1: view switch)
- Test: `FE/test/traceLabWatch.test.mjs` (append); existing `FE/test/noInternalNames.test.mjs`, M1's `FE/test/traceLabShell.test.mjs` and `FE/test/traceLabViews.test.mjs`

**Interfaces:**
- Consumes: Task 19 `traceLabWatchMethods`, `traceLabWatchVals`, `tlWatchResetPatch`, `CHART`; M1's `traceLab.js` (`TL_VIEWS`, `TL_TABS`, `tlTabsFor`, `tlReadView`, `freshPatch()`) as M2–M5 left it (M4's platform-aware `tlTabsFor`; M5's Judges tab entry, which is the last one), `session.js` `TL_VIEW_KEYS`, the view switch in `src/screens/TraceLab.jsx` as M5 left it.
- Produces: default export `Watch({ vals })`; the Watch tab in `TL_VIEWS`, `TL_TABS` (marked `platform: true`, M4's flag that also offers a tab to the platform team when no company is viewed; a company admin has a company and so reads it either way) and `TL_VIEW_KEYS`; `tlReadView()` loading only the Watch on that tab; `...tlWatchResetPatch(),` inside `freshPatch()` (so every Watch key resets on a company switch or sign-out); `TraceLab.jsx` mounting `Watch` and hiding `FilterBar` / `UpdateLine` on it. Reading is open to a company admin (R19) and to the platform team; starting a batch is platform-only, enforced by the routes (`platform_only`) and by `tlwCanStart` (superadmin) in the view.

- [ ] **Step 0: Confirm the anchors** (read-only)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend
grep -n "import { tracesPageMethods } from './tracesPage.js';" src/logic/HoistraLogic.js
grep -c "tracesPageMethods, " src/logic/HoistraLogic.js
grep -n "import { tracesPageVals } from './tracesPage.js';" src/logic/renderVals.js
grep -n "      ...tracesPageVals(this)," src/logic/renderVals.js
grep -n "^import { RANGES, sinceFor } from './tracesPage.js';\|^export const TL_VIEWS = \|key: 'judges', label: 'Judges', platform: true\|^export function tlTabsFor\|if (view === 'overview') { this.tlStopPoll(); this.tpLoad(); return; }\|return Object.assign({}, TL_SCALARS, TP_SCALARS, {" src/logic/traceLab.js
grep -n "^const TL_VIEW_KEYS = " src/logic/session.js
grep -n "^import Judges from './traceLab/Judges.jsx';\|vals.tlView === 'judges' ? null : <FilterBar vals={vals} />\|vals.tlView === 'judges' ? null : <UpdateLine vals={vals} />\|vals.tlView === 'judges' ? <Judges" src/screens/TraceLab.jsx
grep -n "'watch'" src/logic/traceLab.js src/logic/session.js
grep -n "\['Judges'" test/traceLabShell.test.mjs test/traceLabViews.test.mjs
```

Expected: the first four print one line each (the `grep -c` prints `1`); the `traceLab.js` grep prints six lines (the import, `TL_VIEWS`, M5's Judges tab entry as the last tab, `tlTabsFor`, the overview branch of `tlReadView`, the `freshPatch()` return line); `session.js` prints its `TL_VIEW_KEYS` line; `TraceLab.jsx` prints M5's four lines; the `'watch'` grep prints nothing (M1 knows no `watch` view); the final grep prints a line only where one of M1's tests still pins an exact tab list. If any other anchor differs, stop and report it.

- [ ] **Step 1: Write the failing test** (append to `FE/test/traceLabWatch.test.mjs`)

```js
test('the Watch tab is registered, mounted, reset with the rest of the Trace Lab, and free of internal names', () => {
  const read = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
  const logic = read('logic/HoistraLogic.js');
  assert.match(logic, /import \{ traceLabWatchMethods \} from '\.\/traceLabWatch\.js';/);
  assert.match(logic, /tracesPageMethods, traceLabWatchMethods, /);
  const vals = read('logic/renderVals.js');
  assert.match(vals, /import \{ traceLabWatchVals \} from '\.\/traceLabWatch\.js';/);
  assert.match(vals, /\.\.\.traceLabWatchVals\(this, new Date\(\)\),/);
  const lab = read('logic/traceLab.js');
  assert.match(lab, /\.\.\.tlWatchResetPatch\(\),/);
  assert.match(lab, /export const TL_VIEWS = \[[^\]]*'watch'/);
  assert.match(lab, /\{ key: 'watch', label: 'Watch', platform: true \}/);
  assert.match(lab, /if \(view === 'watch'\) \{ this\.tlStopPoll\(\); this\.tlWatchEnsure\(\); return; \}/);
  assert.match(read('logic/session.js'), /const TL_VIEW_KEYS = \[[^\]]*'watch'/);
  const shell = read('screens/TraceLab.jsx');
  assert.match(shell, /import Watch from '\.\/traceLab\/Watch\.jsx';/);
  assert.match(shell, /\{vals\.tlView === 'watch' \? <Watch vals=\{vals\} \/> : null\}/);
  assert.match(shell, /vals\.tlView === 'judges' \|\| vals\.tlView === 'watch' \? null : <FilterBar vals=\{vals\} \/>/);
  assert.match(shell, /vals\.tlView === 'judges' \|\| vals\.tlView === 'watch' \? null : <UpdateLine vals=\{vals\} \/>/);
  const view = read('screens/traceLab/Watch.jsx');
  for (const text of ['No batches yet', 'Run now', 'Open flagged answers as a review batch', 'Baseline (5 runs)',
    'Run a release check', 'Checked automatically vs by people', 'strokeDasharray']) {
    assert.ok(view.includes(text), text);
  }
  for (const bad of [/\bsvc-[a-z]/i, /plenum_/, /\bUDR\b/, /deep-?agents/, /judge:/, /code:[a-z]/]) {
    assert.doesNotMatch(view, bad);
  }
});

test('the Watch tab: admins read it, the platform team owns it, users never see it; starting stays platform-only', async () => {
  const { tlTabsFor, tlParseLink, TL_VIEWS } = await import('../src/logic/traceLab.js');
  const has = (s) => tlTabsFor(s).some((t) => t.key === 'watch');
  assert.equal(has({ account: { role: 'admin' } }), true);                              // R19: a company admin reads its company's batches
  assert.equal(has({ account: { role: 'superadmin' }, viewOrgId: 'org-2' }), true);
  assert.equal(has({ account: { role: 'superadmin' } }), true);                         // the platform team, with no company viewed
  assert.equal(has({ account: { role: 'user' } }), false);
  assert.equal(has({}), false);
  assert.ok(TL_VIEWS.includes('watch'));
  assert.deepEqual(tlParseLink('?tl=watch'), { view: 'watch', run: 'live', conv: null, turn: null });
  assert.equal(traceLabWatchVals(ctl({ account: { role: 'admin' } }), new Date()).tlwCanStart, false);
  assert.equal(traceLabWatchVals(ctl({ account: { role: 'superadmin' } }), new Date()).tlwCanStart, true);
});

test('reading the Watch tab stops the conversation poll and loads only the Watch', async () => {
  const { traceLabMethods } = await import('../src/logic/traceLab.js');
  const c = ctl({ account: { role: 'admin' }, tlView: 'watch' });
  Object.assign(c, { tlSyncUrl() {}, tlStopPoll() { c.calls.push('stopPoll'); }, tlWatchEnsure() { c.calls.push('ensure'); },
    tpLoad() { c.calls.push('tpLoad'); }, tlLoadLists() { c.calls.push('tlLoadLists'); } });
  traceLabMethods.tlReadView.call(c);
  assert.deepEqual(c.calls, ['stopPoll', 'ensure']);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs`
Expected: the new test fails on the first `assert.match` (no `traceLabWatchMethods` import yet).

- [ ] **Step 3: Implement**

Create `FE/src/screens/traceLab/Watch.jsx`:

```jsx
// Watch — the Trace Lab's Watch tab (logic/traceLabWatch.js). Monitoring batches with the corrected failure rate per
// week and a threshold per failure mode, code-check rates, eval cases, release checks, and the "checked
// automatically vs by people" trend. A pure view: every number comes from vals; a missing one reads "—".
import React from 'react';

const CARD = { borderRadius: '10px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-sm)', padding: '16px 18px', marginBottom: '14px' };
const KICK = { fontSize: '10.5px', letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--color-neutral-500)' };
const QUIET = { fontSize: '12.5px', color: 'var(--color-neutral-500)' };
const BTN = { fontSize: '12px', padding: '6px 11px', borderRadius: '7px', border: '1px solid var(--color-divider)', background: 'transparent', color: 'var(--color-neutral-300)', cursor: 'pointer' };
const PRIMARY = { ...BTN, border: '1px solid var(--color-accent)', color: 'var(--color-accent)' };
const LINK = { ...BTN, border: 'none', padding: 0, color: 'var(--color-accent)', textAlign: 'left' };
const TH = { ...KICK, fontWeight: '400', textAlign: 'left', padding: '0 10px 8px 0', borderBottom: '1px solid var(--color-divider)', whiteSpace: 'nowrap' };
const TD = { padding: '8px 10px 8px 0', borderBottom: '1px solid var(--color-divider)', fontSize: '12.5px', verticalAlign: 'top' };
const NUM = { ...TD, textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' };
const INPUT = { fontSize: '12.5px', padding: '6px 8px', borderRadius: '6px', border: '1px solid var(--color-divider)', background: 'var(--color-bg)', color: 'var(--color-text)' };
const TABLE = { borderCollapse: 'collapse', width: '100%' };
const pill = (tone) => ({ fontSize: '11px', padding: '2px 8px', borderRadius: '999px', whiteSpace: 'nowrap', marginLeft: '6px', background: 'var(--st-' + tone + '-bg)', color: 'var(--st-' + tone + ')' });

function Section({ title, right, children }) {
  return (
    <section style={CARD} aria-label={title}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, flex: 1 }}>{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function Problem({ text, retry }) {
  if (!text) return null;
  return (
    <div role="alert" style={{ ...QUIET, color: 'var(--st-risk)', margin: '6px 0' }}>
      {text}
      {retry ? <button type="button" style={{ ...BTN, marginLeft: '8px' }} onClick={retry}>Retry</button> : null}
    </div>
  );
}

function PrevalenceChart({ chart }) {
  const g = chart.geometry;
  if (chart.loading) return <div style={QUIET}>Reading…</div>;
  if (chart.error) return <Problem text={chart.error} />;
  if (g.empty) return <div style={QUIET}>No batches yet</div>;
  return (
    <svg viewBox={'0 0 ' + g.width + ' ' + g.height} width="100%" role="img" aria-label={'Corrected failure rate by week for ' + chart.name}>
      {g.ticks.map((t, i) => (
        <g key={'tick' + i}>
          <line x1={g.padL} x2={g.width - g.padR} y1={t.y} y2={t.y} stroke="var(--color-divider)" strokeWidth="1" />
          <text x={g.padL - 6} y={t.y + 3} textAnchor="end" fontSize="9" fill="var(--color-neutral-500)">{t.label}</text>
        </g>
      ))}
      {g.thresholdY !== null ? (
        <g>
          <line x1={g.padL} x2={g.width - g.padR} y1={g.thresholdY} y2={g.thresholdY} stroke="var(--st-warn)" strokeDasharray="5 4" strokeWidth="1.5" />
          <text x={g.width - g.padR} y={g.thresholdY - 4} textAnchor="end" fontSize="9.5" fill="var(--st-warn)">{g.thresholdLabel}</text>
        </g>
      ) : null}
      {g.points.map((p, i) => (
        <g key={'point' + i}>
          <title>{p.title}</title>
          {p.low !== null && p.high !== null ? <line x1={p.x} x2={p.x} y1={p.low} y2={p.high} stroke="var(--color-neutral-400)" strokeWidth="1.5" /> : null}
          {p.raw !== null ? <circle cx={p.x} cy={p.raw} r="3.5" fill="none" stroke="var(--color-neutral-400)" strokeWidth="1.2" /> : null}
          {p.corrected !== null ? <circle cx={p.x} cy={p.corrected} r="4.5" fill={p.crossed ? 'var(--st-risk)' : 'var(--color-accent)'} /> : null}
          <text x={p.x} y={g.height - 10} textAnchor="middle" fontSize="9" fill="var(--color-neutral-500)">{p.label}</text>
        </g>
      ))}
    </svg>
  );
}

function PlanPanel({ plan }) {
  const d = plan.data;
  return (
    <div role="dialog" aria-label="Run monitoring now" style={{ ...CARD, borderLeft: '3px solid var(--color-accent)' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '10px' }}>
        <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, flex: 1 }}>Run monitoring now</h3>
        <button type="button" style={BTN} aria-label="Close" onClick={plan.close}>Close</button>
      </div>
      {plan.loading && !d ? <div style={QUIET}>Reading…</div> : null}
      <Problem text={plan.error} retry={plan.refresh} />
      {d ? (
        <div style={{ fontSize: '12.5px', lineHeight: 1.6 }}>
          <div>{d.period + ': ' + d.nWindow + ' chat answers. Every one gets the code checks; ' + d.nRandom + ' are drawn at random for the judges, plus ' + d.nRiskOnly + ' from the risk groups.'}</div>
          <div>{'Judges in use: ' + (d.judges.length ? d.judges.join(', ') : 'none yet') + '. Estimated cost: ' + d.cost + '.'}</div>
          {d.warnings.map((w, i) => <div key={i} style={{ ...QUIET, color: 'var(--st-warn)' }}>{w}</div>)}
        </div>
      ) : null}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginTop: '10px', flexWrap: 'wrap' }}>
        <label style={QUIET}>Sample rate <input style={{ ...INPUT, width: '70px' }} value={plan.rate} placeholder="0.25" onChange={plan.setRate} onBlur={plan.refresh} /></label>
        <label style={QUIET}>Cost cap ($) <input style={{ ...INPUT, width: '70px' }} value={plan.cap} placeholder="5" onChange={plan.setCap} /></label>
        <button type="button" style={PRIMARY} disabled={plan.starting || !d} onClick={plan.start}>{plan.starting ? 'Starting…' : 'Start'}</button>
      </div>
    </div>
  );
}

function BatchPanel({ b }) {
  return (
    <Section title={'Batch ' + (b.period || '')} right={<button type="button" style={BTN} aria-label="Close batch" onClick={b.close}>Close</button>}>
      {b.loading ? <div style={QUIET}>Reading…</div> : null}
      <Problem text={b.error} />
      {b.modes.length ? (
        <table style={TABLE}>
          <thead><tr>
            <th style={TH}>Failure mode</th><th style={{ ...TH, textAlign: 'right' }}>Corrected rate</th><th style={{ ...TH, textAlign: 'right' }}>95% interval</th>
            <th style={{ ...TH, textAlign: 'right' }}>Flagged in the sample</th><th style={{ ...TH, textAlign: 'right' }}>Answers judged</th><th style={TH}>Threshold</th><th style={TH}></th>
          </tr></thead>
          <tbody>{b.modes.map((m, i) => (
            <tr key={i}>
              <td style={TD}>{m.name}{m.crossed ? <span style={pill('risk')}>Above threshold</span> : null}
                {m.warning ? <div style={{ ...QUIET, color: 'var(--st-warn)' }}>{m.warning}</div> : null}
                {m.reason ? <div style={QUIET}>{m.reason}</div> : null}</td>
              <td style={NUM}>{m.corrected}</td><td style={NUM}>{m.interval}</td><td style={NUM}>{m.raw}</td><td style={NUM}>{m.nSample}</td>
              <td style={TD}>{m.threshold}</td>
              <td style={TD}>{m.reviewIds.length ? <span style={QUIET}>{'Review batch opened (' + m.reviewIds.length + ')'}</span>
                : m.canReview ? <button type="button" style={BTN} disabled={m.reviewing} onClick={m.review}>Open flagged answers as a review batch</button> : null}</td>
            </tr>))}</tbody>
        </table>
      ) : null}
      <h4 style={{ ...KICK, margin: '14px 0 6px' }}>Flagged answers</h4>
      {b.flagged.length ? (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>{b.flagged.map((f) => (
          <li key={f.turnId + f.mode} style={{ padding: '8px 0', borderBottom: '1px solid var(--color-divider)' }}>
            <button type="button" style={LINK} onClick={f.openAnswer}>{f.question}</button>
            <div style={QUIET}>{f.mode + ' · ' + f.group}</div>
            {f.critique ? <div style={{ fontSize: '12px', marginTop: '3px' }}>{f.critique}</div> : null}
          </li>))}</ul>
      ) : <div style={QUIET}>No flagged answers in this batch.</div>}
    </Section>
  );
}

function AutomationBars({ weeks }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {weeks.map((w) => (
        <div key={w.week} style={{ display: 'grid', gridTemplateColumns: '78px 1fr 170px', gap: '10px', alignItems: 'center' }}>
          <span style={{ fontSize: '11.5px', fontVariantNumeric: 'tabular-nums' }}>{w.week}</span>
          {w.empty ? <span style={QUIET}>No answer was checked this week</span> : (
            <div role="img" aria-label={w.week + ': ' + w.autoText + ' checked automatically, ' + w.peopleText + ' by people'}
              style={{ display: 'flex', height: '14px', borderRadius: '4px', overflow: 'hidden', background: 'var(--color-neutral-900)' }}>
              <div style={{ width: w.autoPct + '%', background: 'var(--color-accent)' }} />
              <div style={{ width: w.peoplePct + '%', background: 'var(--color-neutral-400)' }} />
            </div>
          )}
          <span style={{ fontSize: '11.5px', color: 'var(--color-neutral-400)' }}>{w.empty ? '—' : w.autoText + ' automatic · ' + w.peopleText + ' people'}</span>
        </div>
      ))}
    </div>
  );
}

export default function Watch({ vals }) {
  const v = vals;
  React.useEffect(() => { v.tlwEnsure(); }, []);
  const rel = v.tlwRelease;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '14px', flexWrap: 'wrap' }}>
        <span style={KICK}>{v.tlwScope}</span>
        {v.tlwDue ? <span style={pill(v.tlwDue.isDue ? 'warn' : 'ok')}>{v.tlwDue.text}</span> : null}
        <span style={{ flex: 1 }} />
        <button type="button" style={BTN} onClick={v.tlwReload}>Refresh</button>
        {v.tlwCanStart ? <button type="button" style={PRIMARY} onClick={v.tlwRunNow}>Run now</button> : null}
      </div>
      {v.tlwPlan ? <PlanPanel plan={v.tlwPlan} /> : null}
      <Problem text={v.tlwError} retry={v.tlwReload} />
      {v.tlwWrites.map((w) => <Problem key={w.key} text={w.error} retry={w.retry} />)}

      <Section title="Monitoring batches">
        {v.tlwLoading ? <div style={QUIET}>Reading…</div> : v.tlwBatchesEmpty ? <div style={QUIET}>{v.tlwEmptyText}</div> : (
          <table style={TABLE}>
            <thead><tr>
              <th style={TH}>Week</th><th style={TH}>Scope</th><th style={TH}>Status</th><th style={{ ...TH, textAlign: 'right' }}>Answers</th>
              <th style={{ ...TH, textAlign: 'right' }}>Sampled</th><th style={{ ...TH, textAlign: 'right' }}>Cost</th><th style={TH}></th>
            </tr></thead>
            <tbody>{v.tlwBatches.map((b) => (
              <tr key={b.id}>
                <td style={TD}><button type="button" style={LINK} onClick={b.open}>{b.period}</button></td>
                <td style={TD}>{b.scope}</td>
                <td style={TD}>{b.statusText}{b.crossed ? <span style={pill('risk')}>Above threshold</span> : null}</td>
                <td style={NUM}>{b.nWindow === null ? '—' : b.nWindow}</td>
                <td style={NUM}>{b.nRandom === null ? '—' : b.nRandom}</td>
                <td style={NUM}>{b.cost}</td>
                <td style={TD}>{b.resume ? <button type="button" style={BTN} disabled={b.resuming} onClick={b.resume}>Resume</button> : null}</td>
              </tr>))}</tbody>
          </table>
        )}
      </Section>
      {v.tlwBatch ? <BatchPanel b={v.tlwBatch} /> : null}

      <Section title="Corrected failure rate by week">
        {v.tlwCharts.length ? v.tlwCharts.map((ch) => (
          <div key={ch.modeId} style={{ marginBottom: '16px' }}>
            <div style={{ fontSize: '12.5px', fontWeight: 600, marginBottom: '4px' }}>{ch.name}{ch.crossed ? <span style={pill('risk')}>Above threshold</span> : null}</div>
            <PrevalenceChart chart={ch} />
          </div>)) : <div style={QUIET}>No batches yet</div>}
        <div style={{ ...QUIET, marginTop: '6px' }}>Filled dot: the corrected failure rate, with its 95% interval as the line through it. Hollow dot: the share flagged in the sample. Dashed line: the threshold.</div>
      </Section>

      <Section title="Code-check rates">
        {v.tlwChecks.length ? (
          <table style={TABLE}>
            <thead><tr><th style={TH}>Check</th><th style={TH}>Status</th>
              {(v.tlwChecks[0].weeks || []).map((w) => <th key={w.period} style={{ ...TH, textAlign: 'right' }}>{w.period}</th>)}</tr></thead>
            <tbody>{v.tlwChecks.map((x) => (
              <tr key={x.label}><td style={TD}>{x.label}</td><td style={TD}>{x.tag}</td>
                {x.weeks.map((w) => <td key={w.period} style={NUM} title={w.n + ' answers'}>{w.rate}</td>)}</tr>))}</tbody>
          </table>
        ) : <div style={QUIET}>No batches yet</div>}
      </Section>

      <Section title="Eval cases">
        {!v.tlwCanCases ? <div style={QUIET}>{v.tlwCasesHint}</div>
          : v.tlwCasesLoading ? <div style={QUIET}>Reading…</div>
          : v.tlwCasesEmpty ? <div style={QUIET}>No eval cases yet. A case starts from an answer marked wrong, or from an answer a batch flagged.</div> : (
            <table style={TABLE}>
              <thead><tr>
                <th style={TH}>Case</th><th style={TH}>Failure mode</th><th style={TH}>Kind</th><th style={TH}>Status</th>
                <th style={{ ...TH, textAlign: 'right' }}>pass@1</th><th style={{ ...TH, textAlign: 'right' }}>pass@3</th>
                <th style={{ ...TH, textAlign: 'right' }}>pass@5</th><th style={{ ...TH, textAlign: 'right' }}>pass^5</th><th style={TH}></th>
              </tr></thead>
              <tbody>{v.tlwCases.map((k) => (
                <tr key={k.id}>
                  <td style={TD}>{k.key}<div style={QUIET}>{k.question}</div></td>
                  <td style={TD}>{k.mode}</td><td style={TD}>{k.kindText}</td><td style={TD}>{k.statusText}</td>
                  <td style={NUM}>{k.passAt1}</td><td style={NUM}>{k.passAt3}</td><td style={NUM}>{k.passAt5}</td><td style={NUM}>{k.passHat}</td>
                  <td style={{ ...TD, minWidth: '240px' }}>
                    {k.canConfirm ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '6px' }}>
                        <textarea aria-label="What a right answer says" placeholder="What a right answer says" value={k.answer} onChange={k.setAnswer} style={{ ...INPUT, minHeight: '54px' }} />
                        <select aria-label="How the answer is checked" value={k.evaluation} onChange={k.setEvaluation} style={INPUT}>
                          <option value="objective">Against this exact answer</option>
                          <option value="judgment">Against this as a criterion</option>
                        </select>
                        <button type="button" style={PRIMARY} disabled={k.busy} onClick={k.confirm}>Confirm the failure</button>
                      </div>
                    ) : null}
                    {k.canBaseline ? <button type="button" style={{ ...PRIMARY, marginRight: '6px' }} disabled={k.busy} onClick={k.baseline}>Baseline (5 runs)</button> : null}
                    {k.canRepeat ? (
                      <span style={{ marginRight: '6px' }}>
                        <input aria-label="Times to repeat" value={k.n} onChange={k.setN} style={{ ...INPUT, width: '44px' }} />
                        <button type="button" style={{ ...BTN, marginLeft: '4px' }} disabled={k.busy} onClick={k.repeat}>Repeat</button>
                      </span>
                    ) : null}
                    {k.canRetire ? (
                      <span>
                        <input aria-label="Why retire it" placeholder="Why retire it" value={k.reason} onChange={k.setReason} style={{ ...INPUT, width: '120px' }} />
                        <button type="button" style={{ ...BTN, marginLeft: '4px' }} disabled={k.busy} onClick={k.retire}>Retire</button>
                      </span>
                    ) : null}
                  </td>
                </tr>))}</tbody>
            </table>
          )}
        <Problem text={v.tlwCasesError} />
      </Section>

      <Section title="Release checks" right={v.tlwCanCases ? (
        <span style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={QUIET}>Runs per case <input style={{ ...INPUT, width: '44px' }} value={rel.k} onChange={rel.setK} /></label>
          <label style={QUIET}><input type="checkbox" checked={rel.includeCapability} onChange={rel.toggleCapability} /> Include capability cases</label>
          <button type="button" style={PRIMARY} disabled={rel.starting} onClick={rel.start}>{rel.starting ? 'Starting…' : 'Run a release check'}</button>
        </span>) : null}>
        {!v.tlwCanCases ? <div style={QUIET}>{v.tlwCasesHint}</div>
          : rel.loading ? <div style={QUIET}>Reading…</div>
          : rel.empty ? <div style={QUIET}>No release check yet.</div> : (
            <table style={TABLE}>
              <thead><tr><th style={TH}>Run</th><th style={TH}>Decision</th><th style={TH}>Why</th><th style={{ ...TH, textAlign: 'right' }}>Runs per case</th><th style={TH}>Report</th></tr></thead>
              <tbody>{rel.list.map((r) => (
                <tr key={r.id}>
                  <td style={TD}><button type="button" style={LINK} onClick={r.openRun}>{r.label}</button><div style={QUIET}>{r.when}</div></td>
                  <td style={TD}><span style={{ ...pill(r.tone), marginLeft: 0 }}>{r.decisionText}</span></td>
                  <td style={TD}>{r.reason}</td><td style={NUM}>{r.k}</td>
                  <td style={TD}>
                    <button type="button" style={{ ...BTN, marginRight: '6px' }} onClick={r.downloadJson}>Download JSON</button>
                    <button type="button" style={BTN} onClick={r.downloadMd}>Download Markdown</button>
                  </td>
                </tr>))}</tbody>
            </table>
          )}
        <Problem text={rel.error} />
        <div style={{ ...QUIET, marginTop: '8px' }}>Run it on the deployment you are about to ship. Regression cases must pass every run; capability cases never block. Attach the JSON report to the deploy.</div>
      </Section>

      <Section title="Checked automatically vs by people">
        {v.tlwAuto.loading ? <div style={QUIET}>Reading…</div>
          : v.tlwAuto.error ? <Problem text={v.tlwAuto.error} retry={v.tlwReload} />
          : v.tlwAuto.empty ? <div style={QUIET}>No answer has been checked yet.</div> : (
            <div>
              <AutomationBars weeks={v.tlwAuto.weeks} />
              <table style={{ ...TABLE, marginTop: '12px' }}>
                <thead><tr>
                  <th style={TH}>Week</th><th style={{ ...TH, textAlign: 'right' }}>Answers checked</th><th style={TH}>Golden answers right</th>
                  <th style={TH}>Fair-sample share right (sample fraction)</th><th style={{ ...TH, textAlign: 'right' }}>Failure modes automated</th>
                </tr></thead>
                <tbody>{v.tlwAuto.weeks.map((w) => (
                  <tr key={w.week}><td style={TD}>{w.week}</td><td style={NUM}>{w.n}</td><td style={TD}>{w.golden}</td><td style={TD}>{w.live}</td>
                    <td style={NUM}>{w.modesAutomated + ' of ' + w.modesConfirmed}</td></tr>))}</tbody>
              </table>
            </div>
          )}
        <div style={{ ...QUIET, marginTop: '8px' }}>{v.tlwAuto.definitions.auto_by_source || ''}</div>
      </Section>
    </div>
  );
}
```

Registration (exact-string edits; these are shared files, so edit only these lines):

`FE/src/logic/HoistraLogic.js` — old:
```js
import { tracesPageMethods } from './tracesPage.js';
```
new:
```js
import { tracesPageMethods } from './tracesPage.js';
import { traceLabWatchMethods } from './traceLabWatch.js';
```
and old (inside the `Object.assign(HoistraLogic.prototype, …)` list at `:383`):
```js
tracesPageMethods, 
```
new:
```js
tracesPageMethods, traceLabWatchMethods, 
```

`FE/src/logic/renderVals.js` — old:
```js
import { tracesPageVals } from './tracesPage.js';
```
new:
```js
import { tracesPageVals } from './tracesPage.js';
import { traceLabWatchVals } from './traceLabWatch.js';
```
and old:
```js
      ...tracesPageVals(this),
```
new:
```js
      ...tracesPageVals(this),
      ...traceLabWatchVals(this, new Date()),
```

`FE/src/logic/traceLab.js` (M1's file, as M2–M5 left it) — six exact-string edits, the same pattern M5 used for Judges:

old:
```js
import { RANGES, sinceFor } from './tracesPage.js';
```
new:
```js
import { RANGES, sinceFor } from './tracesPage.js';
import { tlWatchResetPatch } from './traceLabWatch.js';
```

old (earlier milestones put their own views at the front of the list the same way, so this keeps whatever is there):
```js
export const TL_VIEWS = [
```
new:
```js
export const TL_VIEWS = ['watch', 
```

old (M5's entry, the last tab; whatever follows it, the closing bracket, stays after the inserted entry):
```js
  { key: 'judges', label: 'Judges', platform: true }
```
new:
```js
  { key: 'judges', label: 'Judges', platform: true },
  { key: 'watch', label: 'Watch', platform: true }
```
(`platform: true` is M4's flag: `tlTabsFor` offers the tab to the platform team when no company is viewed, as well as to every admin and acting superadmin through `tlHasCompany`. The tab itself is readable by a company admin; the Run now button and every start are platform-only, in `tlwCanStart` and in the routes.)

old:
```js
    if (view === 'overview') { this.tlStopPoll(); this.tpLoad(); return; }
```
new:
```js
    if (view === 'overview') { this.tlStopPoll(); this.tpLoad(); return; }
    if (view === 'watch') { this.tlStopPoll(); this.tlWatchEnsure(); return; }
```

old (the reset patch is spread right after this line, where M2–M5 put theirs; `tlResetPatch()` is `freshPatch()`, so there is no `return {` line to edit):
```js
  return Object.assign({}, TL_SCALARS, TP_SCALARS, {
```
new:
```js
  return Object.assign({}, TL_SCALARS, TP_SCALARS, {
    ...tlWatchResetPatch(),
```

`FE/src/logic/session.js` (M1's restored-page allow-list) — old:
```js
const TL_VIEW_KEYS = [
```
new:
```js
const TL_VIEW_KEYS = ['watch', 
```

`FE/src/screens/TraceLab.jsx` (M1, as M5 left it) — four edits:

old:
```jsx
import Judges from './traceLab/Judges.jsx';
```
new:
```jsx
import Judges from './traceLab/Judges.jsx';
import Watch from './traceLab/Watch.jsx';
```
old:
```jsx
{vals.tlView === 'judges' ? <Judges vals={vals} /> : null}
```
new:
```jsx
{vals.tlView === 'judges' ? <Judges vals={vals} /> : null}
{vals.tlView === 'watch' ? <Watch vals={vals} /> : null}
```
old:
```jsx
{vals.tlView === 'judges' ? null : <FilterBar vals={vals} />}
```
new:
```jsx
{vals.tlView === 'judges' || vals.tlView === 'watch' ? null : <FilterBar vals={vals} />}
```
old:
```jsx
{vals.tlView === 'judges' ? null : <UpdateLine vals={vals} />}
```
new:
```jsx
{vals.tlView === 'judges' || vals.tlView === 'watch' ? null : <UpdateLine vals={vals} />}
```

M1's `FE/test/traceLabShell.test.mjs` and `FE/test/traceLabViews.test.mjs`: M2 (ledger decision 12) and M4 relaxed their tab-list assertions so they derive the lists from `TL_TABS` and the `platform` flag; the Watch entry then needs no edit. If `grep -n "\['Judges'" test/traceLabShell.test.mjs test/traceLabViews.test.mjs` still prints an exact list, append `'watch'` / `['Watch', …]` after the Judges element in each of those assertions, the way M5 appended Judges, and record the line as found in the task log.

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabWatch.test.mjs test/noInternalNames.test.mjs test/traceLabShell.test.mjs test/mount.test.mjs`
Expected: all pass (15 Watch tests; `noInternalNames` scans `Watch.jsx` and `traceLabWatch.js`, which are not on its SKIP list; the shell test's "tlResetPatch clears every tl* key" scan now sees the Watch keys reset).

- [ ] **Step 5: Checkpoint** — leave uncommitted; task log: `FE/src/screens/traceLab/Watch.jsx`, `FE/src/logic/HoistraLogic.js`, `FE/src/logic/renderVals.js`, `FE/src/logic/traceLab.js`, `FE/src/screens/TraceLab.jsx`, `FE/test/traceLabWatch.test.mjs`.

---

### Task 21: Milestone gate — full suites, build, guards, stub-backend walk

**Files:**
- Create: `~/Desktop/hoist/eval-demo/walks/verify-tracelab-watch.mjs` (Frontend §7 "Browser walks")
- Modify: `~/Desktop/hoist/eval-demo/stub-eval.mjs` (insert after `:261`, the `const D = '/backend/deep-agents';` line)
- Test: every M6 test file plus the full suites

**Interfaces:**
- Consumes: everything above; the stub-backend recipe `~/Desktop/hoist/eval-demo/start-demo.sh` and `stub-eval.mjs` (memory: hoistra-ui-verification — playwright-core with the installed Chrome, mock auth through `VITE_DEV_PROXY_TARGET`).
- Produces: a green milestone and a list of changed files for Hussain.

- [ ] **Step 1: Write the failing check** — the stub replies and the walk

In `~/Desktop/hoist/eval-demo/stub-eval.mjs`, directly after the line `  const D = '/backend/deep-agents';` (inside the request handler, `:261`), insert the Watch replies (canned, in memory, never a database):

```js
  // Trace Lab Watch (M6): canned replies for walks/verify-tracelab-watch.mjs.
  if (p.startsWith(D + '/api/trace-lab/monitor/') || p === D + '/api/trace-lab/eval-cases' || p === D + '/api/trace-lab/release-checks') {
    const W41 = { id: 'mb-1', organization_id: null, scope: 'platform', period_label: '2026-W41', status: 'done', usd: 0.42, crossed: true,
      results: { n_window: 412, n_random: 103, code_checks: [], modes: [{ mode_id: 'm-1', mode_name: 'wrong_period', crossed: true, flagged: 19,
        corrected: 0.17, ci_low: 0.06, ci_high: 0.28, raw: 0.19, n_sample: 101, threshold: 0.15, review_batch_ids: [] }] } };
    if (p === D + '/api/trace-lab/monitor/batches') return send(res, 200, { ok: true, batches: [W41], due: { period_label: '2026-W41', is_due: false } });
    if (p === D + '/api/trace-lab/monitor/batches/mb-1') return send(res, 200, { ok: true, batch: W41, flagged: [{ turn_id: 't1',
      question: 'How many work orders were raised there last month?', mode_name: 'wrong_period', critique: 'Counts cancelled jobs.', group: 'uniform sample' }] });
    if (p === D + '/api/trace-lab/monitor/batches/mb-1/review-batch' && req.method === 'POST') return send(res, 201, { ok: true, review_batch_ids: ['rb-1'] });
    if (p === D + '/api/trace-lab/monitor/chart') return send(res, 200, { ok: true, mode: { id: 'm-1', name: 'wrong_period' }, threshold: 0.15, y_max: 0.35, points: [
      { period_label: '2026-W40', raw: 0.12, corrected: 0.1, ci_low: 0.02, ci_high: 0.2, threshold: 0.15, crossed: false },
      { period_label: '2026-W41', raw: 0.19, corrected: 0.17, ci_low: 0.06, ci_high: 0.28, threshold: 0.15, crossed: true }] });
    if (p === D + '/api/trace-lab/monitor/code-checks') return send(res, 200, { ok: true, checks: [] });
    if (p === D + '/api/trace-lab/monitor/automation') return send(res, 200, { ok: true, definitions: {}, weeks: [
      { week: '2026-W40', checked_any: 4, auto_share: 0.25, people_share: 0.75 }, { week: '2026-W41', checked_any: 5, auto_share: 0.4, people_share: 0.6 }] });
    if (p === D + '/api/trace-lab/eval-cases') return send(res, 200, { ok: true, cases: [] });
    if (p === D + '/api/trace-lab/release-checks') return send(res, 200, { ok: true, checks: [] });
  }
```

Create `~/Desktop/hoist/eval-demo/walks/verify-tracelab-watch.mjs` (sign-in steps as in `walks/verify-watch.mjs:24-40`):

```js
// verify-tracelab-watch — the Trace Lab's Watch tab against the stub on :4010 (via Vite on :5180) as the platform
// team: the Due marker, Run now, the prevalence chart with its threshold label, a batch's flagged answers, and the
// review-batch action. Logs every non-GET request; fails on any write to the traces feedback route or to memories.
import { chromium } from 'playwright-core';

const b = await chromium.launch({ channel: 'chrome', headless: true });
const page = await b.newPage({ viewport: { width: 1440, height: 1100 } });
const writes = [];
const errors = [];
page.on('request', (r) => { if (r.method() !== 'GET') writes.push(r.method() + ' ' + new URL(r.url()).pathname); });
page.on('pageerror', (e) => errors.push(String(e)));
const ph = (t) => page.getByPlaceholder(t, { exact: true });
const text = (t) => page.getByText(t, { exact: true });

await page.goto('http://127.0.0.1:5180/', { waitUntil: 'domcontentloaded' });
await ph('you@portfolio.com').waitFor({ timeout: 30000 });
await ph('you@portfolio.com').fill('team@plenum.example');
await ph('Password').fill('correct-horse-battery-staple');
await text('Continue').click();
{ const full = page.getByRole('button', { name: 'Enter Full platform' }); await full.waitFor({ timeout: 8000 }).then(() => full.click(), () => {}); }
await page.getByTitle('Tess Team').waitFor({ timeout: 20000 });
let nav = text('Hoist Traces').first();
if (!(await nav.isVisible().catch(() => false))) {
  await page.getByTitle('Tess Team').click();
  const adminView = text('Admin view');
  if (await adminView.count()) await adminView.first().click();
  nav = text('Hoist Traces').first();
}
await nav.click();
await page.getByRole('tab', { name: 'Watch', exact: true }).click();
await text('Up to date: 2026-W41 checked').waitFor({ timeout: 20000 });
await page.getByRole('button', { name: 'Run now', exact: true }).waitFor();
await page.locator('svg[aria-label^="Corrected failure rate by week"]').first().waitFor();
if (!(await text('threshold 0.15').count())) throw new Error('the threshold line has no label');
await page.getByRole('button', { name: '2026-W41', exact: true }).click();
await text('How many work orders were raised there last month?').waitFor();
await page.getByRole('button', { name: 'Open flagged answers as a review batch', exact: true }).click();
await text('Review batch opened (1)').waitFor();
await b.close();
console.log('non-GET requests:', writes);
const bad = writes.filter((w) => /\/api\/traces\/.*\/feedback|\/memories/.test(w));
if (bad.length) throw new Error('forbidden writes: ' + bad.join(', '));
if (!writes.some((w) => w.endsWith('/monitor/batches/mb-1/review-batch'))) throw new Error('the review-batch action was not sent');
if (errors.length) throw new Error('page errors: ' + errors.join(' | '));
console.log('verify-tracelab-watch: ok');
```

- [ ] **Step 2: Run everything and see what fails**

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
da_test tests/test_trace_lab_m6_interfaces.py tests/test_trace_lab_stats_watch.py tests/test_trace_lab_watch_keep_list.py tests/test_trace_lab_monitor.py tests/test_trace_lab_eval_cases.py tests/test_trace_lab_monitor_routes.py tests/test_trace_lab_watch_sql.py
da_test tests
cd apps/frontend && npm test && npm run build
```

Expected first time: any failure points at a task above; fix it in that task's files and re-run. The full `da_test tests` count must equal the pre-M6 baseline (memory: deepagents 992 in testenv, plus M0–M5's own tests) plus the new M6 tests, with no new failures. Start the stub stack per `eval-demo/start-demo.sh`, then run `node ~/Desktop/hoist/eval-demo/walks/verify-tracelab-watch.mjs`.

- [ ] **Step 3: Implement** — only fixes the runs above call for (each in the owning task's files, test first).

- [ ] **Step 4: Run it and see it pass**

All seven M6 backend files green (scratch DB running, so the SQL file runs rather than skips); full `da_test tests` green against the baseline; `npm test` green; `npm run build` exits 0; the walk prints `verify-tracelab-watch: ok`. Also confirm the file rules once more: `grep -nE "plenum_cafm|IF NOT EXISTS|CONCURRENTLY|GRANT|REVOKE" apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/0006_watch.sql` prints nothing.

- [ ] **Step 5: Checkpoint** — leave uncommitted; the task log lists every file in **File Structure** plus the walk and the stub additions. Report to Hussain that `0006_watch.sql` creates four tables (and their indexes, guard trigger and foreign keys) only in `plenum_tracelab` and alters no existing table, and that `catalog.json` is regenerated at version 6.

---

### Task 22: Hussain's verification and deploy (not for a Claude session)

Gates: **P1** (hoist-f5's company-boundary work has landed on the branch being built) before any rebuild; **P3** (this SQL reviewed, `migrate plan` read, `SHOW server_version` read on hoistra_test, `0005_judges.sql` already applied) before applying `0006`; **P4** (Anthropic key in svc-deepagents, and the caps chosen: `TRACE_LAB_MONITOR_USD_CAP` (default 5), `TRACE_LAB_EVAL_GRADING_USD_CAP` (default 2), and M3's golden caps) before the first monitoring batch or graded eval run.

- [ ] **Step 1: Check for foreign edits before building** (memory: concurrent sessions share the working tree)

```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
git status --short
git diff --stat
```

Read the list; anything not in this milestone's task log belongs to another session — coordinate before building.

- [ ] **Step 2: Rebuild svc-deepagents and the frontend, reload the gateway**

```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps svc-deepagents
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps frontend-app
docker ps --format '{{.Names}}' | grep gateway
docker exec hoistra_plenum-gateway-app-1 nginx -s reload
docker logs --since 5m hoistra_plenum-svc-deepagents-1 2>&1 | grep -E "trace_lab.schema|trace.purge_skipped"
```

Expected log: `trace_lab.schema.unverified` with `status=behind`, `pending=[6]`, and `trace.purge_skipped`. The chat answers normally; the Watch tab shows "Trace Lab is not available right now. The chat is not affected."

- [ ] **Step 3: Review, dry-run and apply 0006** (interactive terminal; `apply` refuses without a TTY)

```sh
docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml \
  exec svc-deepagents python -m src.trace_lab.migrate plan
docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml \
  exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0006 --applied-by hussain --dry-run
docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml \
  exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0006 --applied-by hussain
```

`plan` must list no open Trace Lab job (the new foreign keys take a momentary lock on `jobs`, `modes`, `judges`, `golden_runs` and `golden_cases`; `0006` alters no other table). Type the database name when asked. Add the `SHIPPED_CHECKSUMS` line the CLI prints to `src/trace_lab/migrate.py` (a code change — rebuild again when convenient). Within 30 s the next Trace Lab request re-verifies (`trace_lab.schema.verified version=6`); or `docker compose … restart svc-deepagents` to re-run startup with the keep-list purge.

- [ ] **Step 4: Manual checks in the browser** (Administration › Hoist Traces › Watch)

1. As the platform superadmin (not viewing as a company): the Due marker names last week; **Run now** shows the plan with answer counts and an estimated cost before anything starts; **Start** creates a batch that finishes `Done` (or `Stopped at its cost cap`); a chart appears per failure mode with a judge in use, with the dashed threshold line where the judge has one (`PATCH /judges/{id}/threshold` from the Judges tab).
2. A mode above its threshold shows "Above threshold"; its review batch appears in Progress as a targeted batch (not counted toward rates); proposed cases appear under Eval cases as "Proposed: confirm the failure".
3. Viewing as a company: confirm a proposed case by writing its expected answer → "Ready for a baseline"; **Baseline (5 runs)** opens Runs with the stepping banner; after the five runs, the case reads Regression or Capability with pass@k figures.
4. **Run a release check** → the run steps in Runs → back on Watch the decision shows ✓ Pass / ✗ Blocked / … Incomplete; **Download JSON** saves `release-check-<run key>.json`. Attach that file to the deploy notes (for example alongside the push memory entry) — it is the record that regression cases passed k of k on this deployment.
5. As a company admin: no **Run now**; only that company's batches; the trend chart shows only that company's answers.
6. `docker logs --since 10m hoistra_plenum-svc-deepagents-1 2>&1 | grep -E "trace_lab.monitor|trace_lab.eval_cases"` shows `batch_started`, `batch_finished`, `classified`; no `batch_failed`.

- [ ] **Step 5: Azure** — not before P1 and the local checks above. Azure images share hoistra_test: with `TRACE_LAB_SCHEMA` unset they only verify; never set `TRACE_LAB_SCHEMA=apply` there. Redeploy the all-in-one image manually as usual (memory: Azure needs a manual all-in-one rebuild), then run a release check against Azure and attach its JSON report to that deploy too (spec §5.6 "Where it runs").

