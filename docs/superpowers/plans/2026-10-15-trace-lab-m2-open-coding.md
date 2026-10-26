# Trace Lab M2 — Open coding & sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reviewers judge answers (Right, or Wrong with a note on the first failure pinned to a verbatim quote) without overwriting each other and without seeing other reviewers' verdicts until they have saved their own; review batches are drawn by recorded, seeded, explainable strategies (only uniform batches count toward rates); the Map shows the period's answers projected and grouped; the Progress tab shows reviewed counts, missing judgments, the fair-sample wrong rate, agreement, coverage and the discovery curve.

**Architecture:** One new migration (`0002_review.sql`: `annotations`, `annotation_events`, `sample_batches`, `sample_items`, guarded by M0's `refuse_change()` / `supersede_only()` triggers). Two new svc-deepagents modules: `trace_lab/annotations.py` (versioned verdicts and comments, blind reads, quote checks against the redacted normalized turn from M1) and `trace_lab/sampling.py` (pure pickers, numpy SVD/k-means, progress metrics, then the database layer). `trace_lab/stats.py` is created with `wilson_interval` and `cohen_kappa`. One router, `api/routes/trace_lab_review.py`, under `/api/trace-lab`. Frontend: `logic/traceLabAnnotate.js` and `logic/traceLabSampling.js` mixins, `screens/traceLab/VerdictBar.jsx`, `MarginNotes.jsx`, `Map.jsx` (SVG), `Progress.jsx`, M2 calls appended to M1's `api/traceLab.js`, wired into M1's Trace Lab shell.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Postgres 16 (scratch) / hoistra_test, numpy 2 (new, R11), pytest + pytest-asyncio (auto mode); React 18, plain ES modules, `node --test`, esbuild (already a dev dependency via Vite), inline SVG.

**Spec:** `docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md` (sections: head — Decisions, Reconciliations R1/R2/R4/R10/R11/R20/R21/R27/R36/R37, Milestones M2 row, Prerequisites; "Data model, migrations, jobs and guards" §1.8, §2.1, §3, §5.3; "Trace capture, normalizer and conversations" §3.1–§3.2, §3.9, §4.1, §5.1, §5.3; "Open coding, sampling, taxonomy, labels and AI proposals" §1–§4, §9.1, §10.1–§10.3, §11, §12; "Frontend: the Trace Lab" §2, §3, §4.2, §4.5, §4.6, §5, §6, §7)

**Depends on:** M0 plan (`db`, `provenance`, `migrate`, 0001's guard functions, `tests/trace_lab_scratch.py`) and M1 plan (`normalize`, `conversations`, `api/traceLab.js`, `logic/traceLab.js`, `logic/traceLabConversations.js`, `logic/traceLabTimeline.js`, the Trace Lab screens) — names listed under Interfaces and pinned by Task 1. Prerequisites: P1 before any rebuild; P3 before 0002 is applied to hoistra_test; R27 (Hussain counts NULL-company turns, read-only) before M2 is used on the shared database.

## Global Constraints

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

Milestone-specific:

- **Scratch database name.** M0's SQL-test guard (spec "Data model" §1.8) fails any run whose database is not named `tracelab_scratch`. Start the throwaway Postgres with that name and point `TRACE_LAB_TEST_DB_URL` at it (same port as above):
  ```sh
  docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine
  ```
- **Test helper used by every backend "Run" step below.** It is the recipe above as a shell function (the `mkdir` paths spelled out, because the container's `sh` is dash, which does not expand `{a,b}`; the scratch URL names `tracelab_scratch`; `$*` is the test list). Define it once per shell, from the repo root `/Users/hussain/Desktop/hoist/Hoistra_Plenum`:
  ```sh
  tl_da_test() {
    C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-$(date +%s)
    B=$R/apps/backend/cafm-connector-service-final
    docker exec $C sh -lc "mkdir -p $B/svc-deepagents $B/svc-operations-intelligence $R/apps/frontend"
    for d in src tests skills migrations pyproject.toml; do docker cp apps/backend/cafm-connector-service-final/svc-deepagents/$d $C:$B/svc-deepagents/$d; done
    docker cp apps/frontend/src $C:$R/apps/frontend/src
    docker cp apps/backend/cafm-connector-service-final/svc-operations-intelligence/src $C:$B/svc-operations-intelligence/src
    N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
    docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy \
      -e TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch $C \
      sh -lc "cd $B/svc-deepagents && python -m pytest $* -q -p no:cacheprovider -p no:warnings"
  }
  ```
  numpy may already be in the testenv image as a transitive dependency; it is not declared until Task 2. Before the first sampling test, check: `docker exec hoistra-testenv-svc-deepagents-1 python -c "import numpy; print(numpy.__version__)"`. A 2.x version means nothing to do. Anything else: stop and ask Hussain before installing anything into that container (memory: test-paths-stay-inside-tmp-path — the testenv container is the running testenv service).
- **numpy is imported lazily** (spec "Data model" §1.7, R11): only inside `sampling.standardize`, `kmeans`, `pca2` and `pick_diversity`, never at module import, so `trace_lab.startup()` and every route import stay light.
- **0002 is append-only once applied.** After Hussain applies `0002_review.sql` to hoistra_test it is never edited (M0 pins its checksum); a correction is `0007_*.sql`.
- **Never reviewable:** golden-run turns and turns whose `organization_id` is NULL (R27) never enter a pool, a verdict, a batch or a metric; superadmin view-as traffic (R20) and turns of deleted chats (R21) are left out of pools unless the batch asks for them.
- **Blind review lives in the read layer** (`annotations._states`, `history`, `for_conversation`), never only in the UI.
- **M0/M1 files do not exist on eval-2 today.** Edits to them (Tasks 3, 4, 13, 14, 19) name the anchor to find with `grep -n` and show the exact code to add; paste the resulting diff into the task log.
- **No reveal in M2.** `POST /turns/{turn_id}/reveal` and the "Show their notes" button arrive with M3's expected-outcome panel. The read rule already honours `reveal` events, so M3 only adds the route.
- **Writes never fail silently in the UI**: a failed or stale write keeps the draft beside the error; polls never touch drafts.

## Review Focus

1. A repeated phrase highlighted at the wrong place (browser and server numbering occurrences differently) — `test_locate_quote_counts_overlapping_occurrences` (Task 5) and "occurrences count overlapping matches, the way the server numbers them" in `test/traceLabAnnotate.test.mjs` (Task 15).
2. Two tabs or two reviewers saving at once, silently overwriting — `test_two_first_saves_race_to_one_row_and_one_409`, `test_two_reviewers_code_the_same_answer_without_overwriting` (Task 6) and "a 409 shows the current version and the draft; Keep mine resends on top of it" (Task 15).
3. Another reviewer's note leaking before the caller judges (through comments, history or a batch) — `test_others_are_hidden_until_you_save_then_shown` (Task 7).
4. A biased or contaminated sample: a turn drawn twice, golden, NULL-company, deleted-chat or view-as traffic in the pool — `test_the_pool_is_one_companys_eligible_chat_answers` and `test_the_next_batch_never_redraws_a_sampled_turn_and_an_empty_pool_is_refused` (Task 10).
5. A half-written note lost on a poll or a failed save — "a poll does not wipe the draft" and "a failed save keeps the draft and offers Retry" (Task 15).

## File Structure

svc-deepagents (`apps/backend/cafm-connector-service-final/svc-deepagents/`):

| File | Change | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | add `numpy>=2,<3` |
| `src/trace_lab/stats.py` | create | `wilson_interval`, `cohen_kappa` (pure) |
| `src/trace_lab/db.py` (M0) | append | review errors + `http_error_for`; keep-list and offboarding entries for version 2 |
| `tests/test_trace_lab_purge_sql.py` (M0) | modify | `EXCUSED_TURN_COLUMNS` gains `annotation_events.turn_id` (R4) |
| `migrations/trace_lab/0002_review.sql` | create | the four M2 tables, indexes, guard triggers |
| `migrations/trace_lab/catalog.json` (M0) | regenerate | expected catalog at version 2 |
| `src/trace_lab/annotations.py` | create | verdicts, comments, blind reads, quote checks, reviewed predicate |
| `src/trace_lab/sampling.py` | create | features, k-means, PCA, pickers, metrics, pool/batches/queue/map/progress |
| `src/trace_lab/conversations.py` (M1) | modify | turn on the `reviewed` filter once version 2 is applied; show gate verdicts once the reviewer has saved a verdict (R23) |
| `src/api/routes/trace_lab_review.py` | create | the M2 routes |
| `src/api/main.py` | modify | import and include `trace_lab_review_router` |
| `tests/test_trace_lab_m2_contracts.py` | create | pins the M0/M1 names this plan imports |
| `tests/test_trace_lab_stats_wilson_and_kappa.py` | create | stats reference values, numpy declared |
| `tests/test_trace_lab_review_errors_and_keep_list.py` | create | error envelope, keep-list v2, excused pair, offboarding v2 |
| `tests/trace_lab_m2_fixtures.py` | create | scratch-DB row helpers for the M2 SQL tests, `truncate_chat` |
| `tests/test_trace_lab_review_history_is_append_only_sql.py` | create | 0002's constraints and triggers |
| `tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py` | create | anchors, quotes, validation (no DB) |
| `tests/test_trace_lab_an_edit_is_a_new_version_and_the_latest_wins_sql.py` | create | verdict versions, 409s, tombstones |
| `tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py` | create | the blind rule (pure) |
| `tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge_sql.py` | create | blind reads, comments, history (DB) |
| `tests/test_trace_lab_sampling_is_deterministic_and_recorded.py` | create | pickers, k-means (pure) |
| `tests/test_trace_lab_sampling_window_stays_inside_span_retention.py` | create | window and request checks (incl. recorded `monitor_flagged` requests) |
| `tests/test_trace_lab_outcome_fields_are_not_sampling_dimensions.py` | create | dimension allow-list |
| `tests/test_trace_lab_map_and_progress_metrics.py` | create | PCA, map payload, progress metrics (pure) |
| `tests/test_trace_lab_a_turn_is_never_in_two_batches_sql.py` | create | pool, batches, `monitor_flagged` batches, queue, manifest, progress (DB) |
| `tests/test_trace_lab_review_is_scoped_to_the_acting_company.py` | create | route auth, envelope, 503s |
| `tests/test_trace_lab_review_router_is_registered.py` | create | router paths and main.py registration |
| `tests/test_trace_lab_reviewed_filter_reaches_the_conversation_list_sql.py` | create | M1 list `reviewed` filter; gate verdicts shown after your own verdict (R23) |

apps/frontend:

| File | Change | Responsibility |
|---|---|---|
| `src/api/traceLab.js` (M1) | append | the 17 M2 calls, each with `orgOverride()` |
| `src/logic/traceLabAnnotate.js` | create | verdict/comment state, drafts, 409s, anchors, margin placement, view model |
| `src/logic/traceLabSampling.js` | create | plan, batches, preview/create, manifest, map and progress view models |
| `src/screens/traceLab/VerdictBar.jsx` | create | verdict bar and conversation comments |
| `src/screens/traceLab/MarginNotes.jsx` | create | annotated block text, margin notes, unplaced notes |
| `src/screens/traceLab/Map.jsx` | create | sampling panel and SVG map |
| `src/screens/traceLab/Progress.jsx` | create | tiles, batches, discovery curve, rates, coverage |
| `src/styles/tracelab.css` (M1) | append | highlight, margin note and chart focus styles |
| `src/logic/HoistraLogic.js` | modify | register the two mixins |
| `src/logic/renderVals.js` | modify | spread the two vals |
| `src/logic/traceLab.js`, `src/logic/session.js`, `src/logic/traceLabConversations.js`, `src/screens/TraceLab.jsx`, `src/screens/traceLab/Conversations.jsx`, `src/screens/traceLab/Timeline.jsx` (M1) | modify | `map`/`progress` in `TL_VIEWS`, `TL_TABS` and `TL_VIEW_KEYS`; reset patch, loaders on tab open, keyboard r/w, mounts |
| `test/traceLabCompanyScope.test.mjs`, `test/traceLabShell.test.mjs`, `test/traceLabViews.test.mjs` (M1) | modify | relaxed once for appended API keys; the exact tab lists gain Map and Progress |
| `test/traceLabM2Contracts.test.mjs` | create | pins the M1 frontend names |
| `test/traceLabReviewApi.test.mjs` | create | routes, methods, company override |
| `test/traceLabAnnotate.test.mjs` | create | annotate helpers and flows |
| `test/traceLabReviewScreens.test.mjs` | create | screens parse, structure, guard coverage |
| `test/traceLabSampling.test.mjs` | create | sampling, map and progress view models |
| `test/traceLabM2Wiring.test.mjs` | create | mixins, reset, vals wired |

Outside the repo (final task only): `~/Desktop/hoist/eval-demo/stub-tracelab-m2.mjs` and one line in `stub-eval.mjs` for the stub walk.

## Interfaces

**Assumed from M0** (pinned by `tests/test_trace_lab_m2_contracts.py`, Task 1; R36 — if M0 named one differently, use M0's name everywhere this plan uses it and never add a second helper):

| Name | Shape this plan relies on |
|---|---|
| `src/trace_lab/db.py` `SCHEMA` | `"plenum_tracelab"` |
| `db.engine()` | returns `database._get_engine()` at call time (so the scratch fixture's monkeypatch reaches it) |
| `db.ready()`, `async db.require_ready()` | FastAPI dependency raising 503 while Trace Lab is not verified |
| `db.state()` | the current `SchemaState` (or None); `.applied_version` |
| `db.http_error(status, reason, error, **extra) -> HTTPException` | the house envelope `{ok: False, error, reason, **extra}` (M0 owns the name; M2's converter is `http_error_for`) |
| `db.require_trace_store()` | 503 `trace_store_unavailable` while the trace tables are not ready |
| `db.require_reader(principal, organization_id) -> Scope`, `db.require_reviewer(principal, organization_id) -> str` | M0's auth rule and reasons: 403 `reviewers_only`, 403 `not_your_company`, 400 `bad_company`, 400 `name_a_company`; `Scope.org_id` is None for the platform view |
| `db.keep_tables()` | union of `KEEP_BY_VERSION[v]` for every applied `v` |
| `db.KEEP_BY_VERSION: dict[int, tuple[tuple[str, str], ...]]` | spec "Data model" §3 map; `KEEP_BY_VERSION[1] == ()`; later versions append `KEEP_BY_VERSION[N] = (...)` at the end of `db.py` |
| `db.OFFBOARD_BY_VERSION: dict[int, tuple[tuple[str, str], ...]]`, `db.offboarding_steps(applied_version) -> list` | spec "Data model" §5.3: (label, SQL with `:org`) steps per version, newest version first; version 1 is `turn_meta`, `jobs`; later versions append `OFFBOARD_BY_VERSION[N] = (...)` at the end of `db.py` |
| `tests/test_trace_lab_purge_sql.py` `EXCUSED_TURN_COLUMNS: set[tuple[str, str]]` | turn columns `test_keep_tables_cover_every_turn_reference` excuses (R4); holds `("plenum_tracelab.turn_meta", "turn_id")`; imported as `from test_trace_lab_purge_sql import EXCUSED_TURN_COLUMNS` |
| `db.company_where(org, *, alias="") -> (sql, {"tl_org": org})` | spec "Data model" §1.10 |
| `db.scope_sql(principal, org, alias="tt") -> (sql, params)` | spec "Trace capture" §5.1: `trace._scope_sql` with columns qualified by `alias` |
| `src/trace_lab/provenance.py` `CHANNELS`, `inferred_channel_sql(alias) -> str` | R7 channels; the read-time CASE over `<alias>.session_id/question/answer/route` (R36) |
| SQL `plenum_tracelab.refuse_change()`, `plenum_tracelab.supersede_only()` | created by `0001_foundation.sql` (spec "Data model" §2.2) |
| `python -m src.trace_lab.migrate snapshot --db-url URL` | writes `migrations/trace_lab/catalog.json` at the head version (§1.4) |
| `tests/trace_lab_scratch.py` `async fresh_schema(monkeypatch, *, apply=True) -> AsyncEngine`; async fixtures `scratch_db` and `tl_scratch` (same engine) | §1.8 steps 1–6, imported as a sibling module (`from trace_lab_scratch import fresh_schema`): drops `plenum_tracelab`, applies every migration, patches `database._get_engine`, sets `db.STATE`, and empties only `agent_trace_turns`/`agent_trace_spans` (M2's fixtures empty `chat_threads`/`chat_turns` themselves with `truncate_chat`) |

**Assumed from M1:**

| Name | Shape this plan relies on |
|---|---|
| `src/trace_lab/normalize.py` `normalize_turn(turn, spans, *, meta=None, activity_input=None, conversation=None, reveal_gate_verdicts=False) -> dict` | record of spec "Trace capture" §3.2; messages carry `role`, `anchor {turn_id, span_id, field}` and the role's content field (`user.text`, `assistant.text`, `tool_result.content`, `db_query.rows`, `model_call.model_call.response`, `plan.plan`, `step.output`, `system_note.output`), redacted (§3.9); every span message anchored on `output` also carries `block` = `normalize.block_text(span, "output")` (`""` on a withheld gate call, R23; None on the question, answer, tool-call and gate messages) |
| `normalize.block_text(span, field) -> str` | the one annotatable-text rule (spec Review 2.2, R36); M2 reads it through the messages' `block` key |
| `normalize.group_key(session_id, turn_id)`, `normalize.session_kind(session_id, *, thread_exists)`, `normalize.redact_text(text)` | §3.1, §4.1 |
| `src/trace_lab/conversations.py` `TurnFilter` (field `reviewed`), `list_conversations(principal, org, f, *, cursor, limit)`, `get_conversation`, `get_turn`, `list_model_calls`, `get_model_call`, `_call_row(r)` | §5.5; gate verdicts withheld until M2 reveals them (R23) |
| `apps/frontend/src/api/traceLab.js` exports `traceLabApi`; module-level `function orgOverride()` | §Review 11 |
| `src/logic/traceLabTimeline.js` `labelFor(kind, name)` | §Frontend 5 |
| `src/logic/traceLab.js` `tlResetPatch()`, `traceLabMethods.tlSetView(view)`, a keyboard handler that already ignores inputs and modifiers | §Frontend 2, 5 |
| `src/logic/traceLabConversations.js` methods incl. `tlOpenConversation(key, turnId)`; state `tlView`, `tlConvKey`, `tlTurnId` | §Frontend 2, 5 |
| `src/screens/TraceLab.jsx`, `src/screens/traceLab/Conversations.jsx`, `Timeline.jsx`; `src/styles/tracelab.css` imported from `base.css` | §Frontend 3, 5 |

**Produced by M2 for later milestones** (exact names):

- `db`: `ReviewError(reason, error=None, **extra)` with subclasses `NotFound` (404), `Invalid` (422), `Forbidden` (403), `Conflict` (409), `Stale(current)` (409, reason `stale`); `REVIEW_ERRORS: dict[str, str]`; `http_error_for(exc) -> HTTPException` (= M0's `http_error(exc.status, exc.reason, exc.error, **exc.extra)`; there is one `http_error`, M0's). Appended at the end of `db.py`: `KEEP_BY_VERSION[2]` and `OFFBOARD_BY_VERSION[2]` (`annotation_events`, `annotations`, `sample_items`, `sample_batches`). M0's `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS` gains `("plenum_tracelab.annotation_events", "turn_id")`.
- `stats`: `Z95 = 1.96`, `wilson_interval(successes, total, z=Z95) -> tuple[float | None, float | None]` (M5's exact contract), `cohen_kappa(a, b) -> float | None`. M3 and M5 import `wilson_interval`; M5 adds its functions to this module.
- `annotations`: `VERDICTS`, `FIELDS`, `TURN_FIELDS`, `NO_FAILURE_NOTE`, `NOTE_MAX`, `QUOTE_MAX`, `Anchor(span_id, field, quote, occurrence=1)`, `locate_quote`, `block_key(span_id, field)`, `block_texts(record) -> {key: {span_id, field, text}}` (a step's text is M1's `normalize.block_text`, read from the messages' `block` key; the question and answer are the user and assistant messages' `text`; M4 checks quotes against the same strings), `validate_verdict`, `validate_comment`, `key_where(conversation_key, alias="tt")`, `visible_set(...)`, `annotation_out(row)`, `company_clause(org, alias)`, `async save_verdict(principal, org, turn_id, *, verdict, note, anchor, base)`, `delete_verdict(principal, org, turn_id, *, base)`, `add_comment(principal, org, conversation_key, *, note, turn_id)`, `edit_comment(principal, org, lineage_id, *, note, base)`, `delete_comment(principal, org, lineage_id, *, base)`, `for_conversation(principal, org, conversation_key, *, turn_id=None)`, `for_turns(principal, org, turn_ids)`, `history(principal, org, turn_id)`, `visible_others(conn, principal, org, turn_ids) -> set[str]`, `reviewed_turns(conn, org) -> set[str]`, `reviewed_predicate(alias, reviewer_id, mode="reviewed") -> (sql, params)`. M3 adds `reveal()`; M4 adds `file_note`, `dispose_note`, `undispose_note`, `unfiled_notes` and calls `proposals.close_for_annotation` from `save_verdict`.
- `sampling`: constants `STRATEGIES` (the five drawable ones), `RECORDED_STRATEGIES = ("monitor_flagged",)`, `RECORDED_MAX = 200`, `PLAN_STEPS`, `HW4_PLAN`, `PLAN_TARGET`, `DIMENSIONS`, `FEATURES`, `FEATURE_LABELS`, `FEATURE_VERSION = "f1"`, `DEFAULT_SEED = 7`, `KMEANS_ITERS = 25`, `POOL_MAX`, `ENRICH_POOL_MAX`, `MAP_MAX`; `Candidate`, `Pick`; pure `route_target`, `feature_matrix`, `raw_matrix`, `standardize`, `kmeans`, `pca2`, `pick_uniform`, `pick_diversity`, `dimension_groups`, `dimension_quotas`, `pick_dimension`, `pick_outliers`, `pick_enrich`, `retention_days`, `check_window`, `check_request`, `random_share_for`, `draw`, `resolve`, `fair_sample`, `plan_view`, `open_coding`, `coverage`, `agreement`, `discovery`, `saturation(points, block, *, final_uniform=None)` (M4 fills `new_modes` on discovery points and this function starts answering), `map_payload`; async `pool`, `preview`, `create_batch`, `list_batches`, `batch_detail`, `withdraw`, `queue`, `plan_status`, `manifest`, `map_view`, `progress`. `create_batch(principal, org, req)` is the one entry point for batches: drawn strategies draw from the pool; `req["strategy"] == "monitor_flagged"` (M6) records `req["items"]` = 1–200 `{turn_id, session_id, reason}` with `req["since"]`, `req["until"]`, `req["seed"]`, `req["params"]` (stored as is), keeping only the company's turns not already in one of its batches (`excluded_sampled`); none left → `db.Invalid("pool_empty")`, a race → `db.Conflict("pool_changed")`.
- Route module `src/api/routes/trace_lab_review.py`: `router` (registered as `trace_lab_review_router`), private `_reader`, `_reviewer` (thin delegations to M0's `require_trace_store` / `require_reader` / `require_reviewer`), `_call`; models `AnchorIn`, `VerdictBody`, `CommentBody`, `CommentEditBody`, `BatchBody`, `WithdrawBody`.
- Frontend: `traceLabApi.{annotations, saveVerdict, deleteVerdict, annotationHistory, addComment, editComment, deleteComment, reviewQueue, samplingPlan, samplingPreview, createBatch, batches, batch, withdrawBatch, manifest, mapData, progress}`; `traceLabAnnotate.js` exports `NO_FAILURE_NOTE`, `NOTE_MAX`, `ANN_POLL_MS`, `blockKey`, `findOccurrence`, `occurrenceAt`, `anchorFromSelection`, `highlightSegments`, `placeMarginNotes`, `unplacedNotes`, `wrongProblem`, `traceLabAnnotateMethods`, `traceLabAnnotateVals`, `traceLabAnnotateResetPatch`; `traceLabSampling.js` exports `SAMPLING_RANGES`, `PLAN_STEP_LABELS`, `STRATEGY_LABELS`, `STEP_DEFAULTS`, `DIMENSION_CHOICES`, `valueLabel`, `defaultBatchForm`, `batchRequest`, `mapGeometry`, `curveGeometry`, `saturationText`, `traceLabSamplingMethods`, `traceLabSamplingVals`, `traceLabSamplingResetPatch`; screens `VerdictBar` (+ `ConversationComments`), `MarginNotes` (+ `AnnotatedText`, `UnplacedNotes`), `Map` (default export `TraceLabMap`), `Progress`; views `map` and `progress` in M1's `TL_VIEWS`, `TL_TABS` and `session.js` `TL_VIEW_KEYS`; `traceLabAnnotateResetPatch()` / `traceLabSamplingResetPatch()` spread right after `  return Object.assign({}, TL_SCALARS, TP_SCALARS, {` in M1's `freshPatch()` (later reset patches go in the same place).

## Reconciliations applied in this plan

Where the spec's sections disagree, the head wins (R-rules), then the owning section; these are the choices made, so no task has to rediscover them:

1. **numpy, not pure Python** (R11 over §Review 1 decision 4 and §3.6): PCA by `numpy.linalg.svd`, k-means with numpy distances; the "no numpy anywhere" test of §12.1 is dropped; `numpy>=2,<3` is added to `pyproject.toml`.
2. **File name `0002_review.sql`** (R1, §Data model 1.2) instead of §9.1's `0002_open_coding.sql`.
3. **Guard triggers** use 0001's `refuse_change()` and `supersede_only()` (§Data model 2.1 names these tables explicitly) instead of §9.1's new `refuse_update_delete()` / `versions_guard()`, which used `CREATE OR REPLACE` (the file rules forbid silently replacing objects) and had no offboarding hatch (§5.3 deletes these rows under `allow_purge`). Every guarded table also refuses TRUNCATE. `annotations` gains `superseded_at` (§2.1's concurrency step sets it); event rows default `at` to `clock_timestamp()` (§2.1); uuid ids default to `gen_random_uuid()` (§2.1); `ix_tl_ann_turn (turn_id)` is a full index so the purge's `NOT EXISTS` probe can use it (§2.1 "an index that leads with each kept turn column"), replacing §9.1's partial `ix_tl_ann_turn_head`.
4. **Annotatable text** comes from the normalized, redacted turn (§Capture 3.9: "Quotes that M2 copies into annotations come from this redacted view"), not from raw `agent_trace_turns` columns (§Review 2.2). The span block text is M1's `normalize.block_text`, copied onto messages as `block` (`""` for a withheld gate call, so R23 does not leak through the blocks); the question and answer blocks are the user and assistant messages' redacted `text`. `annotations.block_texts(record)` only collects them (M4's quote checks use the same strings); `GET /annotations?…&turn_id=` returns the active turn's block texts as `blocks`, and the timeline renders those strings, so a browser selection is always a substring of what the server checks.
5. **Discovery curve in M2.** The M2 acceptance says "the discovery curve updates", but new failure modes exist only from M4 (§Review 4 marks the curve M4). M2 ships the curve's blocks of reviews (in first-version order, golden turns excluded) with the wrong verdicts found per block and `new_modes: null`; the saturation hint answers "not available" until M4 supplies `new_modes`. Nothing is shown as zero.
6. **Keep-list coverage.** `annotation_events.turn_id` is a turn column but not on R4's keep-list; Task 3 adds it to M0's excuse set `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS` with its reason as a comment (every event accompanies an annotation of the same turn), so M0's `test_keep_tables_cover_every_turn_reference` still passes. There is no `db.KEEP_EXCUSED`.
7. **`stats.wilson_interval`** (M5's name, rounding and `(None, None)` contract) is created here; §Golden 5.5's `wilson()` and §Review 1's `wilson()` are this function.
8. **Frontend module names follow §Frontend** (R37): `traceLabAnnotate.js` (not `traceLabReview.js` / `traceLabAnchors.js`), `MarginNotes.jsx` + `VerdictBar.jsx` (not `ReviewLayer.jsx` / `Comments.jsx`; the comment list is `ConversationComments` in `VerdictBar.jsx`). Saturation copy follows §Frontend 4.6 with the block count from the rule.
9. **Route auth** follows M0 (R36, one auth rule): the route helpers delegate to M0. `_reader` is `db.require_trace_store()` then `db.require_reader(principal, organization_id).org_id` (None for the read-only platform view of a superadmin naming no company); `_reviewer` is `db.require_trace_store()` then `db.require_reviewer(principal, organization_id)`. The reasons are M0's: 403 `reviewers_only`, 403 `not_your_company`, 400 `bad_company`, 400 `name_a_company`.
10. **Small additions, recorded:** 422 `bad_window`, `bad_channel`, `needs_failure_modes` (mode-based enrich and depth-scan batches wait for M4), `name_a_conversation`; 409 `pool_changed` (another batch took a turn between draw and insert); `BatchBody.include_view_as` / `include_deleted` (R20/R21 filters); map nodes carry `conversation_key` and a redacted `question` (§Frontend 4.5 hover and click). The caller's own comments are always returned; only other people's are counted as hidden on a blind conversation.
11. **Map colour.** One focus colour (categorical slot 1, light `#2a78d6` / dark `#3987e5`) with one group brought forward at a time, instead of eight cluster hues on a scatter (eight hues cannot all be told apart on an all-pairs chart); groups are also named in a legend and a table.
12. **M2 records monitor_flagged batches; M6 calls create_batch.** §Judges names `sampling.create_batch(*, organization_id, method, items, seed, params, created_by)` with `method='monitor_flagged'`; M2 keeps its one entry point `create_batch(principal, org, req)` instead. `0002_review.sql`'s `strategy` CHECK includes `monitor_flagged`; a request with that strategy records the answers it names (`req["items"]`, 1–200) instead of drawing: plan step `extra`, never counted toward rates, one review each, the company's turns only (never golden) and none already in one of its batches. The route `BatchBody` keeps the five drawable strategies; only M6 records `monitor_flagged` batches (with a Principal built from the monitor batch's starter), and 0006 does not alter `sample_batches`.
13. **M2 is the first plan to extend M1's shared pieces, so it makes the one-time edits.** Task 14 relaxes M1's `traceLabCompanyScope.test.mjs` (exact key set and one-line-per-call scan) because `Object.assign` appends calls; Task 19 adds `map` and `progress` to `TL_VIEWS`, `TL_TABS` and `session.js` `TL_VIEW_KEYS` and updates M1's two exact tab-list tests; every later reset patch is spread right after `  return Object.assign({}, TL_SCALARS, TP_SCALARS, {` in `freshPatch()`. Task 13 passes M1's `reveal_gate_verdicts=True` once the reviewer has a live verdict of their own on the answer (R23), because no other plan does.


## Tasks

Paths in task headers are relative to `apps/backend/cafm-connector-service-final/svc-deepagents/` (backend tasks) or `apps/frontend/` (frontend tasks). Run backend steps from the repo root `/Users/hussain/Desktop/hoist/Hoistra_Plenum` with `tl_da_test` (Global Constraints). Start the scratch Postgres (`tracelab_scratch`) before any `_sql.py` test; without it those tests skip, which is not a pass.

### Task 1: Pin the M0 and M1 names this milestone imports

**Files:**
- Create: `tests/test_trace_lab_m2_contracts.py` (backend)
- Create: `apps/frontend/test/traceLabM2Contracts.test.mjs`

**Interfaces:**
- Consumes: every name in the Interfaces tables ("Assumed from M0", "Assumed from M1").
- Produces: two guard tests that fail loudly if M0/M1 shipped a different name or shape. Later tasks rely on them passing.

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_lab_m2_contracts.py`:

```python
"""The M0 and M1 names Trace Lab M2 imports exist with the shapes the M2 plan assumes (R36: the owning
milestone fixes a name; a later plan imports it and never defines a second helper for the same job)."""
from __future__ import annotations

import inspect


def _params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)


def test_m0_db_helpers():
    from src.trace_lab import db

    assert db.SCHEMA == "plenum_tracelab"
    for name in ("engine", "ready", "state", "keep_tables", "company_where", "scope_sql", "http_error",
                 "require_trace_store", "require_reader", "require_reviewer", "offboarding_steps"):
        assert callable(getattr(db, name)), name
    assert inspect.iscoroutinefunction(db.require_ready)
    assert _params(db.http_error)[:3] == ["status", "reason", "error"]
    assert _params(db.require_reviewer)[:2] == ["principal", "organization_id"]
    assert _params(db.company_where)[:2] == ["org", "alias"]
    assert db.company_where("o", alias="a")[0].startswith("a.organization_id")
    assert _params(db.scope_sql)[:3] == ["principal", "org", "alias"]
    assert isinstance(db.KEEP_BY_VERSION, dict) and db.KEEP_BY_VERSION[1] == ()
    assert isinstance(db.OFFBOARD_BY_VERSION, dict) and [l for l, _ in db.OFFBOARD_BY_VERSION[1]] == ["turn_meta", "jobs"]


def test_m0_purge_excuses():
    from test_trace_lab_purge_sql import EXCUSED_TURN_COLUMNS

    assert ("plenum_tracelab.turn_meta", "turn_id") in EXCUSED_TURN_COLUMNS


def test_m0_provenance_names():
    from src.trace_lab import provenance

    assert "chat" in provenance.CHANNELS and "golden" in provenance.CHANNELS
    sql = provenance.inferred_channel_sql("t")
    assert "t.session_id" in sql and "tt." not in sql


def test_m1_normalizer_names():
    from src.trace_lab import normalize

    assert _params(normalize.normalize_turn)[:2] == ["turn", "spans"] and "meta" in _params(normalize.normalize_turn)
    assert normalize.group_key(None, "turn-1") == "t:turn-1"
    assert normalize.group_key("rerun", "turn-1") == "r:turn-1"
    assert normalize.group_key("abc", "turn-1") == "s:abc"
    assert normalize.session_kind("report-1", thread_exists=False) == "synthetic"
    assert "abcdefghijklmnop" not in normalize.redact_text("Authorization: Bearer abcdefghijklmnop")
    assert callable(normalize.block_text) and _params(normalize.block_text)[:2] == ["span", "field"]
    assert "reveal_gate_verdicts" in _params(normalize.normalize_turn)


def test_m1_conversation_filter_names():
    from src.trace_lab import conversations

    assert "reviewed" in conversations.TurnFilter.__dataclass_fields__
    assert _params(conversations.list_conversations)[:3] == ["principal", "org", "f"]


def test_m0_scratch_helper_names():
    import trace_lab_scratch

    assert inspect.iscoroutinefunction(trace_lab_scratch.fresh_schema)
    assert hasattr(trace_lab_scratch, "tl_scratch") and hasattr(trace_lab_scratch, "scratch_db")
```

`apps/frontend/test/traceLabM2Contracts.test.mjs`:

```js
// The M1 frontend names Trace Lab M2 builds on (R36): the Trace Lab API object and its company
// override, the timeline's label map, the shell's reset patch and view switch, and the state keys
// for the open conversation and answer.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };

test('api/traceLab.js exports traceLabApi and keeps a module-level orgOverride()', async () => {
  const { traceLabApi } = await import('../src/api/traceLab.js');
  assert.equal(typeof traceLabApi, 'object');
  const src = readFileSync(new URL('../src/api/traceLab.js', import.meta.url), 'utf8');
  assert.match(src, /function orgOverride\(\)/);
});

test('the timeline label map and the shell names exist', async () => {
  const { labelFor } = await import('../src/logic/traceLabTimeline.js');
  assert.equal(typeof labelFor('agent', 'compliance'), 'string');
  const shell = await import('../src/logic/traceLab.js');
  const patch = shell.tlResetPatch();
  ['tlView', 'tlConvKey', 'tlTurnId'].forEach((k) => assert.ok(k in patch, k));
  assert.equal(typeof shell.traceLabMethods.tlSetView, 'function');
  const conv = await import('../src/logic/traceLabConversations.js');
  const methods = Object.values(conv).find((v) => v && typeof v === 'object' && typeof v.tlOpenConversation === 'function');
  assert.ok(methods, 'tlOpenConversation');
});
```

- [ ] **Step 2: Run them and see what fails**

```sh
tl_da_test tests/test_trace_lab_m2_contracts.py
cd apps/frontend && node --test test/traceLabM2Contracts.test.mjs; cd ../..
```

Expected before M0/M1 land: `ModuleNotFoundError: No module named 'src.trace_lab'` and `ERR_MODULE_NOT_FOUND .../src/api/traceLab.js`. After M0/M1 land, any remaining failure names a mismatch (most likely a helper spelled differently, or M1's `normalize.block_text` not shipped yet).

- [ ] **Step 3: Resolve every mismatch at its owner, not here**

Nothing new is implemented in this task. For each failure:
- A name M0/M1 spelled differently (for example `scope_sql` vs another spelling): R36 — use the M0/M1 name. Replace this plan's spelling with it in every later task's code before running that task (`grep -n "<this plan's name>"` over this plan file lists the places), and change the contract test to the real name.
- `normalize.block_text` or the messages' `block` key missing: M1 owns that rule (spec Review 2.2); stop and ask Hussain to have M1's plan add it. M2 never defines its own block-text rule.
- `tlOpenConversation` absent: find M1's method that opens a conversation at a given answer (`grep -n "tlOpen" apps/frontend/src/logic/traceLabConversations.js`) and use that name in Tasks 15 and 17.

- [ ] **Step 4: Run them and see them pass**

```sh
tl_da_test tests/test_trace_lab_m2_contracts.py
cd apps/frontend && node --test test/traceLabM2Contracts.test.mjs; cd ../..
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list in the task log: `tests/test_trace_lab_m2_contracts.py`, `apps/frontend/test/traceLabM2Contracts.test.mjs`, and any name substitutions made.

### Task 2: Declare numpy and create `stats.py`

**Files:**
- Modify: `pyproject.toml:32-33` (dependencies list end)
- Create: `src/trace_lab/stats.py`
- Test: `tests/test_trace_lab_stats_wilson_and_kappa.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `stats.Z95`, `stats.wilson_interval(successes: int, total: int, z: float = Z95) -> tuple[float | None, float | None]`, `stats.cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
"""stats.py as M2 creates it: the Wilson interval (the reference values M5 pins) and Cohen's kappa, both
pure and rounded to 4 places; numpy is declared for the projection and k-means (R11)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.trace_lab import stats


@pytest.mark.parametrize("k,n,expected", [(16, 20, (0.584, 0.9193)), (0, 5, (0.0, 0.4345)), (5, 5, (0.5655, 1.0)),
                                          (12, 12, (0.7575, 1.0)), (0, 0, (None, None)), (3, 12, (0.0889, 0.5323))])
def test_wilson_interval_reference_values(k, n, expected):
    assert stats.wilson_interval(k, n) == expected


def test_wilson_refuses_impossible_counts():
    with pytest.raises(ValueError):
        stats.wilson_interval(6, 5)


def test_cohen_kappa():
    a = ["right"] * 15 + ["wrong"] * 5
    b = ["right"] * 13 + ["wrong"] * 6 + ["right"]
    assert stats.cohen_kappa(a, b) == pytest.approx(0.625, abs=1e-4)
    assert stats.cohen_kappa(["right", "wrong"], ["right", "wrong"]) == 1.0
    assert stats.cohen_kappa(["right"] * 4, ["right"] * 4) is None          # chance agreement is 1: undefined
    assert stats.cohen_kappa([], []) is None
    with pytest.raises(ValueError):
        stats.cohen_kappa(["right"], [])


def test_numpy_is_declared_for_svc_deepagents():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"numpy>=2,<3",' in pyproject
```

- [ ] **Step 2: Run it and see it fail**

```sh
tl_da_test tests/test_trace_lab_stats_wilson_and_kappa.py
```

Expected: `ImportError: cannot import name 'stats' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

`pyproject.toml` — exact replacement (current lines 32-33):

old:
```toml
    "pymupdf>=1.24",
]
```
new:
```toml
    "pymupdf>=1.24",
    "numpy>=2,<3",  # Trace Lab: PCA by SVD and k-means for the Map and sampling (R11)
]
```

`src/trace_lab/stats.py`:

```python
"""Trace Lab statistics. Pure: no database, no model calls, no I/O.

M2 creates this module with the two functions open coding needs. M3 imports `wilson_interval`
from here; M5 adds the confusion, Rogan-Gladen, bootstrap and pass@k functions named in the spec
(section "Judges ... 1. stats.py"), keeping these two unchanged.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

Z95 = 1.96


def wilson_interval(successes: int, total: int, z: float = Z95) -> tuple[float | None, float | None]:
    """95% Wilson score interval for successes/total, rounded to 4 places. (None, None) when total is 0."""
    if total <= 0:
        return (None, None)
    if not 0 <= successes <= total:
        raise ValueError("successes must be between 0 and total")
    p = successes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters over the same items, rounded to 4 places.

    None when there are no items, or when chance agreement is 1 (both raters used one and the same
    category throughout), where kappa is undefined."""
    if len(a) != len(b):
        raise ValueError("both raters must judge the same items")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((list(a).count(c) / n) * (list(b).count(c) / n) for c in sorted(set(a) | set(b)))
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_stats_wilson_and_kappa.py
```

Expected: `9 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `pyproject.toml`, `src/trace_lab/stats.py`, `tests/test_trace_lab_stats_wilson_and_kappa.py`.

### Task 3: Review errors and the version-2 keep-list in `db.py`

**Files:**
- Modify: `src/trace_lab/db.py` (M0) — append at the end of the file
- Modify: `tests/test_trace_lab_purge_sql.py` (M0) — the `EXCUSED_TURN_COLUMNS` line
- Test: `tests/test_trace_lab_review_errors_and_keep_list.py`

**Interfaces:**
- Consumes: M0 `db.http_error(status, reason, error, **extra)`, `db.KEEP_BY_VERSION`, `db.OFFBOARD_BY_VERSION`, `db.offboarding_steps(applied_version)`, `tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS`.
- Produces: `db.ReviewError`, `NotFound`, `Invalid`, `Forbidden`, `Conflict`, `Stale(current)`, `REVIEW_ERRORS`, `http_error_for(exc) -> HTTPException`; `KEEP_BY_VERSION[2]`; `OFFBOARD_BY_VERSION[2]`; `("plenum_tracelab.annotation_events", "turn_id")` in `EXCUSED_TURN_COLUMNS`.

- [ ] **Step 1: Write the failing test**

```python
"""M2's additions to db.py: review errors become the house envelope with the right status and a plain
sentence (through M0's http_error), version 2 puts annotations and sample items on the span-purge
keep-list (D5, R4) with annotation_events excused, and version 2 registers its offboarding steps (spec
"Data model" 5.3)."""
from __future__ import annotations

import re

from src.trace_lab import db
from test_trace_lab_purge_sql import EXCUSED_TURN_COLUMNS  # M0's excuse set (tests/test_trace_lab_purge_sql.py)

INTERNAL = re.compile(r"plenum_|svc-|\bUDR\b|\bRAG\b|deep-?agents|agent_trace|turn_id|span", re.I)


def test_review_errors_keep_the_house_envelope():
    stale = db.http_error_for(db.Stale({"id": "v2"}))
    assert stale.status_code == 409
    assert stale.detail == {"ok": False, "error": "Changed since you opened it.", "reason": "stale", "current": {"id": "v2"}}
    assert db.http_error_for(db.Invalid("quote_not_found")).status_code == 422
    assert db.http_error_for(db.NotFound("batch_not_found")).status_code == 404
    assert db.http_error_for(db.Forbidden("not_author")).status_code == 403
    assert db.http_error_for(db.Conflict("already_withdrawn")).detail["reason"] == "already_withdrawn"
    custom = db.http_error_for(db.Invalid("window_outside_retention", "Pick a period inside the last 90 days."))
    assert custom.detail["error"] == "Pick a period inside the last 90 days."


def test_m0_http_error_keeps_its_signature():
    plain = db.http_error(404, "batch_not_found", "That batch was not found.", retryable=False)
    assert plain.status_code == 404
    assert plain.detail == {"ok": False, "error": "That batch was not found.", "reason": "batch_not_found", "retryable": False}


def test_error_sentences_name_nothing_internal():
    for reason, sentence in db.REVIEW_ERRORS.items():
        assert not INTERNAL.search(sentence), reason
        assert sentence.endswith("."), reason
    for code in ("not_a_reviewer", "choose_company", "not_your_company"):   # M0's auth reasons are the only ones
        assert code not in db.REVIEW_ERRORS, code


def test_version_2_keeps_annotations_and_sample_items():
    assert db.KEEP_BY_VERSION[2] == (("plenum_tracelab.annotations", "turn_id"),
                                     ("plenum_tracelab.sample_items", "turn_id"))
    assert ("plenum_tracelab.annotation_events", "turn_id") in EXCUSED_TURN_COLUMNS
    assert not hasattr(db, "KEEP_EXCUSED")


def test_version_2_offboards_events_annotations_items_and_batches_before_version_1():
    assert [label for label, _ in db.offboarding_steps(2)] == [
        "annotation_events", "annotations", "sample_items", "sample_batches", "turn_meta", "jobs"]
    for label, sql in db.OFFBOARD_BY_VERSION[2]:
        assert sql == f"DELETE FROM plenum_tracelab.{label} WHERE organization_id = CAST(:org AS uuid)", label
```

- [ ] **Step 2: Run it and see it fail**

```sh
tl_da_test tests/test_trace_lab_review_errors_and_keep_list.py
```

Expected: `AttributeError: module 'src.trace_lab.db' has no attribute 'http_error_for'`, `KeyError: 2` for the keep-list and offboarding tests, and an `AssertionError` on the excused pair.

- [ ] **Step 3: Implement**

Check what M0 already has (read-only): `grep -n "KEEP_BY_VERSION\|OFFBOARD_BY_VERSION\|^def http_error" apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/db.py` and `grep -n "EXCUSED_TURN_COLUMNS = " apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_trace_lab_purge_sql.py`. M0's literals stay `KEEP_BY_VERSION = {1: ()}` and `OFFBOARD_BY_VERSION = {1: (turn_meta, jobs)}`; version 2 is added only by the two statements appended below (one registration form for every milestone). M0's `def http_error(status: int, reason: str, error: str, **extra)` keeps its name and signature: M0's `scope_for`, `require_reader`, `require_reviewer`, `require_platform`, `require_trace_store` and M5's routes call it, so M2 never defines a second `http_error`.

M0's `tests/test_trace_lab_purge_sql.py` — exact replacement (the excuse set `test_keep_tables_cover_every_turn_reference` subtracts):

old:
```python
EXCUSED_TURN_COLUMNS = {("plenum_tracelab.turn_meta", "turn_id")}
```
new:
```python
EXCUSED_TURN_COLUMNS = {
    ("plenum_tracelab.turn_meta", "turn_id"),
    # M2 (R4): an annotation_events row only ever accompanies an annotation of the same turn (create, edit,
    # delete) or reveals a turn other reviewers annotated, so annotations.turn_id already keeps it.
    ("plenum_tracelab.annotation_events", "turn_id"),
}
```

Append to the end of `src/trace_lab/db.py`:

```python


# ── M2: review errors (open coding and sampling) ───────────────────────────────────────
# (These two imports may repeat M0's; harmless.)
from typing import Any  # noqa: E402,F811

from fastapi import HTTPException  # noqa: E402,F811

# Raised by annotations.py and sampling.py (and, from M4, taxonomy.py and labels.py); route
# modules turn them into HTTP answers with http_error_for(), which builds M0's http_error() envelope.
# The `error` text is shown on screen, so it is a plain sentence with no internal names; `reason` is
# the code the frontend switches on. Extra fields are keyword arguments only (never status, reason
# or error). Who may ask is M0's rule (require_reader / require_reviewer and their reasons).

class ReviewError(Exception):
    status = 400

    def __init__(self, reason: str, error: str | None = None, **extra: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error or REVIEW_ERRORS.get(reason, "That could not be done.")
        self.extra = extra


class NotFound(ReviewError):
    status = 404


class Invalid(ReviewError):
    status = 422


class Forbidden(ReviewError):
    status = 403


class Conflict(ReviewError):
    status = 409


class Stale(Conflict):
    """A write whose `base` is not the current head. `current` is that head (or None)."""

    def __init__(self, current: dict[str, Any] | None) -> None:
        super().__init__("stale", current=current)
        self.current = current


REVIEW_ERRORS: dict[str, str] = {
    "turn_not_found": "That answer was not found.",
    "annotation_not_found": "There is no verdict or comment of yours to change.",
    "batch_not_found": "That batch was not found.",
    "note_required": "Say what went wrong first.",
    "anchor_required": "Select the text where it first went wrong.",
    "anchor_on_right": "A Right verdict has no highlighted text.",
    "quote_not_found": "The selected text is no longer in that step. Select it again.",
    "span_not_in_turn": "That step is not part of this answer.",
    "window_outside_retention": "Pick a more recent period; the steps of older answers are no longer kept.",
    "bad_window": "The period must end after it starts.",
    "dimension_not_allowed": "Batches can be split only by channel, where the question was routed, the asker's role or page.",
    "needs_failure_modes": "This option needs failure modes, which are not set up yet.",
    "pool_empty": "No answers are left to draw in that period.",
    "pool_changed": "Another batch took some of these answers just now. Preview again.",
    "not_author": "Only the person who wrote a comment can change it.",
    "already_withdrawn": "That batch was already withdrawn.",
    "stale": "Changed since you opened it.",
}


def http_error_for(exc: ReviewError) -> HTTPException:
    """A review error as M0's house envelope {ok: false, error, reason, ...extra} with the error's status."""
    return http_error(exc.status, exc.reason, exc.error, **exc.extra)


# ── M2: span-purge keep-list, version 2 (0002_review.sql; spec "Data model" 3, R4) ───────
# annotation_events.turn_id is excused in M0's tests/test_trace_lab_purge_sql.py::EXCUSED_TURN_COLUMNS.
KEEP_BY_VERSION[2] = (("plenum_tracelab.annotations", "turn_id"), ("plenum_tracelab.sample_items", "turn_id"))

# ── M2: company offboarding, version 2 (spec "Data model" 5.3) ─────────────────────────
# Runs before version 1's steps, under allow_purge (the 0002 guard triggers honour it); the items go
# before the batches they reference (sample_items.batch_id).
OFFBOARD_BY_VERSION[2] = (
    ("annotation_events", "DELETE FROM plenum_tracelab.annotation_events WHERE organization_id = CAST(:org AS uuid)"),
    ("annotations", "DELETE FROM plenum_tracelab.annotations WHERE organization_id = CAST(:org AS uuid)"),
    ("sample_items", "DELETE FROM plenum_tracelab.sample_items WHERE organization_id = CAST(:org AS uuid)"),
    ("sample_batches", "DELETE FROM plenum_tracelab.sample_batches WHERE organization_id = CAST(:org AS uuid)"),
)
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_review_errors_and_keep_list.py tests/test_trace_lab_m2_contracts.py
```

Expected: all pass (M0's `tests/test_trace_lab_purge_sql.py` itself runs with the scratch database in Task 4, Step 4).

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/db.py`, `tests/test_trace_lab_purge_sql.py` (M0, the excused pair), `tests/test_trace_lab_review_errors_and_keep_list.py`.

### Task 4: Migration `0002_review.sql` and the regenerated catalog

**Files:**
- Create: `migrations/trace_lab/0002_review.sql`
- Regenerate: `migrations/trace_lab/catalog.json` (M0)
- Create: `tests/trace_lab_m2_fixtures.py`
- Test: `tests/test_trace_lab_review_history_is_append_only_sql.py`

**Interfaces:**
- Consumes: 0001's `plenum_tracelab.refuse_change()`, `plenum_tracelab.supersede_only()`; M0 `tests/trace_lab_scratch.py` `fresh_schema(monkeypatch, *, apply=True)` (imported as `from trace_lab_scratch import fresh_schema`); `db.KEEP_BY_VERSION` and M0's `EXCUSED_TURN_COLUMNS` (both extended in Task 3).
- Produces: tables `plenum_tracelab.sample_batches` (`strategy` includes the recorded `monitor_flagged`), `sample_items`, `annotations`, `annotation_events` with the constraint and index names the later code catches (`ux_tl_items_turn`, `ux_tl_ann_verdict_head`, `ux_tl_ann_supersedes`, `ux_tl_ann_lineage_head`, `ck_tl_batches_prevalence`); fixtures `truncate_chat(engine)` (empties `plenum_cafm.chat_threads` and `chat_turns`, which `fresh_schema` does not touch), `principal(org, role="admin", email=None)`, `turn_id(n)`, `add_turn(engine, n, org, *, session=None, question=…, answer="There were 758 work orders in August.", minutes_ago=60, tokens=1000, route="single:wo_engine", tools=None) -> str`, `add_span(engine, tid, *, kind="tool", name="answer_from_records", agent="wo_engine", output=None, seq=1) -> str`, `add_meta(engine, tid, org, *, channel="chat", asker_role="admin", view_as=False)`, `add_thread(engine, session, org, *, deleted=False)`, `NOW`.

- [ ] **Step 1: Write the failing test and the fixtures module**

`tests/trace_lab_m2_fixtures.py`:

```python
"""Scratch-database fixtures shared by the M2 SQL tests (plain helper module, like M0's trace_lab_scratch).

Rows written here go only to the guarded scratch database M0's helper connects to (database name
tracelab_scratch on a local host); nothing here can reach hoistra_test.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.services.principal import Principal

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def principal(org: str | None, role: str = "admin", email: str | None = None) -> Principal:
    uid = uuid.uuid4()
    return Principal(user_id=uid, email=email or f"{str(uid)[:6]}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def turn_id(n: int) -> str:
    return f"turn-{n:016x}"


async def add_turn(engine, n: int, org: str | None, *, session: str | None = None, question: str = "How many work orders?",
                   answer: str = "There were 758 work orders in August.", minutes_ago: int = 60, tokens: int = 1000,
                   route: str | None = "single:wo_engine", tools: list[str] | None = None) -> str:
    tid = turn_id(n)
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), 'asker@example.com', :q, :a, :route,
                    :at, :at, 2000, 2, 1, :tin, 50, CAST('["m"]' AS jsonb), CAST(:tools AS jsonb), true)"""),
            {"tid": tid, "sid": session if session is not None else f"sess-{n}", "org": org, "uid": str(uuid.uuid4()),
             "q": question, "a": answer, "route": route, "at": NOW - timedelta(minutes=minutes_ago), "tin": tokens,
             "tools": json.dumps(tools or ["answer_from_records"])})
    return tid


async def add_span(engine, tid: str, *, kind: str = "tool", name: str = "answer_from_records", agent: str = "wo_engine",
                   output=None, seq: int = 1) -> str:
    sid = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, seq, kind, name, agent, started_at, output, ok)
            VALUES (CAST(:id AS uuid), :tid, :seq, :kind, :name, :agent, :at, CAST(:out AS jsonb), true)"""),
            {"id": sid, "tid": tid, "seq": seq, "kind": kind, "name": name, "agent": agent, "at": NOW,
             "out": json.dumps(output if output is not None else {"count": 758, "period": "2026-07"})})
    return sid


async def add_meta(engine, tid: str, org: str, *, channel: str = "chat", asker_role: str = "admin",
                   view_as: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_tracelab.turn_meta (turn_id, organization_id, channel, channel_source, classifier_version,
                                                   asker_role, asker_scope)
            VALUES (:tid, CAST(:org AS uuid), :ch, 'client', 'prov-1', :role, CAST(:scope AS jsonb))"""),
            {"tid": tid, "org": org, "ch": channel, "role": asker_role, "scope": json.dumps({"view_as": view_as})})


async def add_thread(engine, session: str, org: str, *, deleted: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title, deleted_at)
            VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), 'A chat', :del)"""),
            {"id": session, "org": org, "uid": str(uuid.uuid4()), "del": NOW if deleted else None})


async def truncate_chat(engine) -> None:
    """Empty the chat tables after a test. M0's fresh_schema already empties agent_trace_turns/spans and
    recreates plenum_tracelab; chat_threads and chat_turns (created by chat_threads.ensure_tables) persist."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE plenum_cafm.chat_threads, plenum_cafm.chat_turns")
```

`tests/test_trace_lab_review_history_is_append_only_sql.py`:

```python
"""0002_review.sql on a scratch Postgres: history is append-only, a version is superseded once and never
edited, a turn is drawn into at most one batch per company, only uniform batches count toward rates,
the offboarding hatch is the only way to delete, and every turn column is covered by the purge keep-list
(spec "Data model" 2.1 and 3; "Open coding ..." 9; R4)."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from trace_lab_m2_fixtures import truncate_chat
from trace_lab_scratch import fresh_schema  # M0's scratch helper (tests/trace_lab_scratch.py)
from test_trace_lab_purge_sql import EXCUSED_TURN_COLUMNS  # M0's excuse set, extended in Task 3
from src.services import chat_threads
from src.trace_lab import db

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

ORG = str(uuid.uuid4())


@pytest.fixture
async def eng(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(chat_threads, "_ready", False)
    await chat_threads.ensure_tables()
    yield engine
    await truncate_chat(engine)
    await engine.dispose()


async def refused(engine, sql: str, params: dict | None = None) -> str:
    with pytest.raises(DBAPIError) as e:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    return str(e.value)


async def run(engine, sql: str, params: dict | None = None):
    async with engine.begin() as conn:
        return await conn.execute(text(sql), params or {})


async def _batch(engine, strategy: str = "uniform", cfp: bool = True) -> str:
    bid = str(uuid.uuid4())
    await run(engine, """
        INSERT INTO plenum_tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (CAST(:id AS uuid), CAST(:org AS uuid), 'extra', :s, 7, now() - interval '1 day', now(), 1, 1, 1, :cfp,
                CAST(:me AS uuid))""", {"id": bid, "org": ORG, "s": strategy, "cfp": cfp, "me": str(uuid.uuid4())})
    return bid


async def _item(engine, bid: str, tid: str, position: int = 1) -> None:
    await run(engine, """
        INSERT INTO plenum_tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:org AS uuid), :tid, :pos, 'uniform pick')""",
              {"b": bid, "org": ORG, "tid": tid, "pos": position})


async def _annotation(engine, verdict: str = "right", note: str = "no failure observed") -> str:
    aid = str(uuid.uuid4())
    await run(engine, """
        INSERT INTO plenum_tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key,
            turn_id, reviewer_id, verdict, note, blind)
        VALUES (CAST(:id AS uuid), CAST(:id AS uuid), 1, 'verdict', CAST(:org AS uuid), 's:x', 'turn-1',
                CAST(:me AS uuid), :v, :n, true)""", {"id": aid, "org": ORG, "me": str(uuid.uuid4()), "v": verdict, "n": note})
    return aid


async def test_sample_items_refuse_update_delete_and_truncate(eng):
    bid = await _batch(eng)
    await _item(eng, bid, "turn-1")
    assert "append-only" in await refused(eng, "UPDATE plenum_tracelab.sample_items SET reason = 'x'")
    assert "append-only" in await refused(eng, "DELETE FROM plenum_tracelab.sample_items")
    assert "append-only" in await refused(eng, "TRUNCATE plenum_tracelab.sample_items CASCADE")


async def test_annotation_events_refuse_update_delete_and_truncate(eng):
    await run(eng, """INSERT INTO plenum_tracelab.annotation_events (organization_id, turn_id, action, actor_id)
                      VALUES (CAST(:org AS uuid), 'turn-1', 'create', gen_random_uuid())""", {"org": ORG})
    assert "append-only" in await refused(eng, "UPDATE plenum_tracelab.annotation_events SET action = 'edit'")
    assert "append-only" in await refused(eng, "DELETE FROM plenum_tracelab.annotation_events")
    assert "append-only" in await refused(eng, "TRUNCATE plenum_tracelab.annotation_events")


async def test_an_annotation_only_gains_superseded_by_once_and_is_never_deleted(eng):
    aid = await _annotation(eng)
    assert "never edited" in await refused(eng, "UPDATE plenum_tracelab.annotations SET note = 'changed'")
    nxt = str(uuid.uuid4())
    await run(eng, "UPDATE plenum_tracelab.annotations SET superseded_by = CAST(:n AS uuid), superseded_at = now() "
                   "WHERE id = CAST(:a AS uuid)", {"n": nxt, "a": aid})
    assert "never edited" in await refused(eng, "UPDATE plenum_tracelab.annotations SET superseded_by = gen_random_uuid()")
    assert "never deleted" in await refused(eng, "DELETE FROM plenum_tracelab.annotations")
    assert "never deleted" in await refused(eng, "TRUNCATE plenum_tracelab.annotations CASCADE")


async def test_the_offboarding_hatch_is_the_only_way_to_delete(eng):
    await _annotation(eng)
    async with eng.begin() as conn:
        await conn.execute(text("SET LOCAL plenum_tracelab.allow_purge = 'on'"))
        res = await conn.execute(text("DELETE FROM plenum_tracelab.annotations WHERE organization_id = CAST(:o AS uuid)"),
                                 {"o": ORG})
    assert res.rowcount == 1


async def test_a_wrong_verdict_row_needs_note_quote_and_occurrence_and_a_right_one_has_none(eng):
    await refused(eng, """INSERT INTO plenum_tracelab.annotations (id, lineage_id, version, kind, organization_id,
        conversation_key, turn_id, reviewer_id, verdict, note) VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict',
        CAST(:o AS uuid), 's:x', 't', gen_random_uuid(), 'wrong', 'counted July')""", {"o": ORG})
    await refused(eng, """INSERT INTO plenum_tracelab.annotations (id, lineage_id, version, kind, organization_id,
        conversation_key, turn_id, reviewer_id, verdict, note, field, quote, quote_occurrence) VALUES (gen_random_uuid(),
        gen_random_uuid(), 1, 'verdict', CAST(:o AS uuid), 's:x', 't', gen_random_uuid(), 'right', 'no failure observed',
        'answer', '758', 1)""", {"o": ORG})


async def test_one_live_verdict_head_per_reviewer_and_turn(eng):
    me = str(uuid.uuid4())
    sql = """INSERT INTO plenum_tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key,
             turn_id, reviewer_id, verdict, note) VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict',
             CAST(:o AS uuid), 's:x', 'turn-9', CAST(:me AS uuid), 'right', 'no failure observed')"""
    await run(eng, sql, {"o": ORG, "me": me})
    assert "ux_tl_ann_verdict_head" in await refused(eng, sql, {"o": ORG, "me": me})


async def test_a_turn_is_never_in_two_batches_of_one_company(eng):
    b1, b2 = await _batch(eng), await _batch(eng)
    await _item(eng, b1, "turn-7")
    assert "ux_tl_items_turn" in await refused(eng, """
        INSERT INTO plenum_tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:o AS uuid), 'turn-7', 1, 'uniform pick')""", {"b": b2, "o": ORG})


async def test_only_uniform_batches_count_toward_rates(eng):
    assert "ck_tl_batches_prevalence" in await refused(eng, """
        INSERT INTO plenum_tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (gen_random_uuid(), CAST(:o AS uuid), 'extra', 'diversity', 7, now() - interval '1 day', now(), 1, 1, 1,
                true, gen_random_uuid())""", {"o": ORG})
    await refused(eng, """
        INSERT INTO plenum_tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (gen_random_uuid(), CAST(:o AS uuid), 'extra', 'uniform', 7, now() - interval '1 day', now(), 1, 1, 1,
                false, gen_random_uuid())""", {"o": ORG})


async def test_every_turn_column_of_0002_is_kept_or_excused(eng):
    async with eng.connect() as conn:
        cols = {(f"plenum_tracelab.{r[0]}", r[1]) for r in (await conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'plenum_tracelab' "
            "AND table_name IN ('annotations', 'annotation_events', 'sample_batches', 'sample_items') "
            "AND (column_name = 'turn_id' OR column_name LIKE '%\\_turn\\_id')"))).all()}
    kept = set(db.KEEP_BY_VERSION[2])
    assert cols - kept == {("plenum_tracelab.annotation_events", "turn_id")}
    assert ("plenum_tracelab.annotation_events", "turn_id") in EXCUSED_TURN_COLUMNS
```

- [ ] **Step 2: Run it and see it fail** (scratch Postgres up)

```sh
tl_da_test tests/test_trace_lab_review_history_is_append_only_sql.py
```

Expected: errors like `sqlalchemy.exc.ProgrammingError: (sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) <class 'asyncpg.exceptions.UndefinedTableError'>: relation "plenum_tracelab.sample_batches" does not exist`.

- [ ] **Step 3: Implement**

Create `migrations/trace_lab/0002_review.sql` (it creates objects only in `plenum_tracelab`; no `IF NOT EXISTS`, no `OR REPLACE`, no `GRANT`, no `CONCURRENTLY`; every statement ends with `;` at the end of its line; comment strings are one line with no apostrophes):

```sql
-- 0002_review.sql (M2): open coding (annotations, annotation_events) and sampling (sample_batches, sample_items).
SET LOCAL lock_timeout = '3s';

CREATE TABLE plenum_tracelab.sample_batches (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id        uuid        NOT NULL,
    plan_step              text        NOT NULL CHECK (plan_step IN ('initial_uniform','cluster_representatives',
                                                                     'dimension','depth_search','final_uniform','extra')),
    strategy               text        NOT NULL CHECK (strategy IN ('uniform','diversity','dimension','outlier','enrich','monitor_flagged')),
    params                 jsonb       NOT NULL DEFAULT '{}'::jsonb,
    seed                   bigint      NOT NULL,
    window_since           timestamptz NOT NULL,
    window_until           timestamptz NOT NULL,
    channel                text        NOT NULL DEFAULT 'chat',
    requested              integer     NOT NULL CHECK (requested BETWEEN 1 AND 200),
    selected               integer     NOT NULL CHECK (selected >= 0),
    pool_size              integer     NOT NULL CHECK (pool_size >= 0),
    excluded_sampled       integer     NOT NULL DEFAULT 0 CHECK (excluded_sampled >= 0),
    counts_for_prevalence  boolean     NOT NULL,
    reviews_required       smallint    NOT NULL DEFAULT 1 CHECK (reviews_required BETWEEN 1 AND 3),
    feature_version        text,
    created_by             uuid        NOT NULL,
    created_by_email       text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    withdrawn_at           timestamptz,
    withdrawn_by           uuid,
    withdrawn_reason       text        CHECK (char_length(withdrawn_reason) <= 500),
    CONSTRAINT ck_tl_batches_prevalence CHECK (counts_for_prevalence = (strategy = 'uniform')),
    CONSTRAINT ck_tl_batches_window CHECK (window_until > window_since)
);
CREATE INDEX ix_tl_batches_org_time ON plenum_tracelab.sample_batches (organization_id, created_at DESC);
COMMENT ON TABLE plenum_tracelab.sample_batches IS 'One drawn review batch: strategy, seed, parameters and window. Only the withdrawn_* columns are ever updated, once.';
COMMENT ON COLUMN plenum_tracelab.sample_batches.counts_for_prevalence IS 'True exactly for uniform batches. Rates from any other batch are sample fractions.';

CREATE TABLE plenum_tracelab.sample_items (
    id               uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id         uuid             NOT NULL REFERENCES plenum_tracelab.sample_batches(id),
    organization_id  uuid             NOT NULL,
    turn_id          text             NOT NULL,
    session_id       text,
    position         integer          NOT NULL CHECK (position >= 1),
    reason           text             NOT NULL CHECK (char_length(reason) <= 300),
    cluster          integer,
    dimension_value  text,
    score            double precision,
    features         jsonb,
    created_at       timestamptz      NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_items_turn UNIQUE (organization_id, turn_id),
    CONSTRAINT ux_tl_items_position UNIQUE (batch_id, position)
);
CREATE INDEX ix_tl_items_turn_only ON plenum_tracelab.sample_items (turn_id);
CREATE TRIGGER sample_items_append_only BEFORE UPDATE OR DELETE ON plenum_tracelab.sample_items
    FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.refuse_change();
CREATE TRIGGER sample_items_no_truncate BEFORE TRUNCATE ON plenum_tracelab.sample_items
    FOR EACH STATEMENT EXECUTE FUNCTION plenum_tracelab.refuse_change();
COMMENT ON COLUMN plenum_tracelab.sample_items.turn_id IS 'plenum_cafm.agent_trace_turns.turn_id, no foreign key. On the span-purge keep-list. A turn is drawn once per company.';

CREATE TABLE plenum_tracelab.annotations (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    lineage_id        uuid        NOT NULL,
    version           integer     NOT NULL CHECK (version >= 1),
    supersedes        uuid        REFERENCES plenum_tracelab.annotations(id),
    superseded_by     uuid,
    superseded_at     timestamptz,
    kind              text        NOT NULL CHECK (kind IN ('verdict','comment')),
    organization_id   uuid        NOT NULL,
    conversation_key  text        NOT NULL,
    turn_id           text,
    reviewer_id       uuid        NOT NULL,
    reviewer_email    text,
    tombstone         boolean     NOT NULL DEFAULT false,
    verdict           text        CHECK (verdict IN ('right','wrong')),
    note              text        CHECK (char_length(note) <= 2000),
    span_id           uuid,
    field             text        CHECK (field IN ('question','answer','input','output')),
    quote             text        CHECK (char_length(quote) <= 2000),
    quote_occurrence  integer     CHECK (quote_occurrence >= 1),
    quote_start       integer     CHECK (quote_start >= 0),
    span_name         text,
    span_kind         text,
    span_agent        text,
    blind             boolean,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_tl_ann_supersedes UNIQUE (supersedes),
    CONSTRAINT ck_tl_ann_verdict_turn CHECK (kind <> 'verdict' OR turn_id IS NOT NULL),
    CONSTRAINT ck_tl_ann_live_verdict CHECK (tombstone OR kind <> 'verdict' OR verdict IS NOT NULL),
    CONSTRAINT ck_tl_ann_wrong CHECK (tombstone OR verdict IS DISTINCT FROM 'wrong'
        OR (btrim(coalesce(note, '')) <> '' AND quote IS NOT NULL AND field IS NOT NULL
            AND quote_occurrence IS NOT NULL)),
    CONSTRAINT ck_tl_ann_right CHECK (verdict IS DISTINCT FROM 'right'
        OR (quote IS NULL AND span_id IS NULL AND field IS NULL)),
    CONSTRAINT ck_tl_ann_comment CHECK (kind <> 'comment' OR verdict IS NULL),
    CONSTRAINT ck_tl_ann_comment_note CHECK (tombstone OR kind <> 'comment' OR btrim(coalesce(note, '')) <> ''),
    CONSTRAINT ck_tl_ann_span_field CHECK (span_id IS NULL OR field IN ('input','output')),
    CONSTRAINT ck_tl_ann_turn_field CHECK (span_id IS NOT NULL OR field IS NULL OR field IN ('question','answer'))
);
CREATE UNIQUE INDEX ux_tl_ann_verdict_head ON plenum_tracelab.annotations (turn_id, reviewer_id)
    WHERE kind = 'verdict' AND superseded_by IS NULL;
CREATE UNIQUE INDEX ux_tl_ann_lineage_head ON plenum_tracelab.annotations (lineage_id)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_ann_turn ON plenum_tracelab.annotations (turn_id);
CREATE INDEX ix_tl_ann_conv_head ON plenum_tracelab.annotations (organization_id, conversation_key)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tl_ann_org_time ON plenum_tracelab.annotations (organization_id, created_at);
CREATE TRIGGER annotations_supersede_only BEFORE UPDATE OR DELETE ON plenum_tracelab.annotations
    FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.supersede_only();
CREATE TRIGGER annotations_no_truncate BEFORE TRUNCATE ON plenum_tracelab.annotations
    FOR EACH STATEMENT EXECUTE FUNCTION plenum_tracelab.supersede_only();
COMMENT ON TABLE plenum_tracelab.annotations IS 'Reviewer verdicts and conversation comments as versions: an edit is a new row, a delete is a tombstone row.';
COMMENT ON COLUMN plenum_tracelab.annotations.turn_id IS 'plenum_cafm.agent_trace_turns.turn_id, no foreign key. On the span-purge keep-list.';
COMMENT ON COLUMN plenum_tracelab.annotations.quote IS 'Verbatim text of the redacted block. span_name, span_kind and span_agent are copied so the note outlives the span purge.';
COMMENT ON COLUMN plenum_tracelab.annotations.blind IS 'True when the reviewer could not see other verdicts on the turn when saving. Agreement uses version 1 rows with blind true.';

CREATE TABLE plenum_tracelab.annotation_events (
    seq               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id   uuid        NOT NULL,
    conversation_key  text,
    turn_id           text,
    lineage_id        uuid,
    annotation_id     uuid,
    action            text        NOT NULL CHECK (action IN ('create','edit','delete','reveal','dispose','undispose')),
    detail            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    actor_id          uuid        NOT NULL,
    actor_email       text,
    at                timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_tl_ann_events_turn ON plenum_tracelab.annotation_events (turn_id, at);
CREATE INDEX ix_tl_ann_events_lineage ON plenum_tracelab.annotation_events (lineage_id, at);
CREATE TRIGGER annotation_events_append_only BEFORE UPDATE OR DELETE ON plenum_tracelab.annotation_events
    FOR EACH ROW EXECUTE FUNCTION plenum_tracelab.refuse_change();
CREATE TRIGGER annotation_events_no_truncate BEFORE TRUNCATE ON plenum_tracelab.annotation_events
    FOR EACH STATEMENT EXECUTE FUNCTION plenum_tracelab.refuse_change();
COMMENT ON TABLE plenum_tracelab.annotation_events IS 'Append-only who-did-what log of annotations. reveal is written from M3, dispose and undispose from M4.';
```

Regenerate `catalog.json` against the scratch database with M0's CLI. Define and run this helper from the repo root (it copies the tree into the testenv container, runs `snapshot`, and copies the new catalog back):

```sh
tl_da_snapshot() {
  C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-snap-$(date +%s)
  B=$R/apps/backend/cafm-connector-service-final
  docker exec $C sh -lc "mkdir -p $B/svc-deepagents"
  for d in src migrations pyproject.toml; do docker cp apps/backend/cafm-connector-service-final/svc-deepagents/$d $C:$B/svc-deepagents/$d; done
  N='postgresql+asyncpg://t:t@127.0.0.1:1/t'; S=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch
  docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N $C \
    sh -lc "cd $B/svc-deepagents && python -m src.trace_lab.migrate snapshot --db-url $S"
  docker cp $C:$B/svc-deepagents/migrations/trace_lab/catalog.json \
    apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/catalog.json
}
tl_da_snapshot
grep -c '"plenum_tracelab.annotations' apps/backend/cafm-connector-service-final/svc-deepagents/migrations/trace_lab/catalog.json
```

Expected: the snapshot command reports version 2; the `grep -c` prints a non-zero count. `0002_review.sql` is not added to `migrate.SHIPPED_CHECKSUMS` now — that line is added only after Hussain applies the file (final task).

- [ ] **Step 4: Run it and see it pass** (together with M0's migration and SQL suites, which now see version 2)

```sh
tl_da_test tests/test_trace_lab_review_history_is_append_only_sql.py tests/test_trace_lab_migrate.py tests/test_trace_lab_migrate_sql.py tests/test_trace_lab_purge_sql.py
tl_da_test tests/ -k "trace_lab and sql"
```

Expected: all pass — including M0's `tests/test_trace_lab_migrate.py::test_files_are_named_and_numbered_contiguously` and `::test_no_file_touches_another_schema_or_grants`, `tests/test_trace_lab_migrate_sql.py::test_catalog_snapshot_matches_the_files` and `::test_append_only_and_supersede_guards_work_on_any_table`, and `tests/test_trace_lab_purge_sql.py::test_keep_tables_cover_every_turn_reference` (which now sees version 2's turn columns, with `annotation_events.turn_id` excused by Task 3).

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `migrations/trace_lab/0002_review.sql`, `migrations/trace_lab/catalog.json`, `tests/trace_lab_m2_fixtures.py`, `tests/test_trace_lab_review_history_is_append_only_sql.py`. Report in the log: "0002_review.sql creates objects only in plenum_tracelab" (P3 input for Hussain).


### Task 5: `annotations.py` — anchors, quotes and the blind rule (pure)

**Files:**
- Create: `src/trace_lab/annotations.py` (first part: header, constants, pure helpers)
- Test: `tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py`, `tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py`

**Interfaces:**
- Consumes: `db.Invalid`, `db.NotFound`, `db.company_where`, `db.scope_sql` (Task 3 / M0); `normalize.normalize_turn`, `normalize.group_key` (M1, used from Task 6 on) and the `block` key `normalize_turn` sets on span messages (M1's `normalize.block_text`; `""` on a withheld gate call); `trace._iso` (`src/agents/trace.py:794`).
- Produces: `VERDICTS`, `FIELDS`, `TURN_FIELDS`, `NO_FAILURE_NOTE`, `NOTE_MAX`, `QUOTE_MAX`, `TIMEOUT`, `Anchor`, `locate_quote(text_, quote, occurrence) -> int | None`, `block_key(span_id, field) -> str`, `block_texts(record) -> dict[str, dict]` (span text = the message's `block`, skipped when None; question/answer text = the user/assistant message's `text`), `validate_verdict(verdict, note, anchor) -> tuple[str, Anchor | None]`, `validate_comment(note) -> str`, `key_where(conversation_key, alias="tt") -> tuple[str, dict]`, `visible_set(*, me, is_owner, turn_ids, heads, items, revealed) -> set[str]`, `annotation_out(row) -> dict | None`, `_constraint(exc) -> str | None`, plus the private `_turn_record` / `_pin` used by the test (defined in Task 6; this task's `_pin` tests are run again there).

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py`:

```python
"""A wrong verdict needs a note on the first failure and a verbatim quote at a stated occurrence; a right
verdict is stored as "no failure observed" with nothing highlighted; the anchor copies the step's name,
kind and agent so the note survives the span purge (spec "Open coding ..." 2.1, 2.2, 2.7)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src.trace_lab import annotations, db
from src.trace_lab.annotations import Anchor

SPAN = str(uuid.uuid4())
# Messages as M1's normalize_turn emits them: span messages anchored on output carry `block`
# (normalize.block_text; "" on a withheld gate call, R23); question, answer, tool-call and gate messages carry None.
RECORD = {"messages": [
    {"role": "user", "anchor": {"turn_id": "turn-1", "span_id": None, "field": "question"}, "text": "How many WOs in Aug?",
     "block": None},
    {"role": "tool_call", "span_id": SPAN, "anchor": None, "arguments": {"month": "2026-08"}, "block": None},
    {"role": "tool_result", "span_id": SPAN, "anchor": {"turn_id": "turn-1", "span_id": SPAN, "field": "output"},
     "content": {"count": 758, "period": "2026-07"}, "block": '{\n  "count": 758,\n  "period": "2026-07"\n}'},
    {"role": "model_call", "span_id": "m1", "anchor": {"turn_id": "turn-1", "span_id": "m1", "field": "output"},
     "model_call": {"response": "There were 758 work orders."}, "block": "There were 758 work orders."},
    {"role": "model_call", "span_id": "m2", "anchor": {"turn_id": "turn-1", "span_id": "m2", "field": "output"},
     "model_call": {"response": None, "withheld": ["response", "raw"]}, "block": ""},
    {"role": "gate", "span_id": "g1", "anchor": {"turn_id": "turn-1", "span_id": "g1", "field": "answer_after"},
     "block": None},
    {"role": "assistant", "anchor": {"turn_id": "turn-1", "span_id": "root", "field": "answer"},
     "text": "There were 758 work orders in August, 758 in total.", "block": None},
]}


def test_locate_quote_counts_overlapping_occurrences():
    assert annotations.locate_quote("aaaa", "aa", 1) == 0
    assert annotations.locate_quote("aaaa", "aa", 2) == 1          # overlapping, as the browser counts
    assert annotations.locate_quote("aaaa", "aa", 3) == 2
    assert annotations.locate_quote("aaaa", "aa", 4) is None
    assert annotations.locate_quote("758 in August, 758 in total", "758", 2) == 15
    assert annotations.locate_quote("x", "", 1) is None


def test_block_texts_follow_the_normalized_messages():
    blocks = annotations.block_texts(RECORD)
    assert set(blocks) == {":question", f"{SPAN}:output", "m1:output", "m2:output", ":answer"}   # no tool args, no gate
    assert blocks[":answer"]["text"].startswith("There were 758")
    assert blocks[":question"]["text"] == "How many WOs in Aug?"
    assert blocks[f"{SPAN}:output"]["text"] == '{\n  "count": 758,\n  "period": "2026-07"\n}'   # M1's block, as is
    assert blocks["m1:output"]["text"] == "There were 758 work orders."
    assert blocks["m2:output"]["text"] == ""                                          # withheld: nothing to quote (R23)
    assert annotations.block_key(None, "answer") == ":answer" and annotations.block_key("s", "answer") == ":answer"
    assert not hasattr(annotations, "BLOCK_SOURCE") and not hasattr(annotations, "as_text")  # the rule is M1's


def test_a_wrong_verdict_without_a_note_or_an_anchor_is_refused():
    a = Anchor(None, "answer", "758 in August")
    for note in (None, "", "   "):
        with pytest.raises(db.Invalid) as e:
            annotations.validate_verdict("wrong", note, a)
        assert e.value.reason == "note_required"
    with pytest.raises(db.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", None)
    assert e.value.reason == "anchor_required"
    with pytest.raises(db.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", Anchor(None, "answer", "   "))
    assert e.value.reason == "anchor_required"
    with pytest.raises(db.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", Anchor(None, "output", "758"))
    assert e.value.reason == "span_not_in_turn"


def test_a_right_verdict_is_no_failure_observed_with_nothing_highlighted():
    assert annotations.validate_verdict("right", "looks fine", None) == ("no failure observed", None)
    with pytest.raises(db.Invalid) as e:
        annotations.validate_verdict("right", None, Anchor(None, "answer", "758"))
    assert e.value.reason == "anchor_on_right"


def test_a_turn_field_anchor_never_keeps_a_span_id():
    note, a = annotations.validate_verdict("wrong", "counted July", Anchor("root", "answer", "758", 2))
    assert a == Anchor(None, "answer", "758", 2) and note == "counted July"


@pytest.fixture
def pinned(monkeypatch):
    spans = [{"id": uuid.UUID(SPAN), "turn_id": "turn-1", "name": "answer_from_records", "kind": "tool", "agent": "wo_engine"}]

    async def fake_record(conn, turn):
        return RECORD, spans

    monkeypatch.setattr(annotations, "_turn_record", fake_record)
    return lambda anchor: asyncio.run(annotations._pin(None, {"turn_id": "turn-1"}, anchor))


def test_the_anchor_copies_the_step_name_kind_and_agent(pinned):
    pin = pinned(Anchor(SPAN, "output", '"period": "2026-07"'))
    assert (pin["sname"], pin["skind"], pin["sagent"], pin["start"]) == ("answer_from_records", "tool", "wo_engine", 20)
    turn = pinned(Anchor(None, "answer", "758", 2))
    assert (turn["span_id"], turn["sname"], turn["skind"], turn["sagent"], turn["start"]) == (None, "turn", "answer", None, 38)


def test_a_quote_missing_at_its_occurrence_is_refused(pinned):
    with pytest.raises(db.Invalid) as e:
        pinned(Anchor(None, "answer", "758", 3))
    assert e.value.reason == "quote_not_found"
    with pytest.raises(db.Invalid) as e:
        pinned(Anchor(None, "answer", "seven hundred"))
    assert e.value.reason == "quote_not_found"


def test_a_span_from_another_turn_is_refused(pinned):
    with pytest.raises(db.Invalid) as e:
        pinned(Anchor(str(uuid.uuid4()), "output", "758"))
    assert e.value.reason == "span_not_in_turn"


def test_conversation_keys_select_their_turns_and_nothing_else():
    assert annotations.key_where("s:abc") == ("tt.session_id = :tl_key_v AND tt.session_id NOT LIKE 'rerun%'", {"tl_key_v": "abc"})
    assert annotations.key_where("r:turn-1") == ("tt.turn_id = :tl_key_v", {"tl_key_v": "turn-1"})
    assert annotations.key_where("t:turn-2")[1] == {"tl_key_v": "turn-2"}
    for bad in ("x:1", "s:", "abc"):
        with pytest.raises(db.NotFound):
            annotations.key_where(bad)


def test_the_reviewed_filter_sql_for_the_conversation_list():
    sql, p = annotations.reviewed_predicate("b", "11111111-1111-1111-1111-111111111111", "mine")
    assert sql.startswith("EXISTS (") and "tl_ra.turn_id = b.turn_id" in sql and p == {"tl_rev": "11111111-1111-1111-1111-111111111111"}
    assert annotations.reviewed_predicate("b", None, "unreviewed")[0].startswith("NOT EXISTS (")
    with pytest.raises(ValueError):
        annotations.reviewed_predicate("b", None, "mine")
```

`tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py`:

```python
"""Blind review: other reviewers' verdicts, notes and comments stay hidden from the caller until the caller
has saved a verdict on that answer, the answer's batch item is complete, or the caller revealed it; the
platform owner sees everything (spec "Open coding ..." 2.3, 2.5). Pure rules here; the database path is in
the _sql twin of this file."""
from __future__ import annotations

from src.trace_lab import annotations

ME, A, B = "me", "a", "b"


def head(tid, reviewer, verdict="wrong", tombstone=False):
    return {"turn_id": tid, "reviewer_id": reviewer, "verdict": verdict, "tombstone": tombstone}


def test_nothing_is_visible_before_you_judge():
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=[head("t1", A)], items={}, revealed=set()) == set()


def test_your_own_live_verdict_opens_the_answer_and_a_tombstone_does_not():
    heads = [head("t1", A), head("t1", ME, "right"), head("t2", A), head("t2", ME, None, tombstone=True)]
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items={}, revealed=set()) == {"t1"}


def test_a_complete_batch_item_has_nothing_left_to_keep_blind():
    heads = [head("t1", A), head("t2", A)]
    items = {"t1": {"reviews_required": 1}, "t2": {"reviews_required": 2}}
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items=items, revealed=set()) == {"t1"}
    heads.append(head("t2", B, "right"))
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items=items, revealed=set()) == {"t1", "t2"}


def test_a_reveal_opens_the_answer():
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=[head("t1", A)], items={}, revealed={"t1"}) == {"t1"}


def test_the_platform_owner_sees_everything():
    assert annotations.visible_set(me=ME, is_owner=True, turn_ids=["t1", "t2"], heads=[], items={}, revealed=set()) == {"t1", "t2"}
```

- [ ] **Step 2: Run them and see them fail**

```sh
tl_da_test tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py
```

Expected: `ImportError: cannot import name 'annotations' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

Create `src/trace_lab/annotations.py` with:

```python
"""Open coding: a reviewer's verdict on one answer, and comments on a conversation (Trace Lab M2).

A verdict is right or wrong. Wrong carries a note on the first failure and an anchor: the step
(span id, or the turn's own question/answer), a verbatim quote and the quote's occurrence number.
Right is stored with the note "no failure observed" and no anchor. Rows are versions: an edit is a
new row, a delete is a tombstone row, and a write whose `base` is not the current head is refused
with db.Stale (409). Other reviewers' verdicts stay hidden until the caller has saved one (blind
until saved); the read functions here enforce that, not the UI.

The text a quote is checked against is the block text of the normalized, redacted turn
(normalize.normalize_turn, M1): a step's text is M1's normalize.block_text, which normalize_turn
copies onto each span message as `block` ("" for a withheld gate call, R23); the question and the
answer are the user and assistant messages' `text`. These are the strings GET /annotations returns
as `blocks` and the Conversations view renders, so a selection is always a substring of what the
server checks, and M4 checks quotes against the same strings.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..agents.trace import _iso
from . import db, normalize

VERDICTS = ("right", "wrong")
FIELDS = ("question", "answer", "input", "output")
TURN_FIELDS = ("question", "answer")
NO_FAILURE_NOTE = "no failure observed"
NOTE_MAX = 2000
QUOTE_MAX = 2000
TIMEOUT = "SET LOCAL statement_timeout = '10s'"
#: The messages whose `text` is the turn's own question and answer block (M1, spec "Trace capture" 3.2).
_TURN_TEXT_ROLES = {"question": "user", "answer": "assistant"}
_HEAD_RACES = ("ux_tl_ann_verdict_head", "ux_tl_ann_supersedes", "ux_tl_ann_lineage_head")


@dataclass(frozen=True)
class Anchor:
    span_id: str | None
    field: str
    quote: str
    occurrence: int = 1


# ── pure helpers ───────────────────────────────────────────────────────────────────────

def locate_quote(text_: str, quote: str, occurrence: int) -> int | None:
    """Code-point start of the n-th (1-based) occurrence; overlapping matches count. None if absent."""
    if not quote or occurrence < 1:
        return None
    pos = -1
    for _ in range(occurrence):
        pos = text_.find(quote, pos + 1)
        if pos < 0:
            return None
    return pos


def block_key(span_id: str | None, field: str) -> str:
    """'<span id>:<field>' for a step, ':question' / ':answer' for the turn's own text."""
    return (":" if field in TURN_FIELDS else f"{span_id or ''}:") + field


def block_texts(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every annotatable block of a normalized turn: {key: {span_id, field, text}}. First message wins.

    A step's text is the message's `block` (M1's normalize.block_text; "" for a withheld gate call,
    R23); a message whose `block` is None has no step text and is skipped. The question and the answer
    are the user and assistant messages' redacted `text`. M2 formats nothing itself."""
    out: dict[str, dict[str, Any]] = {}
    for m in record.get("messages") or ():
        anchor = m.get("anchor") or None
        if not anchor or anchor.get("field") not in FIELDS:
            continue
        field = anchor["field"]
        if field in TURN_FIELDS:
            if m.get("role") != _TURN_TEXT_ROLES[field]:
                continue
            span_id, value = None, m.get("text") or ""
        else:
            value = m.get("block")
            if value is None:
                continue
            span_id = str(anchor.get("span_id")) if anchor.get("span_id") else None
        key = block_key(span_id, field)
        if key not in out:
            out[key] = {"span_id": span_id, "field": field, "text": str(value)}
    return out


def validate_verdict(verdict: str, note: str | None, anchor: Anchor | None) -> tuple[str, Anchor | None]:
    """The note and anchor to store, or db.Invalid. Right -> NO_FAILURE_NOTE and no anchor."""
    if verdict not in VERDICTS:
        raise db.Invalid("bad_verdict", "A verdict is Right or Wrong.")
    if verdict == "right":
        if anchor is not None:
            raise db.Invalid("anchor_on_right")
        return NO_FAILURE_NOTE, None
    if note is None or not note.strip():
        raise db.Invalid("note_required")
    if len(note) > NOTE_MAX:
        raise db.Invalid("note_required", f"Keep the note under {NOTE_MAX} characters.")
    if anchor is None or not anchor.quote or not anchor.quote.strip() or len(anchor.quote) > QUOTE_MAX:
        raise db.Invalid("anchor_required")
    if anchor.field not in FIELDS:
        raise db.Invalid("anchor_required")
    if anchor.field in TURN_FIELDS:
        anchor = Anchor(None, anchor.field, anchor.quote, anchor.occurrence)
    elif not anchor.span_id:
        raise db.Invalid("span_not_in_turn")
    return note, anchor


def validate_comment(note: str | None) -> str:
    if note is None or not note.strip():
        raise db.Invalid("note_required", "Write the comment first.")
    if len(note) > NOTE_MAX:
        raise db.Invalid("note_required", f"Keep the comment under {NOTE_MAX} characters.")
    return note


def key_where(conversation_key: str, alias: str = "tt") -> tuple[str, dict[str, str]]:
    """The turns of one conversation key (M1 GROUP_KEY_SQL: t:<turn>, r:<turn>, s:<session>)."""
    kind, _, value = conversation_key.partition(":")
    if not value or kind not in ("t", "r", "s"):
        raise db.NotFound("turn_not_found")
    if kind == "s":
        return (f"{alias}.session_id = :tl_key_v AND {alias}.session_id NOT LIKE 'rerun%'", {"tl_key_v": value})
    return (f"{alias}.turn_id = :tl_key_v", {"tl_key_v": value})


def visible_set(*, me: str, is_owner: bool, turn_ids: Sequence[str], heads: Sequence[Mapping[str, Any]],
                items: Mapping[str, Mapping[str, Any]], revealed: set[str]) -> set[str]:
    """Turns on which the caller may see other reviewers' verdicts (spec "Open coding" 2.3).

    heads = live verdict heads (any reviewer, tombstones included); items = {turn_id: sample item with
    reviews_required}; revealed = turns the caller has a 'reveal' event on (written from M3 on)."""
    if is_owner:
        return set(turn_ids)
    live: dict[str, set[str]] = {}
    for h in heads:
        if not h.get("tombstone"):
            live.setdefault(h["turn_id"], set()).add(str(h["reviewer_id"]))
    out = set()
    for tid in turn_ids:
        reviewers = live.get(tid, set())
        item = items.get(tid)
        if me in reviewers or tid in revealed or (item and len(reviewers) >= int(item["reviews_required"])):
            out.add(tid)
    return out


def annotation_out(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One stored version as the API returns it."""
    if row is None:
        return None
    r = _iso(dict(row))
    anchor = None
    if r.get("quote") is not None:
        anchor = {"span_id": r.get("span_id"), "field": r.get("field"), "quote": r.get("quote"),
                  "occurrence": r.get("quote_occurrence"), "start": r.get("quote_start"),
                  "span_name": r.get("span_name"), "span_kind": r.get("span_kind"), "span_agent": r.get("span_agent")}
    return {"id": r["id"], "lineage_id": r["lineage_id"], "version": r["version"], "supersedes": r.get("supersedes"),
            "kind": r["kind"], "turn_id": r.get("turn_id"), "conversation_key": r["conversation_key"],
            "reviewer_id": r["reviewer_id"], "reviewer_email": r.get("reviewer_email"), "tombstone": bool(r["tombstone"]),
            "verdict": r.get("verdict"), "note": r.get("note"), "anchor": anchor, "blind": r.get("blind"),
            "created_at": r.get("created_at")}


def _constraint(exc: BaseException) -> str | None:
    seen: Any = exc
    for _ in range(5):
        if seen is None:
            break
        name = getattr(seen, "constraint_name", None)
        if name:
            return str(name)
        seen = getattr(seen, "orig", None) or getattr(seen, "__cause__", None)
    msg = str(exc)
    return next((n for n in _HEAD_RACES if n in msg), None)
```

- [ ] **Step 4: Run them and see the pure tests pass**

```sh
tl_da_test tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py
```

Expected: every test passes except four that need later parts of the module: the three that use the `pinned` fixture (`test_the_anchor_copies_the_step_name_kind_and_agent`, `test_a_quote_missing_at_its_occurrence_is_refused`, `test_a_span_from_another_turn_is_refused`) fail with `AttributeError: ... has no attribute '_turn_record'` until Task 6, and `test_the_reviewed_filter_sql_for_the_conversation_list` fails with `AttributeError: ... has no attribute 'reviewed_predicate'` until Task 7.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/annotations.py`, the two test files.

### Task 6: Verdict writes — versions, tombstones, 409s, blind flag

**Files:**
- Modify: `src/trace_lab/annotations.py` — append
- Test: `tests/test_trace_lab_an_edit_is_a_new_version_and_the_latest_wins_sql.py`

**Interfaces:**
- Consumes: Task 5 helpers; `db.engine()`, `db.scope_sql`, `db.Stale`, `db.NotFound`, `db.Invalid`; `normalize.normalize_turn`, `normalize.group_key`; tables from Task 4.
- Produces: `async save_verdict(principal, org: str, turn_id: str, *, verdict: str, note: str | None, anchor: Anchor | None, base: str | None) -> dict`, `async delete_verdict(principal, org: str, turn_id: str, *, base: str) -> dict`; private `_scoped_turn`, `_turn_record`, `_pin`, `_verdict_head`, `_current_verdict`, `_blind`, `_INSERT`, `_SUPERSEDE`, `_EVENT`, `_NO_PIN` (reused by Task 7).

Write algorithm (spec §Review 2.4, one transaction): scoped turn read (404 `turn_not_found`, which also covers another company's turn and a NULL-company turn, R27) → quote checked against the block text of the normalized turn (422 `quote_not_found` / `span_not_in_turn`) → head `FOR UPDATE` → `base` must equal the head id (409 `stale` with the head) → `blind` (version 1: not revealed and the sample item not already complete; later versions: no other live verdict) → set `superseded_by`/`superseded_at` on the head once → insert the new version → insert the `create`/`edit`/`delete` event. A unique violation on `ux_tl_ann_verdict_head`, `ux_tl_ann_supersedes` or `ux_tl_ann_lineage_head` (two first saves, two edits of one head) becomes 409 `stale` with the re-read head.

- [ ] **Step 1: Write the failing test**

```python
"""Verdict writes on a scratch Postgres: an edit is a new version and the latest wins, a stale base is a
409 carrying the current head, two first saves race to one row and one 409, a delete is a tombstone
plus a delete event, judging again continues the lineage, and two reviewers code the same answer
without overwriting each other (spec "Open coding ..." 2.4; M2 acceptance)."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text

from trace_lab_m2_fixtures import add_span, add_turn, principal, truncate_chat
from trace_lab_scratch import fresh_schema  # M0's scratch helper (tests/trace_lab_scratch.py)
from src.agents import trace
from src.services import chat_threads
from src.trace_lab import annotations, db
from src.trace_lab.annotations import Anchor

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

ORG = str(uuid.uuid4())


@pytest.fixture
async def eng(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(chat_threads, "_ready", False)
    await chat_threads.ensure_tables()
    monkeypatch.setattr(trace, "_ready", True)
    yield engine
    await truncate_chat(engine)
    await engine.dispose()


async def _rows(engine, tid):
    async with engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, version, superseded_by::text AS superseded_by, tombstone, verdict "
            "FROM plenum_tracelab.annotations WHERE turn_id = :t ORDER BY version"), {"t": tid})).mappings().all()]


async def test_an_edit_is_a_new_version_and_the_latest_wins(eng):
    tid = await add_turn(eng, 1, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert (v1["version"], v1["note"], v1["anchor"], v1["blind"]) == (1, "no failure observed", None, True)
    v2 = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Counted July, not August.",
                                        anchor=Anchor(None, "answer", "758"), base=v1["id"])
    assert (v2["version"], v2["supersedes"], v2["lineage_id"]) == (2, v1["id"], v1["id"])
    assert v2["anchor"]["span_name"] == "turn" and v2["anchor"]["span_kind"] == "answer" and v2["anchor"]["start"] == 11
    rows = await _rows(eng, tid)
    assert [(r["version"], r["superseded_by"]) for r in rows] == [(1, v2["id"]), (2, None)]


async def test_a_stale_base_is_refused_with_the_current_head(eng):
    tid = await add_turn(eng, 2, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    v2 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"])
    with pytest.raises(db.Stale) as e:
        await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"])
    assert e.value.current["id"] == v2["id"]
    with pytest.raises(db.Stale):
        await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)


async def test_two_first_saves_race_to_one_row_and_one_409(eng):
    tid = await add_turn(eng, 3, ORG)
    me = principal(ORG)
    results = await asyncio.gather(
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None),
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None),
        return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]
    assert len(await _rows(eng, tid)) == 1


async def test_a_delete_is_a_tombstone_and_judging_again_continues_the_lineage(eng):
    tid = await add_turn(eng, 4, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    tomb = await annotations.delete_verdict(me, ORG, tid, base=v1["id"])
    assert tomb["tombstone"] is True and tomb["version"] == 2 and tomb["verdict"] is None
    with pytest.raises(db.NotFound) as e:
        await annotations.delete_verdict(me, ORG, tid, base=tomb["id"])
    assert e.value.reason == "annotation_not_found"
    v3 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=tomb["id"])
    assert (v3["version"], v3["lineage_id"]) == (3, v1["id"])
    async with eng.connect() as conn:
        actions = [r[0] for r in (await conn.execute(text(
            "SELECT action FROM plenum_tracelab.annotation_events WHERE turn_id = :t ORDER BY seq"), {"t": tid})).all()]
    assert actions == ["create", "delete", "edit"]


async def test_a_step_quote_copies_the_step_and_a_missing_one_is_refused(eng):
    tid = await add_turn(eng, 5, ORG)
    span = await add_span(eng, tid, output={"count": 758, "period": "2026-07"})
    other = await add_span(eng, await add_turn(eng, 6, ORG))
    me = principal(ORG)
    v = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Read July.",
                                       anchor=Anchor(span, "output", '"period": "2026-07"'), base=None)
    assert (v["anchor"]["span_name"], v["anchor"]["span_kind"], v["anchor"]["span_agent"]) == ("answer_from_records", "tool", "wo_engine")
    with pytest.raises(db.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(other, "output", "758"), base=None)
    assert e.value.reason == "span_not_in_turn"
    with pytest.raises(db.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(None, "answer", "758", 2), base=None)
    assert e.value.reason == "quote_not_found"


async def test_two_reviewers_code_the_same_answer_without_overwriting(eng):
    tid = await add_turn(eng, 7, ORG)
    a, b = principal(ORG), principal(ORG)
    va = await annotations.save_verdict(a, ORG, tid, verdict="wrong", note="Counted July.",
                                        anchor=Anchor(None, "answer", "758"), base=None)
    vb = await annotations.save_verdict(b, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert va["blind"] is True and vb["blind"] is True
    heads = [r for r in await _rows(eng, tid) if r["superseded_by"] is None]
    assert sorted(r["verdict"] for r in heads) == ["right", "wrong"]
    va2 = await annotations.save_verdict(a, ORG, tid, verdict="wrong", note="Counted July, and the wrong site.",
                                         anchor=Anchor(None, "answer", "758"), base=va["id"])
    assert va2["blind"] is False                       # b already had a verdict when this version was saved


async def test_another_companys_turn_and_a_turn_without_a_company_are_not_found(eng):
    other = await add_turn(eng, 8, str(uuid.uuid4()))
    orphan = await add_turn(eng, 9, None)
    for tid in (other, orphan):
        with pytest.raises(db.NotFound) as e:
            await annotations.save_verdict(principal(ORG), ORG, tid, verdict="right", note=None, anchor=None, base=None)
        assert e.value.reason == "turn_not_found"
```

- [ ] **Step 2: Run it and see it fail** (scratch Postgres up)

```sh
tl_da_test tests/test_trace_lab_an_edit_is_a_new_version_and_the_latest_wins_sql.py
```

Expected: `AttributeError: module 'src.trace_lab.annotations' has no attribute 'save_verdict'`.

- [ ] **Step 3: Implement**

Append to `src/trace_lab/annotations.py`:

```python
# ── reads shared by the writes ──────────────────────────────────────────────────────────

async def _scoped_turn(conn, principal, org: str | None, turn_id: str) -> dict[str, Any] | None:
    where, p = db.scope_sql(principal, org, alias="tt")
    row = (await conn.execute(text(
        f"SELECT tt.* FROM plenum_cafm.agent_trace_turns tt "
        f"WHERE tt.turn_id = :tl_tid AND tt.organization_id IS NOT NULL AND {where}"),
        {**p, "tl_tid": turn_id})).mappings().first()
    return dict(row) if row else None


async def _turn_record(conn, turn: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The normalized turn (M1) and its raw span rows."""
    spans = [dict(s) for s in (await conn.execute(text(
        "SELECT * FROM plenum_cafm.agent_trace_spans WHERE turn_id = :tl_tid ORDER BY seq"),
        {"tl_tid": turn["turn_id"]})).mappings().all()]
    meta = (await conn.execute(text("SELECT * FROM plenum_tracelab.turn_meta WHERE turn_id = :tl_tid"),
                               {"tl_tid": turn["turn_id"]})).mappings().first()
    record = normalize.normalize_turn(dict(turn), spans, meta=dict(meta) if meta else None)
    return record, spans


async def _pin(conn, turn: Mapping[str, Any], anchor: Anchor) -> dict[str, Any]:
    """Check the quote against the block it names; return the columns the row copies (D5)."""
    record, spans = await _turn_record(conn, turn)
    span = None
    if anchor.span_id:
        span = next((s for s in spans if str(s["id"]) == str(anchor.span_id)), None)
        if span is None:
            raise db.Invalid("span_not_in_turn")
    block = block_texts(record).get(block_key(anchor.span_id, anchor.field))
    start = locate_quote(block["text"], anchor.quote, anchor.occurrence) if block else None
    if start is None:
        raise db.Invalid("quote_not_found")
    if span is None:
        return {"span_id": None, "field": anchor.field, "quote": anchor.quote, "occ": anchor.occurrence,
                "start": start, "sname": "turn", "skind": anchor.field, "sagent": None}
    return {"span_id": str(span["id"]), "field": anchor.field, "quote": anchor.quote, "occ": anchor.occurrence,
            "start": start, "sname": span.get("name"), "skind": span.get("kind"), "sagent": span.get("agent")}


_NO_PIN = {"span_id": None, "field": None, "quote": None, "occ": None, "start": None,
           "sname": None, "skind": None, "sagent": None}

_INSERT = text("""
    INSERT INTO plenum_tracelab.annotations
        (id, lineage_id, version, supersedes, kind, organization_id, conversation_key, turn_id, reviewer_id,
         reviewer_email, tombstone, verdict, note, span_id, field, quote, quote_occurrence, quote_start,
         span_name, span_kind, span_agent, blind)
    VALUES (CAST(:id AS uuid), CAST(:lineage AS uuid), :version, CAST(:supersedes AS uuid), :kind,
            CAST(:org AS uuid), :ckey, :tid, CAST(:me AS uuid), :email, :tomb, :verdict, :note,
            CAST(:span_id AS uuid), :field, :quote, :occ, :start, :sname, :skind, :sagent, :blind)
    RETURNING *""")
_SUPERSEDE = text("""
    UPDATE plenum_tracelab.annotations SET superseded_by = CAST(:new AS uuid), superseded_at = clock_timestamp()
     WHERE id = CAST(:head AS uuid) AND superseded_by IS NULL""")
_EVENT = text("""
    INSERT INTO plenum_tracelab.annotation_events
        (organization_id, conversation_key, turn_id, lineage_id, annotation_id, action, detail, actor_id, actor_email)
    VALUES (CAST(:org AS uuid), :ckey, :tid, CAST(:lineage AS uuid), CAST(:aid AS uuid), :action,
            CAST(:detail AS jsonb), CAST(:me AS uuid), :email)""")


async def _verdict_head(conn, turn_id: str, me: str, *, lock: bool) -> dict[str, Any] | None:
    row = (await conn.execute(text(
        "SELECT * FROM plenum_tracelab.annotations WHERE kind = 'verdict' AND turn_id = :tl_tid "
        "AND reviewer_id = CAST(:tl_me AS uuid) AND superseded_by IS NULL" + (" FOR UPDATE" if lock else "")),
        {"tl_tid": turn_id, "tl_me": me})).mappings().first()
    return dict(row) if row else None


async def _current_verdict(turn_id: str, me: str) -> dict[str, Any] | None:
    async with db.engine().connect() as conn:
        return annotation_out(await _verdict_head(conn, turn_id, me, lock=False))


async def _blind(conn, org: str, turn_id: str, me: str, head: Mapping[str, Any] | None) -> bool:
    """Version 1: blind unless the caller revealed the turn or its sample item was already complete.
    Later versions: blind only if no other reviewer had a current verdict (spec 2.3)."""
    others = (await conn.execute(text(
        "SELECT count(DISTINCT reviewer_id) FROM plenum_tracelab.annotations WHERE kind = 'verdict' "
        "AND turn_id = :tl_tid AND superseded_by IS NULL AND NOT tombstone AND reviewer_id <> CAST(:tl_me AS uuid)"),
        {"tl_tid": turn_id, "tl_me": me})).scalar_one()
    if head is not None:
        return int(others) == 0
    revealed = (await conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM plenum_tracelab.annotation_events WHERE turn_id = :tl_tid "
        "AND actor_id = CAST(:tl_me AS uuid) AND action = 'reveal')"), {"tl_tid": turn_id, "tl_me": me})).scalar_one()
    required = (await conn.execute(text(
        "SELECT b.reviews_required FROM plenum_tracelab.sample_items si JOIN plenum_tracelab.sample_batches b "
        "ON b.id = si.batch_id WHERE si.organization_id = CAST(:tl_org AS uuid) AND si.turn_id = :tl_tid"),
        {"tl_org": org, "tl_tid": turn_id})).scalar_one_or_none()
    complete = required is not None and int(others) >= int(required)
    return not (bool(revealed) or complete)


# ── verdicts ───────────────────────────────────────────────────────────────────────────

async def save_verdict(principal, org: str, turn_id: str, *, verdict: str, note: str | None,
                       anchor: Anchor | None, base: str | None) -> dict[str, Any]:
    """Write the caller's verdict as a new version (spec "Open coding" 2.4). db.NotFound, Invalid, Stale."""
    note, anchor = validate_verdict(verdict, note, anchor)
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    try:
        async with db.engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            turn = await _scoped_turn(conn, principal, org, turn_id)
            if turn is None:
                raise db.NotFound("turn_not_found")
            pin = await _pin(conn, turn, anchor) if anchor else dict(_NO_PIN)
            head = await _verdict_head(conn, turn_id, me, lock=True)
            if (str(head["id"]) if head else None) != (base or None):
                raise db.Stale(annotation_out(head))
            blind = await _blind(conn, org, turn_id, me, head)
            if head is not None:
                res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
                if res.rowcount != 1:
                    raise db.Stale(annotation_out(head))
            lineage = str(head["lineage_id"]) if head else new_id
            ckey = normalize.group_key(turn.get("session_id"), turn_id)
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": lineage, "version": (int(head["version"]) + 1) if head else 1,
                "supersedes": str(head["id"]) if head else None, "kind": "verdict", "org": org, "ckey": ckey,
                "tid": turn_id, "me": me, "email": principal.email or None, "tomb": False, "verdict": verdict,
                "note": note, "blind": blind, **pin})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": ckey, "tid": turn_id, "lineage": lineage, "aid": new_id,
                "action": "edit" if head else "create",
                "detail": json.dumps({"verdict": verdict, "version": row["version"]}), "me": me,
                "email": principal.email or None})
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            raise db.Stale(await _current_verdict(turn_id, me)) from exc
        raise
    return annotation_out(row)


async def delete_verdict(principal, org: str, turn_id: str, *, base: str) -> dict[str, Any]:
    """Write a tombstone version of the caller's verdict. db.NotFound(annotation_not_found), Stale."""
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    try:
        async with db.engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            turn = await _scoped_turn(conn, principal, org, turn_id)
            if turn is None:
                raise db.NotFound("turn_not_found")
            head = await _verdict_head(conn, turn_id, me, lock=True)
            if head is None or head["tombstone"]:
                raise db.NotFound("annotation_not_found")
            if str(head["id"]) != base:
                raise db.Stale(annotation_out(head))
            res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
            if res.rowcount != 1:
                raise db.Stale(annotation_out(head))
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": str(head["lineage_id"]), "version": int(head["version"]) + 1,
                "supersedes": str(head["id"]), "kind": "verdict", "org": org, "ckey": head["conversation_key"],
                "tid": turn_id, "me": me, "email": principal.email or None, "tomb": True, "verdict": None,
                "note": None, "blind": None, **_NO_PIN})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": head["conversation_key"], "tid": turn_id, "lineage": str(head["lineage_id"]),
                "aid": new_id, "action": "delete", "detail": json.dumps({"version": row["version"]}), "me": me,
                "email": principal.email or None})
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            raise db.Stale(await _current_verdict(turn_id, me)) from exc
        raise
    return annotation_out(row)
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_an_edit_is_a_new_version_and_the_latest_wins_sql.py tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py
```

Expected: all pass except `test_the_reviewed_filter_sql_for_the_conversation_list` (Task 7); the three `pinned` tests of Task 5 now pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/annotations.py`, `tests/test_trace_lab_an_edit_is_a_new_version_and_the_latest_wins_sql.py`.

### Task 7: Comments and the blind read layer

**Files:**
- Modify: `src/trace_lab/annotations.py` — append
- Test: `tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge_sql.py`

**Interfaces:**
- Consumes: Tasks 5–6; `db.company_where`, `db.Forbidden`.
- Produces: `async add_comment(principal, org, conversation_key, *, note, turn_id) -> dict`, `edit_comment(principal, org, lineage_id, *, note, base) -> dict`, `delete_comment(principal, org, lineage_id, *, base) -> dict`, `company_clause(org, alias) -> tuple[str, dict]`, `async for_conversation(principal, org, conversation_key, *, turn_id=None) -> dict` (shape: `{ok, conversation_key, turns: {turn_id: {mine, others, others_count, others_hidden, reviews_required, sample_item}}, comments, comments_hidden, blocks}`), `for_turns(principal, org, turn_ids) -> dict[str, dict]`, `history(principal, org, turn_id) -> {ok, turn_id, versions, events}`, `visible_others(conn, principal, org, turn_ids) -> set[str]`, `reviewed_turns(conn, org) -> set[str]`, `reviewed_predicate(alias, reviewer_id, mode="reviewed") -> tuple[str, dict]`.

Rules (spec §Review 2.3, 2.5): other reviewers' verdicts come back as `others: null` with `others_count` until the caller has a live verdict on that answer, revealed it (M3 writes `reveal` events), or its sample item has as many distinct live verdicts as `reviews_required`; the platform owner (superadmin, not acting) sees everything. Other people's comments on a conversation are counted, not returned, while any of its answers is still blind for the caller; the caller's own comments are always returned. Only the author edits or deletes a comment (403 `not_author`). `history` returns other reviewers' versions and events only once visible.

- [ ] **Step 1: Write the failing test**

```python
"""Blind review end to end on a scratch Postgres: GET /annotations' data hides other reviewers until the
caller saves, shows them after, counts hidden comments, serves the active answer's block texts, keeps
history to the caller's own rows while blind, and lets the platform owner read everything."""
from __future__ import annotations

import os
import uuid

import pytest

from trace_lab_m2_fixtures import add_turn, principal, truncate_chat
from trace_lab_scratch import fresh_schema  # M0's scratch helper (tests/trace_lab_scratch.py)
from src.agents import trace
from src.services import chat_threads
from src.trace_lab import annotations, db
from src.trace_lab.annotations import Anchor

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

ORG = str(uuid.uuid4())


@pytest.fixture
async def eng(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(chat_threads, "_ready", False)
    await chat_threads.ensure_tables()
    monkeypatch.setattr(trace, "_ready", True)
    yield engine
    await truncate_chat(engine)
    await engine.dispose()


async def test_others_are_hidden_until_you_save_then_shown(eng):
    t1 = await add_turn(eng, 1, ORG, session="sess-blind", minutes_ago=30)
    t2 = await add_turn(eng, 2, ORG, session="sess-blind", minutes_ago=20)
    a, b = principal(ORG), principal(ORG)
    await annotations.save_verdict(a, ORG, t1, verdict="wrong", note="Counted July.", anchor=Anchor(None, "answer", "758"), base=None)
    await annotations.add_comment(a, ORG, "s:sess-blind", note="Same failure as last week.", turn_id=t1)

    before = await annotations.for_conversation(b, ORG, "s:sess-blind", turn_id=t1)
    st = before["turns"][t1]
    assert (st["mine"], st["others"], st["others_count"], st["others_hidden"]) == (None, None, 1, True)
    assert before["comments"] == [] and before["comments_hidden"] == 1
    assert before["blocks"][t1][":answer"] == "There were 758 work orders in August."
    hist = await annotations.history(b, ORG, t1)
    assert hist["versions"] == [] and hist["events"] == []

    await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    await annotations.save_verdict(b, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    after = await annotations.for_conversation(b, ORG, "s:sess-blind")
    assert after["turns"][t1]["others"][0]["note"] == "Counted July."
    assert after["comments_hidden"] == 0 and after["comments"][0]["note"] == "Same failure as last week."
    assert len((await annotations.history(b, ORG, t1))["versions"]) == 2


async def test_a_complete_sample_item_is_visible_and_the_next_verdict_is_not_blind(eng):
    t1 = await add_turn(eng, 3, ORG)
    a, b = principal(ORG), principal(ORG)
    from sqlalchemy import text
    async with eng.begin() as conn:
        bid = str(uuid.uuid4())
        await conn.execute(text("""
            INSERT INTO plenum_tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
                window_until, requested, selected, pool_size, counts_for_prevalence, reviews_required, created_by)
            VALUES (CAST(:b AS uuid), CAST(:o AS uuid), 'initial_uniform', 'uniform', 7, now() - interval '1 day',
                    now(), 1, 1, 1, true, 1, gen_random_uuid())"""), {"b": bid, "o": ORG})
        await conn.execute(text("""
            INSERT INTO plenum_tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
            VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:o AS uuid), :t, 1, 'uniform pick')"""),
            {"b": bid, "o": ORG, "t": t1})
    await annotations.save_verdict(a, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    seen = await annotations.for_turns(b, ORG, [t1])
    assert seen[t1]["others"] is not None and seen[t1]["reviews_required"] == 1
    assert seen[t1]["sample_item"]["reason"] == "uniform pick"
    vb = await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    assert vb["blind"] is False


async def test_the_platform_owner_reads_every_company_and_an_admin_only_their_own(eng):
    t1 = await add_turn(eng, 4, ORG)
    await annotations.save_verdict(principal(ORG), ORG, t1, verdict="right", note=None, anchor=None, base=None)
    owner = principal(None, role="superadmin")
    st = (await annotations.for_turns(owner, None, [t1]))[t1]
    assert st["others"] is not None and st["others_count"] == 1
    stranger = str(uuid.uuid4())
    assert await annotations.for_turns(principal(stranger), stranger, [t1]) == {}
    with pytest.raises(db.NotFound):
        await annotations.for_conversation(principal(stranger), stranger, f"t:{t1}")


async def test_only_the_author_changes_a_comment_and_a_stale_base_is_refused(eng):
    await add_turn(eng, 5, ORG, session="sess-c")
    a, b = principal(ORG), principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-c", note="First thought.", turn_id=None)
    with pytest.raises(db.Forbidden) as e:
        await annotations.edit_comment(b, ORG, c1["lineage_id"], note="Not mine.", base=c1["id"])
    assert e.value.reason == "not_author"
    c2 = await annotations.edit_comment(a, ORG, c1["lineage_id"], note="Second thought.", base=c1["id"])
    with pytest.raises(db.Stale):
        await annotations.edit_comment(a, ORG, c1["lineage_id"], note="Third.", base=c1["id"])
    gone = await annotations.delete_comment(a, ORG, c1["lineage_id"], base=c2["id"])
    assert gone["tombstone"] is True
    assert (await annotations.for_conversation(a, ORG, "s:sess-c"))["comments"] == []
```

- [ ] **Step 2: Run it and see it fail** (scratch Postgres up)

```sh
tl_da_test tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge_sql.py
```

Expected: `AttributeError: module 'src.trace_lab.annotations' has no attribute 'add_comment'`.

- [ ] **Step 3: Implement**

Append to `src/trace_lab/annotations.py`:

```python
# ── comments ───────────────────────────────────────────────────────────────────────────

async def _turns_of_key(conn, principal, org: str | None, conversation_key: str) -> list[dict[str, Any]]:
    where, p = db.scope_sql(principal, org, alias="tt")
    kw, kp = key_where(conversation_key)
    rows = (await conn.execute(text(
        f"SELECT tt.turn_id, tt.session_id, tt.organization_id::text AS organization_id, tt.started_at "
        f"FROM plenum_cafm.agent_trace_turns tt WHERE {kw} AND tt.organization_id IS NOT NULL AND {where} "
        f"ORDER BY tt.started_at, tt.turn_id LIMIT 500"), {**p, **kp})).mappings().all()
    return [dict(r) for r in rows]


async def _comment_head(conn, org: str, lineage_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(str(lineage_id))
    except ValueError:
        return None
    row = (await conn.execute(text(
        "SELECT * FROM plenum_tracelab.annotations WHERE kind = 'comment' AND lineage_id = CAST(:tl_lid AS uuid) "
        "AND organization_id = CAST(:tl_org AS uuid) AND superseded_by IS NULL FOR UPDATE"),
        {"tl_lid": lineage_id, "tl_org": org})).mappings().first()
    return dict(row) if row else None


async def add_comment(principal, org: str, conversation_key: str, *, note: str,
                      turn_id: str | None) -> dict[str, Any]:
    note = validate_comment(note)
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    async with db.engine().begin() as conn:
        await conn.execute(text(TIMEOUT))
        turns = await _turns_of_key(conn, principal, org, conversation_key)
        if not turns or (turn_id and turn_id not in {t["turn_id"] for t in turns}):
            raise db.NotFound("turn_not_found")
        row = (await conn.execute(_INSERT, {
            "id": new_id, "lineage": new_id, "version": 1, "supersedes": None, "kind": "comment", "org": org,
            "ckey": conversation_key, "tid": turn_id, "me": me, "email": principal.email or None, "tomb": False,
            "verdict": None, "note": note, "blind": None, **_NO_PIN})).mappings().one()
        await conn.execute(_EVENT, {
            "org": org, "ckey": conversation_key, "tid": turn_id, "lineage": new_id, "aid": new_id,
            "action": "create", "detail": json.dumps({"kind": "comment", "version": 1}), "me": me,
            "email": principal.email or None})
    return annotation_out(row)


async def _comment_version(principal, org: str, lineage_id: str, *, note: str | None, base: str,
                           tombstone: bool) -> dict[str, Any]:
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    try:
        async with db.engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            head = await _comment_head(conn, org, lineage_id)
            if head is None or head["tombstone"]:
                raise db.NotFound("annotation_not_found")
            if str(head["reviewer_id"]) != me:
                raise db.Forbidden("not_author")
            if str(head["id"]) != base:
                raise db.Stale(annotation_out(head))
            res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
            if res.rowcount != 1:
                raise db.Stale(annotation_out(head))
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": str(head["lineage_id"]), "version": int(head["version"]) + 1,
                "supersedes": str(head["id"]), "kind": "comment", "org": org, "ckey": head["conversation_key"],
                "tid": head.get("turn_id"), "me": me, "email": principal.email or None, "tomb": tombstone,
                "verdict": None, "note": None if tombstone else note, "blind": None, **_NO_PIN})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": head["conversation_key"], "tid": head.get("turn_id"),
                "lineage": str(head["lineage_id"]), "aid": new_id, "action": "delete" if tombstone else "edit",
                "detail": json.dumps({"kind": "comment", "version": row["version"]}), "me": me,
                "email": principal.email or None})
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            async with db.engine().connect() as conn:
                cur = (await conn.execute(text(
                    "SELECT * FROM plenum_tracelab.annotations WHERE lineage_id = CAST(:l AS uuid) "
                    "AND superseded_by IS NULL"), {"l": lineage_id})).mappings().first()
            raise db.Stale(annotation_out(cur)) from exc
        raise
    return annotation_out(row)


async def edit_comment(principal, org: str, lineage_id: str, *, note: str, base: str) -> dict[str, Any]:
    return await _comment_version(principal, org, lineage_id, note=validate_comment(note), base=base, tombstone=False)


async def delete_comment(principal, org: str, lineage_id: str, *, base: str) -> dict[str, Any]:
    return await _comment_version(principal, org, lineage_id, note=None, base=base, tombstone=True)


# ── reads ──────────────────────────────────────────────────────────────────────────────

def company_clause(org: str | None, alias: str) -> tuple[str, dict[str, str]]:
    """db.company_where for a company; TRUE for the platform view (a superadmin not acting)."""
    return db.company_where(org, alias=alias) if org else ("TRUE", {})


async def _states(conn, principal, org: str | None, turn_ids: list[str]) -> dict[str, dict[str, Any]]:
    me = str(principal.user_id)
    cw, cp = company_clause(org, "a")
    heads = [dict(r) for r in (await conn.execute(text(
        f"SELECT a.* FROM plenum_tracelab.annotations a WHERE a.kind = 'verdict' AND a.superseded_by IS NULL "
        f"AND a.turn_id = ANY(CAST(:tl_tids AS text[])) AND {cw} ORDER BY a.created_at"),
        {**cp, "tl_tids": turn_ids})).mappings().all()]
    iw, ip = company_clause(org, "si")
    items = {r["turn_id"]: dict(r) for r in (await conn.execute(text(
        f"SELECT si.turn_id, si.batch_id::text AS batch_id, si.position, si.reason, b.plan_step, b.strategy, "
        f"b.reviews_required, b.counts_for_prevalence, b.withdrawn_at "
        f"FROM plenum_tracelab.sample_items si JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id "
        f"WHERE si.turn_id = ANY(CAST(:tl_tids AS text[])) AND {iw}"), {**ip, "tl_tids": turn_ids})).mappings().all()}
    revealed = {r[0] for r in (await conn.execute(text(
        "SELECT DISTINCT turn_id FROM plenum_tracelab.annotation_events WHERE action = 'reveal' "
        "AND actor_id = CAST(:tl_me AS uuid) AND turn_id = ANY(CAST(:tl_tids AS text[]))"),
        {"tl_me": me, "tl_tids": turn_ids})).all()}
    visible = visible_set(me=me, is_owner=principal.role == "superadmin" and not org, turn_ids=turn_ids,
                          heads=heads, items=items, revealed=revealed)
    out: dict[str, dict[str, Any]] = {}
    for tid in turn_ids:
        mine = next((h for h in heads if h["turn_id"] == tid and str(h["reviewer_id"]) == me), None)
        others = [h for h in heads if h["turn_id"] == tid and str(h["reviewer_id"]) != me and not h["tombstone"]]
        item = items.get(tid)
        shown = tid in visible
        out[tid] = {
            "mine": annotation_out(mine), "others": [annotation_out(h) for h in others] if shown else None,
            "others_count": len(others), "others_hidden": bool(others) and not shown,
            "reviews_required": int(item["reviews_required"]) if item else None,
            "sample_item": _iso({k: item[k] for k in ("batch_id", "position", "reason", "plan_step", "strategy",
                                                       "counts_for_prevalence", "withdrawn_at")}) if item else None,
        }
    return out


async def for_conversation(principal, org: str | None, conversation_key: str, *,
                           turn_id: str | None = None) -> dict[str, Any]:
    """GET /annotations: every turn's verdict state, the conversation's comments, and - for `turn_id` -
    the annotatable block texts of that turn. db.NotFound when nothing in scope matches."""
    me = str(principal.user_id)
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        turns = await _turns_of_key(conn, principal, org, conversation_key)
        if not turns or (turn_id and turn_id not in {t["turn_id"] for t in turns}):
            raise db.NotFound("turn_not_found")
        tids = [t["turn_id"] for t in turns]
        states = await _states(conn, principal, org, tids)
        cw, cp = company_clause(org, "a")
        rows = [dict(r) for r in (await conn.execute(text(
            f"SELECT a.* FROM plenum_tracelab.annotations a WHERE a.kind = 'comment' AND a.superseded_by IS NULL "
            f"AND NOT a.tombstone AND a.conversation_key = :tl_key AND {cw} ORDER BY a.created_at"),
            {**cp, "tl_key": conversation_key})).mappings().all()]
        blind_somewhere = not (principal.role == "superadmin" and not org) and any(
            s["others"] is None for s in states.values())
        mine = [r for r in rows if str(r["reviewer_id"]) == me]
        others = [r for r in rows if str(r["reviewer_id"]) != me]
        blocks = None
        if turn_id:
            turn = await _scoped_turn(conn, principal, org, turn_id)
            record, _ = await _turn_record(conn, turn) if turn else ({}, [])
            blocks = {turn_id: {k: v["text"] for k, v in block_texts(record).items()}}
    comments = [annotation_out(r) for r in (rows if not blind_somewhere else mine)]
    return {"ok": True, "conversation_key": conversation_key, "turns": states, "comments": comments,
            "comments_hidden": len(others) if blind_somewhere else 0, "blocks": blocks}


async def for_turns(principal, org: str | None, turn_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Verdict states for the given turns that are in the caller's scope (others' blind as above)."""
    if not turn_ids:
        return {}
    where, p = db.scope_sql(principal, org, alias="tt")
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        known = [r[0] for r in (await conn.execute(text(
            f"SELECT tt.turn_id FROM plenum_cafm.agent_trace_turns tt WHERE tt.turn_id = ANY(CAST(:tl_tids AS text[])) "
            f"AND tt.organization_id IS NOT NULL AND {where}"), {**p, "tl_tids": list(turn_ids)})).all()]
        return await _states(conn, principal, org, known) if known else {}


async def history(principal, org: str | None, turn_id: str) -> dict[str, Any]:
    """Every verdict version and event on one turn; other reviewers' only once visible to the caller."""
    me = str(principal.user_id)
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        if await _scoped_turn(conn, principal, org, turn_id) is None:
            raise db.NotFound("turn_not_found")
        visible = await visible_others(conn, principal, org, [turn_id])
        versions = [dict(r) for r in (await conn.execute(text(
            "SELECT * FROM plenum_tracelab.annotations WHERE kind = 'verdict' AND turn_id = :tl_tid "
            "ORDER BY created_at, version"), {"tl_tid": turn_id})).mappings().all()]
        events = [dict(r) for r in (await conn.execute(text(
            "SELECT seq, lineage_id, annotation_id, action, detail, actor_id, actor_email, at "
            "FROM plenum_tracelab.annotation_events WHERE turn_id = :tl_tid ORDER BY at, seq"),
            {"tl_tid": turn_id})).mappings().all()]
    show = turn_id in visible
    return {"ok": True, "turn_id": turn_id,
            "versions": [annotation_out(v) for v in versions if show or str(v["reviewer_id"]) == me],
            "events": [_iso(e) for e in events if show or str(e["actor_id"]) == me]}


async def visible_others(conn, principal, org: str | None, turn_ids: list[str]) -> set[str]:
    states = await _states(conn, principal, org, list(turn_ids))
    return {tid for tid, s in states.items() if s["others"] is not None}


async def reviewed_turns(conn, org: str | None) -> set[str]:
    """Turns with at least one live, non-tombstone verdict head."""
    cw, cp = company_clause(org, "a")
    return {r[0] for r in (await conn.execute(text(
        f"SELECT DISTINCT a.turn_id FROM plenum_tracelab.annotations a WHERE a.kind = 'verdict' "
        f"AND a.superseded_by IS NULL AND NOT a.tombstone AND {cw}"), cp)).all()}


def reviewed_predicate(alias: str, reviewer_id: str | None, mode: str = "reviewed") -> tuple[str, dict[str, str]]:
    """SQL for M1's conversation-list `reviewed` filter over turns aliased `alias` (M1 spec 5.3)."""
    live = ("SELECT 1 FROM plenum_tracelab.annotations tl_ra WHERE tl_ra.kind = 'verdict' "
            f"AND tl_ra.turn_id = {alias}.turn_id AND tl_ra.superseded_by IS NULL AND NOT tl_ra.tombstone")
    if mode == "reviewed":
        return f"EXISTS ({live})", {}
    if mode == "unreviewed":
        return f"NOT EXISTS ({live})", {}
    if mode == "mine":
        if not reviewer_id:
            raise ValueError("mine needs a reviewer")
        return f"EXISTS ({live} AND tl_ra.reviewer_id = CAST(:tl_rev AS uuid))", {"tl_rev": str(reviewer_id)}
    raise ValueError(f"unknown reviewed mode: {mode}")
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge_sql.py tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge.py tests/test_trace_lab_a_wrong_verdict_needs_a_note_on_a_verbatim_quote.py
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/annotations.py`, `tests/test_trace_lab_other_reviewers_stay_hidden_until_you_judge_sql.py`.


### Task 8: `sampling.py` — features, k-means, PCA, the five pickers and request checks (pure)

**Files:**
- Create: `src/trace_lab/sampling.py` (first part)
- Test: `tests/test_trace_lab_sampling_is_deterministic_and_recorded.py`, `tests/test_trace_lab_sampling_window_stays_inside_span_retention.py`, `tests/test_trace_lab_outcome_fields_are_not_sampling_dimensions.py`

**Interfaces:**
- Consumes: `db.Invalid` (Task 3); `normalize.group_key`, `normalize.session_kind`, `normalize.redact_text` (M1); `provenance.CHANNELS`, `provenance.inferred_channel_sql` (M0); `trace.DEFAULT_RETENTION_DAYS` (`src/agents/trace.py:49`); `stats.wilson_interval`, `stats.cohen_kappa` (Task 2); `annotations` (Tasks 5–7).
- Produces: the constants and pure functions listed under Interfaces → `sampling` (everything up to `draw`), including `RECORDED_STRATEGIES = ("monitor_flagged",)` and `RECORDED_MAX = 200`, `Candidate(turn_id, session_id, started_at, features, dims, text=None, question=None, organization_id=None, batch_id=None)` with property `conversation_key`, `Pick(turn_id, reason, cluster=None, dimension_value=None, score=None)`, `dimension_quotas(sizes, k) -> list[int]`, `check_window(since, until, *, now=None)`, `check_request(req, *, now=None)` (a `monitor_flagged` request is accepted only with a non-empty `req["items"]` list of at most 200 items, each with a non-empty str `turn_id`; 422 `bad_items` otherwise), `draw(pool, req, *, seeds=()) -> list[Pick]`.

Rules (spec §Review 3.2–3.3): features `f1` are the eight structural columns, NULL → column median, then `log1p` (not for `distinct_tools`), then z-scores with the population deviation (a flat column stays 0); no strategy picks two turns of one conversation (M1 `group_key`); uniform = `random.Random(seed).sample` over the pool sorted by turn id; diversity = k-means (seed 7, at most 25 iterations, `random.Random(seed).sample` initial centroids) with representatives closest to their centroid first, round-robin across clusters, then the random share; dimension = quotas by value size with shortfalls passed on; outlier = 1.5×IQR fences on tokens, both tails, furthest first; enrich = bag-of-words cosine to wrong answers, no repeated question, then the random share, falling back to diversity without seeds.

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_lab_sampling_is_deterministic_and_recorded.py`:

```python
"""Sampling for open coding is deterministic and explainable: the same pool, seed and strategy give the
same picks, every pick carries its reason, no conversation is picked twice, and the diversity, dimension,
outlier and enrich strategies follow the spec's rules (spec "Open coding, sampling ..." 3.2-3.3)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import db, sampling
from src.trace_lab.sampling import Candidate

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def cand(i: int, *, session: str | None = None, tokens: float | None = 1000, llm: float = 2, tools: float = 1,
         distinct: float = 1, dbq: float = 0, latency: float = 3000, chars: float = 400, pos: float = 1,
         route: str = "compliance", channel: str = "chat", role: str | None = "admin", page: str | None = "cc",
         text: str | None = None, question: str | None = None) -> Candidate:
    tid = f"turn-{i:016x}"
    return Candidate(
        turn_id=tid, session_id=session if session is not None else f"sess-{i}", started_at=T0 + timedelta(minutes=i),
        features={"turn_position": pos, "llm_calls": llm, "tool_calls": tools, "distinct_tools": distinct,
                  "db_queries": dbq, "tokens": tokens, "latency_ms": latency, "answer_chars": chars},
        dims={"channel": channel, "route_target": route, "asker_role": role, "page": page},
        text=text, question=question)


def clustered_pool(groups: int, per: int) -> list[Candidate]:
    """`groups` well-separated groups of `per` turns; group g scales every feature by 10**g."""
    pool = []
    for g in range(groups):
        for j in range(per):
            i = g * 100 + j
            f = 10 ** (g % 4) * (1 + 0.01 * j)
            pool.append(cand(i, tokens=1000 * f * (g + 1), llm=2 * f, tools=1 + g, distinct=g, dbq=g * 3,
                             latency=1000 * f, chars=100 * (g + 1) * (1 + 0.02 * j), pos=1 + j % 3))
    return pool


def test_uniform_is_repeatable_and_reasoned():
    pool = [cand(i) for i in range(60)]
    a = sampling.pick_uniform(pool, 15, 7)
    b = sampling.pick_uniform(list(reversed(pool)), 15, 7)
    assert [p.turn_id for p in a] == [p.turn_id for p in b]          # pool order does not matter: sorted first
    assert len(a) == 15 and len({p.turn_id for p in a}) == 15
    assert all(p.reason == "uniform pick" for p in a)
    assert [p.turn_id for p in sampling.pick_uniform(pool, 15, 8)] != [p.turn_id for p in a]


def test_no_strategy_picks_two_turns_of_one_conversation():
    pool = [cand(i, session=f"s-{i // 3}") for i in range(60)]          # 20 conversations of 3 turns
    for picks in (sampling.pick_uniform(pool, 30, 7), sampling.pick_diversity(pool, 30, 7),
                  sampling.pick_dimension(pool, 30, 7, dimension="route_target"), sampling.pick_outliers(pool, 30)):
        keys = [f"s-{int(p.turn_id[5:], 16) // 3}" for p in picks]
        assert len(keys) == len(set(keys))
    assert len(sampling.pick_uniform(pool, 30, 7)) == 20


def test_diversity_k15_share0_is_15_representatives_over_7_clusters():
    pool = clustered_pool(7, 12)
    picks = sampling.pick_diversity(pool, 15, 7, random_share=0)
    assert len(picks) == 15
    assert all(p.reason.startswith("cluster ") and "representative (distance " in p.reason for p in picks)
    assert {p.cluster for p in picks} == set(range(1, 8))


def test_diversity_k24_default_share_is_16_representatives_over_8_clusters_and_8_random():
    pool = clustered_pool(8, 10)
    picks = sampling.pick_diversity(pool, 24, 7)
    reps = [p for p in picks if p.cluster is not None]
    rand = [p for p in picks if p.reason == "random pick"]
    assert len(reps) == 16 and len(rand) == 8
    assert {p.cluster for p in reps} == set(range(1, 9))


def test_representatives_are_closest_to_their_centroid_first():
    pool = clustered_pool(3, 9)
    picks = sampling.pick_diversity(pool, 6, 7, random_share=0)   # 3 clusters, 2 rounds
    first_round = picks[:3]
    for p in first_round:
        same = [q for q in picks if q.cluster == p.cluster]
        assert same[0] is p and all(p.score <= q.score for q in same)


def test_kmeans_with_seed_7_is_stable():
    x = sampling.standardize(sampling.feature_matrix(clustered_pool(4, 6)))
    a = sampling.kmeans(x, 4, seed=7)
    b = sampling.kmeans(x, 4, seed=7)
    assert a == b
    assert len(set(a[0])) == 4
    assert sampling.kmeans([], 3) == ([], [])
    assert len(sampling.kmeans(x[:2], 5)[1]) == 2                      # k is capped at n


def test_standardize_uses_population_deviation_and_keeps_flat_columns_at_zero():
    z = sampling.standardize([[1.0, 5.0], [3.0, 5.0]])
    assert z == [[-1.0, 0.0], [1.0, 0.0]]


def test_feature_matrix_fills_nulls_with_the_median_before_log1p():
    pool = [cand(1, tokens=None), cand(2, tokens=99), cand(3, tokens=999)]
    col = [row[sampling.FEATURES.index("tokens")] for row in sampling.feature_matrix(pool)]
    assert col[1] == pytest.approx(math.log1p(99), abs=1e-4)
    assert col[0] == pytest.approx(math.log1p(549), abs=1e-4)          # median of 99 and 999
    distinct = [row[sampling.FEATURES.index("distinct_tools")] for row in sampling.feature_matrix(pool)]
    assert distinct == [1.0, 1.0, 1.0]                                  # not log-transformed


def test_dimension_quotas_go_to_the_largest_values_and_shortfalls_move_on():
    pool = ([cand(i, route="compliance") for i in range(20)] + [cand(100 + i, route="wo_engine") for i in range(10)]
            + [cand(200 + i, route="energy") for i in range(2)])
    picks = sampling.pick_dimension(pool, 10, 7, dimension="route_target")
    by = {}
    for p in picks:
        by[p.dimension_value] = by.get(p.dimension_value, 0) + 1
    # quotas 4/3/3 by size; energy has only 2, so its shortfall of 1 wraps round to compliance
    assert by == {"compliance": 5, "wo_engine": 3, "energy": 2}
    assert picks[0].reason == "dimension route_target = compliance (quota 5 of 10)"
    assert sampling.dimension_quotas([20, 10, 2], 9) == [4, 3, 2]
    assert sampling.dimension_quotas([1, 1], 5) == [1, 1]
    assert sampling.dimension_quotas([5, 5, 5, 5], 2) == [1, 1, 0, 0]
    picks2 = sampling.pick_dimension(pool[20:] + pool[:20], 9, 7, dimension="route_target")
    assert len(picks2) == 9


def test_dimension_keeps_the_top_ten_values_and_folds_the_rest_and_none():
    pool = [cand(i, page=f"p{i:02d}" if i < 12 else None) for i in range(14)]
    names = [v for v, _ in sampling.dimension_groups(pool, "page")]
    assert "(other)" in names and "(none)" in names and len(names) == 11


@pytest.mark.parametrize("dim", ["feedback_rating", "ok", "verdict"])
def test_outcome_fields_are_refused_as_dimensions(dim):
    with pytest.raises(db.Invalid) as e:
        sampling.pick_dimension([cand(1)], 5, 7, dimension=dim)
    assert e.value.reason == "dimension_not_allowed"


def test_outliers_come_from_both_tails_furthest_past_the_fence_first():
    pool = [cand(i, tokens=1000 + i) for i in range(20)] + [cand(50, tokens=40000), cand(51, tokens=9000),
                                                           cand(52, tokens=-20000)]
    picks = sampling.pick_outliers(pool, 5)
    assert [p.turn_id for p in picks] == [cand(50).turn_id, cand(52).turn_id, cand(51).turn_id]
    assert picks[0].reason.startswith("IQR outlier on tokens: 40,000 (fence ")


def test_enrich_ranks_by_similarity_to_wrong_answers_and_keeps_its_random_share():
    seeds = [cand(900, text="How many boilers failed their gas safety check\nThree.")]
    pool = ([cand(i, text=f"How many boilers failed their gas safety check at site {i}\nAnswer {i}",
                  question=f"How many boilers failed their gas safety check at site {i}") for i in range(10)]
            + [cand(100 + i, text=f"Draft an email to vendor {i}\nDone", question=f"Draft an email to vendor {i}")
               for i in range(10)])
    picks = sampling.pick_enrich(pool, 10, 7, seeds=seeds)
    top = [p for p in picks if p.reason.startswith("similar to a wrong answer (")]
    rand = [p for p in picks if p.reason == "random pick"]
    assert len(top) == 8 and len(rand) == 2
    assert all(int(p.turn_id[5:], 16) < 10 for p in top)


def test_enrich_skips_a_question_it_already_picked():
    seeds = [cand(900, text="open work orders at B-301\n")]
    pool = [cand(i, text="open work orders at B-301\nx", question="Open work orders at  B-301") for i in range(5)]
    top = [p for p in sampling.pick_enrich(pool, 5, 7, seeds=seeds, random_share=0)]
    assert len(top) == 1


def test_enrich_without_seeds_falls_back_to_diversity_with_its_share():
    pool = clustered_pool(4, 8)
    assert sampling.pick_enrich(pool, 10, 7, seeds=[]) == sampling.pick_diversity(pool, 10, 7, random_share=0.2)


def test_draw_is_the_same_for_the_same_request():
    pool = clustered_pool(5, 8)
    now = datetime.now(timezone.utc)
    req = {"plan_step": "cluster_representatives", "strategy": "diversity", "k": 15, "seed": 7,
           "since": now - timedelta(days=7), "until": now}
    assert sampling.draw(pool, req) == sampling.draw(list(reversed(pool)), req)
    assert sampling.random_share_for(req) == 0.0
    assert sampling.random_share_for({**req, "plan_step": "extra"}) == pytest.approx(1 / 3)
```

`tests/test_trace_lab_sampling_window_stays_inside_span_retention.py`:

```python
"""A sampling window must lie inside span retention, so every sampled answer still has its steps; the
batch kinds that need failure modes wait for M4 (spec "Open coding ..." 3.1, 10.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import db, sampling

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def req(**kw):
    base = {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15, "seed": 7, "channel": "chat",
            "since": NOW - timedelta(days=7), "until": NOW, "source": "bow"}
    base.update(kw)
    return base


def test_a_window_older_than_retention_is_refused(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    sampling.check_request(req(since=NOW - timedelta(days=89)), now=NOW)
    with pytest.raises(db.Invalid) as e:
        sampling.check_request(req(since=NOW - timedelta(days=91)), now=NOW)
    assert e.value.reason == "window_outside_retention" and "90 days" in e.value.error


def test_the_retention_setting_is_respected(monkeypatch):
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "30")
    with pytest.raises(db.Invalid) as e:
        sampling.check_request(req(since=NOW - timedelta(days=31)), now=NOW)
    assert e.value.reason == "window_outside_retention"


def test_a_window_must_end_after_it_starts_and_carry_a_time_zone():
    with pytest.raises(db.Invalid) as e:
        sampling.check_request(req(until=NOW - timedelta(days=8)), now=NOW)
    assert e.value.reason == "bad_window"
    with pytest.raises(db.Invalid):
        sampling.check_request(req(since=datetime(2026, 10, 10), until=datetime(2026, 10, 11)), now=NOW)


@pytest.mark.parametrize("extra", [{"mode_id": "m1"}, {"source": "depth_scan"}, {"proposal_run_id": "r1"}])
def test_options_that_need_failure_modes_wait_for_them(extra):
    with pytest.raises(db.Invalid) as e:
        sampling.check_request(req(strategy="enrich", plan_step="depth_search", **extra), now=NOW)
    assert e.value.reason == "needs_failure_modes"


def test_golden_runs_are_never_a_sampling_channel():
    with pytest.raises(db.Invalid) as e:
        sampling.check_request(req(channel="golden"), now=NOW)
    assert e.value.reason == "bad_channel"


def test_a_monitor_flagged_request_names_between_1_and_200_answers(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    flagged = {"strategy": "monitor_flagged", "seed": 7, "since": NOW - timedelta(days=7), "until": NOW,
               "params": {"monitor_batch_id": "batch-1"}, "items": [{"turn_id": "turn-1", "session_id": None, "reason": "flagged"}]}
    sampling.check_request(flagged, now=NOW)                                  # no plan_step or k needed: nothing is drawn
    too_many = [{"turn_id": f"turn-{i}"} for i in range(sampling.RECORDED_MAX + 1)]
    for items in (None, [], too_many, [{"turn_id": ""}], [{"reason": "no id"}], ["turn-1"]):
        with pytest.raises(db.Invalid) as e:
            sampling.check_request({**flagged, "items": items}, now=NOW)
        assert e.value.reason == "bad_items"
    with pytest.raises(db.Invalid) as e:
        sampling.check_request({**flagged, "since": NOW - timedelta(days=91)}, now=NOW)
    assert e.value.reason == "window_outside_retention"
    assert sampling.RECORDED_STRATEGIES == ("monitor_flagged",) and "monitor_flagged" not in sampling.STRATEGIES
```

`tests/test_trace_lab_outcome_fields_are_not_sampling_dimensions.py`:

```python
"""A batch may be split by channel, routing, the asker's role or page - never by an outcome (a rating,
ok, a verdict, a label, a judge result), because a split chosen by outcome would bias what people read
(spec "Open coding ..." 3.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import db, sampling

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("dim", ["feedback_rating", "ok", "verdict", "label", "failure_present", None])
def test_outcome_fields_are_refused(dim):
    with pytest.raises(db.Invalid) as e:
        sampling.check_request({"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": dim,
                                "since": NOW - timedelta(days=7), "until": NOW}, now=NOW)
    assert e.value.reason == "dimension_not_allowed"


@pytest.mark.parametrize("dim", sampling.DIMENSIONS)
def test_the_four_structural_dimensions_are_allowed(dim):
    sampling.check_request({"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": dim,
                            "since": NOW - timedelta(days=7), "until": NOW}, now=NOW)
    assert sampling.DIMENSIONS == ("channel", "route_target", "asker_role", "page")
```

- [ ] **Step 2: Run them and see them fail**

```sh
tl_da_test tests/test_trace_lab_sampling_is_deterministic_and_recorded.py tests/test_trace_lab_sampling_window_stays_inside_span_retention.py tests/test_trace_lab_outcome_fields_are_not_sampling_dimensions.py
```

Expected: `ImportError: cannot import name 'sampling' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

Create `src/trace_lab/sampling.py` with:

```python
"""Sampling for open coding, the Map, and the Progress metrics (Trace Lab M2).

A batch draws answers (turns) from a company's pool with one strategy, records every pick with a
one-line reason, its seed and its parameters, and never draws a turn that is already in a batch of
that company. Only uniform batches count toward rates (a CHECK constraint enforces it). The pickers,
the feature matrix, k-means and the PCA projection are pure functions; numpy is imported inside them,
so importing this module stays light (spec "Data model" 1.7, R11).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import statistics
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..agents import trace
from ..agents.trace import _iso
from . import annotations, db, normalize, provenance
from .stats import cohen_kappa, wilson_interval

STRATEGIES = ("uniform", "diversity", "dimension", "outlier", "enrich")   # drawable (the route's BatchBody)
#: Strategies whose items the caller names instead of drawing them (M6's flagged answers; create_batch).
RECORDED_STRATEGIES = ("monitor_flagged",)
RECORDED_MAX = 200
PLAN_STEPS = ("initial_uniform", "cluster_representatives", "dimension", "depth_search", "final_uniform", "extra")
HW4_PLAN = (("initial_uniform", "uniform", 15), ("cluster_representatives", "diversity", 15),
            ("dimension", "dimension", 30), ("depth_search", "enrich", 25), ("final_uniform", "uniform", 15))
PLAN_TARGET = 100
DIMENSIONS = ("channel", "route_target", "asker_role", "page")
FEATURES = ("turn_position", "llm_calls", "tool_calls", "distinct_tools", "db_queries", "tokens",
            "latency_ms", "answer_chars")
LOG1P_FEATURES = frozenset(FEATURES) - {"distinct_tools"}
FEATURE_LABELS = {"turn_position": "Position in the conversation", "llm_calls": "Model calls",
                  "tool_calls": "Tool calls", "distinct_tools": "Different tools", "db_queries": "Database reads",
                  "tokens": "Tokens", "latency_ms": "Time to answer", "answer_chars": "Answer length"}
FEATURE_VERSION = "f1"
DEFAULT_SEED = 7
KMEANS_ITERS = 25
POOL_MAX = 20000
ENRICH_POOL_MAX = 5000
SEEDS_MAX = 500
MAP_MAX = 3000
MAP_CLUSTERS = 8
DIMENSION_TOP = 10
MISSING_LISTED = 25
TIMEOUT = "SET LOCAL statement_timeout = '10s'"
_TOKEN = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class Candidate:
    turn_id: str
    session_id: str | None
    started_at: datetime
    features: dict[str, float | None]
    dims: dict[str, str | None]
    text: str | None = None            # question + "\n" + answer; loaded only for enrich
    question: str | None = None        # redacted, for enrich de-duplication and the Map's hover
    organization_id: str | None = None
    batch_id: str | None = None        # the batch that drew it, when the pool includes sampled turns

    @property
    def conversation_key(self) -> str:
        return normalize.group_key(self.session_id, self.turn_id)


@dataclass(frozen=True)
class Pick:
    turn_id: str
    reason: str
    cluster: int | None = None
    dimension_value: str | None = None
    score: float | None = None


# ── features, standardizing, k-means and PCA (pure) ────────────────────────────────────

def route_target(route: str | None) -> str:
    """'single:compliance' -> 'compliance'; 'multi:a,b' -> 'multi'; NULL -> 'unrouted'."""
    if not route:
        return "unrouted"
    mode, _, targets = route.partition(":")
    if mode == "multi":
        return "multi"
    return targets or mode


def feature_matrix(pool: Sequence[Candidate]) -> list[list[float]]:
    """Rows in pool order, columns in FEATURES order: NULL -> column median, then log1p where listed."""
    if not pool:
        return []
    cols: list[list[float]] = []
    for name in FEATURES:
        raw = [c.features.get(name) for c in pool]
        present = [float(v) for v in raw if v is not None]
        median = statistics.median(present) if present else 0.0
        filled = [float(v) if v is not None else median for v in raw]
        if name in LOG1P_FEATURES:
            filled = [math.log1p(max(0.0, v)) for v in filled]
        cols.append(filled)
    return [list(row) for row in zip(*cols)]


def raw_matrix(pool: Sequence[Candidate]) -> list[list[float]]:
    """As feature_matrix, without the log1p: the values a person reads."""
    cols = []
    for name in FEATURES:
        raw = [c.features.get(name) for c in pool]
        present = [float(v) for v in raw if v is not None]
        median = statistics.median(present) if present else 0.0
        cols.append([float(v) if v is not None else median for v in raw])
    return [list(row) for row in zip(*cols)] if pool else []


def standardize(rows: list[list[float]]) -> list[list[float]]:
    """z-score every column with the population deviation; a zero deviation becomes 1 (repo rule)."""
    if not rows:
        return []
    import numpy as np

    x = np.asarray(rows, dtype=float)
    sd = x.std(axis=0)
    sd[sd == 0] = 1.0
    return ((x - x.mean(axis=0)) / sd).tolist()


def kmeans(vectors: list[list[float]], k: int, *, seed: int = DEFAULT_SEED,
           iters: int = KMEANS_ITERS) -> tuple[list[int], list[list[float]]]:
    """Lloyd's k-means. Initial centroids: random.Random(seed).sample(range(n), k); an empty cluster keeps
    its centroid; ties go to the lowest cluster index. Labels are 0-based."""
    n = len(vectors)
    if n == 0 or k < 1:
        return [], []
    import numpy as np

    k = min(k, n)
    x = np.asarray(vectors, dtype=float)
    centroids = x[random.Random(seed).sample(range(n), k)].copy()
    labels = None
    sq = (x * x).sum(axis=1)[:, None]
    for _ in range(iters):
        d = sq - 2.0 * (x @ centroids.T) + (centroids * centroids).sum(axis=1)[None, :]
        new = d.argmin(axis=1)
        if labels is not None and np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                centroids[j] = members.mean(axis=0)
    return [int(v) for v in labels], centroids.tolist()


def pca2(vectors: list[list[float]]) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Project to the first two principal components by SVD. Each component is flipped so its
    largest-magnitude loading is positive, so the plot is the same on every run."""
    n = len(vectors)
    if n == 0:
        return [], (0.0, 0.0)
    import numpy as np

    x = np.asarray(vectors, dtype=float)
    x = x - x.mean(axis=0)
    _, s, vt = np.linalg.svd(x, full_matrices=False)
    total = float((s ** 2).sum())
    coords = []
    shares = []
    for i in range(2):
        if i < len(s) and total > 0 and s[i] > 1e-12:
            v = vt[i].copy()
            if v[int(np.argmax(np.abs(v)))] < 0:
                v = -v
            coords.append(x @ v)
            shares.append(round(float(s[i] ** 2 / total), 4))
        else:
            coords.append(np.zeros(n))
            shares.append(0.0)
    return ([(round(float(a), 4), round(float(b), 4)) for a, b in zip(coords[0], coords[1])],
            (shares[0], shares[1]))


# ── pickers (pure) ─────────────────────────────────────────────────────────────────────

def _by_id(pool: Sequence[Candidate]) -> list[Candidate]:
    return sorted(pool, key=lambda c: c.turn_id)


def _fill(order: Sequence[Candidate], n: int, used: set[str], picks: list[Pick], reason: str) -> None:
    """Append up to n picks from `order`, one per conversation."""
    want = len(picks) + n
    for c in order:
        if len(picks) >= want:
            break
        if c.conversation_key in used or any(p.turn_id == c.turn_id for p in picks):
            continue
        used.add(c.conversation_key)
        picks.append(Pick(c.turn_id, reason))


def pick_uniform(pool: list[Candidate], k: int, seed: int) -> list[Pick]:
    """The repo's random.Random(seed).sample over the pool sorted by turn_id, one per conversation."""
    ordered = _by_id(pool)
    order = random.Random(seed).sample(ordered, len(ordered))
    picks: list[Pick] = []
    _fill(order, k, set(), picks, "uniform pick")
    return picks


def pick_diversity(pool: list[Candidate], k: int, seed: int, *, random_share: float = 1 / 3) -> list[Pick]:
    """Cluster representatives (closest to the centroid first, round-robin across clusters), then random picks."""
    if not pool or k < 1:
        return []
    import numpy as np

    n_rand = math.floor(k * random_share)
    n_rep = max(1, k - n_rand)
    ordered = _by_id(pool)
    n_clusters = min(max(1, n_rep // 2), len(ordered))
    x = standardize(feature_matrix(ordered))
    labels, centroids = kmeans(x, n_clusters, seed=seed)
    xa, ca = np.asarray(x), np.asarray(centroids)
    dist = np.sqrt(((xa - ca[labels]) ** 2).sum(axis=1))
    queues = {j: sorted((i for i in range(len(ordered)) if labels[i] == j), key=lambda i: (dist[i], ordered[i].turn_id))
              for j in range(len(centroids))}
    picks: list[Pick] = []
    used: set[str] = set()
    moved = True
    while len(picks) < n_rep and moved:
        moved = False
        for j in range(len(centroids)):
            if len(picks) >= n_rep:
                break
            queue = queues[j]
            while queue:
                i = queue.pop(0)
                c = ordered[i]
                if c.conversation_key in used:
                    continue
                used.add(c.conversation_key)
                picks.append(Pick(c.turn_id, f"cluster {j + 1} representative (distance {dist[i]:.2f})",
                                  cluster=j + 1, score=round(float(dist[i]), 4)))
                moved = True
                break
    taken = {p.turn_id for p in picks}
    rest = [c for c in ordered if c.turn_id not in taken]
    _fill(random.Random(seed).sample(rest, len(rest)), n_rand, used, picks, "random pick")
    return picks


def dimension_value(c: Candidate, dimension: str) -> str:
    v = c.dims.get(dimension)
    return "(none)" if v is None or str(v).strip() == "" else str(v)


def dimension_groups(pool: Sequence[Candidate], dimension: str) -> list[tuple[str, list[Candidate]]]:
    """Values by pool count (largest first, then by name); the top 10 kept, the rest as '(other)'."""
    if dimension not in DIMENSIONS:
        raise db.Invalid("dimension_not_allowed")
    groups: dict[str, list[Candidate]] = {}
    for c in pool:
        groups.setdefault(dimension_value(c, dimension), []).append(c)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    kept = ranked[:DIMENSION_TOP]
    other = [c for _, cs in ranked[DIMENSION_TOP:] for c in cs]
    if other:
        kept.append(("(other)", other))
    return sorted(kept, key=lambda kv: (-len(kv[1]), kv[0]))


def dimension_quotas(sizes: Sequence[int], k: int) -> list[int]:
    """k // m per value, the remainder to the largest values; a value with too few turns hands its
    shortfall on to the next value that has room (wrapping round to the first)."""
    m = len(sizes)
    if m == 0 or k < 1:
        return [0] * m
    quotas = [k // m + (1 if i < k % m else 0) for i in range(m)]
    short = 0
    for i, size in enumerate(sizes):
        if quotas[i] > size:
            short += quotas[i] - size
            quotas[i] = size
    i = 0
    while short and any(q < s for q, s in zip(quotas, sizes)):
        if quotas[i % m] < sizes[i % m]:
            quotas[i % m] += 1
            short -= 1
        i += 1
    return quotas


def pick_dimension(pool: list[Candidate], k: int, seed: int, *, dimension: str) -> list[Pick]:
    """A quota per value (dimension_quotas), drawn uniformly within the value, one turn per conversation."""
    groups = dimension_groups(pool, dimension)
    if not groups or k < 1:
        return []
    quotas = dimension_quotas([len(members) for _, members in groups], k)
    rng = random.Random(seed)
    picks: list[Pick] = []
    used: set[str] = set()
    carry = 0
    for (value, members), quota in zip(groups, quotas):
        want = quota + carry
        got = 0
        ordered = _by_id(members)
        for c in rng.sample(ordered, len(ordered)):
            if got >= want:
                break
            if c.conversation_key in used:
                continue
            used.add(c.conversation_key)
            picks.append(Pick(c.turn_id, f"dimension {dimension} = {value} (quota {quota} of {k})",
                              dimension_value=value))
            got += 1
        carry = want - got
    return picks


def pick_outliers(pool: list[Candidate], k: int) -> list[Pick]:
    """1.5 x IQR fences on tokens (q1 = s[n//4], q3 = s[3n//4]); both tails, furthest past its fence first."""
    if not pool or k < 1:
        return []
    tokens = sorted(float(c.features.get("tokens") or 0) for c in pool)
    n = len(tokens)
    q1, q3 = tokens[n // 4], tokens[(3 * n) // 4]
    low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    flagged = []
    for c in pool:
        t = float(c.features.get("tokens") or 0)
        if t > high:
            flagged.append((t - high, c, t, high))
        elif t < low:
            flagged.append((low - t, c, t, low))
    flagged.sort(key=lambda r: (-r[0], r[1].turn_id))
    picks: list[Pick] = []
    used: set[str] = set()
    for distance, c, t, fence in flagged:
        if len(picks) >= k:
            break
        if c.conversation_key in used:
            continue
        used.add(c.conversation_key)
        picks.append(Pick(c.turn_id, f"IQR outlier on tokens: {t:,.0f} (fence {fence:,.0f})",
                          score=round(distance, 4)))
    return picks


def _bow(text_: str | None) -> Counter:
    return Counter(_TOKEN.findall((text_ or "").lower()))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(t, 0) for t, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _norm_question(c: Candidate) -> str:
    q = c.question if c.question is not None else (c.text or "").split("\n", 1)[0]
    return " ".join(q.lower().split())


def pick_enrich(pool: list[Candidate], k: int, seed: int, *, seeds: list[Candidate],
                random_share: float = 0.2) -> list[Pick]:
    """Bag-of-words cosine to the seeds (best match), top first, no repeated question; then random picks.
    With no seeds it falls back to diversity with the same random share (repo behaviour)."""
    if not seeds:
        return pick_diversity(pool, k, seed, random_share=random_share)
    seed_bows = [_bow(s.text) for s in seeds]
    seed_ids = {s.turn_id for s in seeds}
    scored = []
    for c in _by_id(pool):
        if c.turn_id in seed_ids:
            continue
        bow = _bow(c.text)
        scored.append((max(_cosine(bow, sb) for sb in seed_bows), c))
    scored.sort(key=lambda r: (-r[0], r[1].turn_id))
    n_rand = math.floor(k * random_share)
    n_top = k - n_rand
    picks: list[Pick] = []
    used: set[str] = set()
    seen: set[str] = set()
    for score, c in scored:
        if len(picks) >= n_top:
            break
        q = _norm_question(c)
        if c.conversation_key in used or (q and q in seen):
            continue
        used.add(c.conversation_key)
        seen.add(q)
        picks.append(Pick(c.turn_id, f"similar to a wrong answer ({score:.2f})", score=round(score, 4)))
    taken = {p.turn_id for p in picks}
    rest = [c for c in _by_id(pool) if c.turn_id not in taken and c.turn_id not in seed_ids]
    _fill(random.Random(seed).sample(rest, len(rest)), n_rand, used, picks, "random pick")
    return picks


# ── requests (pure) ────────────────────────────────────────────────────────────────────

def retention_days() -> int:
    return int(os.environ.get("TRACE_RETENTION_DAYS") or trace.DEFAULT_RETENTION_DAYS)


def check_window(since: datetime, until: datetime, *, now: datetime | None = None) -> None:
    """since must lie inside span retention (every sampled turn still has its spans); until > since."""
    now = now or datetime.now(timezone.utc)
    if since.tzinfo is None or until.tzinfo is None:
        raise db.Invalid("bad_window", "Send the period with a time zone.")
    if until <= since:
        raise db.Invalid("bad_window")
    if since < now - timedelta(days=retention_days()):
        raise db.Invalid("window_outside_retention",
                         f"Pick a period inside the last {retention_days()} days; older steps are no longer kept.")


def _check_recorded(req: Mapping[str, Any], *, now: datetime | None = None) -> None:
    """A recorded batch (monitor_flagged) names 1-200 answers, each with a turn id, inside span retention."""
    items = req.get("items")
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= RECORDED_MAX or not all(
            isinstance(it, Mapping) and isinstance(it.get("turn_id"), str) and it["turn_id"].strip() for it in items):
        raise db.Invalid("bad_items", f"Name between 1 and {RECORDED_MAX} answers, each by its id.")
    check_window(req["since"], req["until"], now=now)


def check_request(req: Mapping[str, Any], *, now: datetime | None = None) -> None:
    """Refuse what M2 cannot draw: outcome dimensions, an outside window, and the M4-only options.
    A recorded strategy (monitor_flagged) is checked by _check_recorded: it draws nothing."""
    if req.get("strategy") in RECORDED_STRATEGIES:
        _check_recorded(req, now=now)
        return
    if req["strategy"] not in STRATEGIES or req["plan_step"] not in PLAN_STEPS:
        raise db.Invalid("bad_strategy", "Unknown batch kind.")
    dim = req.get("dimension")
    if req["strategy"] == "dimension" and dim not in DIMENSIONS:
        raise db.Invalid("dimension_not_allowed")
    if dim is not None and dim not in DIMENSIONS:
        raise db.Invalid("dimension_not_allowed")
    if req.get("mode_id") or req.get("source", "bow") != "bow" or req.get("proposal_run_id"):
        raise db.Invalid("needs_failure_modes")
    channel = req.get("channel") or "chat"
    if channel not in provenance.CHANNELS or channel == "golden":
        raise db.Invalid("bad_channel", "Pick a channel other than golden runs.")
    check_window(req["since"], req["until"], now=now)


def random_share_for(req: Mapping[str, Any]) -> float:
    share = req.get("random_share")
    if share is not None:
        return float(share)
    if req["strategy"] == "enrich":
        return 0.2
    return 0.0 if req["plan_step"] == "cluster_representatives" else 1 / 3


def draw(pool: list[Candidate], req: Mapping[str, Any], *, seeds: Sequence[Candidate] = ()) -> list[Pick]:
    s, k, seed = req["strategy"], int(req["k"]), int(req.get("seed", DEFAULT_SEED))
    if s == "uniform":
        return pick_uniform(pool, k, seed)
    if s == "diversity":
        return pick_diversity(pool, k, seed, random_share=random_share_for(req))
    if s == "dimension":
        return pick_dimension(pool, k, seed, dimension=req["dimension"])
    if s == "outlier":
        return pick_outliers(pool, k)
    return pick_enrich(pool, k, seed, seeds=list(seeds), random_share=random_share_for(req))
```

- [ ] **Step 4: Run them and see them pass**

```sh
tl_da_test tests/test_trace_lab_sampling_is_deterministic_and_recorded.py tests/test_trace_lab_sampling_window_stays_inside_span_retention.py tests/test_trace_lab_outcome_fields_are_not_sampling_dimensions.py
```

Expected: all pass (if `import numpy` fails inside the container, see the numpy note in Global Constraints).

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/sampling.py`, the three test files.

### Task 9: Progress metrics and the Map payload (pure)

**Files:**
- Modify: `src/trace_lab/sampling.py` — append
- Test: `tests/test_trace_lab_map_and_progress_metrics.py`

**Interfaces:**
- Consumes: Task 8 (`standardize`, `feature_matrix`, `raw_matrix`, `kmeans`, `pca2`, `dimension_groups`), `stats.wilson_interval`, `stats.cohen_kappa`.
- Produces: `resolve(verdicts) -> str | None`, `fair_sample(items) -> dict` (items `{strategy, withdrawn, verdicts}`), `plan_view(batches, distinct_sampled) -> dict`, `open_coding(heads, active_items) -> dict`, `coverage(pool, reviewed) -> dict`, `agreement(rows) -> dict | None`, `discovery(heads, block) -> dict`, `saturation(points, block, *, final_uniform=None) -> dict`, `map_payload(pool, *, reviewed, mine) -> dict` (`{ok, feature_names, feature_version, pool_size, too_few, explained_variance, nodes: [{turn_id, conversation_key, x, y, cluster (1-based), batch_id, reviewed, reviewed_by_me, channel, route_target, question, started_at}], clusters: [{id, size, reviewed, centroid}]}`).

Rules (spec §Review 3.5, 3.6, 4): only uniform, non-withdrawn batch items count toward the wrong rate, each resolving to its reviewers' common verdict (disagreement → disputed, left out), with a 95% Wilson interval; coverage marks a value under-covered when its pool share is ≥ 0.05 and its reviewed share is under half that; agreement uses version-1 blind verdicts and reports kappa from 20 shared answers; the discovery curve is per block of reviews with `new_modes: null` until M4; the saturation hint is "not available" until every point carries `new_modes`; the Map needs at least 3 answers.

- [ ] **Step 1: Write the failing test**

```python
"""The Map projection and the Progress metrics, as pure functions: PCA by SVD is deterministic, the fair
sample counts uniform batches only, coverage flags thin dimension values, agreement is measured on blind
first verdicts, and the discovery curve and saturation hint never claim what M2 cannot know
(spec "Open coding ..." 3.5, 3.6, 4; R11)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.trace_lab import sampling, stats
from src.trace_lab.sampling import Candidate

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def cand(i, *, tokens=1000.0, route="compliance", page="cc", role="admin", batch=None, question="q"):
    return Candidate(turn_id=f"turn-{i:016x}", session_id=f"s-{i}", started_at=T0 + timedelta(minutes=i),
                     features={"turn_position": 1, "llm_calls": 1 + i % 3, "tool_calls": i % 4, "distinct_tools": i % 2,
                               "db_queries": i % 5, "tokens": tokens + 37 * i, "latency_ms": 900 + 11 * i,
                               "answer_chars": 300 + 7 * i},
                     dims={"channel": "chat", "route_target": route, "asker_role": role, "page": page},
                     question=question, batch_id=batch)


def test_points_on_a_line_put_all_variance_on_the_first_axis():
    rows = [[float(i), 2.0 * i, 0, 0, 0, 0, 0, 0] for i in range(10)]
    pts, shares = sampling.pca2(rows)
    assert shares[0] == pytest.approx(1.0, abs=1e-4) and shares[1] == pytest.approx(0.0, abs=1e-4)
    assert pts[0][0] == pytest.approx(-10.0623, abs=1e-4)


def test_projection_signs_do_not_depend_on_row_order():
    rows = sampling.standardize(sampling.feature_matrix([cand(i) for i in range(30)]))
    a, _ = sampling.pca2(rows)
    b, _ = sampling.pca2(list(reversed(rows)))
    assert a == list(reversed(b))


def test_the_map_needs_three_answers_and_numbers_clusters_from_one():
    few = sampling.map_payload([cand(1), cand(2)], reviewed=set(), mine=set())
    assert few["too_few"] is True and few["nodes"] == [] and few["explained_variance"] is None
    pool = [cand(i, batch="b1" if i < 3 else None) for i in range(20)]
    m = sampling.map_payload(pool, reviewed={pool[0].turn_id}, mine={pool[0].turn_id})
    assert m["too_few"] is False and len(m["nodes"]) == 20
    assert {n["cluster"] for n in m["nodes"]} == {c["id"] for c in m["clusters"]} and min(c["id"] for c in m["clusters"]) == 1
    assert len(m["clusters"]) == 8 and sum(c["size"] for c in m["clusters"]) == 20
    first = m["nodes"][0]
    assert first["reviewed"] and first["reviewed_by_me"] and first["batch_id"] == "b1"
    assert first["conversation_key"] == "s:s-0"
    assert [f["key"] for f in m["feature_names"]] == list(sampling.FEATURES)


def test_fair_sample_counts_uniform_batches_only_and_leaves_disputes_out():
    items = ([{"strategy": "uniform", "withdrawn": False, "verdicts": ["wrong"]}] * 3
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": ["right"]}] * 9
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": ["right", "wrong"]}]
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": []}]
             + [{"strategy": "uniform", "withdrawn": True, "verdicts": ["wrong"]}] * 5
             + [{"strategy": "diversity", "withdrawn": False, "verdicts": ["wrong"]}] * 20)
    f = sampling.fair_sample(items)
    assert (f["wrong"], f["right"], f["disputed"], f["unreviewed"], f["items"]) == (3, 9, 1, 1, 14)
    assert f["rate"] == 0.25 and (f["ci_low"], f["ci_high"]) == stats.wilson_interval(3, 12)
    assert f["label"] == "From fair (uniform) batches only"
    assert sampling.fair_sample([])["rate"] is None


def test_coverage_flags_a_value_reviewed_at_under_half_its_share():
    pool = [cand(i, route="compliance") for i in range(60)] + [cand(100 + i, route="wo_engine") for i in range(40)]
    reviewed = {c.turn_id for c in pool[:10]}                       # all ten reviews are compliance
    cov = sampling.coverage(pool, reviewed)
    rows = {r["value"]: r for r in cov["dimensions"]["route_target"]}
    assert rows["wo_engine"]["pool_share"] == 0.4 and rows["wo_engine"]["reviewed_share"] == 0.0
    assert rows["wo_engine"]["under_covered"] is True and rows["compliance"]["under_covered"] is False
    empty = sampling.coverage(pool, set())
    assert all(r["reviewed_share"] is None and not r["under_covered"] for r in empty["dimensions"]["route_target"])


def test_agreement_uses_the_pair_with_most_shared_answers_and_kappa_from_20():
    rows = []
    for i in range(20):
        rows.append({"turn_id": f"t{i}", "reviewer_id": "a", "verdict": "right" if i < 15 else "wrong"})
        rows.append({"turn_id": f"t{i}", "reviewer_id": "b", "verdict": "right" if i < 13 or i == 19 else "wrong"})
    rows.append({"turn_id": "t0", "reviewer_id": "c", "verdict": "wrong"})
    ag = sampling.agreement(rows)
    assert ag["reviewers"] == ["a", "b"] and ag["shared"] == 20
    assert ag["percent"] == 0.85 and ag["kappa"] == pytest.approx(0.625, abs=1e-4) and ag["note"] is None
    few = sampling.agreement(rows[:10])
    assert few["kappa"] is None and few["note"] == "fewer than 20 shared answers"
    assert sampling.agreement([{"turn_id": "t", "reviewer_id": "a", "verdict": "right"}]) is None


def _heads(n, wrong_every=4):
    return [{"turn_id": f"t{i:03d}", "reviewer_id": "a", "reviewer_email": "a@x", "verdict": "wrong" if i % wrong_every == 0 else "right",
             "first_at": T0 + timedelta(minutes=i), "batch_id": "b1" if i < 5 else None} for i in range(n)]


def test_discovery_curve_moves_with_every_block_of_reviews_without_inventing_modes():
    d = sampling.discovery(_heads(25), 10)
    assert [(p["block"], p["reviews_through"], p["wrong"], p["partial"]) for p in d["points"]] == [
        (1, 10, 3, False), (2, 20, 2, False), (3, 25, 2, True)]
    assert all(p["new_modes"] is None for p in d["points"]) and d["modes_available"] is False
    assert d["by_batch"] == [{"batch_id": "b1", "reviews": 5, "wrong": 2, "new_modes": None}]
    assert sampling.discovery(_heads(26), 10)["points"][-1]["reviews_through"] == 26


def test_saturation_is_unavailable_until_modes_exist_then_follows_the_rule():
    pts = sampling.discovery(_heads(30), 10)["points"]
    assert sampling.saturation(pts, 10) == {"available": False, "hint": False, "kind": None,
                                           "reviews_without_new": None, "final_new_modes": None}
    with_modes = [{**p, "new_modes": n} for p, n in zip(pts, [3, 0, 0])]
    s = sampling.saturation(with_modes, 10)
    assert s["hint"] is True and s["kind"] == "quiet" and s["reviews_without_new"] == 20
    assert sampling.saturation([{**p, "new_modes": n} for p, n in zip(pts, [3, 1, 0])], 10)["hint"] is False
    fin = sampling.saturation(with_modes, 10, final_uniform={"complete": True, "new_modes": 2})
    assert fin["kind"] == "final_batch" and fin["final_new_modes"] == 2


def test_plan_view_points_at_the_first_unfinished_step():
    batches = [{"id": "b1", "plan_step": "initial_uniform", "selected": 15, "reviewed": 15, "withdrawn_at": None},
               {"id": "b2", "plan_step": "cluster_representatives", "selected": 9, "reviewed": 2, "withdrawn_at": None},
               {"id": "b3", "plan_step": "cluster_representatives", "selected": 6, "reviewed": 0, "withdrawn_at": "x"}]
    plan = sampling.plan_view(batches, 24)
    assert plan["next_step"] == "cluster_representatives" and plan["target_total"] == 100
    step = plan["steps"][1]
    assert (step["target"], step["selected"], step["reviewed"], step["batch_ids"]) == (15, 9, 2, ["b2"])


def test_open_coding_lists_missing_verdicts_25_at_a_time():
    heads = _heads(3)
    items = [{"turn_id": f"t{i:03d}", "reviews_required": 1} for i in range(40)]
    oc = sampling.open_coding(heads, items)
    assert (oc["reviewed"], oc["sampled"], oc["missing"], oc["missing_more"]) == (3, 40, 37, 12)
    assert len(oc["missing_turns"]) == 25 and oc["wrong"] == 1 and oc["right"] == 2
    assert oc["reviewers"] == [{"email": "a@x", "reviewed": 3, "first_reviewed_at": T0.isoformat()}]
```

- [ ] **Step 2: Run it and see it fail**

```sh
tl_da_test tests/test_trace_lab_map_and_progress_metrics.py
```

Expected: the two PCA tests pass (`pca2` exists from Task 8); the rest fail with `AttributeError: module 'src.trace_lab.sampling' has no attribute 'map_payload'` (and `fair_sample`, `coverage`, `agreement`, `discovery`, `saturation`, `plan_view`, `open_coding`).

- [ ] **Step 3: Implement**

Append to `src/trace_lab/sampling.py`:

```python
# ── progress metrics (pure) ────────────────────────────────────────────────────────────

def resolve(verdicts: Sequence[str]) -> str | None:
    """An item's common verdict: 'right', 'wrong', 'disputed' when its reviewers disagree, None when unreviewed."""
    kinds = set(verdicts)
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else "disputed"


def fair_sample(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Wrong rate over the items of uniform, non-withdrawn batches only; each item resolves to its
    reviewers' common verdict and disputed items are left out. items: {strategy, withdrawn, verdicts}."""
    fair = [i for i in items if i["strategy"] == "uniform" and not i.get("withdrawn")]
    counts = Counter(resolve(i["verdicts"]) for i in fair)
    wrong, right = counts.get("wrong", 0), counts.get("right", 0)
    n = wrong + right
    low, high = wilson_interval(wrong, n)
    return {"label": "From fair (uniform) batches only", "items": len(fair), "wrong": wrong, "right": right,
            "disputed": counts.get("disputed", 0), "unreviewed": counts.get(None, 0),
            "rate": round(wrong / n, 4) if n else None, "ci_low": low, "ci_high": high}


def plan_view(batches: Sequence[Mapping[str, Any]], distinct_sampled: int) -> dict[str, Any]:
    """The HW4 plan: per step, its target, what was drawn and reviewed, and the next step."""
    live = [b for b in batches if not b.get("withdrawn_at")]
    steps = []
    for step, strategy, target in HW4_PLAN:
        mine = [b for b in live if b["plan_step"] == step]
        steps.append({"step": step, "strategy": strategy, "target": target,
                      "selected": sum(int(b["selected"]) for b in mine),
                      "reviewed": sum(int(b.get("reviewed") or 0) for b in mine),
                      "batch_ids": [str(b["id"]) for b in mine]})
    nxt = next((s["step"] for s in steps if s["selected"] < s["target"]), "extra")
    return {"steps": steps, "next_step": nxt, "distinct_sampled": int(distinct_sampled), "target_total": PLAN_TARGET}


def open_coding(heads: Sequence[Mapping[str, Any]], active_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reviewed, wrong, right, missing verdicts (first 25 listed) and per-reviewer counts.

    heads = live non-tombstone verdict heads {turn_id, reviewer_id, reviewer_email, verdict, first_at};
    active_items = items of non-withdrawn batches {turn_id, reviews_required}."""
    by_turn: dict[str, list[str]] = {}
    for h in heads:
        by_turn.setdefault(h["turn_id"], []).append(h["verdict"])
    resolved = Counter(resolve(v) for v in by_turn.values())
    reviewers: dict[str, dict[str, Any]] = {}
    for h in heads:
        r = reviewers.setdefault(str(h["reviewer_id"]), {"email": h.get("reviewer_email"), "reviewed": 0,
                                                         "first_reviewed_at": h["first_at"]})
        r["reviewed"] += 1
        if h["first_at"] < r["first_reviewed_at"]:
            r["first_reviewed_at"] = h["first_at"]
    distinct_reviewers = {}
    for h in heads:
        distinct_reviewers.setdefault(h["turn_id"], set()).add(str(h["reviewer_id"]))
    missing = [i["turn_id"] for i in active_items
               if len(distinct_reviewers.get(i["turn_id"], ())) < int(i["reviews_required"])]
    return {"reviewed": len(by_turn), "sampled": len(active_items), "wrong": resolved.get("wrong", 0),
            "right": resolved.get("right", 0), "disputed": resolved.get("disputed", 0),
            "missing": len(missing), "missing_turns": missing[:MISSING_LISTED],
            "missing_more": max(0, len(missing) - MISSING_LISTED),
            "reviewers": sorted(({"email": r["email"], "reviewed": r["reviewed"],
                                  "first_reviewed_at": _ts(r["first_reviewed_at"])} for r in reviewers.values()),
                                key=lambda r: (-r["reviewed"], r["email"] or ""))}


def _ts(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def coverage(pool: Sequence[Candidate], reviewed: set[str]) -> dict[str, Any]:
    """Per dimension value: pool and reviewed counts and shares; under-covered when the pool share is
    >= 0.05 and the reviewed share is below half of it."""
    total = len(pool)
    reviewed_total = sum(1 for c in pool if c.turn_id in reviewed)
    out: dict[str, Any] = {"pool": total, "reviewed": reviewed_total, "dimensions": {}}
    for dim in DIMENSIONS:
        rows = []
        for value, members in (dimension_groups(pool, dim) if pool else []):
            n_rev = sum(1 for c in members if c.turn_id in reviewed)
            pool_share = len(members) / total if total else None
            rev_share = n_rev / reviewed_total if reviewed_total else None
            rows.append({"value": value, "pool": len(members), "reviewed": n_rev,
                         "pool_share": round(pool_share, 4) if pool_share is not None else None,
                         "reviewed_share": round(rev_share, 4) if rev_share is not None else None,
                         "under_covered": bool(pool_share is not None and rev_share is not None
                                               and pool_share >= 0.05 and rev_share < pool_share / 2)})
        out["dimensions"][dim] = rows
    return out


def agreement(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Version-1 blind verdicts {turn_id, reviewer_id, verdict}: the reviewer pair sharing the most turns,
    their percent agreement, and Cohen's kappa once they share >= 20 turns. None without a shared turn."""
    by_turn: dict[str, dict[str, str]] = {}
    for r in rows:
        by_turn.setdefault(r["turn_id"], {})[str(r["reviewer_id"])] = r["verdict"]
    pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for tid in sorted(by_turn):
        votes = by_turn[tid]
        ids = sorted(votes)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.setdefault((ids[i], ids[j]), []).append((votes[ids[i]], votes[ids[j]]))
    if not pairs:
        return None
    (a, b), shared = sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
    agree = sum(1 for x, y in shared if x == y)
    enough = len(shared) >= 20
    return {"reviewers": [a, b], "shared": len(shared), "percent": round(agree / len(shared), 4),
            "kappa": cohen_kappa([x for x, _ in shared], [y for _, y in shared]) if enough else None,
            "note": None if enough else "fewer than 20 shared answers"}


def discovery(heads: Sequence[Mapping[str, Any]], block: int) -> dict[str, Any]:
    """Reviews in order of their first version, in blocks of `block`. M2 has no failure modes yet, so each
    point carries the wrong verdicts found in its block and new_modes = None (M4 fills new_modes)."""
    ordered = sorted(heads, key=lambda h: (h["first_at"], h["turn_id"], str(h["reviewer_id"])))
    points = []
    for start in range(0, len(ordered), block):
        chunk = ordered[start:start + block]
        points.append({"block": start // block + 1, "reviews_through": start + len(chunk),
                       "wrong": sum(1 for h in chunk if h["verdict"] == "wrong"),
                       "partial": len(chunk) < block, "new_modes": None, "cumulative_modes": None})
    by_batch: dict[str, dict[str, Any]] = {}
    for h in ordered:
        if h.get("batch_id"):
            b = by_batch.setdefault(str(h["batch_id"]), {"batch_id": str(h["batch_id"]), "reviews": 0, "wrong": 0,
                                                        "new_modes": None})
            b["reviews"] += 1
            b["wrong"] += 1 if h["verdict"] == "wrong" else 0
    return {"block": block, "reviews": len(ordered), "points": points, "by_batch": list(by_batch.values()),
            "modes_available": False}


def saturation(points: Sequence[Mapping[str, Any]], block: int, *,
               final_uniform: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The saturation hint (spec "Open coding" 4). Needs new_modes on every point (M4); until then it says
    it is not available, never 'saturated'."""
    off = {"available": False, "hint": False, "kind": None, "reviews_without_new": None, "final_new_modes": None}
    if not points or any(p.get("new_modes") is None for p in points):
        return off
    if final_uniform and final_uniform.get("complete") and int(final_uniform.get("new_modes") or 0) >= 2:
        return {"available": True, "hint": True, "kind": "final_batch", "reviews_without_new": None,
                "final_new_modes": int(final_uniform["new_modes"])}
    full = [p for p in points if not p.get("partial")]
    reviews = points[-1]["reviews_through"]
    if reviews >= 3 * block and len(full) >= 2 and full[-1]["new_modes"] == 0 and full[-2]["new_modes"] == 0:
        return {"available": True, "hint": True, "kind": "quiet", "reviews_without_new": 2 * block,
                "final_new_modes": None}
    return {**off, "available": True}


def map_payload(pool: Sequence[Candidate], *, reviewed: set[str], mine: set[str]) -> dict[str, Any]:
    """Map view data (spec "Open coding" 3.6, R11): PCA by SVD on the standardized features and k-means
    clusters (k = min(8, n), seed 7). Fewer than 3 answers -> too_few, no points."""
    base = {"ok": True, "feature_names": [{"key": f, "label": FEATURE_LABELS[f]} for f in FEATURES],
            "feature_version": FEATURE_VERSION, "pool_size": len(pool)}
    if len(pool) < 3:
        return {**base, "too_few": True, "explained_variance": None, "nodes": [], "clusters": []}
    x = standardize(feature_matrix(pool))
    points, shares = pca2(x)
    labels, _ = kmeans(x, min(MAP_CLUSTERS, len(pool)), seed=DEFAULT_SEED)
    raw = raw_matrix(pool)
    nodes = []
    for c, (px, py), lab in zip(pool, points, labels):
        nodes.append({"turn_id": c.turn_id, "conversation_key": c.conversation_key, "x": px, "y": py,
                      "cluster": lab + 1, "batch_id": c.batch_id, "reviewed": c.turn_id in reviewed,
                      "reviewed_by_me": c.turn_id in mine, "channel": c.dims.get("channel"),
                      "route_target": c.dims.get("route_target"), "question": (c.question or "")[:160],
                      "started_at": _ts(c.started_at)})
    clusters = []
    for j in sorted(set(labels)):
        idx = [i for i, lab in enumerate(labels) if lab == j]
        clusters.append({"id": j + 1, "size": len(idx), "reviewed": sum(1 for i in idx if pool[i].turn_id in reviewed),
                         "centroid": {f: round(sum(raw[i][fi] for i in idx) / len(idx), 2)
                                      for fi, f in enumerate(FEATURES)}})
    return {**base, "too_few": False, "explained_variance": list(shares), "nodes": nodes, "clusters": clusters}
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_map_and_progress_metrics.py
```

Expected: `10 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/sampling.py`, `tests/test_trace_lab_map_and_progress_metrics.py`.

### Task 10: Pool, batches, queue, manifest, map and progress against the database

**Files:**
- Modify: `src/trace_lab/sampling.py` — append
- Test: `tests/test_trace_lab_a_turn_is_never_in_two_batches_sql.py`

**Interfaces:**
- Consumes: Tasks 8–9; `annotations.company_clause`, `annotations.reviewed_turns`, `annotations._constraint` (Task 5/7); `db.engine()`, `db.scope_sql`, `db.Invalid`, `db.Conflict`, `db.NotFound`; `provenance.inferred_channel_sql('t')`; tables from Task 4; `plenum_cafm.chat_threads.deleted_at` (`src/services/chat_threads.py:57-71`).
- Produces: `async pool(principal, org, *, since, until, channel="chat", exclude_sampled=True, with_text=False, limit=POOL_MAX, include_view_as=False, include_deleted=False) -> tuple[list[Candidate], bool]`, `preview(principal, org, req) -> {ok, pool_size, excluded_sampled, truncated, picks}`, `create_batch(principal, org, req) -> {ok, batch, items}` (drawn; or, for `req["strategy"] == "monitor_flagged"`, recorded from `req["items"]` by the private `_record_flagged`), `list_batches(principal, org, *, include_withdrawn=False) -> list[dict]` (each batch row plus `items, reviewed, mine, missing, wrong, right, disputed`), `batch_detail(principal, org, batch_id) -> {batch, items} | None`, `withdraw(principal, org, batch_id, *, reason) -> dict`, `queue(principal, org, *, batch_id=None, limit=20) -> {ok, remaining, items}`, `plan_status(principal, org, *, since=None, until=None) -> dict`, `manifest(principal, org) -> dict`, `map_view(principal, org, *, since, until, channel="chat", limit=MAP_MAX) -> dict`, `progress(principal, org, *, since, until, block=10, filter_org=None) -> dict`.

Rules (spec §Review 3.1, 3.4, 3.5): the window's eligible turns are one company's, never NULL-company (R27), never golden, the requested effective channel (turn_meta, else M0's classifier), no superadmin view-as unless `include_view_as` (R20), no deleted chat unless `include_deleted` (R21); `turn_position` is computed over the whole window before sampled turns are removed; a batch and its items are written in one transaction; a unique violation on `ux_tl_items_turn` (another batch took a turn first) is 409 `pool_changed`; the queue lists items I have not judged that still need reviews, in `md5(turn_id || reviewer_id)` order; a withdrawn batch's unjudged items leave the queue and progress. The k-means and PCA run in `asyncio.to_thread` so the event loop never blocks. A `monitor_flagged` request (M6, the lead's decision) skips the draw: it keeps the named turns that are the company's (never golden) and not already in one of its batches, and writes the batch with plan step `extra`, the request's `params`, `seed` and window, `requested` = the number named, `pool_size` / `excluded_sampled` / `selected` from that filter, `counts_for_prevalence` false, one review each and no feature version; items keep the given order and `reason[:300]`.

- [ ] **Step 1: Write the failing test**

```python
"""Sampling on a scratch Postgres: the pool is one company's chat answers inside the window - no golden
run, no turn without a company, no deleted chat, no superadmin view-as unless asked; a uniform batch of
15 is drawn with its seed and every reason recorded; the next batch never redraws a sampled turn; a
withdrawn batch leaves the queue; the manifest is the record (spec "Open coding ..." 3.1-3.5; M2
acceptance)."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from trace_lab_m2_fixtures import NOW, add_meta, add_thread, add_turn, principal, truncate_chat, turn_id
from trace_lab_scratch import fresh_schema  # M0's scratch helper (tests/trace_lab_scratch.py)
from src.agents import trace
from src.services import chat_threads
from src.trace_lab import annotations, db, sampling

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
async def eng(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(chat_threads, "_ready", False)
    await chat_threads.ensure_tables()
    monkeypatch.setattr(trace, "_ready", True)
    for n in range(30):
        await add_turn(engine, n, ORG, minutes_ago=10 + n, tokens=1000 + 50 * n)
    for n in range(100, 105):
        await add_turn(engine, n, OTHER)
    await add_turn(engine, 200, ORG, session="golden-run-1-case-a-1")
    await add_turn(engine, 201, None)
    await add_thread(engine, "sess-deleted", ORG, deleted=True)
    await add_turn(engine, 202, ORG, session="sess-deleted")
    vas = await add_turn(engine, 203, ORG)
    await add_meta(engine, vas, ORG, asker_role="superadmin", view_as=True)
    yield engine
    await truncate_chat(engine)
    await engine.dispose()


def req(**kw):
    base = {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15, "seed": 7, "channel": "chat",
            "since": NOW - timedelta(days=7), "until": NOW + timedelta(hours=1), "reviews_required": 1, "source": "bow"}
    base.update(kw)
    return base


async def test_the_pool_is_one_companys_eligible_chat_answers(eng):
    me = principal(ORG)
    pool, truncated = await sampling.pool(me, ORG, since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1))
    ids = {c.turn_id for c in pool}
    assert len(ids) == 30 and not truncated
    for n in (100, 200, 201, 202, 203):
        assert turn_id(n) not in ids
    with_va, _ = await sampling.pool(me, ORG, since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1),
                                     include_view_as=True, include_deleted=True)
    assert {turn_id(202), turn_id(203)} <= {c.turn_id for c in with_va}


async def test_a_uniform_batch_of_15_records_its_seed_and_reasons_and_preview_writes_nothing(eng):
    me = principal(ORG)
    pv = await sampling.preview(me, ORG, req())
    assert len(pv["picks"]) == 15 and pv["pool_size"] == 30
    async with eng.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM plenum_tracelab.sample_batches"))).scalar_one() == 0
    out = await sampling.create_batch(me, ORG, req())
    b = out["batch"]
    assert (b["seed"], b["selected"], b["counts_for_prevalence"], b["strategy"]) == (7, 15, True, "uniform")
    assert [i["turn_id"] for i in out["items"]] == [p["turn_id"] for p in pv["picks"]]
    assert all(i["reason"] == "uniform pick" for i in out["items"])
    man = await sampling.manifest(me, ORG)
    mb = man["batches"][0]
    assert man["schema_version"] == 1 and mb["seed"] == 7 and len(mb["items"]) == 15 and mb["items"][0]["reason"] == "uniform pick"


async def test_the_next_batch_never_redraws_a_sampled_turn_and_an_empty_pool_is_refused(eng):
    me = principal(ORG)
    first = {i["turn_id"] for i in (await sampling.create_batch(me, ORG, req()))["items"]}
    second_out = await sampling.create_batch(me, ORG, req(plan_step="extra", seed=8))
    second = {i["turn_id"] for i in second_out["items"]}
    assert len(second) == 15 and not (first & second)
    assert second_out["batch"]["excluded_sampled"] == 15
    with pytest.raises(db.Invalid) as e:
        await sampling.create_batch(me, ORG, req(plan_step="extra", seed=9))
    assert e.value.reason == "pool_empty"


async def test_a_dimension_batch_does_not_count_toward_rates(eng):
    out = await sampling.create_batch(principal(ORG), ORG, req(plan_step="dimension", strategy="dimension", k=10,
                                                               dimension="route_target"))
    assert out["batch"]["counts_for_prevalence"] is False
    assert all(i["reason"].startswith("dimension route_target = ") for i in out["items"])


async def test_the_queue_follows_my_verdicts_and_a_withdrawn_batch_leaves_it(eng):
    me, other = principal(ORG), principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=5))
    q = await sampling.queue(me, ORG)
    assert q["remaining"] == 5 and len(q["items"]) == 5
    first = q["items"][0]["turn_id"]
    await annotations.save_verdict(me, ORG, first, verdict="right", note=None, anchor=None, base=None)
    assert first not in {i["turn_id"] for i in (await sampling.queue(me, ORG))["items"]}
    assert first not in {i["turn_id"] for i in (await sampling.queue(other, ORG))["items"]}   # 1 review needed, done
    plan = await sampling.plan_status(me, ORG)
    assert plan["steps"][0]["selected"] == 5 and plan["steps"][0]["reviewed"] == 1 and plan["distinct_sampled"] == 5
    wd = await sampling.withdraw(me, ORG, out["batch"]["id"], reason="Drawn from the wrong period.")
    assert wd["withdrawn_reason"] == "Drawn from the wrong period."
    with pytest.raises(db.Conflict) as e:
        await sampling.withdraw(me, ORG, out["batch"]["id"], reason="again")
    assert e.value.reason == "already_withdrawn"
    assert (await sampling.queue(me, ORG))["items"] == []
    assert (await sampling.plan_status(me, ORG))["distinct_sampled"] == 1   # only the judged item still counts


async def test_progress_counts_reviews_and_the_fair_sample(eng):
    me = principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=4))
    tids = [i["turn_id"] for i in out["items"]]
    await annotations.save_verdict(me, ORG, tids[0], verdict="right", note=None, anchor=None, base=None)
    from src.trace_lab.annotations import Anchor
    await annotations.save_verdict(me, ORG, tids[1], verdict="wrong", note="Counted July.", anchor=Anchor(None, "answer", "758"), base=None)
    p = await sampling.progress(me, ORG, since=None, until=None, block=5)
    assert p["open_coding"]["reviewed"] == 2 and p["open_coding"]["missing"] == 2
    assert (p["fair_sample"]["wrong"], p["fair_sample"]["right"], p["fair_sample"]["rate"]) == (1, 1, 0.5)
    assert p["discovery"]["points"][0]["reviews_through"] == 2 and p["saturation"]["available"] is False
    assert p["coverage"]["pool"] == 30 and p["open_batches"] == 1


async def test_a_monitor_flagged_batch_records_only_the_companys_unsampled_named_answers(eng):
    me = principal(ORG)
    taken = (await sampling.create_batch(me, ORG, req(k=1)))["items"][0]["turn_id"]
    free = [t for t in (turn_id(n) for n in range(30)) if t != taken][:2]
    named = free + [taken, turn_id(100), turn_id(200), turn_id(201), "turn-not-there"]   # sampled, other company,
    flagged = {"strategy": "monitor_flagged", "seed": 11, "channel": "chat",               # golden, no company, absent
               "since": NOW - timedelta(days=7), "until": NOW + timedelta(hours=1),
               "params": {"monitor_batch_id": "batch-1", "mode_id": "mode-1"},
               "items": [{"turn_id": t, "session_id": None, "reason": "flagged by the judge"} for t in named]}
    out = await sampling.create_batch(me, ORG, flagged)
    b = out["batch"]
    assert (b["strategy"], b["plan_step"], b["counts_for_prevalence"], b["reviews_required"], b["seed"]) == (
        "monitor_flagged", "extra", False, 1, 11)
    assert (b["requested"], b["pool_size"], b["excluded_sampled"], b["selected"]) == (7, 3, 1, 2)
    assert b["feature_version"] is None and b["params"] == {"monitor_batch_id": "batch-1", "mode_id": "mode-1"}
    assert [(i["turn_id"], i["position"], i["reason"]) for i in out["items"]] == [
        (free[0], 1, "flagged by the judge"), (free[1], 2, "flagged by the judge")]
    assert set(free) <= {i["turn_id"] for i in (await sampling.queue(me, ORG))["items"]}
    with pytest.raises(db.Invalid) as e:
        await sampling.create_batch(me, ORG, flagged)                                   # all three now sampled
    assert e.value.reason == "pool_empty"
```

- [ ] **Step 2: Run it and see it fail** (scratch Postgres up)

```sh
tl_da_test tests/test_trace_lab_a_turn_is_never_in_two_batches_sql.py
```

Expected: `AttributeError: module 'src.trace_lab.sampling' has no attribute 'pool'`.

- [ ] **Step 3: Implement**

Append to `src/trace_lab/sampling.py`:

```python
# ── database: pool, batches, queue, manifest, map, progress ────────────────────────────

def _channel_sql() -> str:
    """The turn's effective channel: turn_meta when present, else M0's read-time classifier."""
    return f"CASE WHEN m.turn_id IS NOT NULL THEN m.channel ELSE {provenance.inferred_channel_sql('t')} END"


def _window_cte(principal, org: str | None, *, since: datetime, until: datetime, channel: str,
                include_view_as: bool, include_deleted: bool, with_text: bool) -> tuple[str, dict[str, Any]]:
    """The window's eligible turns (spec 3.1): company scope, never a NULL company (R27), no golden-run
    turn, the requested channel, no superadmin view-as traffic unless asked (R20), no deleted chat unless
    asked (R21). turn_position is computed over the whole window before sampled turns are taken out."""
    where, p = db.scope_sql(principal, org, alias="t")
    text_cols = ", t.question, t.answer" if with_text else ", left(t.question, 300) AS question, NULL::text AS answer"
    ch = _channel_sql()
    sql = f"""
        WITH w AS (
          SELECT t.turn_id, t.session_id, t.organization_id::text AS organization_id, t.started_at,
                 t.llm_calls, t.tool_calls,
                 CASE WHEN jsonb_typeof(t.tools) = 'array' THEN jsonb_array_length(t.tools) ELSE 0 END AS distinct_tools,
                 coalesce(t.input_tokens, 0) + coalesce(t.output_tokens, 0) AS tokens,
                 t.latency_ms, length(coalesce(t.answer, '')) AS answer_chars, t.route,
                 {ch} AS channel, m.page, m.asker_role, si.batch_id::text AS batch_id,
                 row_number() OVER (PARTITION BY t.session_id ORDER BY t.started_at, t.turn_id) AS turn_position
                 {text_cols}
            FROM plenum_cafm.agent_trace_turns t
            LEFT JOIN plenum_tracelab.turn_meta m ON m.turn_id = t.turn_id
            LEFT JOIN plenum_cafm.chat_threads th ON th.id = t.session_id
            LEFT JOIN plenum_tracelab.sample_items si
                   ON si.organization_id = t.organization_id AND si.turn_id = t.turn_id
           WHERE {where} AND t.organization_id IS NOT NULL
             AND t.started_at >= :tl_since AND t.started_at < :tl_until
             AND m.golden_run_id IS NULL
             AND {ch} = :tl_channel
             AND (CAST(:tl_view_as AS boolean) OR NOT (coalesce(m.asker_role, '') = 'superadmin'
                  AND coalesce(m.asker_scope->>'view_as', 'false') = 'true'))
             AND (CAST(:tl_deleted AS boolean) OR th.deleted_at IS NULL)
        )"""
    return sql, {**p, "tl_since": since, "tl_until": until, "tl_channel": channel,
                 "tl_view_as": bool(include_view_as), "tl_deleted": bool(include_deleted)}


async def _db_counts(conn, turn_ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(turn_ids), 5000):
        chunk = turn_ids[i:i + 5000]
        for r in (await conn.execute(text(
                "SELECT turn_id, count(*) FILTER (WHERE kind = 'db') AS n FROM plenum_cafm.agent_trace_spans "
                "WHERE turn_id = ANY(CAST(:tl_ids AS text[])) GROUP BY turn_id"), {"tl_ids": chunk})).all():
            out[r[0]] = int(r[1])
    return out


def _candidate(r: Mapping[str, Any], db_counts: Mapping[str, int], with_text: bool) -> Candidate:
    sid = r["session_id"]
    single = not sid or not str(sid).strip() or normalize.session_kind(sid, thread_exists=False) == "synthetic"
    question = normalize.redact_text(r["question"] or "")
    return Candidate(
        turn_id=r["turn_id"], session_id=sid, started_at=r["started_at"],
        features={"turn_position": 1 if single else int(r["turn_position"]), "llm_calls": r["llm_calls"],
                  "tool_calls": r["tool_calls"], "distinct_tools": r["distinct_tools"],
                  "db_queries": db_counts.get(r["turn_id"], 0), "tokens": r["tokens"], "latency_ms": r["latency_ms"],
                  "answer_chars": r["answer_chars"]},
        dims={"channel": r["channel"], "route_target": route_target(r["route"]), "asker_role": r["asker_role"],
              "page": r["page"]},
        text=(question + "\n" + normalize.redact_text(r["answer"] or "")) if with_text else None,
        question=question, organization_id=r["organization_id"], batch_id=r["batch_id"])


async def _load_pool(conn, principal, org: str | None, *, since: datetime, until: datetime, channel: str,
                     exclude_sampled: bool, with_text: bool, limit: int, include_view_as: bool = False,
                     include_deleted: bool = False) -> tuple[list[Candidate], bool, int]:
    """(candidates newest first, truncated, sampled turns left out of the window)."""
    cte, p = _window_cte(principal, org, since=since, until=until, channel=channel, include_view_as=include_view_as,
                         include_deleted=include_deleted, with_text=with_text)
    rows = (await conn.execute(text(
        cte + f" SELECT * FROM w {'WHERE batch_id IS NULL' if exclude_sampled else ''} "
              "ORDER BY started_at DESC, turn_id DESC LIMIT :tl_lim"), {**p, "tl_lim": limit + 1})).mappings().all()
    excluded = 0
    if exclude_sampled:
        excluded = int((await conn.execute(text(cte + " SELECT count(*) FROM w WHERE batch_id IS NOT NULL"), p)).scalar_one())
    truncated = len(rows) > limit
    rows = rows[:limit]
    counts = await _db_counts(conn, [r["turn_id"] for r in rows])
    return [_candidate(r, counts, with_text) for r in rows], truncated, excluded


async def pool(principal, org: str, *, since: datetime, until: datetime, channel: str = "chat",
               exclude_sampled: bool = True, with_text: bool = False, limit: int = POOL_MAX,
               include_view_as: bool = False, include_deleted: bool = False) -> tuple[list[Candidate], bool]:
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        cands, truncated, _ = await _load_pool(conn, principal, org, since=since, until=until, channel=channel,
                                               exclude_sampled=exclude_sampled, with_text=with_text, limit=limit,
                                               include_view_as=include_view_as, include_deleted=include_deleted)
    return cands, truncated


async def _wrong_seeds(conn, org: str) -> list[Candidate]:
    """M2's enrich seeds: the company's turns with a live wrong verdict (M4 adds mode seeds)."""
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (a.turn_id) a.turn_id, t.session_id, t.started_at, t.question, t.answer "
        "FROM plenum_tracelab.annotations a JOIN plenum_cafm.agent_trace_turns t ON t.turn_id = a.turn_id "
        "WHERE a.organization_id = CAST(:tl_org AS uuid) AND a.kind = 'verdict' AND a.superseded_by IS NULL "
        "AND NOT a.tombstone AND a.verdict = 'wrong' ORDER BY a.turn_id LIMIT :tl_lim"),
        {"tl_org": org, "tl_lim": SEEDS_MAX})).mappings().all()
    return [Candidate(turn_id=r["turn_id"], session_id=r["session_id"], started_at=r["started_at"], features={},
                      dims={}, text=normalize.redact_text(r["question"] or "") + "\n" + normalize.redact_text(r["answer"] or ""),
                      question=normalize.redact_text(r["question"] or "")) for r in rows]


def _pick_out(p: Pick) -> dict[str, Any]:
    return {"turn_id": p.turn_id, "reason": p.reason, "cluster": p.cluster, "dimension_value": p.dimension_value,
            "score": p.score}


async def _drawn(conn, principal, org: str, req: Mapping[str, Any]) -> tuple[list[Candidate], list[Pick], bool, int]:
    limit = ENRICH_POOL_MAX if req["strategy"] == "enrich" else POOL_MAX
    cands, truncated, excluded = await _load_pool(
        conn, principal, org, since=req["since"], until=req["until"], channel=req.get("channel") or "chat",
        exclude_sampled=True, with_text=req["strategy"] == "enrich", limit=limit,
        include_view_as=bool(req.get("include_view_as")), include_deleted=bool(req.get("include_deleted")))
    seeds = await _wrong_seeds(conn, org) if req["strategy"] == "enrich" else []
    picks = await asyncio.to_thread(draw, cands, req, seeds=seeds)
    return cands, picks, truncated, excluded


async def preview(principal, org: str, req: dict) -> dict:
    """What a batch would draw. Writes nothing."""
    check_request(req)
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        cands, picks, truncated, excluded = await _drawn(conn, principal, org, req)
    return {"ok": True, "pool_size": len(cands), "excluded_sampled": excluded, "truncated": truncated,
            "picks": [_pick_out(p) for p in picks]}


_BATCH_INSERT = text("""
    INSERT INTO plenum_tracelab.sample_batches
        (id, organization_id, plan_step, strategy, params, seed, window_since, window_until, channel, requested,
         selected, pool_size, excluded_sampled, counts_for_prevalence, reviews_required, feature_version,
         created_by, created_by_email)
    VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :plan_step, :strategy, CAST(:params AS jsonb), :seed, :since,
            :until, :channel, :requested, :selected, :pool_size, :excluded, :cfp, :rr, :fv, CAST(:me AS uuid), :email)
    RETURNING *""")
_ITEM_INSERT = text("""
    INSERT INTO plenum_tracelab.sample_items
        (id, batch_id, organization_id, turn_id, session_id, position, reason, cluster, dimension_value, score, features)
    VALUES (CAST(:id AS uuid), CAST(:batch AS uuid), CAST(:org AS uuid), :tid, :sid, :pos, :reason, :cluster, :dim,
            :score, CAST(:features AS jsonb))""")


async def create_batch(principal, org: str, req: dict) -> dict:
    """Draw and record a batch and its items in one transaction. db.Invalid (incl. pool_empty) or
    db.Conflict(pool_changed) when another batch took one of these turns first. A monitor_flagged
    request (M6) records the answers it names instead of drawing (_record_flagged)."""
    check_request(req)
    if req["strategy"] in RECORDED_STRATEGIES:
        return await _record_flagged(principal, org, req)
    batch_id = str(uuid.uuid4())
    try:
        async with db.engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            cands, picks, truncated, excluded = await _drawn(conn, principal, org, req)
            if not picks:
                raise db.Invalid("pool_empty")
            by_id = {c.turn_id: c for c in cands}
            params = {"k": int(req["k"]), "dimension": req.get("dimension"), "source": req.get("source", "bow"),
                      "random_share": random_share_for(req) if req["strategy"] in ("diversity", "enrich") else None,
                      "include_view_as": bool(req.get("include_view_as")),
                      "include_deleted": bool(req.get("include_deleted")), "pool_truncated": truncated}
            batch = (await conn.execute(_BATCH_INSERT, {
                "id": batch_id, "org": org, "plan_step": req["plan_step"], "strategy": req["strategy"],
                "params": json.dumps(params), "seed": int(req.get("seed", DEFAULT_SEED)), "since": req["since"],
                "until": req["until"], "channel": req.get("channel") or "chat", "requested": int(req["k"]),
                "selected": len(picks), "pool_size": len(cands), "excluded": excluded,
                "cfp": req["strategy"] == "uniform", "rr": int(req.get("reviews_required") or 1),
                "fv": FEATURE_VERSION, "me": str(principal.user_id), "email": principal.email or None})).mappings().one()
            await conn.execute(_ITEM_INSERT, [{
                "id": str(uuid.uuid4()), "batch": batch_id, "org": org, "tid": p.turn_id,
                "sid": by_id[p.turn_id].session_id, "pos": i + 1, "reason": p.reason[:300], "cluster": p.cluster,
                "dim": p.dimension_value, "score": p.score, "features": json.dumps(by_id[p.turn_id].features)}
                for i, p in enumerate(picks)])
    except IntegrityError as exc:
        if "ux_tl_items_turn" in str(exc) or annotations._constraint(exc) == "ux_tl_items_turn":
            raise db.Conflict("pool_changed") from exc
        raise
    out = _batch_out(dict(batch))
    out["items"] = len(picks)
    return {"ok": True, "batch": out, "items": [
        {**_pick_out(p), "position": i + 1, "conversation_key": by_id[p.turn_id].conversation_key}
        for i, p in enumerate(picks)]}


def _batch_out(row: dict[str, Any]) -> dict[str, Any]:
    r = _iso(dict(row))
    for k in ("n_items", "n_reviewed", "n_mine", "n_missing", "n_wrong", "n_right", "n_disputed"):
        r[k[2:]] = int(r.pop(k, 0) or 0)
    if isinstance(r.get("params"), str):
        r["params"] = json.loads(r["params"])
    return r


async def _record_flagged(principal, org: str, req: Mapping[str, Any]) -> dict:
    """Record the answers a monitor batch flagged (M6) as one review batch, in one transaction; nothing
    is drawn. Keeps the named turns that are this company's (never another company's or a NULL
    company's, R27; never a golden run) and not already in one of its batches (excluded_sampled);
    none left -> db.Invalid(pool_empty); a concurrent batch took one -> db.Conflict(pool_changed)."""
    named: dict[str, Mapping[str, Any]] = {}
    for it in req["items"]:
        named.setdefault(it["turn_id"].strip(), it)
    batch_id = str(uuid.uuid4())
    try:
        async with db.engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            found = {r["turn_id"]: dict(r) for r in (await conn.execute(text(f"""
                SELECT t.turn_id, t.session_id, (si.id IS NOT NULL) AS sampled
                  FROM plenum_cafm.agent_trace_turns t
                  LEFT JOIN plenum_tracelab.turn_meta m ON m.turn_id = t.turn_id
                  LEFT JOIN plenum_tracelab.sample_items si
                         ON si.organization_id = t.organization_id AND si.turn_id = t.turn_id
                 WHERE t.turn_id = ANY(CAST(:tl_ids AS text[])) AND t.organization_id = CAST(:tl_org AS uuid)
                   AND m.golden_run_id IS NULL AND {_channel_sql()} <> 'golden'"""),
                {"tl_ids": list(named), "tl_org": org})).mappings().all()}
            eligible = [tid for tid in named if tid in found]
            kept = [tid for tid in eligible if not found[tid]["sampled"]]
            if not kept:
                raise db.Invalid("pool_empty")
            picks = [Pick(tid, str(named[tid].get("reason") or "flagged by monitoring")[:300]) for tid in kept]
            batch = (await conn.execute(_BATCH_INSERT, {
                "id": batch_id, "org": org, "plan_step": "extra", "strategy": req["strategy"],
                "params": json.dumps(req.get("params") or {}, default=str),
                "seed": DEFAULT_SEED if req.get("seed") is None else int(req["seed"]),
                "since": req["since"], "until": req["until"], "channel": req.get("channel") or "chat",
                "requested": len(req["items"]), "selected": len(picks), "pool_size": len(eligible),
                "excluded": len(eligible) - len(picks), "cfp": False, "rr": 1, "fv": None,
                "me": str(principal.user_id), "email": principal.email or None})).mappings().one()
            await conn.execute(_ITEM_INSERT, [{
                "id": str(uuid.uuid4()), "batch": batch_id, "org": org, "tid": p.turn_id,
                "sid": found[p.turn_id]["session_id"], "pos": i + 1, "reason": p.reason, "cluster": None,
                "dim": None, "score": None, "features": None} for i, p in enumerate(picks)])
    except IntegrityError as exc:
        if "ux_tl_items_turn" in str(exc) or annotations._constraint(exc) == "ux_tl_items_turn":
            raise db.Conflict("pool_changed") from exc
        raise
    out = _batch_out(dict(batch))
    out["items"] = len(picks)
    return {"ok": True, "batch": out, "items": [
        {**_pick_out(p), "position": i + 1,
         "conversation_key": normalize.group_key(found[p.turn_id]["session_id"], p.turn_id)}
        for i, p in enumerate(picks)]}


async def _batch_rows(conn, principal, org: str | None, *, include_withdrawn: bool) -> list[dict[str, Any]]:
    """Batches with the per-batch counts of spec section 4."""
    bw, bp = annotations.company_clause(org, "b")
    aw, ap = annotations.company_clause(org, "a")
    rows = (await conn.execute(text(f"""
        WITH v AS (
          SELECT a.turn_id, a.organization_id, count(DISTINCT a.reviewer_id) AS reviewers,
                 count(*) FILTER (WHERE a.verdict = 'wrong') AS wrong_n,
                 count(*) FILTER (WHERE a.verdict = 'right') AS right_n,
                 bool_or(a.reviewer_id = CAST(:tl_me AS uuid)) AS mine
            FROM plenum_tracelab.annotations a
           WHERE a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone AND {aw}
           GROUP BY a.turn_id, a.organization_id)
        SELECT b.*, count(si.id) AS n_items,
               count(si.id) FILTER (WHERE coalesce(v.reviewers, 0) >= 1) AS n_reviewed,
               count(si.id) FILTER (WHERE coalesce(v.mine, false)) AS n_mine,
               count(si.id) FILTER (WHERE coalesce(v.reviewers, 0) < b.reviews_required) AS n_missing,
               count(si.id) FILTER (WHERE v.wrong_n > 0 AND v.right_n = 0) AS n_wrong,
               count(si.id) FILTER (WHERE v.right_n > 0 AND v.wrong_n = 0) AS n_right,
               count(si.id) FILTER (WHERE v.right_n > 0 AND v.wrong_n > 0) AS n_disputed
          FROM plenum_tracelab.sample_batches b
          LEFT JOIN plenum_tracelab.sample_items si ON si.batch_id = b.id
          LEFT JOIN v ON v.turn_id = si.turn_id AND v.organization_id = si.organization_id
         WHERE {bw} AND (CAST(:tl_incw AS boolean) OR b.withdrawn_at IS NULL)
         GROUP BY b.id ORDER BY b.created_at DESC"""),
        {**bp, **ap, "tl_me": str(principal.user_id), "tl_incw": include_withdrawn})).mappings().all()
    return [_batch_out(dict(r)) for r in rows]


async def list_batches(principal, org: str | None, *, include_withdrawn: bool = False) -> list[dict]:
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        return await _batch_rows(conn, principal, org, include_withdrawn=include_withdrawn)


def _uuid_or_none(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


async def batch_detail(principal, org: str | None, batch_id: str) -> dict | None:
    bid = _uuid_or_none(batch_id)
    if bid is None:
        return None
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        batch = next((b for b in await _batch_rows(conn, principal, org, include_withdrawn=True) if b["id"] == bid), None)
        if batch is None:
            return None
        items = (await conn.execute(text("""
            SELECT si.turn_id, si.session_id, si.position, si.reason, si.cluster, si.dimension_value, si.score,
                   count(DISTINCT a.reviewer_id) AS reviewers,
                   coalesce(bool_or(a.reviewer_id = CAST(:tl_me AS uuid)), false) AS mine,
                   count(DISTINCT a.reviewer_id) FILTER (WHERE a.reviewer_id <> CAST(:tl_me AS uuid)) AS others_count
              FROM plenum_tracelab.sample_items si
              LEFT JOIN plenum_tracelab.annotations a
                     ON a.turn_id = si.turn_id AND a.organization_id = si.organization_id AND a.kind = 'verdict'
                    AND a.superseded_by IS NULL AND NOT a.tombstone
             WHERE si.batch_id = CAST(:tl_bid AS uuid)
             GROUP BY si.id ORDER BY si.position"""), {"tl_me": str(principal.user_id), "tl_bid": bid})).mappings().all()
    return {"batch": batch, "items": [
        {"turn_id": i["turn_id"], "conversation_key": normalize.group_key(i["session_id"], i["turn_id"]),
         "position": i["position"], "reason": i["reason"], "cluster": i["cluster"],
         "dimension_value": i["dimension_value"], "score": i["score"], "reviewed": int(i["reviewers"]) > 0,
         "reviewed_by_me": bool(i["mine"]), "others_count": int(i["others_count"])} for i in items]}


async def withdraw(principal, org: str, batch_id: str, *, reason: str) -> dict:
    """Set the three withdrawn_* columns once (the only update sample_batches ever gets)."""
    bid = _uuid_or_none(batch_id)
    if bid is None:
        raise db.NotFound("batch_not_found")
    async with db.engine().begin() as conn:
        await conn.execute(text(TIMEOUT))
        row = (await conn.execute(text(
            "UPDATE plenum_tracelab.sample_batches SET withdrawn_at = now(), withdrawn_by = CAST(:tl_me AS uuid), "
            "withdrawn_reason = :tl_reason WHERE id = CAST(:tl_bid AS uuid) AND organization_id = CAST(:tl_org AS uuid) "
            "AND withdrawn_at IS NULL RETURNING *"),
            {"tl_me": str(principal.user_id), "tl_reason": reason[:500], "tl_bid": bid, "tl_org": org})).mappings().first()
        if row is None:
            exists = (await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM plenum_tracelab.sample_batches WHERE id = CAST(:tl_bid AS uuid) "
                "AND organization_id = CAST(:tl_org AS uuid))"), {"tl_bid": bid, "tl_org": org})).scalar_one()
            raise db.Conflict("already_withdrawn") if exists else db.NotFound("batch_not_found")
    return _batch_out(dict(row))


async def queue(principal, org: str, *, batch_id: str | None = None, limit: int = 20) -> dict:
    """Items I have not judged that still need reviews, in a stable per-reviewer order."""
    if batch_id and _uuid_or_none(batch_id) is None:
        raise db.NotFound("batch_not_found")
    me = str(principal.user_id)
    live = ("SELECT {sel} FROM plenum_tracelab.annotations a WHERE a.kind = 'verdict' AND a.turn_id = si.turn_id "
            "AND a.organization_id = si.organization_id AND a.superseded_by IS NULL AND NOT a.tombstone")
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        rows = (await conn.execute(text(f"""
            SELECT si.turn_id, si.session_id, si.batch_id::text AS batch_id, b.plan_step, si.reason,
                   ({live.format(sel='count(DISTINCT a.reviewer_id)')} AND a.reviewer_id <> CAST(:tl_me AS uuid)) AS others_count,
                   count(*) OVER () AS remaining
              FROM plenum_tracelab.sample_items si
              JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id
             WHERE si.organization_id = CAST(:tl_org AS uuid) AND b.withdrawn_at IS NULL
               AND (CAST(:tl_bid AS uuid) IS NULL OR si.batch_id = CAST(:tl_bid AS uuid))
               AND NOT EXISTS ({live.format(sel='1')} AND a.reviewer_id = CAST(:tl_me AS uuid))
               AND ({live.format(sel='count(DISTINCT a.reviewer_id)')}) < b.reviews_required
             ORDER BY md5(si.turn_id || CAST(:tl_me AS text)), si.turn_id
             LIMIT :tl_lim"""), {"tl_org": org, "tl_bid": _uuid_or_none(batch_id), "tl_me": me,
                                  "tl_lim": max(1, min(int(limit), 50))})).mappings().all()
    return {"ok": True, "remaining": int(rows[0]["remaining"]) if rows else 0, "items": [
        {"turn_id": r["turn_id"], "conversation_key": normalize.group_key(r["session_id"], r["turn_id"]),
         "batch_id": r["batch_id"], "plan_step": r["plan_step"], "reason": r["reason"],
         "others_count": int(r["others_count"] or 0)} for r in rows]}


async def _distinct_sampled(conn, org: str) -> int:
    return int((await conn.execute(text(
        "SELECT count(*) FROM plenum_tracelab.sample_items si JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id "
        "WHERE si.organization_id = CAST(:tl_org AS uuid) AND (b.withdrawn_at IS NULL OR EXISTS (SELECT 1 FROM "
        "plenum_tracelab.annotations a WHERE a.kind = 'verdict' AND a.turn_id = si.turn_id AND "
        "a.organization_id = si.organization_id AND a.superseded_by IS NULL AND NOT a.tombstone))"),
        {"tl_org": org})).scalar_one())


async def plan_status(principal, org: str, *, since: datetime | None = None, until: datetime | None = None) -> dict:
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        batches = await _batch_rows(conn, principal, org, include_withdrawn=False)
        plan = plan_view(batches, await _distinct_sampled(conn, org))
        if since and until:
            check_window(since, until)
            cands, _, _ = await _load_pool(conn, principal, org, since=since, until=until, channel="chat",
                                           exclude_sampled=True, with_text=False, limit=POOL_MAX)
            plan["pool_size"] = len(cands)
    return {"ok": True, **plan}


async def manifest(principal, org: str | None) -> dict:
    """The record of every batch drawn: seed, parameters, window and each item's reason (spec 3.5)."""
    bw, bp = annotations.company_clause(org, "b")
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        batches = await _batch_rows(conn, principal, org, include_withdrawn=True)
        items = (await conn.execute(text(
            f"SELECT si.batch_id::text AS batch_id, si.turn_id, si.position, si.reason, si.cluster, si.dimension_value, "
            f"si.score FROM plenum_tracelab.sample_items si JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id "
            f"WHERE {bw} ORDER BY si.batch_id, si.position"), bp)).mappings().all()
        distinct = await _distinct_sampled(conn, org) if org else sum(int(b["items"]) for b in batches)
    by_batch: dict[str, list[dict[str, Any]]] = {}
    for i in items:
        by_batch.setdefault(i["batch_id"], []).append({k: i[k] for k in ("turn_id", "position", "reason", "cluster",
                                                                        "dimension_value", "score")})
    return {"schema_version": 1, "organization_id": org, "generated_at": datetime.now(timezone.utc).isoformat(),
            "plan": plan_view([b for b in batches if not b.get("withdrawn_at")], distinct) if org else None,
            "batches": [{"id": b["id"], "organization_id": b["organization_id"], "plan_step": b["plan_step"],
                         "strategy": b["strategy"], "seed": b["seed"], "params": b["params"],
                         "window": {"since": b["window_since"], "until": b["window_until"]}, "channel": b["channel"],
                         "requested": b["requested"], "selected": b["selected"], "pool_size": b["pool_size"],
                         "excluded_sampled": b["excluded_sampled"], "counts_for_prevalence": b["counts_for_prevalence"],
                         "reviews_required": b["reviews_required"], "created_by_email": b["created_by_email"],
                         "created_at": b["created_at"], "withdrawn_at": b["withdrawn_at"],
                         "items": by_batch.get(b["id"], [])} for b in batches]}


async def _mine(conn, org: str | None, me: str) -> set[str]:
    cw, cp = annotations.company_clause(org, "a")
    return {r[0] for r in (await conn.execute(text(
        f"SELECT a.turn_id FROM plenum_tracelab.annotations a WHERE a.kind = 'verdict' AND a.superseded_by IS NULL "
        f"AND NOT a.tombstone AND a.reviewer_id = CAST(:tl_me AS uuid) AND {cw}"), {**cp, "tl_me": me})).all()}


async def map_view(principal, org: str | None, *, since: datetime, until: datetime, channel: str = "chat",
                   limit: int = MAP_MAX) -> dict:
    check_window(since, until)
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        cands, truncated, _ = await _load_pool(conn, principal, org, since=since, until=until, channel=channel,
                                               exclude_sampled=False, with_text=False, limit=max(1, min(limit, MAP_MAX)))
        reviewed = await annotations.reviewed_turns(conn, org)
        mine = await _mine(conn, org, str(principal.user_id))
    payload = await asyncio.to_thread(map_payload, cands, reviewed=reviewed, mine=mine)
    return {**payload, "truncated": truncated}


_NOT_GOLDEN = ("NOT EXISTS (SELECT 1 FROM plenum_tracelab.turn_meta gm WHERE gm.turn_id = a.turn_id "
               "AND gm.golden_run_id IS NOT NULL)")


async def progress(principal, org: str | None, *, since: datetime | None, until: datetime | None,
                   block: int = 10, filter_org: str | None = None) -> dict:
    """Section 4's M2 metrics. A company's reviewers see their company; the platform owner (org None)
    sees totals and a per-company breakdown, or one company with filter_org. Golden turns never count."""
    if org is None and filter_org:
        org = _uuid_or_none(filter_org)
        if org is None:
            raise db.Invalid("bad_company", "That company id is not valid.")
    now = datetime.now(timezone.utc)
    until = until or now + timedelta(days=1)
    since = since or max(now - timedelta(days=30), now - timedelta(days=retention_days()) + timedelta(minutes=1))
    check_window(since, until)
    cw, cp = annotations.company_clause(org, "a")
    iw, ip = annotations.company_clause(org, "si")
    async with db.engine().connect() as conn:
        await conn.execute(text(TIMEOUT))
        heads = [dict(r) for r in (await conn.execute(text(f"""
            SELECT a.turn_id, a.organization_id::text AS organization_id, a.reviewer_id::text AS reviewer_id,
                   a.reviewer_email, a.verdict, f.created_at AS first_at, si.batch_id::text AS batch_id
              FROM plenum_tracelab.annotations a
              JOIN plenum_tracelab.annotations f ON f.id = a.lineage_id
              LEFT JOIN plenum_tracelab.sample_items si ON si.organization_id = a.organization_id AND si.turn_id = a.turn_id
             WHERE a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone AND {cw} AND {_NOT_GOLDEN}
             ORDER BY f.created_at, a.turn_id"""), cp)).mappings().all()]
        items = [dict(r) for r in (await conn.execute(text(f"""
            SELECT si.turn_id, si.organization_id::text AS organization_id, b.strategy, b.reviews_required,
                   b.withdrawn_at IS NOT NULL AS withdrawn
              FROM plenum_tracelab.sample_items si JOIN plenum_tracelab.sample_batches b ON b.id = si.batch_id
             WHERE {iw}"""), ip)).mappings().all()]
        firsts = [dict(r) for r in (await conn.execute(text(f"""
            SELECT a.turn_id, a.reviewer_id::text AS reviewer_id, a.verdict FROM plenum_tracelab.annotations a
             WHERE a.kind = 'verdict' AND a.version = 1 AND a.blind AND NOT a.tombstone AND {cw} AND {_NOT_GOLDEN}"""),
            cp)).mappings().all()]
        batches = await _batch_rows(conn, principal, org, include_withdrawn=True) if org else []
        distinct = await _distinct_sampled(conn, org) if org else None
        cands: list[Candidate] = []
        if org:
            cands, _, _ = await _load_pool(conn, principal, org, since=since, until=until, channel="chat",
                                           exclude_sampled=False, with_text=False, limit=MAP_MAX)
    verdicts: dict[str, list[str]] = {}
    for h in heads:
        verdicts.setdefault(h["turn_id"], []).append(h["verdict"])
    reviewed_ids = set(verdicts)
    active = [i for i in items if not i["withdrawn"] or i["turn_id"] in reviewed_ids]
    disc = discovery(heads, block)
    out: dict[str, Any] = {
        "ok": True, "scope": "company" if org else "platform", "organization_id": org, "block": block,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "plan": plan_view([b for b in batches if not b.get("withdrawn_at")], distinct) if org else None,
        "batches": batches,
        "open_batches": sum(1 for b in batches if not b.get("withdrawn_at") and int(b["missing"]) > 0) if org else None,
        "open_coding": open_coding(heads, active),
        "fair_sample": fair_sample([{"strategy": i["strategy"], "withdrawn": i["withdrawn"],
                                     "verdicts": verdicts.get(i["turn_id"], [])} for i in items]),
        "coverage": None, "agreement": agreement(firsts), "discovery": disc,
        "saturation": saturation(disc["points"], block), "by_company": None}
    if org:
        cov = coverage(cands, reviewed_ids)
        if len(cands) >= 3:
            m = await asyncio.to_thread(map_payload, cands, reviewed=reviewed_ids, mine=set())
            cov["clusters_unreviewed"] = [{"id": c["id"], "size": c["size"]} for c in m["clusters"] if not c["reviewed"]]
        else:
            cov["clusters_unreviewed"] = None
        out["coverage"] = cov
    else:
        by: dict[str, dict[str, Any]] = {}
        for i in items:
            by.setdefault(i["organization_id"], {"organization_id": i["organization_id"], "sampled": 0, "reviewed": 0})
            by[i["organization_id"]]["sampled"] += 1
        for tid in reviewed_ids:
            o = next(h["organization_id"] for h in heads if h["turn_id"] == tid)
            by.setdefault(o, {"organization_id": o, "sampled": 0, "reviewed": 0})["reviewed"] += 1
        out["by_company"] = sorted(by.values(), key=lambda r: -r["reviewed"])
    return out
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_a_turn_is_never_in_two_batches_sql.py tests/test_trace_lab_sampling_is_deterministic_and_recorded.py tests/test_trace_lab_map_and_progress_metrics.py
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/sampling.py`, `tests/test_trace_lab_a_turn_is_never_in_two_batches_sql.py`.

### Task 11: The review routes

**Files:**
- Create: `src/api/routes/trace_lab_review.py`
- Test: `tests/test_trace_lab_review_is_scoped_to_the_acting_company.py`

**Interfaces:**
- Consumes: `annotations.*` and `sampling.*` (Tasks 5–10); M0's `db.require_ready`, `db.require_trace_store()`, `db.require_reader(principal, organization_id) -> Scope`, `db.require_reviewer(principal, organization_id) -> str` (the one auth rule, R36: 403 `reviewers_only`, 403 `not_your_company`, 400 `bad_company`, 400 `name_a_company`); `db.ReviewError` and subclasses, `db.http_error_for` (Task 3); `Principal`, `current_principal` (`src/services/principal.py:39`, `:130`). No second auth rule: this module does not import `traces._act` and defines no reason codes of its own.
- Produces: `router = APIRouter(prefix="/api/trace-lab", tags=["Trace Lab"], dependencies=[Depends(db.require_ready)])` with the 17 routes of spec §Review 10.3 except reveal; `_reader(principal, organization_id) -> str | None` and `_reviewer(principal, organization_id) -> str` (thin delegations to M0), `_call(coro)`; request models `AnchorIn`, `VerdictBody`, `CommentBody`, `CommentEditBody`, `BatchBody`, `WithdrawBody`.

- [ ] **Step 1: Write the failing test**

```python
"""The review routes: only admins review, a company admin reviews only their company, a superadmin
writes only while acting for a company, the error envelope carries the reason the page switches on,
and every route is down (503) while either store is (spec "Open coding ..." 10.1-10.3). Who may ask and
the reasons are M0's one auth rule (db.require_reader / db.require_reviewer, R36): 403 reviewers_only,
403 not_your_company, 400 bad_company, 400 name_a_company."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.agents import trace
from src.api.routes import trace_lab_review as routes
from src.services.principal import Principal, current_principal
from src.trace_lab import annotations, db, sampling

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def _principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email="rev@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[db.require_ready] = lambda: None
    calls = []

    async def fake_save(principal, org, turn_id, **kw):
        calls.append(("save", org, turn_id, kw))
        return {"id": "a1", "verdict": kw["verdict"]}

    async def fake_batches(principal, org, **kw):
        calls.append(("batches", org, kw))
        return []

    monkeypatch.setattr(annotations, "save_verdict", fake_save)
    monkeypatch.setattr(sampling, "list_batches", fake_batches)

    def as_(p):
        app.dependency_overrides[current_principal] = lambda: p
        return TestClient(app)

    return as_, calls, app


RIGHT = {"verdict": "right", "base": None}


def test_a_plain_user_is_refused(client):
    as_, calls, _ = client
    r = as_(_principal("user")).put("/api/trace-lab/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only" and not calls


def test_an_admin_naming_another_company_is_refused(client):
    as_, calls, _ = client
    r = as_(_principal()).get("/api/trace-lab/sampling/batches", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "not_your_company" and not calls


def test_a_company_id_that_is_not_an_id_is_refused(client):
    as_, calls, _ = client
    r = as_(_principal("superadmin")).get("/api/trace-lab/sampling/batches", params={"organization_id": "not-a-company"})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_company" and not calls


def test_an_admin_writes_for_their_own_company(client):
    as_, calls, _ = client
    r = as_(_principal()).put("/api/trace-lab/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 200 and r.json()["annotation"]["id"] == "a1"
    assert calls == [("save", ORG, "turn-1", {"verdict": "right", "note": None, "anchor": None, "base": None})]


def test_a_superadmin_not_acting_reads_the_platform_but_cannot_write(client):
    as_, calls, _ = client
    c = as_(_principal("superadmin"))
    r = c.put("/api/trace-lab/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"
    assert c.get("/api/trace-lab/sampling/batches").status_code == 200
    assert calls == [("batches", None, {"include_withdrawn": False})]


def test_a_superadmin_acting_reviews_that_company(client):
    as_, calls, _ = client
    r = as_(_principal("superadmin")).put("/api/trace-lab/turns/turn-1/verdict", json=RIGHT,
                                          params={"organization_id": OTHER})
    assert r.status_code == 200 and calls[0][1] == OTHER


@pytest.mark.parametrize("exc,status,reason", [
    (db.Stale({"id": "head-2"}), 409, "stale"),
    (db.Invalid("quote_not_found"), 422, "quote_not_found"),
    (db.NotFound("turn_not_found"), 404, "turn_not_found"),
    (db.Forbidden("not_author"), 403, "not_author"),
])
def test_review_errors_keep_the_house_envelope(client, monkeypatch, exc, status, reason):
    as_, _, _ = client

    async def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(annotations, "save_verdict", boom)
    r = as_(_principal()).put("/api/trace-lab/turns/turn-1/verdict", json=RIGHT)
    body = r.json()["detail"]
    assert r.status_code == status and body["reason"] == reason and body["ok"] is False and body["error"]
    if reason == "stale":
        assert body["current"] == {"id": "head-2"}


def test_bodies_are_validated_before_anything_runs(client):
    as_, calls, _ = client
    c = as_(_principal())
    assert c.put("/api/trace-lab/turns/t/verdict", json={"verdict": "maybe"}).status_code == 422
    assert c.put("/api/trace-lab/turns/t/verdict", json={"verdict": "wrong", "note": "x",
                                                         "anchor": {"field": "html", "quote": "q"}}).status_code == 422
    assert c.post("/api/trace-lab/sampling/batches/b/withdraw", json={"reason": ""}).status_code == 422
    assert c.get("/api/trace-lab/progress", params={"block": 4}).status_code == 422
    r = c.get("/api/trace-lab/annotations")
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "name_a_conversation"
    assert not calls


def test_the_routes_answer_503_while_a_store_is_down(client, monkeypatch):
    as_, _, app = client
    monkeypatch.setattr(trace, "_ready", False)
    r = as_(_principal()).get("/api/trace-lab/sampling/batches")
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_store_unavailable"
    monkeypatch.setattr(trace, "_ready", True)

    def down():
        raise HTTPException(status_code=503, detail={"ok": False, "error": "Trace Lab is not available right now."})

    app.dependency_overrides[db.require_ready] = down
    assert as_(_principal()).get("/api/trace-lab/sampling/batches").status_code == 503


def test_preview_takes_the_batch_fields_as_query_parameters(client, monkeypatch):
    as_, _, _ = client
    seen = {}

    async def fake_preview(principal, org, req):
        seen.update(req, org=org)
        return {"ok": True, "pool_size": 0, "excluded_sampled": 0, "truncated": False, "picks": []}

    monkeypatch.setattr(sampling, "preview", fake_preview)
    c = as_(_principal())
    q = {"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": "route_target",
         "since": "2026-10-01T00:00:00Z", "until": "2026-10-15T00:00:00Z"}
    r = c.get("/api/trace-lab/sampling/preview", params=q)
    assert r.status_code == 200 and seen["org"] == ORG and seen["k"] == 30 and seen["seed"] == 7
    assert c.get("/api/trace-lab/sampling/preview", params={**q, "strategy": "thumbs"}).status_code == 422
```

- [ ] **Step 2: Run it and see it fail**

```sh
tl_da_test tests/test_trace_lab_review_is_scoped_to_the_acting_company.py
```

Expected: `ImportError: cannot import name 'trace_lab_review' from 'src.api.routes'`.

- [ ] **Step 3: Implement**

Create `src/api/routes/trace_lab_review.py`:

```python
"""Trace Lab M2 routes: open coding (verdicts, comments, history) and sampling (plan, preview, batches,
manifest, map, progress, queue). Spec "Open coding, sampling ..." section 10.3.

Who (M0's one auth rule, R36 - this module adds no rule and no reason code): reader = a company admin,
or a superadmin (acting for a company, or - not acting - the read-only platform view); reviewer = the
same, but writes need one company. Reasons: 403 reviewers_only, 403 not_your_company, 400 bad_company,
400 name_a_company. Every route answers 503 while the Trace Lab store is not verified
(db.require_ready, M0) or the trace store is down (db.require_trace_store, M0).
The POST /turns/{turn_id}/reveal route arrives with M3's expected-outcome panel.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...services.principal import Principal, current_principal
from ...trace_lab import annotations, db, sampling

router = APIRouter(prefix="/api/trace-lab", tags=["Trace Lab"], dependencies=[Depends(db.require_ready)])


def _reader(principal: Principal, organization_id: str | None) -> str | None:
    """Admins and superadmins only (M0's rule). The acting company, or None for the platform view."""
    db.require_trace_store()
    return db.require_reader(principal, organization_id).org_id


def _reviewer(principal: Principal, organization_id: str | None) -> str:
    """The one company a write is for (M0's rule): an admin's own, or the one a superadmin names."""
    db.require_trace_store()
    return db.require_reviewer(principal, organization_id)


async def _call(coro):
    try:
        return await coro
    except db.ReviewError as exc:
        raise db.http_error_for(exc) from exc


class AnchorIn(BaseModel):
    span_id: str | None = Field(None, max_length=64)
    field: str = Field(..., pattern="^(question|answer|input|output)$")
    quote: str = Field(..., min_length=1, max_length=2000)
    occurrence: int = Field(1, ge=1, le=10000)


class VerdictBody(BaseModel):
    verdict: str = Field(..., pattern="^(right|wrong)$")
    note: str | None = Field(None, max_length=2000)
    anchor: AnchorIn | None = None
    base: str | None = Field(None, max_length=64)


class CommentBody(BaseModel):
    conversation_key: str = Field(..., min_length=1, max_length=200)
    turn_id: str | None = Field(None, max_length=64)
    note: str = Field(..., min_length=1, max_length=2000)


class CommentEditBody(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)
    base: str = Field(..., max_length=64)


class BatchBody(BaseModel):
    plan_step: str = Field(..., pattern="^(initial_uniform|cluster_representatives|dimension|depth_search|final_uniform|extra)$")
    strategy: str = Field(..., pattern="^(uniform|diversity|dimension|outlier|enrich)$")
    k: int = Field(..., ge=1, le=200)
    seed: int = Field(7, ge=0, le=2**31 - 1)
    since: datetime
    until: datetime
    channel: str = Field("chat", max_length=40)
    dimension: str | None = Field(None, max_length=40)
    mode_id: str | None = None
    random_share: float | None = Field(None, ge=0.0, le=0.5)
    reviews_required: int = Field(1, ge=1, le=3)
    source: str = Field("bow", pattern="^(bow|depth_scan)$")
    proposal_run_id: str | None = None
    include_view_as: bool = False
    include_deleted: bool = False


class WithdrawBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ── open coding ────────────────────────────────────────────────────────────────────────

@router.get("/annotations")
async def get_annotations(conversation_key: str | None = Query(None, max_length=200),
                          turn_id: str | None = Query(None, max_length=64),
                          organization_id: str | None = Query(None),
                          principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    if conversation_key:
        return await _call(annotations.for_conversation(principal, org, conversation_key, turn_id=turn_id))
    if turn_id:
        states = await _call(annotations.for_turns(principal, org, [turn_id]))
        if turn_id not in states:
            raise db.http_error_for(db.NotFound("turn_not_found"))
        return {"ok": True, "conversation_key": None, "turns": states, "comments": None, "comments_hidden": 0,
                "blocks": None}
    raise db.http_error_for(db.Invalid("name_a_conversation", "Name a conversation or an answer."))


@router.put("/turns/{turn_id}/verdict")
async def put_verdict(turn_id: str, body: VerdictBody, organization_id: str | None = Query(None),
                      principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    anchor = (annotations.Anchor(body.anchor.span_id, body.anchor.field, body.anchor.quote, body.anchor.occurrence)
              if body.anchor else None)
    row = await _call(annotations.save_verdict(principal, org, turn_id, verdict=body.verdict, note=body.note,
                                               anchor=anchor, base=body.base))
    return {"ok": True, "annotation": row}


@router.delete("/turns/{turn_id}/verdict")
async def delete_verdict(turn_id: str, base: str = Query(..., max_length=64), organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return {"ok": True, "annotation": await _call(annotations.delete_verdict(principal, org, turn_id, base=base))}


@router.get("/turns/{turn_id}/annotation-history")
async def annotation_history(turn_id: str, organization_id: str | None = Query(None),
                             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return await _call(annotations.history(principal, org, turn_id))


@router.post("/comments", status_code=201)
async def post_comment(body: CommentBody, organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    row = await _call(annotations.add_comment(principal, org, body.conversation_key, note=body.note,
                                              turn_id=body.turn_id))
    return {"ok": True, "annotation": row}


@router.put("/comments/{lineage_id}")
async def put_comment(lineage_id: str, body: CommentEditBody, organization_id: str | None = Query(None),
                      principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    row = await _call(annotations.edit_comment(principal, org, lineage_id, note=body.note, base=body.base))
    return {"ok": True, "annotation": row}


@router.delete("/comments/{lineage_id}")
async def delete_comment(lineage_id: str, base: str = Query(..., max_length=64), organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return {"ok": True, "annotation": await _call(annotations.delete_comment(principal, org, lineage_id, base=base))}


# ── sampling ───────────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def get_queue(batch_id: str | None = Query(None, max_length=64), limit: int = Query(20, ge=1, le=50),
                    organization_id: str | None = Query(None),
                    principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return await _call(sampling.queue(principal, org, batch_id=batch_id, limit=limit))


@router.get("/sampling/plan")
async def get_plan(since: datetime | None = Query(None), until: datetime | None = Query(None),
                   organization_id: str | None = Query(None),
                   principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return await _call(sampling.plan_status(principal, org, since=since, until=until))


@router.get("/sampling/preview")
async def get_preview(
        plan_step: str = Query(..., pattern=f"^({'|'.join(sampling.PLAN_STEPS)})$"),
        strategy: str = Query(..., pattern=f"^({'|'.join(sampling.STRATEGIES)})$"),
        k: int = Query(..., ge=1, le=200), seed: int = Query(7, ge=0, le=2**31 - 1),
        since: datetime = Query(...), until: datetime = Query(...), channel: str = Query("chat", max_length=40),
        dimension: str | None = Query(None, max_length=40), mode_id: str | None = Query(None, max_length=64),
        random_share: float | None = Query(None, ge=0.0, le=0.5), reviews_required: int = Query(1, ge=1, le=3),
        source: str = Query("bow", pattern="^(bow|depth_scan)$"),
        proposal_run_id: str | None = Query(None, max_length=64),
        include_view_as: bool = Query(False), include_deleted: bool = Query(False),
        organization_id: str | None = Query(None),
        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """The BatchBody fields as query parameters; writes nothing."""
    org = _reviewer(principal, organization_id)
    body = BatchBody(plan_step=plan_step, strategy=strategy, k=k, seed=seed, since=since, until=until, channel=channel,
                     dimension=dimension, mode_id=mode_id, random_share=random_share,
                     reviews_required=reviews_required, source=source, proposal_run_id=proposal_run_id,
                     include_view_as=include_view_as, include_deleted=include_deleted)
    return await _call(sampling.preview(principal, org, body.model_dump()))


@router.post("/sampling/batches", status_code=201)
async def post_batch(body: BatchBody, organization_id: str | None = Query(None),
                     principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return await _call(sampling.create_batch(principal, org, body.model_dump()))


@router.get("/sampling/batches")
async def get_batches(include_withdrawn: bool = Query(False), organization_id: str | None = Query(None),
                      principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return {"ok": True, "batches": await _call(sampling.list_batches(principal, org, include_withdrawn=include_withdrawn))}


@router.get("/sampling/batches/{batch_id}")
async def get_batch(batch_id: str, organization_id: str | None = Query(None),
                    principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    out = await _call(sampling.batch_detail(principal, org, batch_id))
    if out is None:
        raise db.http_error_for(db.NotFound("batch_not_found"))
    return {"ok": True, **out}


@router.post("/sampling/batches/{batch_id}/withdraw")
async def post_withdraw(batch_id: str, body: WithdrawBody, organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reviewer(principal, organization_id)
    return {"ok": True, "batch": await _call(sampling.withdraw(principal, org, batch_id, reason=body.reason))}


@router.get("/sampling/manifest")
async def get_manifest(organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return {"ok": True, **await _call(sampling.manifest(principal, org))}


@router.get("/map")
async def get_map(since: datetime = Query(...), until: datetime = Query(...), channel: str = Query("chat", max_length=40),
                  limit: int = Query(sampling.MAP_MAX, ge=1, le=sampling.MAP_MAX),
                  organization_id: str | None = Query(None),
                  principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return await _call(sampling.map_view(principal, org, since=since, until=until, channel=channel, limit=limit))


@router.get("/progress")
async def get_progress(since: datetime | None = Query(None), until: datetime | None = Query(None),
                       block: int = Query(10, ge=5, le=50), filter_org: str | None = Query(None, max_length=64),
                       organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _reader(principal, organization_id)
    return await _call(sampling.progress(principal, org, since=since, until=until, block=block,
                                         filter_org=filter_org if org is None else None))
```

`GET /sampling/preview` takes the batch fields as explicit query parameters and validates them through `BatchBody`, which works on every FastAPI version the image may resolve (no query-model feature needed). `BatchBody` keeps the five drawable strategies: a `monitor_flagged` batch is recorded only by M6, which calls `sampling.create_batch` directly (no HTTP route).

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_review_is_scoped_to_the_acting_company.py
```

Expected: `13 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/api/routes/trace_lab_review.py`, `tests/test_trace_lab_review_is_scoped_to_the_acting_company.py`.

### Task 12: Register the router in `main.py`

**Files:**
- Modify: `src/api/main.py:40` (router imports) and `src/api/main.py:255` (`include_router` list) — shared file, exact-string edits on the current content
- Test: `tests/test_trace_lab_review_router_is_registered.py`

**Interfaces:**
- Consumes: `trace_lab_review.router` (Task 11).
- Produces: the M2 routes served by svc-deepagents.

- [ ] **Step 1: Write the failing test**

```python
"""The M2 router is part of the service: main.py imports and includes it next to the traces router."""
from __future__ import annotations

from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "src" / "api" / "main.py"


def test_the_review_router_is_imported_and_included():
    src = MAIN.read_text()
    assert "from .routes.trace_lab_review import router as trace_lab_review_router" in src
    assert "app.include_router(trace_lab_review_router)" in src


def test_the_review_router_serves_the_m2_paths():
    from src.api.routes.trace_lab_review import router

    paths = {(m, r.path) for r in router.routes for m in r.methods}
    for want in [("GET", "/api/trace-lab/annotations"), ("PUT", "/api/trace-lab/turns/{turn_id}/verdict"),
                 ("DELETE", "/api/trace-lab/turns/{turn_id}/verdict"), ("GET", "/api/trace-lab/turns/{turn_id}/annotation-history"),
                 ("POST", "/api/trace-lab/comments"), ("PUT", "/api/trace-lab/comments/{lineage_id}"),
                 ("DELETE", "/api/trace-lab/comments/{lineage_id}"), ("GET", "/api/trace-lab/queue"),
                 ("GET", "/api/trace-lab/sampling/plan"), ("GET", "/api/trace-lab/sampling/preview"),
                 ("POST", "/api/trace-lab/sampling/batches"), ("GET", "/api/trace-lab/sampling/batches"),
                 ("GET", "/api/trace-lab/sampling/batches/{batch_id}"),
                 ("POST", "/api/trace-lab/sampling/batches/{batch_id}/withdraw"),
                 ("GET", "/api/trace-lab/sampling/manifest"), ("GET", "/api/trace-lab/map"), ("GET", "/api/trace-lab/progress")]:
        assert want in paths, want
    assert not any(p.endswith("/reveal") for _, p in paths)              # M3 adds reveal with the expected outcome
```

- [ ] **Step 2: Run it and see it fail**

```sh
tl_da_test tests/test_trace_lab_review_router_is_registered.py
```

Expected: `test_the_review_router_is_imported_and_included` fails (`AssertionError` on the import line); the path test passes.

- [ ] **Step 3: Implement**

First check for foreign edits in this shared file: `git -C /Users/hussain/Desktop/hoist/Hoistra_Plenum diff --stat -- apps/backend/cafm-connector-service-final/svc-deepagents/src/api/main.py` (read-only) and re-read the two anchors. M0 and M1 add their own lines next to these anchors; insert directly after each anchor, leaving theirs in place.

`src/api/main.py` — imports:

old:
```python
from .routes.traces import router as traces_router
```
new:
```python
from .routes.traces import router as traces_router
from .routes.trace_lab_review import router as trace_lab_review_router
```

`src/api/main.py` — routers:

old:
```python
app.include_router(traces_router)
```
new:
```python
app.include_router(traces_router)
app.include_router(trace_lab_review_router)
```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_review_router_is_registered.py tests/test_agent_modules_import.py
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/api/main.py`, `tests/test_trace_lab_review_router_is_registered.py`.

### Task 13: Turn on the `reviewed` filter in M1's conversation list

**Files:**
- Modify: `src/trace_lab/conversations.py` (M1) — the place that answers 400 `reviewed_filter_unavailable`; and `get_conversation`, `get_model_call`, `list_model_calls`, `_call_row` (gate verdicts, R23)
- Modify: M1's `test_reviewed_filter_is_refused_until_open_coding_exists` (in `tests/test_trace_lab_conversations.py`)
- Test: `tests/test_trace_lab_reviewed_filter_reaches_the_conversation_list_sql.py`

**Interfaces:**
- Consumes: `annotations.reviewed_predicate(alias, reviewer_id, mode)` (Task 7); M1 `conversations.TurnFilter`, `ModelCallFilter`, `list_conversations`, `get_conversation`, `get_turn`, `list_model_calls`, `get_model_call`, `_call_row`, `_read()`; M1 `normalize.normalize_turn(..., reveal_gate_verdicts=False)`; M0 `db.state()` (`SchemaState.applied_version`).
- Produces: `GET /api/trace-lab/conversations?reviewed=reviewed|unreviewed|mine` works once version 2 is applied; it stays 400 `reviewed_filter_unavailable` while the applied version is below 2. Gate verdicts (R23) are shown to a reviewer once they have saved a live verdict of their own on that answer: private `conversations._revealed_turns(conn, principal, turn_ids) -> set[str]`; `get_conversation` and `get_model_call` pass `reveal_gate_verdicts=` to `normalize_turn` (`get_turn` goes through `get_conversation`); `_call_row(r, *, revealed=False)`.

- [ ] **Step 1: Write the failing test**

```python
"""M1's conversation list `reviewed` filter works once open coding exists: reviewed, unreviewed and mine
select conversations by live verdicts (spec "Trace capture ..." 5.3; annotations.reviewed_predicate). A gate's
verdict stays hidden from a reviewer - on the gate message, the judge's model-call card and the model-call
list - until they have saved a verdict of their own on that answer (R23; M1 withholds it until M2 passes
reveal_gate_verdicts=True)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from trace_lab_m2_fixtures import NOW, add_turn, principal, truncate_chat
from trace_lab_scratch import fresh_schema  # M0's scratch helper (tests/trace_lab_scratch.py)
from src.agents import trace
from src.services import chat_threads
from src.trace_lab import annotations, conversations

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"),
                                reason="needs a scratch Postgres in TRACE_LAB_TEST_DB_URL")

ORG = str(uuid.uuid4())


@pytest.fixture
async def eng(monkeypatch):
    engine = await fresh_schema(monkeypatch)
    monkeypatch.setattr(chat_threads, "_ready", False)
    await chat_threads.ensure_tables()
    monkeypatch.setattr(trace, "_ready", True)
    yield engine
    await truncate_chat(engine)
    await engine.dispose()


async def _keys(me, reviewed):
    f = conversations.TurnFilter(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1), reviewed=reviewed)
    out = await conversations.list_conversations(me, ORG, f, cursor=None, limit=50)
    return sorted(c["key"] for c in out["conversations"])


async def test_the_reviewed_filter_selects_by_live_verdicts(eng):
    t1 = await add_turn(eng, 1, ORG, session="sess-r1")
    t2 = await add_turn(eng, 2, ORG, session="sess-r2")
    await add_turn(eng, 3, ORG, session="sess-r3")
    me, other = principal(ORG), principal(ORG)
    await annotations.save_verdict(me, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    v = await annotations.save_verdict(other, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    assert await _keys(me, "mine") == ["s:sess-r1"]
    assert await _keys(me, "reviewed") == ["s:sess-r1", "s:sess-r2"]
    assert await _keys(me, "unreviewed") == ["s:sess-r3"]
    await annotations.delete_verdict(other, ORG, t2, base=v["id"])          # a tombstone is not a review
    assert await _keys(me, "unreviewed") == ["s:sess-r2", "s:sess-r3"]


async def _gate_tree(engine, tid: str) -> str:
    """Give a turn a span tree with a gate and the judge's model call under it; returns the judge call's span id."""
    root, gate, judge = (str(uuid.uuid4()) for _ in range(3))
    spans = [(root, None, 0, "turn", "turn", None, None, None, None),
             (gate, root, 1, "gate", "el_udr", "el_udr", None, {"answer_before": "A"},
              {"outcome": "degraded", "changed": True, "answer_after": "A, checked", "verdict": {"issues": ["count off"]}}),
             (judge, gate, 2, "llm", "judge", "el_udr", "claude-sonnet-5",
              {"system_prompt": "Judge", "user_message": "check"}, {"model_output": {"issues": ["count off"]}})]
    async with engine.begin() as conn:
        for sid, parent, seq, kind, name, agent, model, inp, out in spans:
            await conn.execute(text("""
                INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, parent_id, seq, kind, name, agent, model,
                                                           started_at, input, output, ok)
                VALUES (CAST(:id AS uuid), :tid, CAST(:parent AS uuid), :seq, :kind, :name, :agent, :model, :at,
                        CAST(:inp AS jsonb), CAST(:out AS jsonb), true)"""),
                {"id": sid, "tid": tid, "parent": parent, "seq": seq, "kind": kind, "name": name, "agent": agent,
                 "model": model, "at": NOW, "inp": json.dumps(inp) if inp is not None else None,
                 "out": json.dumps(out) if out is not None else None})
    return judge


def _gate(record):
    return next(m for m in record["messages"] if m["role"] == "gate")


async def test_a_gate_verdict_stays_hidden_until_the_reviewer_has_saved_a_verdict(eng):
    tid = await add_turn(eng, 4, ORG, session="sess-gate")
    judge = await _gate_tree(eng, tid)
    me, other = principal(ORG), principal(ORG)
    f = conversations.ModelCallFilter(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1))

    async def seen(who):
        conv = (await conversations.get_conversation(who, ORG, "s:sess-gate"))["active"]
        one = (await conversations.get_turn(who, ORG, tid))["trace"]
        card = (await conversations.get_model_call(who, ORG, judge))["model_call"]
        row = next(c for c in (await conversations.list_model_calls(who, ORG, f, cursor=None, limit=50))["calls"]
                   if c["span_id"] == judge)
        return conv, one, card, row

    conv, one, card, row = await seen(me)                                     # before my verdict: hidden everywhere
    for rec in (conv, one):
        assert _gate(rec)["verdict_hidden"] is True and _gate(rec)["verdict"] is None
        assert "count off" not in json.dumps(rec, default=str)
    assert card["response"] is None and card["withheld"] == ["response", "raw"]
    assert row["withheld"] is True and row["response_preview"] is None

    v = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    conv, one, card, row = await seen(me)                                     # after my own verdict: shown
    for rec in (conv, one):
        assert _gate(rec)["verdict_hidden"] is False and _gate(rec)["verdict"] == {"issues": ["count off"]}
    assert card["response"] == {"model_output": {"issues": ["count off"]}} and row["withheld"] is False
    assert _gate((await conversations.get_conversation(other, ORG, "s:sess-gate"))["active"])["verdict_hidden"] is True

    await annotations.delete_verdict(me, ORG, tid, base=v["id"])              # a tombstone hides it again
    assert _gate((await conversations.get_conversation(me, ORG, "s:sess-gate"))["active"])["verdict_hidden"] is True
```

- [ ] **Step 2: Run it and see it fail** (scratch Postgres up)

```sh
tl_da_test tests/test_trace_lab_reviewed_filter_reaches_the_conversation_list_sql.py
```

Expected: M1's refusal (the 400 `reviewed_filter_unavailable` raised as an `HTTPException` or M1's error type) or, if M1 already calls `reviewed_predicate`, a pass — in which case skip Step 3.

- [ ] **Step 3: Implement**

Find M1's refusal: `grep -n "reviewed_filter_unavailable" apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/conversations.py apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/trace_lab_conversations.py`.
1. Where it refuses any `reviewed` value other than `any`, narrow the condition to "version 2 is not applied" (add `from . import db` at the top of the module if it is not imported there):
   ```python
   _st = db.state()
   if f.reviewed != "any" and (_st is None or (_st.applied_version or 0) < 2):
       ...  # M1's existing 400 reviewed_filter_unavailable, unchanged
   ```
2. Where M1 builds the AND of the turn filters (`HIT_SQL`, spec "Trace capture" §5.5; it is evaluated over the `base b` rows, so the turn alias is `b`), add the predicate and its parameters to that list and to the query's bind dict:
   ```python
   if f.reviewed != "any":
       from . import annotations as _ann  # M2: open coding
       _pred, _pred_params = _ann.reviewed_predicate("b", str(principal.user_id), f.reviewed)
       hits.append(_pred)          # M1's list of HIT_SQL parts (use M1's variable name)
       params.update(_pred_params) # M1's bind dict for the phase-1 query (use M1's variable name)
   ```
   If the updates query (`conversation_updates`) shares `HIT_SQL`, it inherits the predicate with alias `tt`: call `reviewed_predicate("tt", …)` there instead.
3. In M1's `test_reviewed_filter_is_refused_until_open_coding_exists`, set the Trace Lab state to applied version 1 before asserting the 400 (the rule is now "refused below version 2"), e.g. `monkeypatch.setattr(db, "STATE", dataclasses.replace(db.STATE, applied_version=1))` or M1's own way of faking the state.
4. Gate verdicts (R23). M1's `normalize.normalize_turn` hides a gate's verdict, the judge's model-call card under it and the list's response preview until `reveal_gate_verdicts=True`; nothing passes `True` yet. Find the calls with `grep -n "normalize_turn(\|def _call_row\|_call_row(\|def get_model_call\|def list_model_calls" apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/conversations.py` and edit M1's code with exact replacements (M1's variable names shown; use the file's if they differ). `get_turn` calls `get_conversation`, so it needs no edit of its own; `load_turn` / `load_turns` (unscoped, for jobs) stay hidden by design.

   a. Add this helper right after M1's `_read()`. It returns before running any query while version 2 is not applied, so the `annotations` table is never touched on a version-1 database and M1's fake-connection tests are unaffected:
   ```python
   async def _revealed_turns(conn, principal: Any, turn_ids: list[str]) -> set[str]:
       """Turns on which the caller has a live verdict of their own: the gate's verdict is shown there (R23)."""
       ids = [str(t) for t in turn_ids if t]
       st = db.state()
       if not ids or st is None or (st.applied_version or 0) < 2:
           return set()
       rows = await conn.execute(text(
           "SELECT a.turn_id FROM plenum_tracelab.annotations a WHERE a.kind = 'verdict' AND a.superseded_by IS NULL "
           "AND NOT a.tombstone AND a.reviewer_id = CAST(:me AS uuid) AND a.turn_id = ANY(CAST(:ids AS text[]))"),
           {"me": str(principal.user_id), "ids": ids})
       return {r[0] for r in rows}
   ```
   b. `get_conversation` — read it inside the existing `async with _read() as conn:` block, and pass it to `normalize_turn`:

   old:
   ```python
           metas = {r["turn_id"]: _meta(r.pop("meta")) for r in rows}
   ```
   new:
   ```python
           revealed = await _revealed_turns(conn, principal, [active["turn_id"]])
           metas = {r["turn_id"]: _meta(r.pop("meta")) for r in rows}
   ```
   old:
   ```python
       record = normalize.normalize_turn(active, spans, meta=metas[active["turn_id"]], activity_input=activity,
                                         conversation={"key": key, "session_kind": kind, "session_label": label,
   ```
   new:
   ```python
       record = normalize.normalize_turn(active, spans, meta=metas[active["turn_id"]], activity_input=activity,
                                         reveal_gate_verdicts=active["turn_id"] in revealed,
                                         conversation={"key": key, "session_kind": kind, "session_label": label,
   ```
   c. `get_model_call` — inside its `async with _read() as conn:` block, after the `spans = [...]` read, add `revealed = turn["turn_id"] in await _revealed_turns(conn, principal, [turn["turn_id"]])`; then:

   old:
   ```python
       record = normalize.normalize_turn(turn, spans, meta=meta, conversation={"key": turn["conversation_key"]})
   ```
   new:
   ```python
       record = normalize.normalize_turn(turn, spans, meta=meta, conversation={"key": turn["conversation_key"]},
                                         reveal_gate_verdicts=revealed)
   ```
   and in the same function's fallback for a span in a discarded duplicate tree:

   old:
   ```python
                                              withheld=bool(parent and parent.get("kind") == "gate"))
   ```
   new:
   ```python
                                              withheld=bool(parent and parent.get("kind") == "gate") and not revealed)
   ```
   d. The model-call list preview. `_call_row` gains a keyword, and `list_model_calls` reads the caller's revealed turns inside its `async with _read() as conn:` block (after the `by_model` read):

   old:
   ```python
   def _call_row(r: Mapping[str, Any]) -> dict[str, Any]:
       withheld = bool(r["under_gate"])
   ```
   new:
   ```python
   def _call_row(r: Mapping[str, Any], *, revealed: bool = False) -> dict[str, Any]:
       withheld = bool(r["under_gate"]) and not revealed
   ```
   old (after the `by_model = [...]` read, still inside the block):
   ```python
           by_model = [dict(r) for r in (await conn.execute(text(by_model_sql), p)).mappings().all()]
   ```
   new:
   ```python
           by_model = [dict(r) for r in (await conn.execute(text(by_model_sql), p)).mappings().all()]
           shown = await _revealed_turns(conn, principal, [r["turn_id"] for r in rows[:limit]])
   ```
   old:
   ```python
               "calls": [_call_row(r) for r in page]}
   ```
   new:
   ```python
               "calls": [_call_row(r, revealed=r["turn_id"] in shown) for r in page]}
   ```

- [ ] **Step 4: Run it and see it pass**

```sh
tl_da_test tests/test_trace_lab_reviewed_filter_reaches_the_conversation_list_sql.py tests/test_trace_lab_conversations.py
```

Expected: all pass, including `test_a_gate_verdict_stays_hidden_until_the_reviewer_has_saved_a_verdict` (hidden on the gate message, the judge's card and the list row before the save; shown after it; still hidden for another reviewer and again after a tombstone) and M1's own gate-hiding tests (`test_gate_verdict_and_its_model_calls_stay_hidden`, `test_gate_children_are_withheld_in_the_model_call_list`), which run without a saved verdict.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `src/trace_lab/conversations.py`, `tests/test_trace_lab_conversations.py`, `tests/test_trace_lab_reviewed_filter_reaches_the_conversation_list_sql.py`; paste the M1 diff into the log.


### Task 14: The M2 calls in `api/traceLab.js`

**Files:**
- Modify: `apps/frontend/src/api/traceLab.js` (M1) — append at the end of the file
- Modify: `apps/frontend/test/traceLabCompanyScope.test.mjs` (M1) — two assertions relaxed once, for every later milestone's appended calls
- Test: `apps/frontend/test/traceLabReviewApi.test.mjs`

**Interfaces:**
- Consumes: M1 `traceLabApi` (exported object) and module-level `orgOverride()`; `BASES`, `apiFetch` (`src/api/client.js:20`, `:137`), `setActingOrg` (`client.js:49`); M1's `test/traceLabCompanyScope.test.mjs` (exact key set and one-line-per-call scan, relaxed below).
- Produces: `traceLabApi.annotations(q)`, `saveVerdict(turnId, body)`, `deleteVerdict(turnId, base)`, `annotationHistory(turnId)`, `addComment(body)`, `editComment(lineageId, body)`, `deleteComment(lineageId, base)`, `reviewQueue(q)`, `samplingPlan(q)`, `samplingPreview(q)`, `createBatch(body)`, `batches(q)`, `batch(batchId)`, `withdrawBatch(batchId, reason)`, `manifest()`, `mapData(q)`, `progress(q)`.

- [ ] **Step 1: Write the failing test**

```js
// The M2 Trace Lab calls (api/traceLab.js): each one goes to its route with its method, sends the
// company only as a superadmin's view-as override, and never the build's default company.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' } };
const seen = [];
globalThis.fetch = async (url, init) => {
  seen.push({ url: new URL(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({ ok: true }), text: async () => '{"ok":true}' };
};
const { setActingOrg } = await import('../src/api/client.js');
const { traceLabApi } = await import('../src/api/traceLab.js');

afterEach(() => { seen.length = 0; setActingOrg(null); });

const CALLS = [
  ['annotations', [{ conversation_key: 's:a', turn_id: 't1' }], 'GET', '/annotations'],
  ['saveVerdict', ['t1', { verdict: 'right', base: null }], 'PUT', '/turns/t1/verdict'],
  ['deleteVerdict', ['t1', 'v1'], 'DELETE', '/turns/t1/verdict'],
  ['annotationHistory', ['t1'], 'GET', '/turns/t1/annotation-history'],
  ['addComment', [{ conversation_key: 's:a', note: 'x' }], 'POST', '/comments'],
  ['editComment', ['l1', { note: 'y', base: 'c1' }], 'PUT', '/comments/l1'],
  ['deleteComment', ['l1', 'c2'], 'DELETE', '/comments/l1'],
  ['reviewQueue', [{ limit: 20 }], 'GET', '/queue'],
  ['samplingPlan', [{}], 'GET', '/sampling/plan'],
  ['samplingPreview', [{ strategy: 'uniform' }], 'GET', '/sampling/preview'],
  ['createBatch', [{ strategy: 'uniform' }], 'POST', '/sampling/batches'],
  ['batches', [{ include_withdrawn: true }], 'GET', '/sampling/batches'],
  ['batch', ['b1'], 'GET', '/sampling/batches/b1'],
  ['withdrawBatch', ['b1', 'wrong period'], 'POST', '/sampling/batches/b1/withdraw'],
  ['manifest', [], 'GET', '/sampling/manifest'],
  ['mapData', [{ since: 'a', until: 'b' }], 'GET', '/map'],
  ['progress', [{ block: 10 }], 'GET', '/progress']
];

test('every review call reaches its route with its method', async () => {
  for (const [name, args, method, path] of CALLS) {
    await traceLabApi[name](...args);
    const last = seen[seen.length - 1];
    assert.equal(last.method, method, name);
    assert.equal(last.url.pathname, '/backend/deep-agents/api/trace-lab' + path, name);
    assert.equal(last.url.searchParams.get('organization_id'), null, name);
  }
  assert.equal(seen.find((s) => s.url.pathname.endsWith('/turns/t1/verdict') && s.method === 'DELETE').url.searchParams.get('base'), 'v1');
  assert.deepEqual(seen.find((s) => s.url.pathname.endsWith('/withdraw')).body, { reason: 'wrong period' });
});

test('a superadmin viewing as a company sends that company on every call', async () => {
  setActingOrg('11111111-1111-1111-1111-111111111111');
  for (const [name, args] of CALLS) {
    await traceLabApi[name](...args);
    assert.equal(seen[seen.length - 1].url.searchParams.get('organization_id'), '11111111-1111-1111-1111-111111111111', name);
  }
});
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabReviewApi.test.mjs; cd ../..
```

Expected: `TypeError: traceLabApi[name] is not a function` for `annotations`.

- [ ] **Step 3: Implement**

Append to the end of `apps/frontend/src/api/traceLab.js` (the block imports `BASES`/`apiFetch` under its own names, so it does not clash with M1's imports; it uses M1's `traceLabApi` and `orgOverride`):

```js

// ── M2: open coding and sampling (svc-deepagents routes/trace_lab_review.py) ─────────────────
// Appended by the M2 plan. Every call sends orgOverride() and nothing else for the company: the
// override is only set during a superadmin's view-as, never the build's default company. The reveal
// call arrives with M3's expected-outcome panel.
import { BASES as BASES_M2, apiFetch as apiFetchM2 } from './client.js';

const TL_M2 = BASES_M2.deepAgents;
const tlM2 = (path) => '/api/trace-lab' + path;
const tlM2q = (q) => Object.assign({}, q || {}, orgOverride());

Object.assign(traceLabApi, {
  annotations: (q) => apiFetchM2(TL_M2, tlM2('/annotations'), { query: tlM2q(q), timeoutMs: 15000 }),
  saveVerdict: (turnId, body) => apiFetchM2(TL_M2, tlM2('/turns/' + encodeURIComponent(turnId) + '/verdict'),
    { method: 'PUT', body: body, query: tlM2q(), timeoutMs: 15000 }),
  deleteVerdict: (turnId, base) => apiFetchM2(TL_M2, tlM2('/turns/' + encodeURIComponent(turnId) + '/verdict'),
    { method: 'DELETE', query: tlM2q({ base: base }), timeoutMs: 15000 }),
  annotationHistory: (turnId) => apiFetchM2(TL_M2, tlM2('/turns/' + encodeURIComponent(turnId) + '/annotation-history'),
    { query: tlM2q(), timeoutMs: 15000 }),
  addComment: (body) => apiFetchM2(TL_M2, tlM2('/comments'), { method: 'POST', body: body, query: tlM2q(), timeoutMs: 15000 }),
  editComment: (lineageId, body) => apiFetchM2(TL_M2, tlM2('/comments/' + encodeURIComponent(lineageId)),
    { method: 'PUT', body: body, query: tlM2q(), timeoutMs: 15000 }),
  deleteComment: (lineageId, base) => apiFetchM2(TL_M2, tlM2('/comments/' + encodeURIComponent(lineageId)),
    { method: 'DELETE', query: tlM2q({ base: base }), timeoutMs: 15000 }),
  reviewQueue: (q) => apiFetchM2(TL_M2, tlM2('/queue'), { query: tlM2q(q), timeoutMs: 15000 }),
  samplingPlan: (q) => apiFetchM2(TL_M2, tlM2('/sampling/plan'), { query: tlM2q(q), timeoutMs: 20000 }),
  samplingPreview: (q) => apiFetchM2(TL_M2, tlM2('/sampling/preview'), { query: tlM2q(q), timeoutMs: 45000 }),
  createBatch: (body) => apiFetchM2(TL_M2, tlM2('/sampling/batches'), { method: 'POST', body: body, query: tlM2q(), timeoutMs: 45000 }),
  batches: (q) => apiFetchM2(TL_M2, tlM2('/sampling/batches'), { query: tlM2q(q), timeoutMs: 15000 }),
  batch: (batchId) => apiFetchM2(TL_M2, tlM2('/sampling/batches/' + encodeURIComponent(batchId)), { query: tlM2q(), timeoutMs: 15000 }),
  withdrawBatch: (batchId, reason) => apiFetchM2(TL_M2, tlM2('/sampling/batches/' + encodeURIComponent(batchId) + '/withdraw'),
    { method: 'POST', body: { reason: reason }, query: tlM2q(), timeoutMs: 15000 }),
  manifest: () => apiFetchM2(TL_M2, tlM2('/sampling/manifest'), { query: tlM2q(), timeoutMs: 20000 }),
  mapData: (q) => apiFetchM2(TL_M2, tlM2('/map'), { query: tlM2q(q), timeoutMs: 30000 }),
  progress: (q) => apiFetchM2(TL_M2, tlM2('/progress'), { query: tlM2q(q), timeoutMs: 30000 })
});
```

Relax M1's `apps/frontend/test/traceLabCompanyScope.test.mjs` (find the lines with `grep -n "Object.keys(traceLabApi)\|src.match" apps/frontend/test/traceLabCompanyScope.test.mjs`). `Object.assign` adds 17 keys and 17 `name: (` lines to `traceLabApi`, several of them without `q(` on their first line, so M1's exact key set and its per-line `q(` scan would fail. Its `CALLS` table keeps pinning M1's eight calls; later milestones append to the object and leave this test alone (the scan now covers M1's own object literal only; the M2 block above is pinned by `traceLabReviewApi.test.mjs`, where every call carries `orgOverride()`):

old:
```js
  assert.deepEqual(Object.keys(traceLabApi).sort(), CALLS.map((c) => c[0]).sort());
```
new:
```js
  for (const [name] of CALLS) assert.equal(typeof traceLabApi[name], 'function', name);
```

old:
```js
  const fns = src.match(/^ {2}\w+: \(.*$/gm) || [];
```
new:
```js
  const m1 = src.indexOf('export const traceLabApi = {');
  const fns = src.slice(m1, src.indexOf('\n};', m1)).match(/^ {2}\w+: \(.*$/gm) || [];
```

(M4's later relaxation of the same two lines finds them already done.)

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabReviewApi.test.mjs test/traceLabCompanyScope.test.mjs; cd ../..
```

Expected: all pass (`traceLabCompanyScope.test.mjs` is M1's guard that every call of M1's object appends `orgOverride()` and that the client never reaches for the build's default company; with the two assertions above it no longer fails on the M2 keys).

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `apps/frontend/src/api/traceLab.js`, `apps/frontend/test/traceLabCompanyScope.test.mjs` (M1, two relaxed assertions), `apps/frontend/test/traceLabReviewApi.test.mjs`.

### Task 15: `traceLabAnnotate.js` — drafts, verdicts, comments, anchors and margin placement

**Files:**
- Create: `apps/frontend/src/logic/traceLabAnnotate.js`
- Test: `apps/frontend/test/traceLabAnnotate.test.mjs`

**Interfaces:**
- Consumes: `traceLabApi.*` (Task 14); `isStaleScope` (`src/api/client.js:53`); M1 `labelFor(kind, name)`; M1 state `tlView`, `tlConvKey`, `tlTurnId`, method `tlOpenConversation(key, turnId)`; `state.account.id` (`src/logic/auth.js:49`).
- Produces: the exports listed under Interfaces → Frontend; methods `tlAnnLoad(opts)`, `tlAnnEnsurePoll()`, `tlDraftPatch(turnId, patch)`, `tlCancelDraft(turnId)`, `tlSelectFromBlock(turnId, block, sel)`, `tlSelectQuote(turnId, anchor)`, `tlSetNote(turnId, note)`, `tlPickVerdict(turnId, verdict)`, `tlSaveNote(turnId)`, `tlSaveVerdict(turnId, verdict, opts)`, `tlRetryVerdict(turnId)`, `tlResolveConflict(turnId, keep)`, `tlDeleteVerdict(turnId)`, `tlAnnShortcut(key)`, `tlQueueLoad(batchId)`, `tlSaveAndNext(turnId, verdict)`, `tlSetCommentDraft(text)`, `tlComment()`, `tlCommentEdit(lineageId, note, base)`, `tlCommentDelete(lineageId, base)`; vals `tlAnnError`, `tlAnnSelErr`, `tlReview`, `tlBlock(anchor)`, `tlMarginNotes`, `tlUnplacedNotes`, `tlComments`, `tlTurnChips(turnId)`; state keys `tlAnn`, `tlAnnBlocks`, `tlAnnComments`, `tlAnnCommentsHidden`, `tlAnnKey`, `tlAnnLoading`, `tlAnnLoadedAt`, `tlAnnErr`, `tlAnnSelErr`, `tlDraft`, `tlDraftComment`, `tlDraftCommentErr`, `tlDraftCommentBusy`, `tlAnnQueue`, `tlAnnQueueRemaining`, `tlAnnQueueLoadedAt`.

Behaviour (spec §Review 2.6, §Frontend 2 and 4.2): occurrences are counted with overlapping `indexOf(quote, pos + 1)`, the server's numbering; a selection across blocks says "Select text inside one step."; overlapping highlights share a segment carrying both ids; margin notes stack with an 8 px gap and fold into "+k more notes here" past 240 px of drift; Right saves "no failure observed" at once; Wrong needs a quote and a note before anything is sent; `base` is the head last seen; a failed save keeps the draft with "Not saved: …" and Retry; a 409 keeps the draft and shows the current version with Keep mine (resend on top of it) and Use theirs; polls (every 30 s while Conversations is visible) never touch drafts; `r`/`w` act only in the Conversations tab; Save & next opens the next answer of the same batch that still needs my verdict.

- [ ] **Step 1: Write the failing test**

```js
// traceLabAnnotate — open coding in the Trace Lab (M2): a quote is found where the server finds it,
// overlapping highlights keep both notes, margin notes never pile up, a failed or stale save keeps the
// reviewer's draft, other reviewers stay hidden until you have saved, and Save & next moves on.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { traceLabApi } from '../src/api/traceLab.js';
import {
  NO_FAILURE_NOTE, blockKey, findOccurrence, occurrenceAt, anchorFromSelection, highlightSegments, placeMarginNotes,
  unplacedNotes, traceLabAnnotateMethods, traceLabAnnotateVals, traceLabAnnotateResetPatch
} from '../src/logic/traceLabAnnotate.js';

const real = Object.assign({}, traceLabApi);
afterEach(() => { Object.assign(traceLabApi, real); });

function ctl(state) {
  const c = Object.assign({}, traceLabAnnotateMethods);
  c.state = Object.assign(traceLabAnnotateResetPatch(), { signedIn: true, view: 'traces', tlView: 'conversations', tlConvKey: 's:abc', tlAnnKey: 's:abc',
    tlTurnId: 'turn-1', account: { id: 'me' } }, state || {});
  c.setState = (p) => { c.state = Object.assign({}, c.state, typeof p === 'function' ? p(c.state) : p); };
  c.opened = [];
  c.tlOpenConversation = (key, turnId) => c.opened.push([key, turnId]);
  c.tlAnnEnsurePoll = () => {};
  return c;
}

const ANSWER = 'There were 758 work orders in August, 758 in total.';
const MINE = { id: 'v1', lineage_id: 'v1', version: 1, reviewer_id: 'me', verdict: 'right', note: NO_FAILURE_NOTE, anchor: null, tombstone: false };

test('occurrences count overlapping matches, the way the server numbers them', () => {
  assert.equal(findOccurrence('aaaa', 'aa', 1), 0);
  assert.equal(findOccurrence('aaaa', 'aa', 2), 1);
  assert.equal(findOccurrence('aaaa', 'aa', 3), 2);
  assert.equal(findOccurrence('aaaa', 'aa', 4), -1);
  assert.equal(occurrenceAt('aaaa', 'aa', 1), 2);
  assert.equal(occurrenceAt(ANSWER, '758', 38), 2);
  assert.equal(occurrenceAt(ANSWER, '758', 12), 0);
  assert.equal(blockKey({ span_id: 'root', field: 'answer' }), ':answer');
  assert.equal(blockKey({ span_id: 's1', field: 'output' }), 's1:output');
  assert.equal(blockKey({ span_id: 'g', field: 'answer_after' }), null);
});

test('a selection inside one block becomes an anchor; across blocks it is refused in words', () => {
  const block = { key: ':answer', span_id: null, field: 'answer', text: ANSWER };
  const ok = anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 38, end: 41 });
  assert.deepEqual(ok, { ok: true, anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' } });
  assert.deepEqual(anchorFromSelection(block, { startKey: ':answer', endKey: 's1:output', start: 38, end: 3 }),
    { ok: false, error: 'Select text inside one step.' });
  assert.equal(anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 5, end: 5 }).ok, false);
});

test('overlapping highlights share a segment so both notes keep their highlight', () => {
  const segs = highlightSegments('abcdefghij', [{ id: 'a', start: 2, end: 6 }, { id: 'b', start: 4, end: 8 }]);
  assert.deepEqual(segs.map((x) => [x.text, x.ids]), [['ab', []], ['cd', ['a']], ['ef', ['a', 'b']], ['gh', ['b']], ['ij', []]]);
  assert.deepEqual(highlightSegments('abc', [{ id: 'x', start: -1, end: 2 }]).map((x) => x.ids), [[]]);
});

test('margin notes stack with an 8 px gap, never overlap, and collapse past 240 px into a stub', () => {
  const items = [{ id: 'n1', top: 100, height: 60 }, { id: 'n2', top: 110, height: 60 }, { id: 'n3', top: 120, height: 60 },
    { id: 'n4', top: 130, height: 60 }, { id: 'n5', top: 131, height: 60 }, { id: 'n6', top: 900, height: 60 }];
  const placed = placeMarginNotes(items, { gap: 8, maxDrift: 240 });
  for (let i = 1; i < placed.length; i += 1) assert.ok(placed[i].top >= placed[i - 1].top + placed[i - 1].height + 8);
  assert.deepEqual(placed.map((p) => p.id), ['n1', 'n2', 'n3', 'n4', 'n6']);
  assert.deepEqual(placed[3].more, ['n5']);                        // n5 would land 241 px below its highlight
  assert.equal(placed[4].top, 900);                               // a far note keeps its own place
  const open = placeMarginNotes(items, { expanded: ['n4'] });
  assert.ok(open.some((p) => p.id === 'n5'));
  assert.ok(placed.every((p) => p.top >= 100));                   // nothing piles up at the top
});

test('notes whose quote is not on screen go to the list at the end of the turn', () => {
  const notes = [{ id: 'a', blockKey: ':answer', quote: '758', occurrence: 2 }, { id: 'b', blockKey: 's9:output', quote: 'x', occurrence: 1 },
    { id: 'c', blockKey: ':answer', quote: '758', occurrence: 3 }];
  assert.deepEqual(unplacedNotes(notes, { ':answer': ANSWER }).map((n) => n.id), ['b', 'c']);
});

test('Right saves "no failure observed" with no anchor and the head I last saw as base', async () => {
  const sent = [];
  traceLabApi.saveVerdict = async (turnId, body) => { sent.push([turnId, body]); return { ok: true, annotation: Object.assign({}, MINE, { id: 'v2', version: 2 }) }; };
  traceLabApi.annotations = async () => ({ ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: null });
  const c = ctl({ tlAnn: { 'turn-1': { mine: MINE } } });
  assert.equal(await c.tlPickVerdict('turn-1', 'right'), true);
  assert.deepEqual(sent, [['turn-1', { verdict: 'right', note: null, anchor: null, base: 'v1' }]]);
  assert.equal(c.state.tlAnn['turn-1'].mine.id, 'v2');
});

test('Wrong needs a quote and a note before anything is sent', async () => {
  let calls = 0;
  traceLabApi.saveVerdict = async () => { calls += 1; return { annotation: MINE }; };
  const c = ctl();
  await c.tlPickVerdict('turn-1', 'wrong');
  assert.equal(await c.tlSaveVerdict('turn-1', 'wrong'), false);
  assert.equal(c.state.tlDraft['turn-1'].error, 'Select the text where it first went wrong.');
  c.tlSelectQuote('turn-1', { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' });
  assert.equal(await c.tlSaveVerdict('turn-1', 'wrong'), false);
  assert.equal(c.state.tlDraft['turn-1'].error, 'Say what went wrong first.');
  assert.equal(calls, 0);
});

test('a failed save keeps the draft and offers Retry', async () => {
  traceLabApi.saveVerdict = async () => { throw Object.assign(new Error('Unreachable'), { status: 502 }); };
  const c = ctl();
  c.tlSelectQuote('turn-1', { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' });
  c.tlSetNote('turn-1', 'Counted July, not August.');
  assert.equal(await c.tlSaveNote('turn-1'), false);
  const d = c.state.tlDraft['turn-1'];
  assert.equal(d.note, 'Counted July, not August.');
  assert.equal(d.anchor.quote, '758');
  assert.equal(d.error, 'Not saved: Unreachable');
  const v = traceLabAnnotateVals(c);
  assert.equal(v.tlReview.error, 'Not saved: Unreachable');
  const sent = [];
  traceLabApi.saveVerdict = async (t, body) => { sent.push(body); return { annotation: Object.assign({}, MINE, { verdict: 'wrong' }) }; };
  traceLabApi.annotations = async () => ({ ok: true, turns: {}, comments: [] });
  await v.tlReview.retry();
  assert.equal(sent[0].note, 'Counted July, not August.');
  assert.equal(c.state.tlDraft['turn-1'], undefined);
});

test('a 409 shows the current version and the draft; Keep mine resends on top of it', async () => {
  const theirs = Object.assign({}, MINE, { id: 'v7', version: 7, verdict: 'wrong', note: 'Wrong building', anchor: { quote: 'August', occurrence: 1 } });
  const err = Object.assign(new Error('Changed since you opened it.'), { status: 409, reason: 'stale', body: { detail: { reason: 'stale', current: theirs } } });
  traceLabApi.saveVerdict = async () => { throw err; };
  const c = ctl({ tlAnn: { 'turn-1': { mine: MINE } } });
  c.tlSelectQuote('turn-1', { span_id: null, field: 'answer', quote: '758', occurrence: 1, key: ':answer' });
  c.tlSetNote('turn-1', 'Counted July.');
  await c.tlSaveNote('turn-1');
  const v = traceLabAnnotateVals(c);
  assert.equal(v.tlReview.conflict.message, 'Changed since you opened it.');
  assert.equal(v.tlReview.conflict.theirs.note, 'Wrong building');
  assert.equal(v.tlReview.note, 'Counted July.');
  const sent = [];
  traceLabApi.saveVerdict = async (t, body) => { sent.push(body); return { annotation: Object.assign({}, theirs, { id: 'v8' }) }; };
  traceLabApi.annotations = async () => ({ ok: true, turns: {}, comments: [] });
  await v.tlReview.conflict.keepMine();
  assert.equal(sent[0].base, 'v7');
  assert.equal(sent[0].note, 'Counted July.');
});

test('other reviewers stay hidden until you save, with a count', () => {
  const c = ctl({ tlAnn: { 'turn-1': { mine: null, others: null, others_count: 1, others_hidden: true } } });
  const r = traceLabAnnotateVals(c).tlReview;
  assert.equal(r.blindCopy, '1 other reviewer has judged this answer. Their note stays hidden until you save yours.');
  assert.deepEqual(r.others, []);
  const two = ctl({ tlAnn: { 'turn-1': { mine: null, others: null, others_count: 2, others_hidden: true } } });
  assert.match(traceLabAnnotateVals(two).tlReview.blindCopy, /^2 other reviewers/);
});

test('a poll does not wipe the draft', async () => {
  traceLabApi.annotations = async () => ({ ok: true, turns: { 'turn-1': { mine: MINE, others: [], others_count: 0 } }, comments: [], blocks: { 'turn-1': { ':answer': ANSWER } } });
  const c = ctl();
  c.tlSelectQuote('turn-1', { span_id: null, field: 'answer', quote: '758', occurrence: 1, key: ':answer' });
  c.tlSetNote('turn-1', 'half-written');
  await c.tlAnnLoad();
  assert.equal(c.state.tlDraft['turn-1'].note, 'half-written');
  assert.equal(c.state.tlAnnBlocks['turn-1'][':answer'], ANSWER);
  const blk = traceLabAnnotateVals(c).tlBlock({ span_id: null, field: 'answer' });
  assert.ok(blk.popover);
  assert.deepEqual(blk.segments.filter((x) => x.ids.length).map((x) => [x.text, x.ids]), [['758', ['draft']]]);
});

test('keyboard r and w act on the open answer only in the Conversations tab', async () => {
  const sent = [];
  traceLabApi.saveVerdict = async (t, body) => { sent.push(body.verdict); return { annotation: MINE }; };
  traceLabApi.annotations = async () => ({ ok: true, turns: {}, comments: [] });
  const c = ctl();
  assert.equal(c.tlAnnShortcut('w'), true);
  assert.equal(c.state.tlDraft['turn-1'].focus, true);
  assert.equal(c.tlAnnShortcut('r'), true);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(sent, ['right']);
  const other = ctl({ tlView: 'map' });
  assert.equal(other.tlAnnShortcut('r'), false);
});

test('Save & next opens the next answer of the same batch that still needs my verdict', async () => {
  const asked = [];
  traceLabApi.saveVerdict = async () => ({ annotation: MINE });
  traceLabApi.annotations = async () => ({ ok: true, turns: {}, comments: [] });
  traceLabApi.reviewQueue = async (q) => { asked.push(q.batch_id); return { ok: true, remaining: 2, items: [
    { turn_id: 'turn-1', conversation_key: 's:abc', batch_id: 'b1' }, { turn_id: 'turn-9', conversation_key: 's:xyz', batch_id: 'b1' }] }; };
  const c = ctl({ tlAnn: { 'turn-1': { mine: null, sample_item: { batch_id: 'b1', reason: 'uniform pick' } } } });
  assert.equal(await c.tlSaveAndNext('turn-1', 'right'), true);
  assert.deepEqual(asked, ['b1']);
  assert.deepEqual(c.opened, [['s:xyz', 'turn-9']]);
});

test('comments: a failed comment keeps its text; hidden comments are counted, not shown', async () => {
  traceLabApi.addComment = async () => { throw new Error('Unreachable'); };
  const c = ctl({ tlAnnCommentsHidden: 2 });
  c.tlSetCommentDraft('Same failure as yesterday');
  assert.equal(await c.tlComment(), false);
  const v = traceLabAnnotateVals(c).tlComments;
  assert.equal(v.draft, 'Same failure as yesterday');
  assert.equal(v.error, 'Not saved: Unreachable');
  assert.equal(v.hiddenCopy, '2 comments from other reviewers stay hidden until you have judged every answer here.');
});
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabAnnotate.test.mjs; cd ../..
```

Expected: `ERR_MODULE_NOT_FOUND ... src/logic/traceLabAnnotate.js`.

- [ ] **Step 3: Implement**

Create `apps/frontend/src/logic/traceLabAnnotate.js`:

```js
// traceLabAnnotate — open coding inside the Trace Lab's Conversations view (M2): a reviewer's verdict
// on one answer (Right, or Wrong with a note on the first failure pinned to a verbatim quote),
// comments on the conversation, and the margin notes beside the timeline.
//
// The server is the source of truth for what may be shown: other reviewers' verdicts arrive as null
// until the caller has saved one (blind review), and every annotatable block's text arrives in
// GET /annotations as `blocks`, so a selection is always a substring of the text the server checks.
// Drafts live in tlDraft keyed by turn; polls and refetches never touch them. A failed write keeps
// the draft and offers Retry; a 409 shows the current version beside the draft.
//
// Methods are mixed into HoistraLogic.prototype (`this` is the controller). The pure helpers
// (findOccurrence … placeMarginNotes) are exported for test/traceLabAnnotate.test.mjs.
import { traceLabApi } from '../api/traceLab.js';
import { isStaleScope } from '../api/client.js';
import { labelFor } from './traceLabTimeline.js';

export const NO_FAILURE_NOTE = 'no failure observed';
export const NOTE_MAX = 2000;
export const ANN_POLL_MS = 30000;
const TURN_FIELDS = ['question', 'answer'];
const FIELDS = ['question', 'answer', 'input', 'output'];
const NOTE_HEIGHT = (note) => 46 + 16 * Math.ceil(Math.min(400, (note || '').length) / 34);
const clip = (s, n) => { const t = String(s || ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; };

// ── pure helpers ─────────────────────────────────────────────────────────────────────

// '<span id>:<field>' for a step, ':question' / ':answer' for the turn's own text (server block_key).
export function blockKey(anchor) {
  if (!anchor || FIELDS.indexOf(anchor.field) < 0) return null;
  return (TURN_FIELDS.indexOf(anchor.field) >= 0 ? '' : (anchor.span_id || '')) + ':' + anchor.field;
}

// Start of the n-th (1-based) occurrence, overlapping matches counted — the server's locate_quote.
export function findOccurrence(text, quote, occurrence) {
  if (!quote || !(occurrence >= 1)) return -1;
  let pos = -1;
  for (let i = 0; i < occurrence; i += 1) {
    pos = String(text).indexOf(quote, pos + 1);
    if (pos < 0) return -1;
  }
  return pos;
}

// Which occurrence of `quote` starts at `start` (1-based), or 0 when none does.
export function occurrenceAt(text, quote, start) {
  if (!quote) return 0;
  let pos = -1;
  for (let n = 1; ; n += 1) {
    pos = String(text).indexOf(quote, pos + 1);
    if (pos < 0 || pos > start) return 0;
    if (pos === start) return n;
  }
}

// A browser selection as an anchor, or a sentence saying why not. `block` = {key, span_id, field, text};
// startKey/endKey = the data-tl-block of each end of the selection; start/end = offsets in block.text.
export function anchorFromSelection(block, sel) {
  if (!block || !sel || sel.startKey !== block.key || sel.endKey !== block.key) return { ok: false, error: 'Select text inside one step.' };
  const start = Math.min(sel.start, sel.end);
  const end = Math.max(sel.start, sel.end);
  const quote = String(block.text).slice(start, end);
  if (!quote.trim()) return { ok: false, error: 'Select some text first.' };
  if (quote.length > NOTE_MAX) return { ok: false, error: 'Select at most 2,000 characters.' };
  const occurrence = occurrenceAt(block.text, quote, start);
  if (!occurrence) return { ok: false, error: 'Select text inside one step.' };
  const turnText = TURN_FIELDS.indexOf(block.field) >= 0;
  return { ok: true, anchor: { span_id: turnText ? null : block.span_id, field: block.field, quote: quote, occurrence: occurrence, key: block.key } };
}

// The text split at every highlight boundary. Overlapping highlights share a segment that carries
// both ids, so no note loses its highlight. items: [{id, start, end}] with start >= 0.
export function highlightSegments(text, items) {
  const t = String(text || '');
  const spans = (items || []).filter((i) => i.start >= 0 && i.end > i.start && i.start < t.length);
  const cuts = new Set([0, t.length]);
  spans.forEach((i) => { cuts.add(i.start); cuts.add(Math.min(i.end, t.length)); });
  const points = Array.from(cuts).sort((a, b) => a - b);
  const out = [];
  for (let k = 0; k < points.length - 1; k += 1) {
    const a = points[k]; const b = points[k + 1];
    if (b <= a) continue;
    const ids = spans.filter((i) => i.start <= a && i.end >= b).map((i) => i.id);
    out.push({ start: a, end: b, text: t.slice(a, b), ids: ids });
  }
  return out;
}

// Margin placement: notes sorted by the top of their highlight, stacked with at least `gap` px; a note
// that would land more than `maxDrift` px below its highlight joins a "+k more notes here" stub on the
// last placed note, unless that note's group is expanded. items: [{id, top, height}].
export function placeMarginNotes(items, opts) {
  const o = opts || {};
  const gap = o.gap === undefined ? 8 : o.gap;
  const maxDrift = o.maxDrift === undefined ? 240 : o.maxDrift;
  const expanded = new Set(o.expanded || []);
  const sorted = (items || []).slice().sort((a, b) => (a.top - b.top) || String(a.id).localeCompare(String(b.id)));
  const placed = [];
  let cursor = -Infinity;
  let last = null;
  sorted.forEach((it) => {
    const top = Math.max(it.top, cursor);
    if (last && top - it.top > maxDrift && !expanded.has(last.id)) { last.more.push(it.id); return; }
    const p = { id: it.id, top: top, height: it.height, anchorTop: it.top, more: [] };
    placed.push(p);
    last = p;
    cursor = top + it.height + gap;
  });
  return placed;
}

// Notes whose quote is not in the text on screen (purged span, a payload cut at 16,000 characters,
// changed text): listed at the end of the turn with the copied quote and step name.
export function unplacedNotes(notes, blocks) {
  return (notes || []).filter((n) => {
    const text = blocks ? blocks[n.blockKey] : undefined;
    return text === undefined || text === null || findOccurrence(text, n.quote, n.occurrence) < 0;
  });
}

// The first problem with a Wrong verdict, or ''.
export function wrongProblem(note, anchor) {
  if (!anchor || !anchor.quote) return 'Select the text where it first went wrong.';
  if (!note || !String(note).trim()) return 'Say what went wrong first.';
  if (String(note).length > NOTE_MAX) return 'Keep the note under 2,000 characters.';
  return '';
}

const blankDraft = () => ({ verdict: null, note: '', anchor: null, saving: false, error: '', conflict: null, popover: false, focus: false });

export function traceLabAnnotateResetPatch() {
  return {
    tlAnn: {}, tlAnnBlocks: {}, tlAnnComments: [], tlAnnCommentsHidden: 0, tlAnnKey: null,
    tlAnnLoading: false, tlAnnLoadedAt: null, tlAnnErr: '', tlAnnSelErr: '',
    tlDraft: {}, tlDraftComment: '', tlDraftCommentErr: '', tlDraftCommentBusy: false,
    tlAnnQueue: [], tlAnnQueueRemaining: null, tlAnnQueueLoadedAt: null
  };
}

export const traceLabAnnotateMethods = {
  // Reads the open conversation's verdict states and comments, and the active turn's block texts.
  // Never touches tlDraft. A reply that lands after the conversation changed is dropped.
  async tlAnnLoad(opts) {
    const s = this.state;
    const key = (opts && opts.key) || s.tlConvKey;
    const turnId = (opts && opts.turnId) || s.tlTurnId;
    if (!key) return;
    const seq = (this._tlAnnSeq = (this._tlAnnSeq || 0) + 1);
    this.setState({ tlAnnLoading: true, tlAnnErr: '' });
    try {
      const out = await traceLabApi.annotations({ conversation_key: key, turn_id: turnId || undefined });
      if (seq !== this._tlAnnSeq) return;
      this.setState((p) => ({
        tlAnnLoading: false, tlAnnLoadedAt: Date.now(), tlAnnKey: key,
        tlAnn: Object.assign({}, p.tlAnnKey === key ? p.tlAnn : {}, (out && out.turns) || {}),
        tlAnnBlocks: Object.assign({}, p.tlAnnKey === key ? p.tlAnnBlocks : {}, (out && out.blocks) || {}),
        tlAnnComments: (out && out.comments) || [], tlAnnCommentsHidden: (out && out.comments_hidden) || 0
      }));
    } catch (e) {
      if (isStaleScope(e) || seq !== this._tlAnnSeq) return;
      this.setState({ tlAnnLoading: false, tlAnnErr: (e && e.message) || String(e) });
    }
    this.tlAnnEnsurePoll();
  },
  // Every 30 s while the Conversations tab is visible: the open conversation's annotations again.
  tlAnnEnsurePoll() {
    if (this._tlAnnTimer || typeof setInterval !== 'function') return;
    this._tlAnnTimer = setInterval(() => {
      const s = this.state;
      const shown = typeof document === 'undefined' || document.visibilityState === 'visible';
      if (s.signedIn && s.view === 'traces' && s.tlView === 'conversations' && s.tlConvKey && shown) this.tlAnnLoad();
    }, ANN_POLL_MS);
    if (this._tlAnnTimer && this._tlAnnTimer.unref) this._tlAnnTimer.unref();
  },
  tlDraftPatch(turnId, patch) {
    this.setState((p) => ({ tlDraft: Object.assign({}, p.tlDraft, { [turnId]: Object.assign(blankDraft(), (p.tlDraft || {})[turnId], patch) }) }));
  },
  tlCancelDraft(turnId) {
    this.setState((p) => { const d = Object.assign({}, p.tlDraft); delete d[turnId]; return { tlDraft: d, tlAnnSelErr: '' }; });
  },
  // A selection inside one block of the active turn (MarginNotes.jsx AnnotatedText).
  tlSelectFromBlock(turnId, block, sel) {
    const res = anchorFromSelection(block, sel);
    if (!res.ok) { this.setState({ tlAnnSelErr: res.error }); return; }
    this.setState({ tlAnnSelErr: '' });
    this.tlSelectQuote(turnId, res.anchor);
  },
  tlSelectQuote(turnId, anchor) {
    const d = (this.state.tlDraft || {})[turnId];
    this.tlDraftPatch(turnId, { verdict: 'wrong', anchor: anchor, popover: true, error: '', note: d ? d.note : '' });
  },
  tlSetNote(turnId, note) { this.tlDraftPatch(turnId, { note: note }); },
  // Wrong opens the note (prefilled from my current wrong verdict when I am editing it); Right saves at once.
  tlPickVerdict(turnId, verdict) {
    if (verdict === 'right') return this.tlSaveVerdict(turnId, 'right');
    const s = this.state;
    const d = (s.tlDraft || {})[turnId];
    const mine = ((s.tlAnn || {})[turnId] || {}).mine;
    const prior = !d && mine && !mine.tombstone && mine.verdict === 'wrong' ? { note: mine.note || '', anchor: mine.anchor ? Object.assign({}, mine.anchor) : null } : {};
    this.tlDraftPatch(turnId, Object.assign({ verdict: 'wrong', error: '', focus: true }, prior));
    return Promise.resolve(false);
  },
  tlSaveNote(turnId) { return this.tlSaveVerdict(turnId, 'wrong'); },
  // PUT the verdict with `base` = the head I last saw. true when saved.
  async tlSaveVerdict(turnId, verdict, opts) {
    const s = this.state;
    const d = (s.tlDraft || {})[turnId] || blankDraft();
    const st = (s.tlAnn || {})[turnId] || {};
    const body = verdict === 'right' ? { verdict: 'right', note: null, anchor: null }
      : { verdict: 'wrong', note: d.note || '', anchor: d.anchor ? { span_id: d.anchor.span_id || null, field: d.anchor.field, quote: d.anchor.quote, occurrence: d.anchor.occurrence || 1 } : null };
    if (verdict === 'wrong') {
      const problem = wrongProblem(body.note, body.anchor);
      if (problem) { this.tlDraftPatch(turnId, { verdict: 'wrong', error: problem }); return false; }
    }
    body.base = opts && Object.prototype.hasOwnProperty.call(opts, 'base') ? opts.base : (st.mine ? st.mine.id : null);
    this.tlDraftPatch(turnId, { verdict: verdict, saving: true, error: '', conflict: null, popover: false });
    try {
      const out = await traceLabApi.saveVerdict(turnId, body);
      this.setState((p) => {
        const drafts = Object.assign({}, p.tlDraft); delete drafts[turnId];
        return { tlDraft: drafts, tlAnn: Object.assign({}, p.tlAnn, { [turnId]: Object.assign({}, (p.tlAnn || {})[turnId], { mine: out && out.annotation }) }) };
      });
      this.tlAnnLoad({ turnId: turnId });
      return true;
    } catch (e) {
      if (isStaleScope(e)) return false;
      if (e && e.status === 409 && e.reason === 'stale') {
        const detail = e.body && e.body.detail;
        this.tlDraftPatch(turnId, { saving: false, conflict: { current: (detail && detail.current) || null } });
        return false;
      }
      this.tlDraftPatch(turnId, { saving: false, error: 'Not saved: ' + ((e && e.message) || e) });
      return false;
    }
  },
  tlRetryVerdict(turnId) {
    const d = (this.state.tlDraft || {})[turnId];
    return d && d.verdict ? this.tlSaveVerdict(turnId, d.verdict) : Promise.resolve(false);
  },
  // After a 409: "Keep mine" resends on top of the current version; "Use theirs" drops my draft.
  tlResolveConflict(turnId, keep) {
    const d = (this.state.tlDraft || {})[turnId];
    if (!d || !d.conflict) return Promise.resolve(false);
    const current = d.conflict.current;
    if (keep) return this.tlSaveVerdict(turnId, d.verdict, { base: current ? current.id : null });
    this.setState((p) => {
      const drafts = Object.assign({}, p.tlDraft); delete drafts[turnId];
      return { tlDraft: drafts, tlAnn: Object.assign({}, p.tlAnn, { [turnId]: Object.assign({}, (p.tlAnn || {})[turnId], { mine: current }) }) };
    });
    return Promise.resolve(true);
  },
  async tlDeleteVerdict(turnId) {
    const mine = ((this.state.tlAnn || {})[turnId] || {}).mine;
    if (!mine || mine.tombstone) return false;
    this.tlDraftPatch(turnId, { saving: true, error: '' });
    try {
      const out = await traceLabApi.deleteVerdict(turnId, mine.id);
      this.setState((p) => {
        const drafts = Object.assign({}, p.tlDraft); delete drafts[turnId];
        return { tlDraft: drafts, tlAnn: Object.assign({}, p.tlAnn, { [turnId]: Object.assign({}, (p.tlAnn || {})[turnId], { mine: out && out.annotation }) }) };
      });
      return true;
    } catch (e) {
      if (isStaleScope(e)) return false;
      if (e && e.status === 409 && e.reason === 'stale') {
        const detail = e.body && e.body.detail;
        this.tlDraftPatch(turnId, { saving: false, verdict: mine.verdict, conflict: { current: (detail && detail.current) || null } });
        return false;
      }
      this.tlDraftPatch(turnId, { saving: false, error: 'Not cleared: ' + ((e && e.message) || e) });
      return false;
    }
  },
  // Keyboard (called by the shell's handler, which already ignores inputs and modifiers): r = Right, w = Wrong.
  tlAnnShortcut(key) {
    const tid = this.state.tlTurnId;
    if (!tid || this.state.tlView !== 'conversations') return false;
    if (key === 'r') { this.tlSaveVerdict(tid, 'right'); return true; }
    if (key === 'w') { this.tlPickVerdict(tid, 'wrong'); return true; }
    return false;
  },
  async tlQueueLoad(batchId) {
    try {
      const out = await traceLabApi.reviewQueue({ batch_id: batchId || undefined, limit: 20 });
      this.setState({ tlAnnQueue: (out && out.items) || [], tlAnnQueueRemaining: out ? out.remaining : null, tlAnnQueueLoadedAt: Date.now() });
      return (out && out.items) || [];
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ tlAnnErr: (e && e.message) || String(e) });
      return [];
    }
  },
  // Save, then open the next answer of the same batch that still needs my verdict.
  async tlSaveAndNext(turnId, verdict) {
    const item = ((this.state.tlAnn || {})[turnId] || {}).sample_item;
    const ok = await this.tlSaveVerdict(turnId, verdict);
    if (!ok) return false;
    const next = (await this.tlQueueLoad(item ? item.batch_id : undefined)).find((i) => i.turn_id !== turnId);
    if (next && typeof this.tlOpenConversation === 'function') this.tlOpenConversation(next.conversation_key, next.turn_id);
    return !!next;
  },
  tlSetCommentDraft(text) { this.setState({ tlDraftComment: text, tlDraftCommentErr: '' }); },
  async tlComment() {
    const s = this.state;
    const note = s.tlDraftComment || '';
    if (!note.trim() || !s.tlConvKey) { this.setState({ tlDraftCommentErr: 'Write the comment first.' }); return false; }
    this.setState({ tlDraftCommentBusy: true, tlDraftCommentErr: '' });
    try {
      const out = await traceLabApi.addComment({ conversation_key: s.tlConvKey, turn_id: s.tlTurnId || null, note: note });
      this.setState((p) => ({ tlDraftComment: '', tlDraftCommentBusy: false, tlAnnComments: (p.tlAnnComments || []).concat([out.annotation]) }));
      return true;
    } catch (e) {
      if (isStaleScope(e)) return false;
      this.setState({ tlDraftCommentBusy: false, tlDraftCommentErr: 'Not saved: ' + ((e && e.message) || e) });
      return false;
    }
  },
  async tlCommentEdit(lineageId, note, base) {
    try { await traceLabApi.editComment(lineageId, { note: note, base: base }); } catch (e) {
      if (!isStaleScope(e)) this.setState({ tlDraftCommentErr: (e && e.status === 409 ? 'Changed since you opened it: ' : 'Not saved: ') + ((e && e.message) || e) });
    }
    return this.tlAnnLoad();
  },
  async tlCommentDelete(lineageId, base) {
    try { await traceLabApi.deleteComment(lineageId, base); } catch (e) {
      if (!isStaleScope(e)) this.setState({ tlDraftCommentErr: 'Not removed: ' + ((e && e.message) || e) });
    }
    return this.tlAnnLoad();
  }
};

// ── view model ───────────────────────────────────────────────────────────────────────

function noteView(a, me) {
  if (!a || a.tombstone) return null;
  return { id: a.id, who: me ? 'You' : (a.reviewer_email || 'Another reviewer'), verdict: a.verdict, mark: a.verdict === 'right' ? '✓ Right' : '✗ Wrong',
    note: a.verdict === 'wrong' ? a.note : '', quote: a.anchor ? clip(a.anchor.quote, 80) : '' };
}

// The step a note is pinned to, in plain words (span names go through the timeline's label map, R13).
function stepLabel(anchor) {
  if (!anchor) return '';
  if (anchor.span_name === 'turn' || !anchor.span_name) return TURN_FIELDS.indexOf(anchor.field) >= 0 ? 'the ' + anchor.field : '';
  return labelFor(anchor.span_kind, anchor.span_name);
}

function wrongNotes(st) {
  if (!st) return [];
  const all = [];
  if (st.mine && !st.mine.tombstone && st.mine.verdict === 'wrong' && st.mine.anchor) all.push(Object.assign({ me: true }, st.mine));
  (st.others || []).forEach((o) => { if (o.verdict === 'wrong' && o.anchor) all.push(Object.assign({ me: false }, o)); });
  return all.map((a) => ({ id: a.id, me: a.me, who: a.me ? 'You' : (a.reviewer_email || 'Another reviewer'), note: a.note, quote: a.anchor.quote,
    clipped: clip(a.anchor.quote, 80), occurrence: a.anchor.occurrence || 1, blockKey: blockKey(a.anchor), step: stepLabel(a.anchor),
    height: NOTE_HEIGHT(a.note) }));
}

export function traceLabAnnotateVals(c) {
  const s = c.state;
  const tid = s.tlTurnId || null;
  const st = tid ? (s.tlAnn || {})[tid] || null : null;
  const d = tid ? (s.tlDraft || {})[tid] || null : null;
  const blocks = tid ? (s.tlAnnBlocks || {})[tid] || null : null;
  const mine = st && st.mine && !st.mine.tombstone ? st.mine : null;
  const verdict = d && d.verdict ? d.verdict : (mine ? mine.verdict : null);
  const n = st ? st.others_count || 0 : 0;
  const notes = wrongNotes(st);
  const draftAnchor = d && d.anchor ? d.anchor : null;
  return {
    tlAnnError: s.tlAnnErr || '',
    tlAnnSelErr: s.tlAnnSelErr || '',
    tlReview: tid ? {
      turnId: tid, verdict: verdict, mine: noteView(mine, true),
      note: d ? d.note : (mine && mine.verdict === 'wrong' ? mine.note || '' : ''),
      quote: draftAnchor ? clip(draftAnchor.quote, 80) : (mine && mine.anchor ? clip(mine.anchor.quote, 80) : ''),
      saving: !!(d && d.saving), error: (d && d.error) || '', focusNote: !!(d && d.focus),
      conflict: d && d.conflict ? {
        theirs: noteView(d.conflict.current, true), message: 'Changed since you opened it.',
        keepMine: () => c.tlResolveConflict(tid, true), useTheirs: () => c.tlResolveConflict(tid, false)
      } : null,
      othersHidden: !!(st && st.others_hidden), othersCount: n,
      blindCopy: st && st.others_hidden ? (n === 1 ? '1 other reviewer has judged this answer. Their note stays hidden until you save yours.'
        : n + ' other reviewers have judged this answer. Their notes stay hidden until you save yours.') : '',
      others: st && st.others ? st.others.map((o) => noteView(o, false)).filter(Boolean) : [],
      inBatch: st && st.sample_item ? { reason: st.sample_item.reason, needs: st.reviews_required || 1 } : null,
      pickRight: () => c.tlPickVerdict(tid, 'right'), pickWrong: () => c.tlPickVerdict(tid, 'wrong'),
      setNote: (e) => c.tlSetNote(tid, e && e.target ? e.target.value : e),
      save: () => c.tlSaveVerdict(tid, verdict || 'wrong'), saveNext: () => c.tlSaveAndNext(tid, verdict || 'wrong'),
      retry: () => c.tlRetryVerdict(tid), cancel: () => c.tlCancelDraft(tid),
      clear: mine ? () => c.tlDeleteVerdict(tid) : null
    } : null,
    // One annotatable block of the active turn, or null when the server sent no text for it (the
    // timeline then shows its own rendering, not selectable).
    tlBlock: (anchor) => {
      const key = blockKey(anchor);
      if (!tid || !blocks || !key || blocks[key] === undefined) return null;
      const text = blocks[key];
      const items = notes.filter((x) => x.blockKey === key).map((x) => {
        const start = findOccurrence(text, x.quote, x.occurrence);
        return { id: x.id, start: start, end: start + x.quote.length, kind: x.me ? 'mine' : 'other' };
      });
      if (draftAnchor && draftAnchor.key === key) {
        const start = findOccurrence(text, draftAnchor.quote, draftAnchor.occurrence);
        items.push({ id: 'draft', start: start, end: start + draftAnchor.quote.length, kind: 'draft' });
      }
      const block = { key: key, span_id: anchor.span_id || null, field: anchor.field, text: text };
      return {
        key: key, text: text, segments: highlightSegments(text, items),
        popover: !!(d && d.popover && draftAnchor && draftAnchor.key === key),
        note: d ? d.note : '', setNote: (e) => c.tlSetNote(tid, e && e.target ? e.target.value : e),
        saveNote: () => c.tlSaveNote(tid), cancel: () => c.tlCancelDraft(tid),
        select: (sel) => c.tlSelectFromBlock(tid, block, sel)
      };
    },
    tlMarginNotes: notes,
    tlUnplacedNotes: blocks ? unplacedNotes(notes, blocks) : [],
    tlComments: {
      rows: (s.tlAnnComments || []).map((a) => ({ id: a.lineage_id, base: a.id, who: a.reviewer_email || 'Someone', note: a.note,
        mine: !!(s.account && a.reviewer_id === s.account.id),
        remove: s.account && a.reviewer_id === s.account.id ? () => c.tlCommentDelete(a.lineage_id, a.id) : null })),
      hiddenCopy: s.tlAnnCommentsHidden ? (s.tlAnnCommentsHidden === 1 ? '1 comment from another reviewer stays hidden until you have judged every answer here.'
        : s.tlAnnCommentsHidden + ' comments from other reviewers stay hidden until you have judged every answer here.') : '',
      draft: s.tlDraftComment || '', setDraft: (e) => c.tlSetCommentDraft(e && e.target ? e.target.value : e),
      add: () => c.tlComment(), busy: !!s.tlDraftCommentBusy, error: s.tlDraftCommentErr || ''
    },
    // Turn header chips for the timeline's turn list: my state, and Right ×n (mine plus visible others).
    tlTurnChips: (turnId) => {
      const t = (s.tlAnn || {})[turnId];
      if (!t) return [];
      const chips = [];
      const m = t.mine && !t.mine.tombstone ? t.mine : null;
      const rights = (m && m.verdict === 'right' ? 1 : 0) + (t.others || []).filter((o) => o.verdict === 'right').length;
      if (m) chips.push(m.verdict === 'right' ? '✓ You: right' : '✗ You: wrong');
      if (rights > 1 || (rights === 1 && !(m && m.verdict === 'right'))) chips.push('Right ×' + rights);
      if (t.others_hidden) chips.push(t.others_count + ' hidden');
      if (t.sample_item) chips.push('In a review batch');
      return chips;
    }
  };
}
```

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabAnnotate.test.mjs test/noInternalNames.test.mjs; cd ../..
```

Expected: `14` annotate tests pass; the guard passes.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `apps/frontend/src/logic/traceLabAnnotate.js`, `apps/frontend/test/traceLabAnnotate.test.mjs`.

### Task 16: `VerdictBar.jsx`, `MarginNotes.jsx` and their styles

**Files:**
- Create: `apps/frontend/src/screens/traceLab/VerdictBar.jsx`, `apps/frontend/src/screens/traceLab/MarginNotes.jsx`
- Modify: `apps/frontend/src/styles/tracelab.css` (M1) — append at the end
- Test: `apps/frontend/test/traceLabReviewScreens.test.mjs`

**Interfaces:**
- Consumes: vals from Task 15 (`tlReview`, `tlBlock`, `tlMarginNotes`, `tlUnplacedNotes`, `tlComments`); `placeMarginNotes`; tokens `--marker`, `--marker-tint` (`src/styles/tokens.css:75-76`, dark `:130-131`), `--shadow-sm`, `--color-*`, `--st-*`.
- Produces: default `VerdictBar({ vals })` and `ConversationComments({ vals })`; default `MarginNotes({ vals, rootRef })`, `AnnotatedText({ vals, anchor, children })` (renders `children` when the server sent no text for that block), `UnplacedNotes({ vals })`; CSS classes `tl-hl`, `tl-hl-draft`, `tl-hover`, `tl-note`; token `--tl-focus`. The timeline container must carry `data-tl-timeline` (Task 19).

- [ ] **Step 1: Write the failing test**

```js
// The M2 Trace Lab screens parse, carry their accessible structure (a radio group for the verdict,
// notes linked to highlights, labelled SVG charts), and are scanned by the internal-names guard -
// none of them is on its skip list (R13: only RawPanel.jsx is).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformSync } from 'esbuild';

const SCREENS = {
  'src/screens/traceLab/VerdictBar.jsx': ['role="radiogroup"', 'Keep mine', 'Use theirs', 'Save & next', 'Retry', 'aria-label="Note on the first failure"'],
  'src/screens/traceLab/MarginNotes.jsx': ['data-tl-block', 'data-tl-hl', 'aria-describedby', 'more notes here', 'Notes whose text is not on screen',
    'What went wrong first? (Enter to save)', 'First failure only, in your own words.']
};
const read = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

test('the M2 screens parse and carry their accessible structure', () => {
  for (const [rel, marks] of Object.entries(SCREENS)) {
    const src = read(rel);
    assert.doesNotThrow(() => transformSync(src, { loader: 'jsx', jsx: 'preserve' }), rel);
    marks.forEach((m) => assert.ok(src.includes(m), rel + ' lacks ' + m));
  }
});

test('the M2 screens and logic are scanned by the internal-names guard', () => {
  const guard = read('test/noInternalNames.test.mjs');
  [...Object.keys(SCREENS), 'src/logic/traceLabAnnotate.js', 'src/logic/traceLabSampling.js', 'src/api/traceLab.js']
    .forEach((rel) => assert.ok(!guard.includes("'" + rel + "'"), rel + ' is on the guard\'s skip list'));
});
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabReviewScreens.test.mjs; cd ../..
```

Expected: `ENOENT: no such file or directory, open '.../src/screens/traceLab/VerdictBar.jsx'`.

- [ ] **Step 3: Implement**

`apps/frontend/src/screens/traceLab/VerdictBar.jsx`:

```jsx
// VerdictBar — the reviewer's verdict on the open answer (logic/traceLabAnnotate.js): Right, or Wrong
// with a note on the first failure, Save and Save & next, the blind-review line, a 409 shown beside
// the draft, and Retry after a failed save. ConversationComments is the comment list under the
// conversation header. Pure: everything comes from vals.
import React from 'react';

const BAR = { borderTop: "1px solid var(--color-divider)", padding: "12px 14px", display: "grid", gap: "10px", background: "var(--color-surface)" };
const QUIET = { fontSize: "12px", color: "var(--color-neutral-500)", lineHeight: "1.5" };
const BTN = { fontSize: "12.5px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-text)", cursor: "pointer" };
const PRIMARY = { ...BTN, background: "var(--color-accent)", borderColor: "var(--color-accent)", color: "#fff" };
const BOX = { width: "100%", minHeight: "64px", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "inherit", boxSizing: "border-box" };
const ALERT = { fontSize: "12.5px", color: "var(--st-risk)", display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" };

export default function VerdictBar({ vals }) {
  const r = vals.tlReview;
  const noteRef = React.useRef(null);
  React.useEffect(() => { if (r && r.focusNote && noteRef.current) noteRef.current.focus(); }, [r && r.focusNote, r && r.turnId]);
  if (!r) return null;
  return (
    <section aria-label="Your verdict on this answer" style={BAR}>
      {r.blindCopy ? <div style={QUIET}>{r.blindCopy}</div> : null}
      {r.inBatch ? <div style={QUIET}>{"In a review batch: " + r.inBatch.reason}</div> : null}
      <div role="radiogroup" aria-label="Verdict" style={{ display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "12.5px", fontWeight: "600" }}>{"Verdict"}</span>
        <label style={{ fontSize: "12.5px", display: "flex", gap: "6px", alignItems: "center", cursor: "pointer" }}>
          <input type="radio" name={"tl-verdict-" + r.turnId} checked={r.verdict === "right"} onChange={r.pickRight} disabled={r.saving} />{"✓ Right"}
        </label>
        <label style={{ fontSize: "12.5px", display: "flex", gap: "6px", alignItems: "center", cursor: "pointer" }}>
          <input type="radio" name={"tl-verdict-" + r.turnId} checked={r.verdict === "wrong"} onChange={r.pickWrong} disabled={r.saving} />{"✗ Wrong"}
        </label>
        {r.mine ? <span style={QUIET}>{"Saved: " + r.mine.mark + (r.mine.note ? " — " + r.mine.note : "")}</span> : null}
        {r.clear ? <button type="button" onClick={r.clear} style={{ ...BTN, marginLeft: "auto" }} disabled={r.saving}>{"Clear my verdict"}</button> : null}
      </div>
      {r.verdict === "wrong" ? (
        <div style={{ display: "grid", gap: "8px" }}>
          <div style={QUIET}>{r.quote ? "First failure at: “" + r.quote + "”" : "Select the text where it first went wrong, inside one step of the answer above."}</div>
          <textarea ref={noteRef} aria-label="Note on the first failure" placeholder="What went wrong first? First failure only, in your own words."
            value={r.note} onChange={r.setNote} style={BOX} maxLength={2000} />
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            <button type="button" onClick={r.save} style={PRIMARY} disabled={r.saving}>{r.saving ? "Saving…" : "Save"}</button>
            <button type="button" onClick={r.saveNext} style={BTN} disabled={r.saving}>{"Save & next"}</button>
            <button type="button" onClick={r.cancel} style={BTN} disabled={r.saving}>{"Cancel"}</button>
          </div>
        </div>
      ) : null}
      {r.error ? (
        <div role="alert" style={ALERT}>{r.error}<button type="button" onClick={r.retry} style={BTN}>{"Retry"}</button></div>
      ) : null}
      {r.conflict ? (
        <div role="alert" style={{ ...QUIET, border: "1px solid var(--st-warn)", borderRadius: "8px", padding: "10px 12px", color: "var(--color-text)" }}>
          <div style={{ fontWeight: "600" }}>{r.conflict.message}</div>
          <div>{r.conflict.theirs ? "Now saved: " + r.conflict.theirs.mark + (r.conflict.theirs.note ? " — " + r.conflict.theirs.note : "") : "Your verdict was cleared in another tab."}</div>
          <div>{"Yours: " + (r.verdict === "right" ? "✓ Right" : "✗ Wrong") + (r.note ? " — " + r.note : "")}</div>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button type="button" onClick={r.conflict.keepMine} style={PRIMARY}>{"Keep mine"}</button>
            <button type="button" onClick={r.conflict.useTheirs} style={BTN}>{"Use theirs"}</button>
          </div>
        </div>
      ) : null}
      {r.others.length ? (
        <div style={{ display: "grid", gap: "6px" }}>
          <div style={{ fontSize: "12px", fontWeight: "600" }}>{"Other reviewers"}</div>
          {r.others.map((o) => (
            <div key={o.id} style={QUIET}>{o.who + ": " + o.mark + (o.note ? " — " + o.note : "") + (o.quote ? " (“" + o.quote + "”)" : "")}</div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ConversationComments({ vals }) {
  const cm = vals.tlComments;
  if (!cm) return null;
  return (
    <section aria-label="Comments on this conversation" style={{ display: "grid", gap: "8px", padding: "10px 14px", borderBottom: "1px solid var(--color-divider)" }}>
      <div style={{ fontSize: "12px", fontWeight: "600" }}>{"Comments"}</div>
      {cm.rows.length ? cm.rows.map((c) => (
        <div key={c.id} style={{ ...QUIET, display: "flex", gap: "8px", alignItems: "baseline" }}>
          <span style={{ color: "var(--color-text)" }}>{c.who + ": "}</span><span style={{ flex: "1" }}>{c.note}</span>
          {c.remove ? <button type="button" onClick={c.remove} style={{ ...BTN, padding: "2px 8px", fontSize: "11.5px" }} aria-label="Remove my comment">{"Remove"}</button> : null}
        </div>
      )) : <div style={QUIET}>{"No comments yet."}</div>}
      {cm.hiddenCopy ? <div style={QUIET}>{cm.hiddenCopy}</div> : null}
      <div style={{ display: "flex", gap: "8px" }}>
        <input className="input" aria-label="Add a comment" placeholder="Add a comment for other reviewers…" value={cm.draft} onChange={cm.setDraft}
          style={{ ...BOX, minHeight: "0", flex: "1" }} maxLength={2000} />
        <button type="button" onClick={cm.add} style={BTN} disabled={cm.busy}>{cm.busy ? "Saving…" : "Comment"}</button>
      </div>
      {cm.error ? <div role="alert" style={ALERT}>{cm.error}</div> : null}
    </section>
  );
}
```

`apps/frontend/src/screens/traceLab/MarginNotes.jsx`:

```jsx
// MarginNotes — the annotation layer on the Conversations timeline (logic/traceLabAnnotate.js).
//
// AnnotatedText renders one annotatable block as the server's own text, pre-wrapped, with yellow
// highlights; selecting inside it opens the note popover. MarginNotes is the 240 px column beside
// the timeline: one note per wrong verdict, aligned to its highlight's top, stacked with an 8 px gap,
// and folded into "+k more notes here" rather than piling up. UnplacedNotes lists notes whose text is
// no longer on screen. Anchors are a step id plus a quote and its occurrence - never a DOM offset.
import React from 'react';
import { placeMarginNotes } from '../../logic/traceLabAnnotate.js';

const PRE = { margin: "0", whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "inherit", fontSize: "12.5px", lineHeight: "1.55", color: "var(--color-text)" };
const QUIET = { fontSize: "11.5px", color: "var(--color-neutral-500)" };
const POP = { marginTop: "8px", padding: "10px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "grid", gap: "6px" };

// Character offset of (node, offset) inside root's text, counting text nodes in document order.
function offsetWithin(root, node, offset) {
  if (!root || !node) return -1;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let total = 0;
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    if (t === node) return total + offset;
    total += t.nodeValue.length;
  }
  return node === root ? total : -1;
}

function blockOf(node) {
  let el = node && node.nodeType === 3 ? node.parentNode : node;
  while (el && !(el.getAttribute && el.getAttribute("data-tl-block"))) el = el.parentNode;
  return el || null;
}

function outline(root, ids, on) {
  if (!root) return;
  ids.forEach((id) => root.querySelectorAll('[data-tl-hl~="' + id + '"], [data-tl-note="' + id + '"]').forEach((el) => el.classList.toggle("tl-hover", on)));
}

export function AnnotatedText({ vals, anchor, children }) {
  const ref = React.useRef(null);
  const b = vals.tlBlock(anchor);
  if (!b) return children || null;
  const onMouseUp = () => {
    const sel = typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const sEl = blockOf(range.startContainer);
    const eEl = blockOf(range.endContainer);
    b.select({
      startKey: sEl ? sEl.getAttribute("data-tl-block") : null, endKey: eEl ? eEl.getAttribute("data-tl-block") : null,
      start: sEl === ref.current ? offsetWithin(ref.current, range.startContainer, range.startOffset) : -1,
      end: eEl === ref.current ? offsetWithin(ref.current, range.endContainer, range.endOffset) : -1
    });
  };
  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); b.saveNote(); }
    else if (e.key === "Escape") { e.preventDefault(); b.cancel(); }
  };
  return (
    <div>
      <pre ref={ref} data-tl-block={b.key} onMouseUp={onMouseUp} style={PRE}>
        {b.segments.map((seg) => seg.ids.length ? (
          <mark key={seg.start} data-tl-hl={seg.ids.join(" ")} className={"tl-hl" + (seg.ids.indexOf("draft") >= 0 ? " tl-hl-draft" : "")}
            aria-describedby={seg.ids.filter((i) => i !== "draft").map((i) => "tl-note-" + i).join(" ") || undefined}
            onMouseEnter={() => outline(ref.current && ref.current.closest("[data-tl-timeline]"), seg.ids, true)}
            onMouseLeave={() => outline(ref.current && ref.current.closest("[data-tl-timeline]"), seg.ids, false)}>{seg.text}</mark>
        ) : <span key={seg.start}>{seg.text}</span>)}
      </pre>
      {b.popover ? (
        <div style={POP} role="dialog" aria-label="Note on the first failure">
          <textarea autoFocus aria-label="What went wrong first?" placeholder="What went wrong first? (Enter to save)" value={b.note} onChange={b.setNote}
            onKeyDown={onKey} maxLength={2000} style={{ ...PRE, minHeight: "54px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)" }} />
          <div style={QUIET}>{"First failure only, in your own words. Enter saves, Shift+Enter adds a line, Escape cancels."}</div>
        </div>
      ) : null}
    </div>
  );
}

export default function MarginNotes({ vals, rootRef }) {
  const notes = vals.tlMarginNotes || [];
  const [tops, setTops] = React.useState({});
  const [expanded, setExpanded] = React.useState([]);
  React.useLayoutEffect(() => {
    const root = rootRef && rootRef.current;
    if (!root) return;
    const base = root.getBoundingClientRect().top;
    const next = {};
    notes.forEach((n) => {
      const el = root.querySelector('[data-tl-hl~="' + n.id + '"]');
      if (el) next[n.id] = Math.round(el.getBoundingClientRect().top - base);
    });
    if (JSON.stringify(next) !== JSON.stringify(tops)) setTops(next);
  });
  const placed = placeMarginNotes(notes.filter((n) => tops[n.id] !== undefined).map((n) => ({ id: n.id, top: tops[n.id], height: n.height })),
    { gap: 8, maxDrift: 240, expanded: expanded });
  const byId = {};
  notes.forEach((n) => { byId[n.id] = n; });
  const root = rootRef && rootRef.current;
  return (
    <aside aria-label="Notes on this answer" style={{ position: "relative", width: "240px", flex: "0 0 240px" }}>
      {placed.map((p) => {
        const n = byId[p.id];
        return (
          <div key={p.id} id={"tl-note-" + p.id} data-tl-note={p.id} className="tl-note" style={{ position: "absolute", top: p.top + "px", left: "0", right: "0", minHeight: p.height + "px" }}
            onMouseEnter={() => outline(root, [p.id], true)} onMouseLeave={() => outline(root, [p.id], false)}>
            <div style={{ fontSize: "12px", lineHeight: "1.45", color: "var(--color-text)" }}>{"✎ " + n.note}</div>
            <div style={QUIET}>{"“" + n.clipped + "” — " + n.who}</div>
            {p.more.length ? (
              <button type="button" onClick={() => setExpanded(expanded.concat([p.id]))} style={{ ...QUIET, border: "none", background: "transparent", padding: "4px 0", cursor: "pointer", textDecoration: "underline" }}>
                {"+" + p.more.length + (p.more.length === 1 ? " more note here" : " more notes here")}
              </button>
            ) : null}
          </div>
        );
      })}
    </aside>
  );
}

export function UnplacedNotes({ vals }) {
  const list = vals.tlUnplacedNotes || [];
  if (!list.length) return null;
  return (
    <section aria-label="Notes whose text is not on screen" style={{ padding: "10px 14px", borderTop: "1px solid var(--color-divider)", display: "grid", gap: "6px" }}>
      <div style={{ fontSize: "12px", fontWeight: "600" }}>{"Notes whose text is not on screen"}</div>
      {list.map((n) => (
        <div key={n.id} style={QUIET}>{"✎ " + n.note + " — “" + n.clipped + "”" + (n.step ? " (step: " + n.step + ")" : "") + " — " + n.who}</div>
      ))}
    </section>
  );
}
```

Append to the end of `apps/frontend/src/styles/tracelab.css`:

```css

/* ── M2: open coding (highlights, margin notes) and the Map / Progress charts ───────────── */
/* One focus colour for charts (categorical slot 1, light and dark steps); the map brings one
   group forward at a time instead of painting eight hues on a scatter. */
:root { --tl-focus: #2a78d6; }
:root[data-theme="dark"] { --tl-focus: #3987e5; }
.tl-hl { background: var(--marker-tint); color: inherit; border-bottom: 2px solid var(--marker); border-radius: 2px; padding: 0; }
.tl-hl-draft { background: transparent; border-bottom: 0; outline: 1px dashed var(--marker); outline-offset: 1px; }
.tl-hl.tl-hover { outline: 2px solid var(--marker); outline-offset: 1px; }
.tl-note { box-sizing: border-box; padding: 8px 10px; border-left: 3px solid var(--marker); background: var(--color-surface);
  border-radius: 0 6px 6px 0; box-shadow: var(--shadow-sm); transition: box-shadow 120ms ease; }
.tl-note.tl-hover { box-shadow: 0 0 0 2px var(--marker); }
@media (prefers-reduced-motion: reduce) { .tl-note { transition: none; } }
```

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabReviewScreens.test.mjs test/noInternalNames.test.mjs; cd ../..
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `VerdictBar.jsx`, `MarginNotes.jsx`, `src/styles/tracelab.css`, `test/traceLabReviewScreens.test.mjs`.

### Task 17: `traceLabSampling.js` — plan, batches, map and progress view models

**Files:**
- Create: `apps/frontend/src/logic/traceLabSampling.js`
- Test: `apps/frontend/test/traceLabSampling.test.mjs`

**Interfaces:**
- Consumes: `traceLabApi.{samplingPlan, batches, samplingPreview, createBatch, withdrawBatch, manifest, mapData, progress}` (Task 14); `isStaleScope`; M1 `labelFor`, `tlSetView`, `tlOpenConversation`.
- Produces: the exports listed under Interfaces → Frontend; methods `tlSamplingWindow()`, `tlSetSamplingRange(key)`, `tlLoadPlan()`, `tlLoadBatches()`, `tlSetBatchForm(patch)`, `tlNextBatch()`, `tlPreviewBatch()`, `tlCreateBatch()`, `tlWithdrawBatch(batchId, reason)`, `tlLoadManifest()`, `tlDownloadManifest()`, `tlLoadMap()`, `tlFocusCluster(id)`, `tlOpenMapNode(node)`, `tlLoadProgress()`, `tlSetProgressBlock(n)`; vals `tlSmp`, `tlMapView`, `tlProgressView`; state keys `tlSmpRange`, `tlPlan`, `tlPlanErr`, `tlPlanLoadedAt`, `tlBatches`, `tlBatchesErr`, `tlBatchesLoadedAt`, `tlBatchForm`, `tlBatchPreview`, `tlBatchBusy`, `tlBatchErr`, `tlBatchNotice`, `tlManifest`, `tlManifestOpen`, `tlManifestErr`, `tlMap`, `tlMapErr`, `tlMapLoading`, `tlMapLoadedAt`, `tlMapFocus`, `tlProgress`, `tlProgressErr`, `tlProgressLoading`, `tlProgressLoadedAt`, `tlProgressBlock`.

Behaviour (spec §Frontend 4.5, 4.6; §Review 3.4–3.6, 4): the plan view names steps in plain words and states a shortfall; "Next batch" fills the plan's next step; only Channel / Where it was routed / Asker's role / Page are offered as dimensions; uniform batches are tagged "counts toward rates", the others "for discovery only"; Create draws exactly what the preview showed (same request, window and seed); the map's empty state reads "Not enough answers in this window to draw a map."; a group is brought forward on click; progress shows "—" before any read and labels the wrong rate "From fair (uniform) batches only"; the saturation line says it is not available until failure modes exist.

- [ ] **Step 1: Write the failing test**

```js
// traceLabSampling — the Map and Progress tabs (M2): the HW4 plan view, batch forms that never offer
// an outcome as a dimension, preview then create, the map's empty state and projection, and progress
// copy that shows "—" for missing data and never calls a sample fraction a rate.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { traceLabApi } from '../src/api/traceLab.js';
import {
  DIMENSION_CHOICES, STEP_DEFAULTS, batchRequest, mapGeometry, curveGeometry, saturationText, valueLabel,
  traceLabSamplingMethods, traceLabSamplingVals, traceLabSamplingResetPatch
} from '../src/logic/traceLabSampling.js';

const real = Object.assign({}, traceLabApi);
afterEach(() => { Object.assign(traceLabApi, real); });

function ctl(state) {
  const c = Object.assign({}, traceLabSamplingMethods);
  c.state = Object.assign(traceLabSamplingResetPatch(), { signedIn: true, view: 'traces', tlView: 'map' }, state || {});
  c.setState = (p) => { c.state = Object.assign({}, c.state, typeof p === 'function' ? p(c.state) : p); };
  c.calls = [];
  c.tlSetView = (v) => c.calls.push(['view', v]);
  c.tlOpenConversation = (k, t) => c.calls.push(['open', k, t]);
  return c;
}

const PLAN = { ok: true, next_step: 'cluster_representatives', distinct_sampled: 24, target_total: 100, pool_size: 40, steps: [
  { step: 'initial_uniform', strategy: 'uniform', target: 15, selected: 15, reviewed: 15, batch_ids: ['b1'] },
  { step: 'cluster_representatives', strategy: 'diversity', target: 15, selected: 9, reviewed: 2, batch_ids: ['b2'] },
  { step: 'dimension', strategy: 'dimension', target: 30, selected: 0, reviewed: 0, batch_ids: [] },
  { step: 'depth_search', strategy: 'enrich', target: 25, selected: 0, reviewed: 0, batch_ids: [] },
  { step: 'final_uniform', strategy: 'uniform', target: 15, selected: 0, reviewed: 0, batch_ids: [] }] };

test('the plan view names each step, marks the next one and states a shortfall', () => {
  const v = traceLabSamplingVals(ctl({ tlPlan: PLAN })).tlSmp.plan;
  assert.deepEqual(v.steps.map((s) => [s.label, s.done, s.next, s.fair]), [
    ['First fair batch', true, false, true], ['One from each group', false, true, false], ['Split by a dimension', false, false, false],
    ['More like the wrong answers', false, false, false], ['Final fair batch', false, false, true]]);
  assert.equal(v.sampled, '24 of 100 answers sampled');
  assert.equal(v.shortfall, 'Only 40 unsampled answers in this period; the plan cannot reach 100.');
});

test('a batch can be split only by channel, routing, role or page - never by an outcome', () => {
  assert.deepEqual(DIMENSION_CHOICES.map((d) => d.key), ['channel', 'route_target', 'asker_role', 'page']);
  const keys = DIMENSION_CHOICES.map((d) => d.key);
  ['feedback_rating', 'ok', 'verdict', 'error', 'thumbs'].forEach((w) => assert.ok(keys.indexOf(w) < 0, w));
});

test('Next batch fills the plan step with its strategy and size; uniform is the only fair kind', () => {
  const c = ctl({ tlPlan: PLAN });
  c.tlNextBatch();
  assert.equal(c.state.tlBatchForm.plan_step, 'cluster_representatives');
  assert.equal(c.state.tlBatchForm.strategy, 'diversity');
  assert.equal(c.state.tlBatchForm.random_share, 0);
  assert.equal(traceLabSamplingVals(c).tlSmp.form.fairNote, 'For discovery only: this batch never counts toward rates.');
  c.tlSetBatchForm({ plan_step: 'final_uniform' });
  assert.equal(traceLabSamplingVals(c).tlSmp.form.fairNote, 'Counts toward rates.');
  assert.deepEqual(STEP_DEFAULTS.dimension, { strategy: 'dimension', k: 30 });
});

test('preview then create sends the same request, with the window and seed', async () => {
  const sent = [];
  traceLabApi.samplingPreview = async (q) => { sent.push(['preview', q]); return { ok: true, pool_size: 80, excluded_sampled: 15, truncated: false,
    picks: [{ turn_id: 'turn-1', reason: 'uniform pick' }] }; };
  traceLabApi.createBatch = async (b) => { sent.push(['create', b]); return { ok: true, batch: { id: 'b9', selected: 15, seed: 7 }, items: [] }; };
  traceLabApi.samplingPlan = async () => PLAN;
  traceLabApi.batches = async () => ({ ok: true, batches: [] });
  traceLabApi.mapData = async () => ({ ok: true, too_few: true, nodes: [], clusters: [] });
  const c = ctl({ tlBatchForm: { plan_step: 'initial_uniform', strategy: 'uniform', k: 15, seed: 7, dimension: '', reviews_required: 1, random_share: '' } });
  await c.tlPreviewBatch();
  const pv = traceLabSamplingVals(c).tlSmp.preview;
  assert.deepEqual([pv.poolSize, pv.excluded, pv.picks[0].reason], [80, 15, 'uniform pick']);
  assert.equal(await c.tlCreateBatch(), true);
  assert.deepEqual(sent[0][1], sent[1][1]);
  assert.equal(sent[1][1].seed, 7);
  assert.ok(sent[1][1].since < sent[1][1].until);
  assert.equal(c.state.tlBatchNotice, 'Drew 15 answers (seed 7). They are in the review queue.');
  assert.deepEqual(batchRequest({ strategy: 'dimension', plan_step: 'dimension', k: 30 }, { since: 'a', until: 'b' }).dimension, 'route_target');
});

test('a failed create keeps the form and shows the reason', async () => {
  traceLabApi.createBatch = async () => { throw new Error('No answers are left to draw in that period.'); };
  const c = ctl({ tlBatchForm: { plan_step: 'extra', strategy: 'outlier', k: 5 } });
  assert.equal(await c.tlCreateBatch(), false);
  assert.equal(c.state.tlBatchErr, 'No answers are left to draw in that period.');
  assert.equal(c.state.tlBatchForm.strategy, 'outlier');
});

test('batches are tagged by whether they count toward rates', () => {
  const v = traceLabSamplingVals(ctl({ tlBatches: [{ id: 'b1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15 },
    { id: 'b2', plan_step: 'dimension', strategy: 'dimension', counts_for_prevalence: false, selected: 30, requested: 30, withdrawn_at: '2026-10-14T10:00:00Z' }] })).tlSmp;
  assert.deepEqual(v.batches.map((b) => [b.tag, b.withdrawn, !!b.withdraw]), [['counts toward rates', false, true], ['for discovery only', true, false]]);
});

test('the map has an empty state, a caution line, and a stable projection into the box', () => {
  const few = traceLabSamplingVals(ctl({ tlMap: { ok: true, too_few: true, nodes: [], clusters: [] }, tlMapLoadedAt: 1 })).tlMapView;
  assert.equal(few.tooFew, true);
  assert.equal(few.tooFewCopy, 'Not enough answers in this window to draw a map.');
  assert.equal(few.caution, 'A cluster is a prompt to read answers, never evidence that they failed.');
  const geo = mapGeometry([{ x: -2, y: 0 }, { x: 2, y: 4 }, { x: 0, y: 2 }], { w: 100, h: 100, pad: 10 });
  assert.deepEqual(geo.map((n) => [n.px, n.py]), [[10, 90], [90, 10], [50, 50]]);
  const m = { ok: true, too_few: false, explained_variance: [0.612, 0.18], feature_names: [{ key: 'tokens', label: 'Tokens' }],
    nodes: [{ turn_id: 't1', conversation_key: 's:a', x: 0, y: 0, cluster: 1, reviewed: true, batch_id: 'b1', question: 'Boilers?' },
      { turn_id: 't2', conversation_key: 's:b', x: 1, y: 1, cluster: 2, reviewed: false, batch_id: null, question: 'Lifts?' }],
    clusters: [{ id: 1, size: 1, reviewed: 1, centroid: { tokens: 900 } }, { id: 2, size: 1, reviewed: 0, centroid: { tokens: 1200 } }] };
  const c = ctl({ tlMap: m, tlMapLoadedAt: 1, tlMapFocus: 2 });
  const v = traceLabSamplingVals(c).tlMapView;
  assert.equal(v.explained, 'The two axes carry 61% and 18% of the spread between answers.');
  assert.deepEqual(v.nodes.map((n) => [n.dim, n.on, n.sampled]), [[true, false, true], [false, true, false]]);
  assert.equal(v.nodes[0].title, 'Boilers? · group 1 · reviewed · in a review batch');
  assert.equal(v.clusters[1].label, 'Group 2 · 1 answers · 0 reviewed');
  v.nodes[1].open();
  assert.deepEqual(c.calls, [['view', 'conversations'], ['open', 's:b', 't2']]);
});

test('progress shows "—" until it has data, and labels the fair-sample rate', () => {
  const empty = traceLabSamplingVals(ctl()).tlProgressView;
  assert.deepEqual(empty.tiles.map((t) => t.value), ['—', '—', '—', '—']);
  assert.equal(empty.agreement, '—');
  const prg = { ok: true, open_batches: 2, batches: [], block: 10,
    open_coding: { reviewed: 12, wrong: 3, right: 9, missing: 37, missing_turns: ['t1'], missing_more: 12, reviewers: [{ email: 'a@x', reviewed: 12 }] },
    fair_sample: { label: 'From fair (uniform) batches only', rate: 0.25, ci_low: 0.0889, ci_high: 0.5323, wrong: 3, right: 9, disputed: 1 },
    agreement: { reviewers: ['a', 'b'], shared: 8, percent: 0.875, kappa: null, note: 'fewer than 20 shared answers' },
    discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3, partial: false }, { block: 2, reviews_through: 12, wrong: 0, partial: true }] },
    saturation: { available: false, hint: false }, coverage: null };
  const v = traceLabSamplingVals(ctl({ tlProgress: prg, tlProgressLoadedAt: 1 })).tlProgressView;
  assert.deepEqual(v.tiles.map((t) => t.value), ['12', '3', '37', '2']);
  assert.equal(v.fair.label, 'From fair (uniform) batches only');
  assert.equal(v.fair.text, '25% wrong (95% interval 9%–53%)');
  assert.equal(v.agreement, '88% agreement on 8 shared answers · fewer than 20 shared answers');
  assert.equal(v.missing.more, '…and 12 more');
  assert.equal(v.curve.dots.length, 2);
  assert.equal(v.saturation, 'Saturation: — (shown once failure modes are named)');
});

test('saturation copy follows the server hint', () => {
  assert.equal(saturationText({ available: true, hint: true, kind: 'quiet', reviews_without_new: 20 }), 'No new failure mode in the last 20 reviews; consider moving to counting.');
  assert.equal(saturationText({ available: true, hint: true, kind: 'final_batch', final_new_modes: 3 }),
    'The final fair batch produced 3 new failure modes. Review another batch before treating the list as stable.');
  assert.equal(saturationText({ available: true, hint: false }), 'New failure modes are still appearing.');
});

test('the curve scales wrong counts into the chart box', () => {
  const g = curveGeometry([{ wrong: 0, reviews_through: 10 }, { wrong: 4, reviews_through: 20 }], { w: 100, h: 100, pad: 10 });
  assert.equal(g.path, 'M10 90 L90 10');
  assert.equal(g.max, 4);
  assert.deepEqual(curveGeometry([]), { path: '', dots: [], max: 0 });
});

test('dimension values read in plain words', () => {
  assert.equal(valueLabel('route_target', '(none)'), 'Not recorded');
  assert.equal(valueLabel('route_target', 'multi'), 'Several areas');
  assert.equal(valueLabel('asker_role', 'superadmin'), 'Platform admin');
  assert.equal(valueLabel('page', 'cc'), 'cc');
});
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabSampling.test.mjs; cd ../..
```

Expected: `ERR_MODULE_NOT_FOUND ... src/logic/traceLabSampling.js`.

- [ ] **Step 3: Implement**

Create `apps/frontend/src/logic/traceLabSampling.js`:

```js
// traceLabSampling — the Trace Lab's Map and Progress tabs (M2): the HW4 batch plan, previewing and
// drawing review batches, the manifest (every answer's reason and seed), the map of answers, and the
// open-coding progress metrics. Only fair (uniform) batches count toward rates; the others are for
// discovery only, and the copy says so everywhere.
//
// Methods are mixed into HoistraLogic.prototype (`this` is the controller). The view models and the
// pure geometry (mapGeometry, curveGeometry) are tested in test/traceLabSampling.test.mjs.
import { traceLabApi } from '../api/traceLab.js';
import { isStaleScope } from '../api/client.js';
import { labelFor } from './traceLabTimeline.js';

export const SAMPLING_RANGES = [{ key: '7d', label: '7 days', days: 7 }, { key: '30d', label: '30 days', days: 30 }, { key: '60d', label: '60 days', days: 60 }];
export const PLAN_STEP_LABELS = { initial_uniform: 'First fair batch', cluster_representatives: 'One from each group', dimension: 'Split by a dimension',
  depth_search: 'More like the wrong answers', final_uniform: 'Final fair batch', extra: 'Extra batch' };
export const STRATEGY_LABELS = { uniform: 'Fair (uniform)', diversity: 'Spread across groups', dimension: 'Split by a dimension',
  outlier: 'Unusual answers', enrich: 'Similar to wrong answers' };
// What a plan step draws by default (the server's HW4_PLAN; `extra` is another fair batch).
export const STEP_DEFAULTS = { initial_uniform: { strategy: 'uniform', k: 15 }, cluster_representatives: { strategy: 'diversity', k: 15, random_share: 0 },
  dimension: { strategy: 'dimension', k: 30 }, depth_search: { strategy: 'enrich', k: 25 }, final_uniform: { strategy: 'uniform', k: 15 },
  extra: { strategy: 'uniform', k: 15 } };
// A batch may be split by these only. Outcomes (ratings, errors, verdicts) are never offered.
export const DIMENSION_CHOICES = [{ key: 'channel', label: 'Channel' }, { key: 'route_target', label: 'Where it was routed' },
  { key: 'asker_role', label: "Asker's role" }, { key: 'page', label: 'Page' }];
const VALUE_LABELS = { '(none)': 'Not recorded', '(other)': 'Other', unrouted: 'Not routed', multi: 'Several areas' };
const MAP_W = 640; const MAP_H = 400; const MAP_PAD = 24;
const CURVE_W = 560; const CURVE_H = 160; const CURVE_PAD = 28;

const pct = (v) => (v === null || v === undefined ? '—' : Math.round(Number(v) * 100) + '%');
const num = (v) => (v === null || v === undefined ? '—' : String(v));

export function valueLabel(dimension, value) {
  if (VALUE_LABELS[value]) return VALUE_LABELS[value];
  if (dimension === 'route_target') return labelFor('agent', value);
  if (dimension === 'asker_role') return { user: 'User', admin: 'Admin', superadmin: 'Platform admin' }[value] || value;
  return String(value);
}

function windowFor(key, now) {
  const r = SAMPLING_RANGES.find((x) => x.key === key) || SAMPLING_RANGES[1];
  const t = now === undefined ? Date.now() : now;
  return { since: new Date(t - r.days * 86400000).toISOString(), until: new Date(t + 3600000).toISOString() };
}

export function defaultBatchForm() {
  return { plan_step: 'initial_uniform', strategy: 'uniform', k: 15, seed: 7, dimension: '', reviews_required: 1, random_share: '' };
}

// The request a form sends: defaults resolved, empty optionals left out.
export function batchRequest(form, win) {
  const f = Object.assign(defaultBatchForm(), form || {});
  const out = { plan_step: f.plan_step, strategy: f.strategy, k: Number(f.k) || 15, seed: Number(f.seed) || 0, since: win.since, until: win.until,
    reviews_required: Number(f.reviews_required) || 1 };
  if (f.strategy === 'dimension') out.dimension = f.dimension || 'route_target';
  if (f.random_share !== '' && f.random_share !== null && f.random_share !== undefined) out.random_share = Number(f.random_share);
  return out;
}

// Points scaled into the SVG box, the same projection every time.
export function mapGeometry(nodes, opts) {
  const o = Object.assign({ w: MAP_W, h: MAP_H, pad: MAP_PAD }, opts || {});
  const list = nodes || [];
  if (!list.length) return [];
  const xs = list.map((n) => n.x); const ys = list.map((n) => n.y);
  const x0 = Math.min(...xs); const x1 = Math.max(...xs); const y0 = Math.min(...ys); const y1 = Math.max(...ys);
  const sx = (o.w - 2 * o.pad) / ((x1 - x0) || 1); const sy = (o.h - 2 * o.pad) / ((y1 - y0) || 1);
  return list.map((n) => Object.assign({}, n, { px: Math.round((o.pad + (n.x - x0) * sx) * 10) / 10, py: Math.round((o.h - o.pad - (n.y - y0) * sy) * 10) / 10 }));
}

// The discovery curve's line: one point per block of reviews, y = wrong verdicts in that block (M2).
export function curveGeometry(points, opts) {
  const o = Object.assign({ w: CURVE_W, h: CURVE_H, pad: CURVE_PAD }, opts || {});
  const list = points || [];
  if (!list.length) return { path: '', dots: [], max: 0 };
  const max = Math.max(1, ...list.map((p) => p.wrong));
  const step = list.length > 1 ? (o.w - 2 * o.pad) / (list.length - 1) : 0;
  const dots = list.map((p, i) => ({ x: Math.round((o.pad + i * step) * 10) / 10, y: Math.round((o.h - o.pad - (p.wrong / max) * (o.h - 2 * o.pad)) * 10) / 10,
    label: 'Reviews up to ' + p.reviews_through + ': ' + p.wrong + ' wrong', p: p }));
  return { path: dots.map((d, i) => (i ? 'L' : 'M') + d.x + ' ' + d.y).join(' '), dots: dots, max: max };
}

export function saturationText(sat) {
  if (!sat || !sat.available) return 'Saturation: — (shown once failure modes are named)';
  if (!sat.hint) return 'New failure modes are still appearing.';
  if (sat.kind === 'final_batch') return 'The final fair batch produced ' + sat.final_new_modes + ' new failure modes. Review another batch before treating the list as stable.';
  return 'No new failure mode in the last ' + sat.reviews_without_new + ' reviews; consider moving to counting.';
}

export function traceLabSamplingResetPatch() {
  return {
    tlSmpRange: '30d', tlPlan: null, tlPlanErr: '', tlPlanLoadedAt: null, tlBatches: null, tlBatchesErr: '', tlBatchesLoadedAt: null,
    tlBatchForm: defaultBatchForm(), tlBatchPreview: null, tlBatchBusy: false, tlBatchErr: '', tlBatchNotice: '',
    tlManifest: null, tlManifestOpen: false, tlManifestErr: '',
    tlMap: null, tlMapErr: '', tlMapLoading: false, tlMapLoadedAt: null, tlMapFocus: null,
    tlProgress: null, tlProgressErr: '', tlProgressLoading: false, tlProgressLoadedAt: null, tlProgressBlock: 10
  };
}

export const traceLabSamplingMethods = {
  tlSamplingWindow() { return windowFor(this.state.tlSmpRange || '30d'); },
  tlSetSamplingRange(key) {
    this.setState({ tlSmpRange: key, tlBatchPreview: null });
    this.tlLoadMap(); this.tlLoadPlan();
    if (this.state.tlView === 'progress') this.tlLoadProgress();
  },
  async tlLoadPlan() {
    try {
      const out = await traceLabApi.samplingPlan(this.tlSamplingWindow());
      this.setState({ tlPlan: out, tlPlanErr: '', tlPlanLoadedAt: Date.now() });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlPlanErr: (e && e.message) || String(e) }); }
  },
  async tlLoadBatches() {
    try {
      const out = await traceLabApi.batches({ include_withdrawn: true });
      this.setState({ tlBatches: (out && out.batches) || [], tlBatchesErr: '', tlBatchesLoadedAt: Date.now() });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlBatchesErr: (e && e.message) || String(e) }); }
  },
  tlSetBatchForm(patch) {
    this.setState((p) => {
      const next = Object.assign({}, p.tlBatchForm || defaultBatchForm(), patch);
      if (patch && patch.plan_step && STEP_DEFAULTS[patch.plan_step]) Object.assign(next, { random_share: '' }, STEP_DEFAULTS[patch.plan_step]);
      return { tlBatchForm: next, tlBatchPreview: null, tlBatchErr: '' };
    });
  },
  // "Next batch": the plan's next step with its strategy and size.
  tlNextBatch() {
    const step = (this.state.tlPlan && this.state.tlPlan.next_step) || 'extra';
    this.tlSetBatchForm({ plan_step: step });
  },
  async tlPreviewBatch() {
    this.setState({ tlBatchBusy: true, tlBatchErr: '', tlBatchNotice: '' });
    const req = batchRequest(this.state.tlBatchForm, this.tlSamplingWindow());
    try {
      const out = await traceLabApi.samplingPreview(req);
      this.setState({ tlBatchBusy: false, tlBatchPreview: Object.assign({}, out, { request: req }) });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlBatchBusy: false, tlBatchErr: (e && e.message) || String(e) }); }
  },
  // Draws what the preview showed: the previewed request (same window and seed) when there is one.
  async tlCreateBatch() {
    const pv = this.state.tlBatchPreview;
    const req = pv && pv.request ? pv.request : batchRequest(this.state.tlBatchForm, this.tlSamplingWindow());
    this.setState({ tlBatchBusy: true, tlBatchErr: '', tlBatchNotice: '' });
    try {
      const out = await traceLabApi.createBatch(req);
      const b = out && out.batch;
      this.setState({ tlBatchBusy: false, tlBatchPreview: null,
        tlBatchNotice: b ? 'Drew ' + b.selected + ' answers (seed ' + b.seed + '). They are in the review queue.' : '' });
      this.tlLoadPlan(); this.tlLoadBatches(); this.tlLoadMap();
      return true;
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ tlBatchBusy: false, tlBatchErr: (e && e.message) || String(e) });
      return false;
    }
  },
  async tlWithdrawBatch(batchId, reason) {
    if (!reason || !String(reason).trim()) { this.setState({ tlBatchErr: 'Say why the batch is withdrawn.' }); return false; }
    try {
      await traceLabApi.withdrawBatch(batchId, String(reason).trim());
      this.tlLoadPlan(); this.tlLoadBatches();
      return true;
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlBatchErr: (e && e.message) || String(e) }); return false; }
  },
  async tlLoadManifest() {
    try {
      const out = await traceLabApi.manifest();
      this.setState({ tlManifest: out, tlManifestOpen: true, tlManifestErr: '' });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlManifestErr: (e && e.message) || String(e) }); }
  },
  tlDownloadManifest() {
    const m = this.state.tlManifest;
    if (!m || typeof document === 'undefined') return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(m, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'review-batches-manifest.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  async tlLoadMap() {
    this.setState({ tlMapLoading: true, tlMapErr: '' });
    try {
      const out = await traceLabApi.mapData(this.tlSamplingWindow());
      this.setState({ tlMap: out, tlMapLoading: false, tlMapLoadedAt: Date.now() });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlMapLoading: false, tlMapErr: (e && e.message) || String(e) }); }
  },
  tlFocusCluster(id) { this.setState((p) => ({ tlMapFocus: p.tlMapFocus === id ? null : id })); },
  tlOpenMapNode(node) {
    if (typeof this.tlSetView === 'function') this.tlSetView('conversations');
    if (typeof this.tlOpenConversation === 'function') this.tlOpenConversation(node.conversation_key, node.turn_id);
  },
  async tlLoadProgress() {
    this.setState({ tlProgressLoading: true, tlProgressErr: '' });
    try {
      const w = this.tlSamplingWindow();
      const out = await traceLabApi.progress({ since: w.since, until: w.until, block: this.state.tlProgressBlock || 10 });
      this.setState({ tlProgress: out, tlProgressLoading: false, tlProgressLoadedAt: Date.now() });
    } catch (e) { if (!isStaleScope(e)) this.setState({ tlProgressLoading: false, tlProgressErr: (e && e.message) || String(e) }); }
  },
  tlSetProgressBlock(n) { this.setState({ tlProgressBlock: n }); this.tlLoadProgress(); }
};

// ── view models ──────────────────────────────────────────────────────────────────────

function planVM(c, plan) {
  if (!plan) return null;
  const sampled = plan.distinct_sampled || 0;
  return {
    steps: (plan.steps || []).map((s) => ({ key: s.step, label: PLAN_STEP_LABELS[s.step] || s.step, strategy: STRATEGY_LABELS[s.strategy] || s.strategy,
      target: s.target, selected: s.selected, reviewed: s.reviewed, done: s.selected >= s.target, next: plan.next_step === s.step,
      fair: s.strategy === 'uniform' })),
    nextLabel: PLAN_STEP_LABELS[plan.next_step] || 'Extra batch',
    sampled: sampled + ' of ' + (plan.target_total || 100) + ' answers sampled',
    shortfall: plan.pool_size !== undefined && plan.pool_size < (plan.target_total || 100) - sampled
      ? 'Only ' + plan.pool_size + ' unsampled answers in this period; the plan cannot reach ' + (plan.target_total || 100) + '.' : '',
    next: () => c.tlNextBatch()
  };
}

function batchVM(c, b) {
  return {
    id: b.id, label: PLAN_STEP_LABELS[b.plan_step] || b.plan_step, strategy: STRATEGY_LABELS[b.strategy] || b.strategy,
    tag: b.counts_for_prevalence ? 'counts toward rates' : 'for discovery only', fair: !!b.counts_for_prevalence,
    selected: b.selected, requested: b.requested, reviewed: b.reviewed || 0, mine: b.mine || 0, missing: b.missing || 0,
    wrong: b.wrong || 0, right: b.right || 0, disputed: b.disputed || 0, seed: b.seed, who: b.created_by_email || '—',
    withdrawn: !!b.withdrawn_at, withdraw: b.withdrawn_at ? null : (reason) => c.tlWithdrawBatch(b.id, reason)
  };
}

export function traceLabSamplingVals(c, now) {
  const s = c.state;
  const form = Object.assign(defaultBatchForm(), s.tlBatchForm || {});
  const map = s.tlMap;
  const focus = s.tlMapFocus;
  const prg = s.tlProgress;
  const oc = prg && prg.open_coding;
  const geo = map && !map.too_few ? mapGeometry(map.nodes) : [];
  const disc = prg && prg.discovery;
  const curve = curveGeometry(disc ? disc.points : []);
  return {
    tlSmp: {
      ranges: SAMPLING_RANGES.map((r) => ({ key: r.key, label: r.label, on: (s.tlSmpRange || '30d') === r.key, pick: () => c.tlSetSamplingRange(r.key) })),
      plan: planVM(c, s.tlPlan), planError: s.tlPlanErr || '',
      form: Object.assign({}, form, {
        steps: Object.keys(PLAN_STEP_LABELS).map((k) => ({ key: k, label: PLAN_STEP_LABELS[k] })),
        strategies: Object.keys(STRATEGY_LABELS).map((k) => ({ key: k, label: STRATEGY_LABELS[k] })),
        dimensions: DIMENSION_CHOICES, showDimension: form.strategy === 'dimension',
        fairNote: form.strategy === 'uniform' ? 'Counts toward rates.' : 'For discovery only: this batch never counts toward rates.',
        set: (patch) => c.tlSetBatchForm(patch), preview: () => c.tlPreviewBatch(), create: () => c.tlCreateBatch()
      }),
      busy: !!s.tlBatchBusy, error: s.tlBatchErr || '', notice: s.tlBatchNotice || '',
      preview: s.tlBatchPreview ? {
        poolSize: s.tlBatchPreview.pool_size, excluded: s.tlBatchPreview.excluded_sampled, truncated: !!s.tlBatchPreview.truncated,
        picks: (s.tlBatchPreview.picks || []).map((p) => ({ turnId: p.turn_id, reason: p.reason })),
        empty: !(s.tlBatchPreview.picks || []).length
      } : null,
      batches: s.tlBatches ? s.tlBatches.map((b) => batchVM(c, b)) : null, batchesError: s.tlBatchesErr || '',
      manifest: s.tlManifestOpen && s.tlManifest ? (s.tlManifest.batches || []).map((b) => ({ id: b.id, label: PLAN_STEP_LABELS[b.plan_step] || b.plan_step,
        seed: b.seed, tag: b.counts_for_prevalence ? 'counts toward rates' : 'for discovery only',
        items: (b.items || []).map((i) => ({ position: i.position, turnId: i.turn_id, reason: i.reason })) })) : null,
      manifestError: s.tlManifestErr || '', openManifest: () => c.tlLoadManifest(), downloadManifest: () => c.tlDownloadManifest()
    },
    tlMapView: {
      loading: !!s.tlMapLoading && !s.tlMapLoadedAt, error: s.tlMapErr || '',
      tooFew: !!(map && map.too_few), tooFewCopy: 'Not enough answers in this window to draw a map.',
      caution: 'A cluster is a prompt to read answers, never evidence that they failed.',
      explained: map && map.explained_variance ? 'The two axes carry ' + pct(map.explained_variance[0]) + ' and ' + pct(map.explained_variance[1]) + ' of the spread between answers.' : '',
      truncated: !!(map && map.truncated), width: MAP_W, height: MAP_H,
      nodes: geo.map((n) => ({ id: n.turn_id, x: n.px, y: n.py, cluster: n.cluster, reviewed: !!n.reviewed, mine: !!n.reviewed_by_me, sampled: !!n.batch_id,
        dim: focus !== null && focus !== n.cluster, on: focus === n.cluster,
        title: (n.question || 'Answer') + ' · group ' + n.cluster + (n.reviewed ? ' · reviewed' : ' · not reviewed') + (n.batch_id ? ' · in a review batch' : ''),
        open: () => c.tlOpenMapNode(n) })),
      clusters: map && map.clusters ? map.clusters.map((g) => ({ id: g.id, label: 'Group ' + g.id + ' · ' + g.size + ' answers · ' + g.reviewed + ' reviewed',
        on: focus === g.id, pick: () => c.tlFocusCluster(g.id),
        centroid: (map.feature_names || []).map((f) => ({ label: f.label, value: g.centroid ? g.centroid[f.key] : null })) })) : [],
      reload: () => c.tlLoadMap()
    },
    tlProgressView: {
      loading: !!s.tlProgressLoading && !s.tlProgressLoadedAt, error: s.tlProgressErr || '',
      tiles: [
        { label: 'Answers reviewed', value: oc ? num(oc.reviewed) : '—' },
        { label: 'Wrong', value: oc ? num(oc.wrong) : '—' },
        { label: 'Missing judgments', value: oc ? num(oc.missing) : '—' },
        { label: 'Open batches', value: prg && prg.open_batches !== null && prg.open_batches !== undefined ? num(prg.open_batches) : '—' }
      ],
      batches: prg && prg.batches ? prg.batches.filter((b) => !b.withdrawn_at).map((b) => batchVM(c, b)) : [],
      reviewers: oc ? oc.reviewers.map((r) => ({ who: r.email || '—', reviewed: r.reviewed })) : [],
      missing: oc ? { turns: oc.missing_turns, more: oc.missing_more ? '…and ' + oc.missing_more + ' more' : '' } : null,
      fair: prg && prg.fair_sample ? {
        label: prg.fair_sample.label,
        text: prg.fair_sample.rate === null ? '—' : pct(prg.fair_sample.rate) + ' wrong (95% interval ' + pct(prg.fair_sample.ci_low) + '–' + pct(prg.fair_sample.ci_high) + ')',
        detail: prg.fair_sample.wrong + ' wrong · ' + prg.fair_sample.right + ' right · ' + prg.fair_sample.disputed + ' disputed'
      } : null,
      agreement: prg && prg.agreement ? pct(prg.agreement.percent) + ' agreement on ' + prg.agreement.shared + ' shared answers'
        + (prg.agreement.kappa !== null ? ' · kappa ' + prg.agreement.kappa.toFixed(2) : ' · ' + prg.agreement.note) : '—',
      curve: { path: curve.path, dots: curve.dots, max: curve.max, width: CURVE_W, height: CURVE_H, empty: !curve.dots.length,
        caption: 'Wrong answers found in each block of ' + ((disc && disc.block) || s.tlProgressBlock || 10) + ' reviews. New failure modes join this chart once they are named.' },
      saturation: saturationText(prg && prg.saturation),
      coverage: prg && prg.coverage ? DIMENSION_CHOICES.map((d) => ({ label: d.label, rows: (prg.coverage.dimensions[d.key] || []).map((r) => ({
        value: valueLabel(d.key, r.value), pool: r.pool, reviewed: r.reviewed, poolShare: pct(r.pool_share), reviewedShare: pct(r.reviewed_share), thin: !!r.under_covered })) })) : [],
      unreviewedGroups: prg && prg.coverage && prg.coverage.clusters_unreviewed ? prg.coverage.clusters_unreviewed.map((g) => 'Group ' + g.id + ' (' + g.size + ' answers)') : [],
      blocks: [5, 10, 20].map((n) => ({ n: n, on: (s.tlProgressBlock || 10) === n, pick: () => c.tlSetProgressBlock(n) })),
      reload: () => c.tlLoadProgress()
    }
  };
}
```

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabSampling.test.mjs test/noInternalNames.test.mjs; cd ../..
```

Expected: `11` sampling tests pass; the guard passes.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `apps/frontend/src/logic/traceLabSampling.js`, `apps/frontend/test/traceLabSampling.test.mjs`.

### Task 18: `Map.jsx` and `Progress.jsx`

**Files:**
- Create: `apps/frontend/src/screens/traceLab/Map.jsx`, `apps/frontend/src/screens/traceLab/Progress.jsx`
- Modify: `apps/frontend/test/traceLabReviewScreens.test.mjs` (the `SCREENS` map)
- Test: `apps/frontend/test/traceLabReviewScreens.test.mjs`

**Interfaces:**
- Consumes: vals `tlSmp`, `tlMapView`, `tlProgressView` (Task 17); token `--tl-focus` (Task 16).
- Produces: default exports `TraceLabMap({ vals })` (file `Map.jsx`) and `Progress({ vals })`.

- [ ] **Step 1: Write the failing test** — extend the `SCREENS` map in `test/traceLabReviewScreens.test.mjs`:

old:
```js
    'What went wrong first? (Enter to save)', 'First failure only, in your own words.']
};
```
new:
```js
    'What went wrong first? (Enter to save)', 'First failure only, in your own words.'],
  'src/screens/traceLab/Map.jsx': ['role="img"', 'Draw a review batch', 'Download the manifest', 'Show the manifest'],
  'src/screens/traceLab/Progress.jsx': ['role="img"', 'Wrong rate', 'Coverage', 'Discovery', 'Agreement between reviewers']
};
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabReviewScreens.test.mjs; cd ../..
```

Expected: `ENOENT: no such file or directory, open '.../src/screens/traceLab/Map.jsx'`.

- [ ] **Step 3: Implement**

`apps/frontend/src/screens/traceLab/Map.jsx`:

```jsx
// Map — the Trace Lab's Map tab (logic/traceLabSampling.js): the sampling panel (the HW4 batch plan,
// create a batch with a preview, the batches drawn, the manifest) above an SVG map of the period's
// answers projected onto their two main axes and grouped. Pick a group to bring it forward; shape
// marks reviewed answers and a ring marks answers already in a review batch. Clicking a point opens
// the conversation. A group is a prompt to read answers, never evidence that they failed.
import React from 'react';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", padding: "14px 16px" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const QUIET = { fontSize: "12px", color: "var(--color-neutral-500)", lineHeight: "1.5" };
const BTN = { fontSize: "12.5px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-text)", cursor: "pointer" };
const FIELD = { fontSize: "12.5px", padding: "6px 8px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)" };
const CELL = { padding: "7px 10px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", textAlign: "left", verticalAlign: "top" };
const chip = (on) => ({ fontSize: "12px", padding: "5px 11px", borderRadius: "999px", cursor: "pointer", border: "1px solid " + (on ? "var(--tl-focus)" : "var(--color-divider)"),
  background: "transparent", color: on ? "var(--color-text)" : "var(--color-neutral-400)", fontWeight: on ? "600" : "400" });

function SamplingPanel({ vals }) {
  const s = vals.tlSmp;
  const f = s.form;
  const [reason, setReason] = React.useState({});
  return (
    <div style={{ display: "grid", gap: "12px" }}>
      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
          <div style={KICK}>{"Review plan"}</div>
          {s.plan ? <span style={QUIET}>{s.plan.sampled}</span> : null}
        </div>
        {s.planError ? <div role="alert" style={{ ...QUIET, color: "var(--st-risk)" }}>{"Could not read the plan: " + s.planError}</div> : null}
        {s.plan ? (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
            <thead><tr><th style={CELL}>{"Step"}</th><th style={CELL}>{"Kind"}</th><th style={CELL}>{"Drawn / target"}</th><th style={CELL}>{"Reviewed"}</th><th style={CELL}></th></tr></thead>
            <tbody>{s.plan.steps.map((st) => (
              <tr key={st.key}>
                <td style={CELL}>{st.label + (st.next ? " · next" : "")}</td>
                <td style={CELL}>{st.strategy + (st.fair ? " · counts toward rates" : " · for discovery only")}</td>
                <td style={CELL}>{st.selected + " / " + st.target}</td>
                <td style={CELL}>{st.reviewed}</td>
                <td style={CELL}>{st.done ? "✓ done" : ""}</td>
              </tr>))}</tbody>
          </table>
        ) : <div style={QUIET}>{"Reading…"}</div>}
        {s.plan && s.plan.shortfall ? <div style={{ ...QUIET, marginTop: "6px" }}>{s.plan.shortfall}</div> : null}
        {s.plan ? <button type="button" onClick={s.plan.next} style={{ ...BTN, marginTop: "8px" }}>{"Next batch: " + s.plan.nextLabel}</button> : null}
      </div>

      <div style={CARD}>
        <div style={KICK}>{"Draw a review batch"}</div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "flex-end", marginTop: "8px" }}>
          <label style={QUIET}>{"Step"}<br />
            <select style={FIELD} value={f.plan_step} onChange={(e) => f.set({ plan_step: e.target.value })}>
              {f.steps.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select></label>
          <label style={QUIET}>{"Kind"}<br />
            <select style={FIELD} value={f.strategy} onChange={(e) => f.set({ strategy: e.target.value })}>
              {f.strategies.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select></label>
          {f.showDimension ? (
            <label style={QUIET}>{"Split by"}<br />
              <select style={FIELD} value={f.dimension || "route_target"} onChange={(e) => f.set({ dimension: e.target.value })}>
                {f.dimensions.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select></label>
          ) : null}
          <label style={QUIET}>{"Answers"}<br /><input type="number" min="1" max="200" style={{ ...FIELD, width: "72px" }} value={f.k} onChange={(e) => f.set({ k: e.target.value })} /></label>
          <label style={QUIET}>{"Seed"}<br /><input type="number" min="0" style={{ ...FIELD, width: "80px" }} value={f.seed} onChange={(e) => f.set({ seed: e.target.value })} /></label>
          <label style={QUIET}>{"Reviews per answer"}<br />
            <select style={FIELD} value={f.reviews_required} onChange={(e) => f.set({ reviews_required: Number(e.target.value) })}>
              {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
            </select></label>
          <button type="button" onClick={f.preview} style={BTN} disabled={s.busy}>{s.busy ? "Working…" : "Preview"}</button>
          <button type="button" onClick={f.create} style={{ ...BTN, background: "var(--color-accent)", borderColor: "var(--color-accent)", color: "#fff" }} disabled={s.busy}>{"Create batch"}</button>
        </div>
        <div style={{ ...QUIET, marginTop: "6px" }}>{f.fairNote}</div>
        {s.error ? <div role="alert" style={{ ...QUIET, color: "var(--st-risk)" }}>{s.error}</div> : null}
        {s.notice ? <div role="status" style={{ ...QUIET, color: "var(--st-ok)" }}>{s.notice}</div> : null}
        {s.preview ? (
          <div style={{ marginTop: "8px" }}>
            <div style={QUIET}>{s.preview.empty ? "No answers are left to draw in this period."
              : s.preview.picks.length + " answers from a pool of " + s.preview.poolSize + " (" + s.preview.excluded + " already sampled, left out)" + (s.preview.truncated ? "; the pool was capped at the newest answers" : "") + "."}</div>
            <ol style={{ margin: "6px 0 0", paddingLeft: "20px" }}>{s.preview.picks.map((p) => <li key={p.turnId} style={QUIET}>{p.reason}</li>)}</ol>
          </div>
        ) : null}
      </div>

      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div style={KICK}>{"Batches drawn"}</div>
          <button type="button" onClick={s.openManifest} style={BTN}>{"Show the manifest"}</button>
        </div>
        {s.batchesError ? <div role="alert" style={{ ...QUIET, color: "var(--st-risk)" }}>{"Could not read the batches: " + s.batchesError}</div> : null}
        {!s.batches ? <div style={QUIET}>{"Reading…"}</div> : !s.batches.length ? <div style={QUIET}>{"No batches yet."}</div> : (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
            <thead><tr><th style={CELL}>{"Batch"}</th><th style={CELL}>{"Counts"}</th><th style={CELL}>{"Reviewed"}</th><th style={CELL}>{"Seed"}</th><th style={CELL}>{"By"}</th><th style={CELL}></th></tr></thead>
            <tbody>{s.batches.map((b) => (
              <tr key={b.id} style={{ opacity: b.withdrawn ? "0.55" : "1" }}>
                <td style={CELL}>{b.label + " · " + b.strategy + (b.withdrawn ? " · withdrawn" : "")}</td>
                <td style={CELL}>{b.tag}</td>
                <td style={CELL}>{b.reviewed + " of " + b.selected + (b.missing ? " · " + b.missing + " missing" : "")}</td>
                <td style={CELL}>{b.seed}</td>
                <td style={CELL}>{b.who}</td>
                <td style={CELL}>{b.withdraw ? (
                  <span style={{ display: "flex", gap: "6px" }}>
                    <input className="input" aria-label="Why withdraw this batch" placeholder="Reason" value={reason[b.id] || ""} onChange={(e) => setReason(Object.assign({}, reason, { [b.id]: e.target.value }))} style={{ ...FIELD, width: "120px" }} />
                    <button type="button" onClick={() => b.withdraw(reason[b.id])} style={BTN}>{"Withdraw"}</button>
                  </span>) : null}</td>
              </tr>))}</tbody>
          </table>
        )}
        {s.manifestError ? <div role="alert" style={{ ...QUIET, color: "var(--st-risk)" }}>{"Could not read the manifest: " + s.manifestError}</div> : null}
        {s.manifest ? (
          <div style={{ marginTop: "10px", display: "grid", gap: "8px" }}>
            <button type="button" onClick={s.downloadManifest} style={{ ...BTN, justifySelf: "start" }}>{"Download the manifest"}</button>
            {s.manifest.map((b) => (
              <details key={b.id}><summary style={QUIET}>{b.label + " · seed " + b.seed + " · " + b.tag + " · " + b.items.length + " answers"}</summary>
                <ol style={{ margin: "4px 0 0", paddingLeft: "20px" }}>{b.items.map((i) => <li key={i.turnId} style={QUIET}>{i.reason}</li>)}</ol>
              </details>))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function TraceLabMap({ vals }) {
  const m = vals.tlMapView;
  return (
    <div style={{ display: "grid", gap: "14px" }}>
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        <span style={QUIET}>{"Period"}</span>
        {vals.tlSmp.ranges.map((r) => <button key={r.key} type="button" onClick={r.pick} style={chip(r.on)} aria-pressed={r.on}>{r.label}</button>)}
      </div>
      <SamplingPanel vals={vals} />
      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
          <div style={KICK}>{"Map of answers"}</div>
          <button type="button" onClick={m.reload} style={BTN}>{"Refresh"}</button>
        </div>
        <div style={{ ...QUIET, marginTop: "4px" }}>{m.caution}</div>
        {m.error ? <div role="alert" style={{ ...QUIET, color: "var(--st-risk)" }}>{"Could not draw the map: " + m.error}</div> : null}
        {m.loading ? <div style={QUIET}>{"Reading…"}</div> : m.tooFew ? <div style={QUIET}>{m.tooFewCopy}</div> : m.nodes.length ? (
          <>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", margin: "10px 0" }} role="group" aria-label="Groups">
              {m.clusters.map((g) => <button key={g.id} type="button" onClick={g.pick} style={chip(g.on)} aria-pressed={g.on}>{g.label}</button>)}
            </div>
            <svg viewBox={"0 0 " + m.width + " " + m.height} role="img" aria-label="Answers projected onto their two main axes" style={{ width: "100%", maxWidth: m.width + "px", height: "auto", display: "block" }}>
              <rect x="0" y="0" width={m.width} height={m.height} fill="var(--color-bg)" rx="8" />
              {m.nodes.map((n) => (
                <g key={n.id} onClick={n.open} style={{ cursor: "pointer" }} opacity={n.dim ? 0.25 : 1}>
                  <title>{n.title}</title>
                  <circle cx={n.x} cy={n.y} r="12" fill="transparent" />
                  {n.sampled ? <circle cx={n.x} cy={n.y} r="8" fill="none" stroke="var(--color-text)" strokeWidth="1.5" /> : null}
                  {n.reviewed
                    ? <rect x={n.x - 4} y={n.y - 4} width="8" height="8" fill={n.on ? "var(--tl-focus)" : "var(--color-neutral-400)"} stroke="var(--color-surface)" strokeWidth="2" />
                    : <circle cx={n.x} cy={n.y} r="4.5" fill={n.on ? "var(--tl-focus)" : "var(--color-surface)"} stroke={n.on ? "var(--tl-focus)" : "var(--color-neutral-400)"} strokeWidth="2" />}
                </g>
              ))}
            </svg>
            <div style={{ ...QUIET, marginTop: "6px" }}>{"■ reviewed · ○ not reviewed · ring: already in a review batch. " + m.explained + (m.truncated ? " Only the newest 3,000 answers are shown." : "")}</div>
            <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "10px" }}>
              <thead><tr><th style={CELL}>{"Group"}</th>{(m.clusters[0] ? m.clusters[0].centroid : []).map((c) => <th key={c.label} style={CELL}>{c.label + " (average)"}</th>)}</tr></thead>
              <tbody>{m.clusters.map((g) => (
                <tr key={g.id}><td style={CELL}>{g.label}</td>{g.centroid.map((c) => <td key={c.label} style={CELL}>{c.value === null || c.value === undefined ? "—" : c.value}</td>)}</tr>
              ))}</tbody>
            </table>
          </>
        ) : <div style={QUIET}>{"No answers in this period."}</div>}
      </div>
    </div>
  );
}
```

`apps/frontend/src/screens/traceLab/Progress.jsx`:

```jsx
// Progress — the Trace Lab's Progress tab, open-coding part (logic/traceLabSampling.js): four tiles,
// the batch table, the discovery curve, the fair-sample wrong rate, agreement between reviewers,
// coverage per dimension and the saturation line. M4 adds the failure-mode treemap and the
// suggestion queue below. Missing data reads "—", never zero.
import React from 'react';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", padding: "14px 16px" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const QUIET = { fontSize: "12px", color: "var(--color-neutral-500)", lineHeight: "1.5" };
const CELL = { padding: "7px 10px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", textAlign: "left", verticalAlign: "top" };
const BTN = { fontSize: "12px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-text)", cursor: "pointer" };

function Curve({ curve }) {
  if (curve.empty) return <div style={QUIET}>{"No reviews yet."}</div>;
  return (
    <svg viewBox={"0 0 " + curve.width + " " + curve.height} role="img" aria-label={curve.caption} style={{ width: "100%", maxWidth: curve.width + "px", height: "auto", display: "block" }}>
      <line x1="28" y1={curve.height - 28} x2={curve.width - 28} y2={curve.height - 28} stroke="var(--color-divider)" strokeWidth="1" />
      <text x="28" y="18" fontSize="11" fill="var(--color-neutral-500)">{"most in a block: " + curve.max}</text>
      <path d={curve.path} fill="none" stroke="var(--tl-focus)" strokeWidth="2" strokeLinejoin="round" />
      {curve.dots.map((d) => (
        <g key={d.x}>
          <circle cx={d.x} cy={d.y} r="10" fill="transparent"><title>{d.label}</title></circle>
          <circle cx={d.x} cy={d.y} r="4" fill="var(--tl-focus)" stroke="var(--color-surface)" strokeWidth="2" />
        </g>
      ))}
    </svg>
  );
}

export default function Progress({ vals }) {
  const p = vals.tlProgressView;
  return (
    <div style={{ display: "grid", gap: "14px" }}>
      {p.error ? <div role="alert" style={{ ...CARD, ...QUIET, color: "var(--st-risk)" }}>{"Could not read progress: " + p.error}</div> : null}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: "12px" }}>
        {p.tiles.map((t) => (
          <div key={t.label} style={CARD}>
            <div style={{ fontSize: "26px", fontWeight: "600", lineHeight: "1" }}>{p.loading ? "…" : t.value}</div>
            <div style={{ ...KICK, marginTop: "8px" }}>{t.label}</div>
          </div>
        ))}
      </div>

      <div style={CARD}>
        <div style={KICK}>{"Batches"}</div>
        {!p.batches.length ? <div style={QUIET}>{"No open batches."}</div> : (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
            <thead><tr><th style={CELL}>{"Batch"}</th><th style={CELL}>{"Reviewed"}</th><th style={CELL}>{"By you"}</th><th style={CELL}>{"Missing"}</th><th style={CELL}>{"Wrong / right / disputed"}</th></tr></thead>
            <tbody>{p.batches.map((b) => (
              <tr key={b.id}>
                <td style={CELL}>{b.label + " · " + b.tag}</td>
                <td style={CELL}>{b.reviewed + " of " + b.selected}</td>
                <td style={CELL}>{b.mine}</td>
                <td style={CELL}>{b.missing}</td>
                <td style={CELL}>{b.wrong + " / " + b.right + " / " + b.disputed}</td>
              </tr>))}</tbody>
          </table>
        )}
        {p.reviewers.length ? <div style={{ ...QUIET, marginTop: "8px" }}>{"Reviewers: " + p.reviewers.map((r) => r.who + " (" + r.reviewed + ")").join(", ")}</div> : null}
        {p.missing && p.missing.turns.length ? <div style={{ ...QUIET, marginTop: "4px" }}>{"Still missing a verdict: " + p.missing.turns.length + " listed" + (p.missing.more ? " " + p.missing.more : "")}</div> : null}
      </div>

      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
          <div style={KICK}>{"Discovery"}</div>
          <div style={{ display: "flex", gap: "6px" }} role="group" aria-label="Reviews per block">
            {p.blocks.map((b) => <button key={b.n} type="button" onClick={b.pick} style={{ ...BTN, fontWeight: b.on ? "600" : "400" }} aria-pressed={b.on}>{b.n + " per block"}</button>)}
          </div>
        </div>
        <Curve curve={p.curve} />
        <div style={{ ...QUIET, marginTop: "4px" }}>{p.curve.caption}</div>
        <div style={{ ...QUIET, marginTop: "4px", color: "var(--color-text)" }}>{p.saturation}</div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: "12px" }}>
        <div style={CARD}>
          <div style={KICK}>{"Wrong rate"}</div>
          <div style={{ fontSize: "18px", fontWeight: "600", marginTop: "6px" }}>{p.fair ? p.fair.text : "—"}</div>
          <div style={QUIET}>{p.fair ? p.fair.label + " · " + p.fair.detail : "From fair (uniform) batches only"}</div>
        </div>
        <div style={CARD}>
          <div style={KICK}>{"Agreement between reviewers"}</div>
          <div style={{ fontSize: "14px", marginTop: "6px" }}>{p.agreement}</div>
          <div style={QUIET}>{"First, blind verdicts only."}</div>
        </div>
      </div>

      <div style={CARD}>
        <div style={KICK}>{"Coverage"}</div>
        <div style={QUIET}>{"Shares of the reviewed sample, not rates for all answers. A value is thin when it is at least 5% of the answers but under half that share of the reviews."}</div>
        {!p.coverage.length ? <div style={QUIET}>{"—"}</div> : p.coverage.map((d) => (
          <details key={d.label} style={{ marginTop: "8px" }}>
            <summary style={{ fontSize: "12.5px" }}>{d.label + (d.rows.some((r) => r.thin) ? " · some values thin" : "")}</summary>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><th style={CELL}>{"Value"}</th><th style={CELL}>{"Answers"}</th><th style={CELL}>{"Reviewed"}</th><th style={CELL}>{"Share of answers"}</th><th style={CELL}>{"Share of reviews"}</th></tr></thead>
              <tbody>{d.rows.map((r) => (
                <tr key={r.value}><td style={CELL}>{r.value + (r.thin ? " · thin" : "")}</td><td style={CELL}>{r.pool}</td><td style={CELL}>{r.reviewed}</td><td style={CELL}>{r.poolShare}</td><td style={CELL}>{r.reviewedShare}</td></tr>
              ))}</tbody>
            </table>
          </details>
        ))}
        {p.unreviewedGroups.length ? <div style={{ ...QUIET, marginTop: "6px" }}>{"Map groups with no reviewed answer: " + p.unreviewedGroups.join(", ")}</div> : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabReviewScreens.test.mjs test/noInternalNames.test.mjs; cd ../..
```

Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `Map.jsx`, `Progress.jsx`, `test/traceLabReviewScreens.test.mjs`.

### Task 19: Wire M2 into the app and M1's Trace Lab shell

**Files:**
- Modify: `apps/frontend/src/logic/HoistraLogic.js:20` and `:383` (exact-string edits, shared file)
- Modify: `apps/frontend/src/logic/renderVals.js:19` and `:1993` (exact-string edits, shared file)
- Modify (M1 files, anchors found with grep): `apps/frontend/src/logic/traceLab.js` (`TL_VIEWS`, `TL_TABS`, `freshPatch()`, `tlSetView`, `tlReadView`, the keyboard handler), `apps/frontend/src/logic/session.js` (`TL_VIEW_KEYS`), `apps/frontend/src/logic/traceLabConversations.js`, `apps/frontend/src/screens/TraceLab.jsx`, `apps/frontend/src/screens/traceLab/Conversations.jsx`, `apps/frontend/src/screens/traceLab/Timeline.jsx`
- Modify (M1 tests, exact tab lists): `apps/frontend/test/traceLabShell.test.mjs` ('tabs per role'), `apps/frontend/test/traceLabViews.test.mjs` (the shell's tablist)
- Test: `apps/frontend/test/traceLabM2Wiring.test.mjs`

**Interfaces:**
- Consumes: Tasks 15–18; M1 `tlResetPatch` (= `freshPatch()`), `TL_VIEWS`, `TL_TABS`, `tlTabsFor`, `tlParseLink`, `tlSetView`, the keyboard handler, `tlOpenConversation`, the Conversations/Timeline components; `session.js` `TL_VIEW_KEYS`.
- Produces: the mixins on `HoistraLogic.prototype`; their vals in `renderVals()`; their state in `tlResetPatch()` (spread right after `  return Object.assign({}, TL_SCALARS, TP_SCALARS, {` in `freshPatch()`; later milestones add theirs in the same place); `map` and `progress` in `TL_VIEWS` (so `?tl=map` parses and a reload keeps the view), `TL_TABS` and `TL_VIEW_KEYS`; loaders run when the Map, Progress and Conversations tabs open; `r`/`w` shortcuts; the annotation layer on the timeline.

- [ ] **Step 1: Write the failing test**

```js
// Trace Lab M2 is wired into the app: its methods are on the controller, its state resets with the
// Trace Lab on a company switch or sign-out, and renderVals carries its view models.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { tlResetPatch, tlTabsFor, tlParseLink, TL_VIEWS } = await import('../src/logic/traceLab.js');

test('Map and Progress are Trace Lab views: in the view list, the tabs, the link parser and the restored page', () => {
  assert.deepEqual(TL_VIEWS.slice(-2), ['map', 'progress']);
  const keys = (s) => tlTabsFor(s).map((t) => t.key);
  assert.deepEqual(keys({ account: { role: 'admin' } }), ['overview', 'conversations', 'runs', 'models', 'map', 'progress']);
  assert.deepEqual(keys({ account: { role: 'superadmin' }, viewOrgId: 'org-2' }), ['overview', 'conversations', 'runs', 'models', 'map', 'progress']);
  assert.deepEqual(keys({ account: { role: 'superadmin' } }), ['overview']);
  assert.equal(tlParseLink('?tl=map').view, 'map');
  assert.equal(tlParseLink('?tl=progress').view, 'progress');
  const session = readFileSync(new URL('../src/logic/session.js', import.meta.url), 'utf8');
  assert.match(session, /const TL_VIEW_KEYS = \[[^\]]*'map', 'progress'\]/);
});

test('the M2 methods are on the controller', () => {
  const c = new HoistraLogic();
  ['tlAnnLoad', 'tlSaveVerdict', 'tlSaveAndNext', 'tlAnnShortcut', 'tlComment', 'tlLoadPlan', 'tlPreviewBatch', 'tlCreateBatch',
    'tlLoadMap', 'tlLoadProgress'].forEach((m) => assert.equal(typeof c[m], 'function', m));
});

test('the M2 state resets with the Trace Lab', () => {
  const p = tlResetPatch();
  ['tlAnn', 'tlAnnBlocks', 'tlDraft', 'tlDraftComment', 'tlBatchForm', 'tlBatchPreview', 'tlMap', 'tlMapFocus', 'tlProgress']
    .forEach((k) => assert.ok(k in p, k));
  assert.deepEqual(p.tlDraft, {});
});

test('render vals carry the M2 view models, with dashes before any read', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', view: 'traces' });
  const v = c.renderVals();
  ['tlReview', 'tlBlock', 'tlMarginNotes', 'tlComments', 'tlTurnChips', 'tlSmp', 'tlMapView', 'tlProgressView']
    .forEach((k) => assert.ok(k in v, k));
  assert.deepEqual(v.tlProgressView.tiles.map((t) => t.value), ['—', '—', '—', '—']);
});
```

- [ ] **Step 2: Run it and see it fail**

```sh
cd apps/frontend && node --test test/traceLabM2Wiring.test.mjs; cd ../..
```

Expected: four failures: the views test (`TL_VIEWS` ends in `'models'`, not `['map', 'progress']`), `AssertionError: tlAnnLoad` (`'undefined' !== 'function'`), the missing M2 reset keys and the missing render vals.

- [ ] **Step 3: Implement**

Before editing the shared files, check for foreign edits (read-only): `git -C /Users/hussain/Desktop/hoist/Hoistra_Plenum diff --stat -- apps/frontend/src/logic/HoistraLogic.js apps/frontend/src/logic/renderVals.js`, and re-read each anchor.

`src/logic/HoistraLogic.js` — import (current line 20):

old:
```js
import { tracesPageMethods } from './tracesPage.js';
```
new:
```js
import { tracesPageMethods } from './tracesPage.js';
import { traceLabAnnotateMethods } from './traceLabAnnotate.js';
import { traceLabSamplingMethods } from './traceLabSampling.js';
```

`src/logic/HoistraLogic.js` — mixins (line 383; M1 has added its own mixins to this list, so anchor on the tail, which stays unique):

old:
```js
, renderValsMethods);
```
new:
```js
, traceLabAnnotateMethods, traceLabSamplingMethods, renderValsMethods);
```

`src/logic/renderVals.js` — import (current line 19):

old:
```js
import { tracesPageVals } from './tracesPage.js';
```
new:
```js
import { tracesPageVals } from './tracesPage.js';
import { traceLabAnnotateVals } from './traceLabAnnotate.js';
import { traceLabSamplingVals } from './traceLabSampling.js';
```

`src/logic/renderVals.js` — vals (current line 1993):

old:
```js
      ...tracesPageVals(this),
```
new:
```js
      ...tracesPageVals(this),
      ...traceLabAnnotateVals(this),
      ...traceLabSamplingVals(this),
```

M1's `src/logic/traceLab.js` (find the anchors with `grep -n "export function tlResetPatch\|tlSetView\|tlReadView\|tlKey\|key ===\|e.key" apps/frontend/src/logic/traceLab.js`):
1. Imports at the top:
   ```js
   import { traceLabAnnotateResetPatch } from './traceLabAnnotate.js';
   import { traceLabSamplingResetPatch } from './traceLabSampling.js';
   ```
2. In `freshPatch()` (what `tlResetPatch()` returns), spread both reset patches right after the `Object.assign` opening line. Every later milestone's reset patch goes in the same place:

   old:
   ```js
     return Object.assign({}, TL_SCALARS, TP_SCALARS, {
   ```
   new:
   ```js
     return Object.assign({}, TL_SCALARS, TP_SCALARS, {
       ...traceLabAnnotateResetPatch(),
       ...traceLabSamplingResetPatch(),
   ```
3. At the end of `tlSetView(view)` (after it stores the view), and at the end of `tlReadView()` with `view` = the restored view:
   ```js
       if (view === 'map') { this.tlLoadPlan(); this.tlLoadBatches(); this.tlLoadMap(); }
       if (view === 'progress') { this.tlLoadPlan(); this.tlLoadProgress(); }
       if (view === 'conversations' && this.state.tlConvKey) this.tlAnnLoad();
   ```
4. In the keyboard handler, right after its early return for INPUT/TEXTAREA/SELECT/contenteditable and modifiers, before its own key switch:
   ```js
       if ((e.key === 'r' || e.key === 'w') && this.tlAnnShortcut(e.key)) { e.preventDefault(); return; }
   ```
5. The two views, as exact replacements (find them with `grep -n "TL_VIEWS\|TL_TABS\|Model calls" apps/frontend/src/logic/traceLab.js`). `tlTabsFor` already gives every tab but Overview to a company admin and a superadmin viewing a company (the same visibility as Conversations); `tlParseLink` and `tlSetView` check `TL_VIEWS` / `tlTabsFor`, so without the first edit `?tl=map` is refused and a reload loses the view:

   old:
   ```js
   export const TL_VIEWS = ['overview', 'conversations', 'runs', 'models'];
   ```
   new:
   ```js
   export const TL_VIEWS = ['overview', 'conversations', 'runs', 'models', 'map', 'progress'];
   ```
   old:
   ```js
     { key: 'models', label: 'Model calls' }
   ];
   ```
   new:
   ```js
     { key: 'models', label: 'Model calls' },
     { key: 'map', label: 'Map' },
     { key: 'progress', label: 'Progress' }
   ];
   ```

M1's `src/logic/session.js` — the restored-page allow-list (`grep -n "TL_VIEW_KEYS" apps/frontend/src/logic/session.js`; M1 keeps it in step with `TL_VIEWS`):

old:
```js
const TL_VIEW_KEYS = ['overview', 'conversations', 'runs', 'models'];
```
new:
```js
const TL_VIEW_KEYS = ['overview', 'conversations', 'runs', 'models', 'map', 'progress'];
```

M1's exact tab-list tests (find them with `grep -n "'models'\]\|Model calls" apps/frontend/test/traceLabShell.test.mjs apps/frontend/test/traceLabViews.test.mjs`). In `test/traceLabShell.test.mjs`, test 'tabs per role', the two key lists change (the first occurrence is the admin's, the second the superadmin viewing a company's):

old:
```js
  assert.deepEqual(keys({ account: { role: 'admin' } }), ['overview', 'conversations', 'runs', 'models']);
  assert.deepEqual(keys({ account: { role: 'superadmin' }, viewOrgId: 'org-2' }), ['overview', 'conversations', 'runs', 'models']);
```
new:
```js
  assert.deepEqual(keys({ account: { role: 'admin' } }), ['overview', 'conversations', 'runs', 'models', 'map', 'progress']);
  assert.deepEqual(keys({ account: { role: 'superadmin' }, viewOrgId: 'org-2' }), ['overview', 'conversations', 'runs', 'models', 'map', 'progress']);
```
and its label list gains the two tabs:

old:
```js
[['Overview', false], ['Conversations', false], ['Runs', true], ['Model calls', false]]);
```
new:
```js
[['Overview', false], ['Conversations', false], ['Runs', true], ['Model calls', false], ['Map', false], ['Progress', false]]);
```
In `test/traceLabViews.test.mjs`, the shell's tablist test:

old:
```js
['Model calls', 'false']]);
```
new:
```js
['Model calls', 'false'], ['Map', 'false'], ['Progress', 'false']]);
```
(M3 and later plans anchor on the new tails `['Progress', false]]);` and `['Progress', 'false']]);`.)

M1's `src/logic/traceLabConversations.js`: at the end of the success path of `tlOpenConversation` and of `tlOpenTurn` (after `tlConvKey` / `tlTurnId` are set):
```js
    this.tlAnnLoad({ key: this.state.tlConvKey, turnId: this.state.tlTurnId });
```

M1's `src/screens/TraceLab.jsx`: import the two tabs and mount them in the view switch (use M1's val for the active tab; `grep -n "Conversations vals" apps/frontend/src/screens/TraceLab.jsx` shows the pattern):
```jsx
import TraceLabMap from './traceLab/Map.jsx';
import Progress from './traceLab/Progress.jsx';
```
```jsx
      {vals.tlView === 'map' ? <TraceLabMap vals={vals} /> : null}
      {vals.tlView === 'progress' ? <Progress vals={vals} /> : null}
```

M1's `src/screens/traceLab/Conversations.jsx`: imports, a ref, and the layout around M1's timeline (keep M1's `<Timeline … />` element exactly as it is, inside the new wrapper; if M1 rendered a placeholder margin column, this replaces it):
```jsx
import VerdictBar, { ConversationComments } from './VerdictBar.jsx';
import MarginNotes, { UnplacedNotes } from './MarginNotes.jsx';
```
```jsx
  const timelineRef = React.useRef(null);
```
```jsx
      <ConversationComments vals={vals} />
      <div data-tl-timeline="" ref={timelineRef} style={{ display: "flex", gap: "16px", alignItems: "flex-start" }}>
        <div style={{ flex: "1", minWidth: "0", maxWidth: "720px" }}>
          {/* M1's <Timeline ... /> element, unchanged */}
        </div>
        <MarginNotes vals={vals} rootRef={timelineRef} />
      </div>
      <UnplacedNotes vals={vals} />
      {vals.tlAnnSelErr ? <div role="status" style={{ fontSize: "12px", color: "var(--st-warn)", padding: "6px 14px" }}>{vals.tlAnnSelErr}</div> : null}
      <VerdictBar vals={vals} />
```
and, where M1 renders a turn's header chips, append the review chips:
```jsx
      {vals.tlTurnChips(turn.turn_id).map((c) => <span key={c} style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginLeft: "8px" }}>{c}</span>)}
```

M1's `src/screens/traceLab/Timeline.jsx`: where a card renders the body of a message whose `anchor` is non-null (the user question, the answer, a tool result, a model response, database rows, a step or note output), wrap that body so the server's block text is rendered and selectable; `AnnotatedText` renders M1's own body when the server sent no text for that block (gate cards keep M1's rendering):
```jsx
import { AnnotatedText } from './MarginNotes.jsx';
```
```jsx
        <AnnotatedText vals={vals} anchor={card.anchor}>
          {/* M1's existing body element for this card, unchanged */}
        </AnnotatedText>
```

- [ ] **Step 4: Run it and see it pass**

```sh
cd apps/frontend && node --test test/traceLabM2Wiring.test.mjs test/traceLabShell.test.mjs test/traceLabViews.test.mjs test/traceLabConversations.test.mjs test/traceLabTimeline.test.mjs test/mount.test.mjs test/noInternalNames.test.mjs; cd ../..
```

Expected: all pass (`traceLabShell.test.mjs` scans `tlResetPatch()` for every `tl*` key, so it now covers the M2 keys too; its 'tabs per role' test and `traceLabViews.test.mjs`'s tablist test now expect Map and Progress).

- [ ] **Step 5: Checkpoint** — leave uncommitted; changed: `HoistraLogic.js`, `renderVals.js`, `traceLab.js`, `session.js`, `traceLabConversations.js`, `TraceLab.jsx`, `traceLab/Conversations.jsx`, `traceLab/Timeline.jsx`, `test/traceLabShell.test.mjs` and `test/traceLabViews.test.mjs` (M1's exact tab lists), `test/traceLabM2Wiring.test.mjs`; paste the M1-file diffs into the log.

### Task 20: Full suites and build

**Files:** none new.

**Interfaces:** consumes everything above; produces the milestone's green bar.

- [ ] **Step 1: Write the failing test** — none new; this task runs every suite the milestone touches, so a regression anywhere shows here.

- [ ] **Step 2: Run the whole backend Trace Lab and trace suites** (scratch Postgres up)

```sh
tl_da_test tests/ -k "trace"
tl_da_test tests/test_rest_turns_are_traced.py tests/test_trace.py tests/test_agent_modules_import.py
```

Expected: all pass, no skips among the `_sql.py` files (a skip means the scratch database was not reachable).

- [ ] **Step 3: Fix any failure at its cause** — re-run the failing file alone with `tl_da_test <file> -x` and correct the task that owns it; never weaken an assertion to get green.

- [ ] **Step 4: Run the whole frontend suite and the build**

```sh
cd apps/frontend && npm test && npm run build; cd ../..
```

Expected: `npm test` all pass (including `noInternalNames`); `npm run build` succeeds and `scripts/check-bundle.mjs` passes.

- [ ] **Step 5: Checkpoint** — leave uncommitted; record in the log the pass counts of both suites and the full list of files changed by M2 (backend and frontend), for Hussain's review.

### Task 21: Hussain's verification and deploy (not for a Claude session)

**Files:** none in the repo, except one line in `migrate.py` after the apply (step 6). Outside the repo: `~/Desktop/hoist/eval-demo/stub-tracelab-m2.mjs` and one line in `stub-eval.mjs`.

**Interfaces:** consumes the whole milestone; gated on P1 (rebuild), P3 (apply), R27 and R32 (read-only reads first).

- [ ] **Step 1: Stub walk first (no database).** Save this module as `~/Desktop/hoist/eval-demo/stub-tracelab-m2.mjs`:

```js
// Trace Lab M2 routes for the eval-demo stand-in server (stub-eval.mjs): open coding and sampling,
// in memory, mirroring routes/trace_lab_review.py's contract - blind until you save, a wrong verdict
// needs a note and a quote, a stale base is a 409 with the current head, only uniform batches count
// toward rates. Nothing here touches a database. Wire it into stub-eval.mjs's handler before the
// final 404:  if (await handleTraceLabM2({ req, res, p, u, body, who: WHO, turns: TURNS, send })) return;
const P = '/backend/deep-agents/api/trace-lab';
const ANN = [];          // every version: {id, lineage_id, version, kind, turn_id, conversation_key, reviewer_id, reviewer_email, verdict, note, anchor, tombstone, superseded_by, created_at}
const BATCHES = [];
const ITEMS = [];
let seq = 0;
const id = (p) => p + '-' + (++seq);
const now = () => new Date().toISOString();
const keyOf = (t) => (t.session_id ? 's:' + t.session_id : 't:' + t.turn_id);
const heads = (pred) => ANN.filter((a) => !a.superseded_by && pred(a));
const out = (a) => (a ? Object.assign({}, a) : null);
// A second reviewer has already judged t2, so the blind line shows.
ANN.push({ id: 'seed-1', lineage_id: 'seed-1', version: 1, kind: 'verdict', turn_id: 't2', conversation_key: 's:chat-2', reviewer_id: 'u-other',
  reviewer_email: 'bea@example.com', verdict: 'wrong', note: 'Names B-101, which has no gas boiler.', tombstone: false, superseded_by: null, blind: true,
  anchor: { span_id: null, field: 'answer', quote: 'B-101 boiler room', occurrence: 1, span_name: 'turn', span_kind: 'answer' }, created_at: now() });

function states(who, tids) {
  const o = {};
  tids.forEach((tid) => {
    const hs = heads((a) => a.kind === 'verdict' && a.turn_id === tid);
    const mine = hs.find((a) => a.reviewer_id === who.id) || null;
    const others = hs.filter((a) => a.reviewer_id !== who.id && !a.tombstone);
    const item = ITEMS.find((i) => i.turn_id === tid);
    const shown = (mine && !mine.tombstone) || (item && hs.filter((a) => !a.tombstone).length >= item.reviews_required);
    o[tid] = { mine: out(mine), others: shown ? others.map(out) : null, others_count: others.length, others_hidden: !shown && others.length > 0,
      reviews_required: item ? item.reviews_required : null, sample_item: item ? { batch_id: item.batch_id, reason: item.reason, plan_step: item.plan_step } : null };
  });
  return o;
}

function verdict(who, t, body, send, res) {
  const head = heads((a) => a.kind === 'verdict' && a.turn_id === t.turn_id && a.reviewer_id === who.id)[0] || null;
  if ((head ? head.id : null) !== (body.base || null)) return send(res, 409, { detail: { ok: false, reason: 'stale', error: 'Changed since you opened it.', current: out(head) } });
  if (body.verdict === 'wrong' && !(body.note || '').trim()) return send(res, 422, { detail: { ok: false, reason: 'note_required', error: 'Say what went wrong first.' } });
  if (body.verdict === 'wrong' && !body.anchor) return send(res, 422, { detail: { ok: false, reason: 'anchor_required', error: 'Select the text where it first went wrong.' } });
  const text = body.anchor && body.anchor.field === 'question' ? t.question : t.answer;
  if (body.anchor && String(text || '').indexOf(body.anchor.quote) < 0) return send(res, 422, { detail: { ok: false, reason: 'quote_not_found', error: 'The selected text is no longer in that step. Select it again.' } });
  const row = { id: id('v'), lineage_id: head ? head.lineage_id : null, version: head ? head.version + 1 : 1, supersedes: head ? head.id : null, kind: 'verdict',
    turn_id: t.turn_id, conversation_key: keyOf(t), reviewer_id: who.id, reviewer_email: who.email, verdict: body.verdict,
    note: body.verdict === 'right' ? 'no failure observed' : body.note, tombstone: false, superseded_by: null, created_at: now(),
    anchor: body.anchor ? Object.assign({ span_name: 'turn', span_kind: body.anchor.field }, body.anchor) : null,
    blind: !heads((a) => a.kind === 'verdict' && a.turn_id === t.turn_id && a.reviewer_id !== who.id && !a.tombstone).length };
  if (!row.lineage_id) row.lineage_id = row.id;
  if (head) head.superseded_by = row.id;
  ANN.push(row);
  return send(res, 200, { ok: true, annotation: out(row) });
}

function poolOf(turns, who) {
  return turns.filter((t) => !String(t.session_id || '').startsWith('report-') && !String(t.session_id || '').startsWith('golden-'));
}

export async function handleTraceLabM2({ req, res, p, u, body, who, turns, send }) {
  if (!p.startsWith(P + '/')) return false;
  const m = req.method; const q = (k) => u.searchParams.get(k);
  if (p === P + '/annotations' && m === 'GET') {
    const key = q('conversation_key'); const tid = q('turn_id');
    const ts = turns.filter((t) => keyOf(t) === key);
    if (!ts.length) return send(res, 404, { detail: { ok: false, reason: 'turn_not_found', error: 'That answer was not found.' } }) || true;
    const st = states(who, ts.map((t) => t.turn_id));
    const blindSomewhere = Object.values(st).some((s) => s.others === null);
    const cs = heads((a) => a.kind === 'comment' && a.conversation_key === key && !a.tombstone);
    const t = ts.find((x) => x.turn_id === tid);
    send(res, 200, { ok: true, conversation_key: key, turns: st, comments: (blindSomewhere ? cs.filter((c) => c.reviewer_id === who.id) : cs).map(out),
      comments_hidden: blindSomewhere ? cs.filter((c) => c.reviewer_id !== who.id).length : 0,
      blocks: t ? { [t.turn_id]: { ':question': t.question || '', ':answer': t.answer || '' } } : null });
    return true;
  }
  let mm = p.match(new RegExp('^' + P + '/turns/([^/]+)/verdict$'));
  if (mm) {
    const t = turns.find((x) => x.turn_id === decodeURIComponent(mm[1]));
    if (!t) { send(res, 404, { detail: { ok: false, reason: 'turn_not_found', error: 'That answer was not found.' } }); return true; }
    if (m === 'PUT') { verdict(who, t, body, send, res); return true; }
    if (m === 'DELETE') {
      const head = heads((a) => a.kind === 'verdict' && a.turn_id === t.turn_id && a.reviewer_id === who.id && !a.tombstone)[0];
      if (!head) { send(res, 404, { detail: { ok: false, reason: 'annotation_not_found', error: 'There is no verdict or comment of yours to change.' } }); return true; }
      if (head.id !== q('base')) { send(res, 409, { detail: { ok: false, reason: 'stale', error: 'Changed since you opened it.', current: out(head) } }); return true; }
      const row = Object.assign({}, head, { id: id('v'), version: head.version + 1, supersedes: head.id, tombstone: true, verdict: null, note: null, anchor: null, created_at: now() });
      head.superseded_by = row.id; ANN.push(row);
      send(res, 200, { ok: true, annotation: out(row) }); return true;
    }
  }
  if (p === P + '/comments' && m === 'POST') {
    const row = { id: id('c'), version: 1, kind: 'comment', conversation_key: body.conversation_key, turn_id: body.turn_id || null, reviewer_id: who.id,
      reviewer_email: who.email, note: body.note, tombstone: false, superseded_by: null, created_at: now() };
    row.lineage_id = row.id; ANN.push(row);
    send(res, 201, { ok: true, annotation: out(row) }); return true;
  }
  if (p === P + '/queue' && m === 'GET') {
    const open = ITEMS.filter((i) => !BATCHES.find((b) => b.id === i.batch_id).withdrawn_at && (!q('batch_id') || i.batch_id === q('batch_id'))
      && !heads((a) => a.kind === 'verdict' && a.turn_id === i.turn_id && a.reviewer_id === who.id && !a.tombstone).length);
    send(res, 200, { ok: true, remaining: open.length, items: open.slice(0, 20).map((i) => ({ turn_id: i.turn_id, conversation_key: keyOf(turns.find((t) => t.turn_id === i.turn_id)),
      batch_id: i.batch_id, plan_step: i.plan_step, reason: i.reason, others_count: 0 })) }); return true;
  }
  const PLAN = [['initial_uniform', 'uniform', 15], ['cluster_representatives', 'diversity', 15], ['dimension', 'dimension', 30], ['depth_search', 'enrich', 25], ['final_uniform', 'uniform', 15]];
  const live = () => BATCHES.filter((b) => !b.withdrawn_at);
  const reviewedIds = () => new Set(heads((a) => a.kind === 'verdict' && !a.tombstone).map((a) => a.turn_id));
  const batchOut = (b) => { const its = ITEMS.filter((i) => i.batch_id === b.id); const r = reviewedIds();
    return Object.assign({}, b, { items: its.length, reviewed: its.filter((i) => r.has(i.turn_id)).length, mine: 0, missing: its.filter((i) => !r.has(i.turn_id)).length, wrong: 0, right: 0, disputed: 0 }); };
  const plan = () => { const steps = PLAN.map(([s, st, k]) => ({ step: s, strategy: st, target: k, selected: live().filter((b) => b.plan_step === s).reduce((n, b) => n + b.selected, 0), reviewed: 0, batch_ids: live().filter((b) => b.plan_step === s).map((b) => b.id) }));
    return { ok: true, steps: steps, next_step: (steps.find((s) => s.selected < s.target) || { step: 'extra' }).step, distinct_sampled: ITEMS.length, target_total: 100, pool_size: poolOf(turns, who).filter((t) => !ITEMS.find((i) => i.turn_id === t.turn_id)).length }; };
  if (p === P + '/sampling/plan' && m === 'GET') { send(res, 200, plan()); return true; }
  const draw = (form) => poolOf(turns, who).filter((t) => !ITEMS.find((i) => i.turn_id === t.turn_id)).slice(0, Number(form.k) || 15)
    .map((t) => ({ turn_id: t.turn_id, reason: form.strategy === 'uniform' ? 'uniform pick' : form.strategy + ' pick', cluster: null, dimension_value: null, score: null }));
  if (p === P + '/sampling/preview' && m === 'GET') {
    const form = Object.fromEntries(u.searchParams.entries());
    if (form.dimension && ['channel', 'route_target', 'asker_role', 'page'].indexOf(form.dimension) < 0) { send(res, 422, { detail: { ok: false, reason: 'dimension_not_allowed', error: 'Batches can be split only by channel, where the question was routed, the asker\'s role or page.' } }); return true; }
    send(res, 200, { ok: true, pool_size: poolOf(turns, who).length, excluded_sampled: ITEMS.length, truncated: false, picks: draw(form) }); return true;
  }
  if (p === P + '/sampling/batches' && m === 'POST') {
    const picks = draw(body);
    if (!picks.length) { send(res, 422, { detail: { ok: false, reason: 'pool_empty', error: 'No answers are left to draw in that period.' } }); return true; }
    const b = { id: id('b'), plan_step: body.plan_step, strategy: body.strategy, seed: Number(body.seed) || 7, requested: Number(body.k) || 15, selected: picks.length,
      pool_size: poolOf(turns, who).length, excluded_sampled: ITEMS.length, counts_for_prevalence: body.strategy === 'uniform', reviews_required: Number(body.reviews_required) || 1,
      created_by_email: who.email, created_at: now(), withdrawn_at: null, params: { k: Number(body.k) || 15 } };
    BATCHES.push(b);
    picks.forEach((pk, i) => ITEMS.push({ batch_id: b.id, turn_id: pk.turn_id, position: i + 1, reason: pk.reason, plan_step: b.plan_step, reviews_required: b.reviews_required }));
    send(res, 201, { ok: true, batch: batchOut(b), items: picks }); return true;
  }
  if (p === P + '/sampling/batches' && m === 'GET') { send(res, 200, { ok: true, batches: BATCHES.map(batchOut).reverse() }); return true; }
  mm = p.match(new RegExp('^' + P + '/sampling/batches/([^/]+)/withdraw$'));
  if (mm && m === 'POST') {
    const b = BATCHES.find((x) => x.id === mm[1]);
    if (!b) { send(res, 404, { detail: { ok: false, reason: 'batch_not_found', error: 'That batch was not found.' } }); return true; }
    if (b.withdrawn_at) { send(res, 409, { detail: { ok: false, reason: 'already_withdrawn', error: 'That batch was already withdrawn.' } }); return true; }
    b.withdrawn_at = now(); b.withdrawn_reason = body.reason;
    send(res, 200, { ok: true, batch: batchOut(b) }); return true;
  }
  if (p === P + '/sampling/manifest' && m === 'GET') {
    send(res, 200, { ok: true, schema_version: 1, organization_id: who.organization_id, generated_at: now(), plan: plan(),
      batches: BATCHES.map((b) => Object.assign({}, b, { items: ITEMS.filter((i) => i.batch_id === b.id).map((i) => ({ turn_id: i.turn_id, position: i.position, reason: i.reason })) })) });
    return true;
  }
  if (p === P + '/map' && m === 'GET') {
    const pool = poolOf(turns, who); const r = reviewedIds();
    if (pool.length < 3) { send(res, 200, { ok: true, too_few: true, nodes: [], clusters: [], explained_variance: null, feature_names: [], pool_size: pool.length, truncated: false }); return true; }
    const nodes = pool.map((t, i) => ({ turn_id: t.turn_id, conversation_key: keyOf(t), x: Math.cos(i) * (1 + i % 3), y: Math.sin(i) * (1 + i % 2), cluster: 1 + (i % 2),
      batch_id: (ITEMS.find((x) => x.turn_id === t.turn_id) || {}).batch_id || null, reviewed: r.has(t.turn_id), reviewed_by_me: false, question: t.question, started_at: t.started_at }));
    send(res, 200, { ok: true, too_few: false, explained_variance: [0.58, 0.21], feature_names: [{ key: 'tokens', label: 'Tokens' }], nodes: nodes,
      clusters: [1, 2].map((c) => ({ id: c, size: nodes.filter((n) => n.cluster === c).length, reviewed: nodes.filter((n) => n.cluster === c && n.reviewed).length, centroid: { tokens: 1000 * c } })),
      pool_size: pool.length, truncated: false });
    return true;
  }
  if (p === P + '/progress' && m === 'GET') {
    const hs = heads((a) => a.kind === 'verdict' && !a.tombstone);
    const block = Number(q('block')) || 10;
    const points = [];
    for (let i = 0; i < hs.length; i += block) { const ch = hs.slice(i, i + block); points.push({ block: i / block + 1, reviews_through: i + ch.length, wrong: ch.filter((a) => a.verdict === 'wrong').length, partial: ch.length < block, new_modes: null, cumulative_modes: null }); }
    const fair = ITEMS.filter((i) => BATCHES.find((b) => b.id === i.batch_id).counts_for_prevalence).map((i) => hs.filter((a) => a.turn_id === i.turn_id).map((a) => a.verdict));
    const w = fair.filter((v) => v.length && v.every((x) => x === 'wrong')).length; const rt = fair.filter((v) => v.length && v.every((x) => x === 'right')).length;
    send(res, 200, { ok: true, scope: 'company', block: block, plan: plan(), batches: BATCHES.map(batchOut), open_batches: live().length,
      open_coding: { reviewed: new Set(hs.map((a) => a.turn_id)).size, sampled: ITEMS.length, wrong: hs.filter((a) => a.verdict === 'wrong').length, right: hs.filter((a) => a.verdict === 'right').length,
        disputed: 0, missing: ITEMS.filter((i) => !hs.find((a) => a.turn_id === i.turn_id)).length, missing_turns: [], missing_more: 0, reviewers: [] },
      fair_sample: { label: 'From fair (uniform) batches only', items: fair.length, wrong: w, right: rt, disputed: 0, unreviewed: fair.length - w - rt,
        rate: w + rt ? w / (w + rt) : null, ci_low: null, ci_high: null },
      agreement: null, discovery: { block: block, reviews: hs.length, points: points, by_batch: [], modes_available: false },
      saturation: { available: false, hint: false, kind: null, reviews_without_new: null, final_new_modes: null },
      coverage: { pool: poolOf(turns, who).length, reviewed: 0, dimensions: { channel: [], route_target: [], asker_role: [], page: [] }, clusters_unreviewed: [] }, by_company: null });
    return true;
  }
  return false;
}
```

In `~/Desktop/hoist/eval-demo/stub-eval.mjs`, add `import { handleTraceLabM2 } from './stub-tracelab-m2.mjs';` after the existing imports, and immediately before the final `return send(res, 404, { detail: 'not stubbed' });` add:
```js
  if (await handleTraceLabM2({ req, res, p, u, body, who: WHO, turns: TURNS, send })) return;
```
(M1's stub routes for `/api/trace-lab/conversations…` must already be in place from M1's verification.) Start it with `sh ~/Desktop/hoist/eval-demo/start-demo.sh`, open http://127.0.0.1:5180, sign in as `ada@example.com` / `correct-horse-battery-staple`, open Administration › Hoist Traces, and check:
1. Conversations: open "Which gas certificates expire this month?". The verdict bar says "1 other reviewer has judged this answer. Their note stays hidden until you save yours." Select "B-101 boiler room" inside the answer: the popover opens ("What went wrong first? (Enter to save)"); type a note and press Enter. The quote turns yellow, a margin note appears beside it, and the other reviewer's note now shows under "Other reviewers".
2. Select text across the question and the answer: "Select text inside one step." appears; nothing is sent.
3. Press `r` with the focus in the note box: nothing happens. Click elsewhere and press `r`: the answer is saved Right.
4. Map tab: the plan table lists the five steps; Preview a "First fair batch" of 15, then Create batch: "Drew N answers (seed 7). They are in the review queue." The batch row reads "counts toward rates"; "Show the manifest" lists each answer's reason ("uniform pick") and the seed.
5. Progress tab: the four tiles show numbers (not dashes) after step 4; the discovery chart has a point; the saturation line reads "Saturation: — (shown once failure modes are named)"; the wrong-rate card says "From fair (uniform) batches only".
6. In `~/Desktop/hoist/eval-demo/stub.log`, no `PUT`/`POST` to `/api/traces/…/feedback` or `/memories` appears.

- [ ] **Step 2: Prerequisites before anything touches hoistra_test.** P1 has landed on the branch to be deployed (hoist-f5's company-boundary fixes). Read-only reads in your psql shell (memory: psql-connection-recipe):
  ```sql
  SHOW server_version;
  SELECT count(*) AS turns_without_company FROM plenum_cafm.agent_trace_turns WHERE organization_id IS NULL;
  ```
  The first must match the scratch image's major version used to build `catalog.json` (16, R32); the second is the R27 count of turns that cannot be reviewed — note it.

- [ ] **Step 3: Rebuild and reload** (only after P1; check `git status` and the peer sessions for half-done work first — memory: concurrent-sessions-share-working-tree):
  ```sh
  cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps svc-deepagents frontend-app
  docker ps --format '{{.Names}}' | grep gateway
  docker exec hoistra_plenum-gateway-app-1 nginx -s reload
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -c "import numpy; print(numpy.__version__)"
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml logs --since 5m svc-deepagents | grep -E "trace_lab.schema|trace.purge_skipped"
  ```
  Expected: numpy 2.x; `trace_lab.schema.unverified` with `status: behind`, `pending: [2]`, and `trace.purge_skipped` (Trace Lab routes answer 503 until the apply; the chat is unaffected).

- [ ] **Step 4: Verify, review what would run (P3), then dry-run:**
  ```sh
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate status
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate plan
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0002 --applied-by hussain --dry-run
  ```
  Expected: `status` reports `behind` with expected version 2, applied version 1, pending `[2]`; `plan` prints the host, `current_database() = hoistra_test`, applied version 1, pending `0002_review.sql` with its checksum and every statement (four `CREATE TABLE`s, indexes, six triggers, comments — all in `plenum_tracelab`), and no open Trace Lab jobs. The dry run rolls back.

- [ ] **Step 5: Apply (interactive TTY; type the database name when asked):**
  ```sh
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml exec svc-deepagents python -m src.trace_lab.migrate apply --expect-db hoistra_test --through 0002 --applied-by hussain
  ```
  Expected: `trace_lab.schema.applied` for version 2, the ledger row `applied_by = cli:hussain`, and the printed `SHIPPED_CHECKSUMS` line for version 2.

- [ ] **Step 6: Pin the checksum.** Add the printed line (`2: "<sha256>",`) to `migrate.SHIPPED_CHECKSUMS` in `apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/migrate.py` (by hand, or ask a Claude session to make exactly that edit), and confirm `tl_da_test tests/test_trace_lab_migrate.py` passes (`test_shipped_files_are_never_edited`).

- [ ] **Step 7: Live checks (within 30 s of the apply, no restart needed):**
  1. `GET /backend/deep-agents/api/trace-lab/status` (signed in as an admin) answers `{"ok": true, "available": true, "status": "ready", "applied_version": 2, …}`; the log shows `trace_lab.schema.verified` with `version: 2`.
  2. Two admins of one company each judge the same answer (one Right, one Wrong with a note): Conversations shows the second reviewer the blind line until they save; afterwards both verdicts are listed and `GET …/turns/<turn_id>/annotation-history` shows two lineages, neither overwritten.
  3. Map tab: draw a "First fair batch" of 15 for the last 30 days; "Show the manifest" lists 15 answers with "uniform pick", seed 7, tagged "counts toward rates".
  4. Progress tab: after ten reviews the discovery chart gains its first full block; "Missing judgments" falls as the batch is reviewed.
  5. On the next svc-deepagents restart the log shows the purge running with the keep-list (`trace.purged` … `kept_by: 2`, or no purge line when nothing is older than the retention window) — never `trace.purge_skipped`.
  6. Gate verdicts (R23): open an answer that went through an answer check without having judged it: the check card says its verdict is hidden and the judge's model-call card (and its row in Model calls) shows no reply. Save a verdict on that answer and reload: the check card shows its outcome and the judge's reply appears. Another reviewer who has not judged it still sees the hidden note.

- [ ] **Step 8: Azure.** No action: images with this code only verify (`TRACE_LAB_SCHEMA` unset). Remind Bala never to set `TRACE_LAB_SCHEMA=apply` in the Container App portal.
