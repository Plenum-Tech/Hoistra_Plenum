# Trace Lab M1 — Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Hoist's own trace tables into Trace Lab conversations a reviewer can read: a read-through normalizer (timeline, model-call cards, redaction, flags), conversation grouping, the read-only conversations / model-calls / runs API, and the Hoist Traces page as a tabbed Trace Lab (Overview, Conversations, Runs, Model calls) with URL state, reload restore, a 45 s update check and read-only asker thumbs.
**Architecture:** svc-deepagents gains `src/trace_lab/normalize.py` (pure functions: one `agent_trace_turns` row + its spans → one record), `src/trace_lab/conversations.py` (company-scoped READ ONLY SQL over `plenum_cafm.agent_trace_turns/spans`, `plenum_tracelab.turn_meta`, `plenum_cafm.chat_threads`, `plenum_cafm.agent_activity_log`) and `src/api/routes/trace_lab_conversations.py` (8 GET routes under `/api/trace-lab`). The frontend mounts `screens/TraceLab.jsx` in place of `Traces.jsx`; the old page becomes the Overview tab; new state lives in `logic/traceLab.js` (shell) and `logic/traceLabConversations.js` (reads), cards come from the pure `logic/traceLabTimeline.js`. M1 adds no DDL and no write route.
**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + asyncpg, pytest + pytest-asyncio (auto mode); React 18 (controller + `vals` pattern), `node --test`, esbuild + react-dom/server for render tests; playwright-core against a stub backend for the browser walk.
**Spec:** `docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md` (sections: head — D4, D13, D14, R7, R13, R14, R20–R24, R36, R37, Milestones M1 row; "Trace capture, normalizer and conversations" §2.1 (inferred classifier, read only), §2.2 (read-time repair), §3, §4, §5, §6 (M1 tests), §7; "Frontend: the Trace Lab inside Hoist Traces" §1–§3, §4.1, §4.2 (read-only parts), §4.3 (list only), §4.4, §5, §6, §7)
**Depends on:** M0 plan (`src/trace_lab/db.py`, `src/trace_lab/provenance.py`, `migrations/trace_lab/0001_foundation.sql` applied, `tests/trace_lab_scratch.py`) / prerequisites P1 (before any rebuild), P3 for M0's `0001` (Trace Lab routes answer 503 until it is applied). M1 needs no new migration.

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

M1-specific:
- **The test helper.** The container's `/bin/sh` is dash, which does not expand `{a,b}`; every task below runs the shared recipe through this function (the same steps, with both `mkdir` paths spelled out as above and in `eval-demo/run_da_tests.sh`, and `TRACE_LAB_TEST_DB_URL` taken from your shell). Paste it once per shell:
  ```sh
  tl_da_test() {
    cd /Users/hussain/Desktop/hoist/Hoistra_Plenum || return 1
    C=hoistra-testenv-svc-deepagents-1; R=/tmp/tl-$(date +%s)-$$
    D=apps/backend/cafm-connector-service-final
    docker exec $C sh -lc "test ! -e $R && mkdir -p $R/$D/svc-deepagents $R/$D/svc-operations-intelligence $R/apps/frontend" || return 1
    for d in src tests skills migrations pyproject.toml; do docker cp $D/svc-deepagents/$d $C:$R/$D/svc-deepagents/$d >/dev/null; done
    docker cp apps/frontend/src $C:$R/apps/frontend/src >/dev/null
    docker cp $D/svc-operations-intelligence/src $C:$R/$D/svc-operations-intelligence/src >/dev/null
    N='postgresql+asyncpg://t:t@127.0.0.1:1/t'
    docker exec -e DB_URL=$N -e db_url=$N -e DATABASE_URL=$N -e database_url=$N -e ANTHROPIC_API_KEY=dummy \
      -e TRACE_LAB_TEST_DB_URL="${TRACE_LAB_TEST_DB_URL:-}" $C \
      sh -lc "cd $R/$D/svc-deepagents && python -m pytest $* -q -p no:cacheprovider -p no:warnings"
  }
  ```
  Unit tasks run with `TRACE_LAB_TEST_DB_URL` unset (SQL tests skip). Task 10 exports it.
- Every M1 query runs inside `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout = '10s'` (Postgres refuses any write, the shared DB is protected from slow scans).
- Every M1 route is GET. A frontend walk that sees any non-GET other than the stub's sign-in fails.
- R14 is a behaviour change: admins can no longer rate an answer from the Overview tab. Tell Bala (spec R14).
- `turn_meta` is read through `to_jsonb(tm)`, so M1 does not depend on the exact column list of M0's table; it reads only the keys named in Interfaces below.

## Review Focus

1. **Loop model calls and tools interleave as they happened.** The orchestrator loop's model spans hang under an `orchestrator loop` agent span while its tools hang from the root (trace.py:343, :519); a literal depth-first walk lists every model call before every tool and breaks "User → Model → Tool → Model → Answer". Pinned by `test_agent_groups_are_transparent_for_order` (Task 3) and `test_a_real_multi_turn_chat_reads_as_one_conversation` (Task 10).
2. **Secrets reaching a reviewer's screen** through prompts, tool results, JSON stored as strings, cut JSON, Fiix credential lines or the model-calls list preview. Pinned by `test_credential_keys_are_redacted_everywhere`, `test_json_inside_strings_and_truncated_json_are_redacted` (Task 2) and `test_model_call_rows_are_redacted` (Task 10).
3. **A gate's verdict leaking before the reviewer's own verdict (R23)** — not only on the gate card, but through the judge's own model-call card under the gate and through the list's response preview. Pinned by `test_gate_verdict_and_its_model_calls_stay_hidden` (Task 5) and `test_gate_children_are_withheld_in_the_model_call_list` (Task 10).
4. **A company switch or sign-out leaving the previous company's conversations on screen**, or a late reply repainting them. Pinned by `tlResetPatch clears every tl* and tp* key the page ever sets` (Task 13) and `a reply that lands after a company switch is dropped` (Task 15).
5. **The 45 s auto-refresh closing what the reviewer has open** (conversation, expanded panels, the model-call side panel). Pinned by `auto-refresh merges new rows by key and never closes the open conversation or its panels` (Task 15).

## Interfaces

**Consumed from M0 (assumed names — Task 1 pins each one with a test before anything else is built):**
```python
# src/trace_lab/db.py  (spec §"Data model" 1.10 + §"Trace capture" 5.1)
SCHEMA = "plenum_tracelab"
def ready() -> bool
async def require_ready() -> None                     # raises HTTPException(503) while Trace Lab is not verified
def require_trace_store() -> None                     # 503 trace_store_unavailable (retryable) while trace.ready() is False
def require_reviewer(principal: Principal, organization_id: str | None) -> str   # _act; 403 reviewers_only; 400 name_a_company
def scope_sql(principal: Principal, org: str, alias: str = "tt") -> tuple[str, dict[str, Any]]  # trace._scope_sql, alias-qualified
# src/trace_lab/provenance.py  (spec §"Trace capture" 2.1; R36 names inferred_channel_sql)
CHANNELS: tuple[str, ...]                             # ("chat","ui_action","support","upload","correction","report","workflow","golden")
LEGACY_CLASSIFIER = "legacy-1"
def inferred_channel_sql(alias: str = "tt") -> str    # the CASE expression of §2.1 over agent_trace_turns columns
def infer_channel(turn: Mapping[str, Any], *, activity_input: Mapping[str, Any] | None = None) -> dict[str, Any]
    # -> {"channel", "origin", "channel_source": "inferred", "classifier_version": LEGACY_CLASSIFIER, "reason"}
# tests/trace_lab_scratch.py  (spec §"Data model" 1.8)
scratch_db                                            # pytest fixture: §1.8 steps 1-6, yields the scratch AsyncEngine,
                                                      # database._get_engine patched to it, trace tables created and truncated
# plenum_tracelab.turn_meta keys M1 reads (0001_foundation.sql, spec §"Data model" 2.2):
#   turn_id, channel, channel_source, classifier_version, origin, origin_params, page, context_text,
#   asker_question, asker_role, asker_scope, rerun_of, golden_run_id, golden_case_key, rep
```
M0 owns every one of these names (M0 Task 3 `0001_foundation.sql`, Task 5 `db.py`, Task 9 `tests/trace_lab_scratch.py`, Tasks 16–17 `provenance.py`; R36), so M1 adds nothing to M0's files. If Task 1 finds one missing or shaped differently, stop and ask Hussain; never add a second definition.

**Produced for later milestones (names fixed here, per R36):**
```python
# src/trace_lab/normalize.py — pure
NORMALIZER_VERSION = "norm-1"
def normalize_turn(turn, spans, *, meta=None, activity_input=None, conversation=None,
                   reveal_gate_verdicts: bool = False) -> dict[str, Any]
    # M2 passes reveal_gate_verdicts=True once the reviewer has saved a verdict on that turn (R23).
def repair_question(turn, meta, activity_input) -> tuple[str, list[str]]   # the asker's words + question_* flags
def redact(value) -> Any; def redact_text(text: str) -> str; def sensitive_key(key) -> bool
def strip_reasoning(value) -> Any
def model_call_record(span, *, turn_id, group, prompt_ref, withheld: bool = False) -> dict[str, Any]
def call_label(kind, name, request_kind, loop_output: bool, requested_tools: int) -> str
def pick_tree(turn, spans) -> tuple[list, list[str]]; def walk(spans, root_id) -> list[tuple[span, depth, group]]
def session_kind(session_id, *, thread_exists: bool) -> str   # "observed" | "synthetic" | "inferred"
def group_key(session_id, turn_id) -> str                       # "t:<turn>" | "r:<turn>" | "s:<session>"
def session_label(kind, session_id, *, title, golden) -> str
def turn_header(turn, meta, *, turn_index: int, attempt: Mapping) -> dict[str, Any]
def attempts(turns, metas, activity_inputs=None) -> dict[str, dict[str, Any]]
def features(turn, messages=None) -> dict[str, Any]; def flatten_text(messages) -> str
def block_text(span, field) -> str                              # field "input" | "output": the text the timeline shows (Review 2.2, R36)
    # Every message carries "block": block_text(span, "output") on span messages anchored on output, "" on a withheld
    # gate call (R23), None on the question, answer, tool-call and gate messages. M2 reads m["block"]; M4 calls it.
# src/trace_lab/conversations.py — DB loaders (normalize.py stays pure, so the "normalize.load_turn(s)" the
# Judges section assumed lives here; M4-M6 import conversations.load_turn / load_turns)
class BadRequest(ValueError): reason: str; error: str
@dataclass(frozen=True) class TurnFilter: ...; class ModelCallFilter(TurnFilter): ...
GROUP_KEY_SQL: str; SESSION_KIND_SQL: str; UPDATE_LOOKBACK_MINUTES = 30
def make_filter(**query) -> TurnFilter | ModelCallFilter       # M2 replaces its reviewed_filter_unavailable branch
def encode_cursor(t: datetime, k: str) -> str; def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None
async def list_conversations(principal, org, f, *, cursor, limit) -> dict
async def conversation_updates(principal, org, f, *, watermark) -> dict
async def conversation_facets(principal, org, f) -> dict
async def get_conversation(principal, org, key, *, turn_id=None, include_deleted=False) -> dict | None
async def get_turn(principal, org, turn_id) -> dict | None
async def list_runs(principal, org, *, since, until) -> dict      # calls golden.run_summaries(principal, org, since, until) when M3's module exists
async def list_model_calls(principal, org, f, *, cursor, limit) -> dict
async def get_model_call(principal, org, span_id) -> dict | None
async def load_turn(turn_id: str) -> dict | None                  # unscoped, for jobs (M4-M6)
async def load_turns(turn_ids: Sequence[str]) -> list[dict]       # unscoped, for jobs; order kept, missing ids skipped
```
```js
// apps/frontend/src/logic/traceLab.js
export const TL_VIEWS, TL_TABS, TL_CHANNELS, CHANNEL_LABELS, TL_FILTER_DEFAULTS, TL_POLL_MS, TL_POLLED_VIEWS;
export function tlResetPatch();               // every tl* and tp* key at its default (later milestones add their keys here)
export function tlTabsFor(state); export function tlHasCompany(state); export function tlQueryString(search, st); export function tlParseLink(search);
export const traceLabMethods;                 // tlOpen, tlSetView, tlReadView, tlPoll, tlRefresh, tlWrite(fn, {key, retry}), tlKey, ...
export function traceLabVals(c, now);
// apps/frontend/src/logic/traceLabConversations.js
export const TL_PAGE_SIZE = 25, TL_CALLS_PAGE_SIZE = 50; export function groupingChip(conv); export function mergeByKey(oldRows, freshRows);
export const traceLabConversationsMethods; export function traceLabConversationsVals(c, now);
// apps/frontend/src/logic/traceLabTimeline.js — pure
export function toCards(record); export function modelCallCard(rec, id); export function labelFor(kind, name);
export function humanize(name); export function collapse(text, n = 240); export function pretty(v); export function wordDiff(a, b);
// apps/frontend/src/api/traceLab.js
export const traceLabApi;                     // conversations, conversationUpdates, conversationFacets, conversation, turn, runs, modelCalls, modelCall
```

**Spec reconciliations made here (report to Hussain):**
- Agent spans are transparent for ordering (Review Focus 1); §3.4's literal depth-first walk cannot meet the M1 acceptance order. Depth counts non-agent ancestors; `group` still names the agent.
- Tool requests link to tool spans in the same *scope* (nearest non-agent ancestor), for the same reason (§3.6 says "same parent").
- `permalink` uses the frontend's URL keys (`?tl=conversations&run=…&conv=…&turn=…`, §Frontend 2) instead of §3.2's `?view=…`.
- The update check sends the backend's `watermark` parameter (§5.3), not §Frontend 2's `since=<cursor>`.
- The model-call card label comes from the server's per-call rule (§3.5: `output.tool_calls` non-empty → "tool decision"); the repo's positional rule (§Frontend 3) is the fallback when a record has no label.
- The UI prints the record's `cot_boundary` under every model-call group and `cot_note` on every card (§3.8 "the UI prints the record's text"), not §Frontend 3's paraphrase.
- `session_kind` values are `observed | synthetic | inferred` (§4.1); the Judges section's assumed `shared | missing` map to "not observed" (M5's split rule only branches on `observed`).
- The Hoist Traces nav badge stays "loaded answers" in M1: the to-review count needs M2's batches.
- Gate verdicts are withheld server-side (R23) — on the gate message, on the judge's model-call cards under the gate, and in the model-calls list preview — until M2 passes `reveal_gate_verdicts=True`.
- The model-calls list rows also carry `question_excerpt` (§Frontend 4.4 asks for a question column; §5.4's row has none).

## File Structure

svc-deepagents (`apps/backend/cafm-connector-service-final/svc-deepagents/`):
| File | Change | Responsibility |
|---|---|---|
| `src/trace_lab/normalize.py` | create | Pure normalizer: redaction, reasoning strip, tree pick/walk, model-call cards, timeline messages, flags, grouping helpers |
| `src/trace_lab/conversations.py` | create | Filters, cursors, company-scoped READ ONLY SQL and loaders for conversations, updates, facets, runs, model calls; unscoped `load_turn(s)` for jobs |
| `src/api/routes/trace_lab_conversations.py` | create | 8 GET routes; reviewer + readiness + trace-store checks; error envelope |
| `src/api/main.py` | modify :40, :255 | Import and include the router |
| `tests/test_trace_lab_m1_preconditions.py` | create | Pins every M0 name M1 consumes |
| `tests/test_trace_lab_redaction.py` | create | Keys and values redaction |
| `tests/test_trace_lab_normalize.py` | create | Tree, order, roles, cards, flags, purity |
| `tests/test_trace_lab_grouping.py` | create | Session kinds, keys, labels, attempts, headers |
| `tests/test_trace_lab_conversations.py` | create | Filters, cursors, routes (auth, 503, 400, 404) without a DB |
| `tests/test_trace_lab_conversations_sql.py` | create | Scratch-DB SQL: acceptance chat, paging, totals, updates, filters, model calls, scope, parity |

apps/frontend:
| File | Change | Responsibility |
|---|---|---|
| `src/api/traceLab.js` | create | One function per M1 route, through `apiFetch` with `orgOverride()` |
| `src/api/deepAgents.js` | modify :53 | Export `orgOverride` |
| `src/logic/traceLab.js` | create | Shell: tabs, defaults/reset, URL, poll, keyboard, write helper, vals |
| `src/logic/traceLabConversations.js` | create | Loaders + vals for conversations, conversation, runs, model calls |
| `src/logic/traceLabTimeline.js` | create | Pure record → cards, labels, collapse, word diff |
| `src/logic/tracesPage.js` | modify | Export `sinceFor`; R14: remove `tpRate`/`tpSetComment`/`tpSaveComment`; read-only rating + "Review this answer in Conversations" |
| `src/logic/session.js` | modify | Persist and validate `tlView`, `tlRunId`, `tlConvKey`, `tlFilters` |
| `src/logic/core.js` | modify | Mount read on `view === 'traces'`; reset patch; clear poll on unmount |
| `src/logic/auth.js` | modify | Read the `?tl=` link at boot, open it after sign-in, reset + scrub on sign-out |
| `src/logic/HoistraLogic.js` | modify | Defaults, mixins, scrub the URL when leaving the page |
| `src/logic/renderVals.js` | modify | Spread the two Trace Lab vals |
| `src/logic/integrations.js` | modify :95 | Nav opens `tlOpen()` |
| `src/logic/support.js` | modify :112 | Support help names the tabs |
| `src/screens/TraceLab.jsx` | create | Tab shell, filters, update line, shortcut sheet; Overview = `Traces` |
| `src/screens/traceLab/Conversations.jsx` | create | List + conversation (chips, answers, flags, timeline) |
| `src/screens/traceLab/Timeline.jsx` | create | Cards as `article` elements |
| `src/screens/traceLab/ToolCard.jsx` | create | Tool call + result, and database query cards |
| `src/screens/traceLab/ModelCallCard.jsx` | create | Model-call card with four collapsed panels |
| `src/screens/traceLab/RawPanel.jsx` | create | The only place raw names, SQL and payloads appear |
| `src/screens/traceLab/Runs.jsx` | create | Runs list (live pseudo-run) |
| `src/screens/traceLab/ModelCalls.jsx` | create | Aggregate header, table, side panel |
| `src/screens/Traces.jsx` | modify | `tabs` prop; "Runs" → "Answers"; read-only rating; link |
| `src/App.jsx` | modify :25, :85 | Mount `TraceLab` |
| `src/styles/tracelab.css` | create | Tabs, cards, panels, focus, reduced motion |
| `src/styles/base.css` | modify | `@import './tracelab.css'` |
| `test/traceLabTimeline.test.mjs` | create | Cards, labels, collapse, diff |
| `test/traceLabShell.test.mjs` | create | Tabs, URL, slice, reset, mount, keyboard, write helper, link |
| `test/traceLabConversations.test.mjs` | create | Paging, chips, merge, stale replies, open-by-turn, model calls |
| `test/traceLabCompanyScope.test.mjs` | create | Every call carries `orgOverride()`, never `currentOrgId()` |
| `test/traceLabViews.test.mjs` | create | Rendered markup: tabs, articles, panels, dialog, Overview |
| `test/tracesPage.test.mjs` | modify | R14 assertions |
| `test/noInternalNames.test.mjs` | modify | SKIP `src/screens/traceLab/RawPanel.jsx` |

Outside the repo (Task 20): `~/Desktop/hoist/eval-demo/stub-tracelab.mjs`, `~/Desktop/hoist/eval-demo/start-tracelab.sh`, `~/Desktop/hoist/eval-demo/walks/verify-tracelab-conversations.mjs`.

---

### Task 1: Pin the M0 names M1 consumes

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_trace_lab_m1_preconditions.py`
- Modify: none — M0's `src/trace_lab/db.py`, `src/trace_lab/provenance.py` and `tests/trace_lab_scratch.py` are read, never edited (R36)

**Interfaces:**
- Consumes: every M0 name listed under Interfaces.
- Produces: a green precondition suite; nothing else.

- [ ] **Step 1: Write the failing test**

```python
"""M1 builds on M0's names. This file fails first, loudly, if any of them is missing or shaped
differently, so no later task is built on a guess (R36)."""
from __future__ import annotations

import inspect
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.services.principal import Principal

ROOT = Path(__file__).resolve().parents[1]


def _principal(role="admin", org=None):
    return Principal(user_id=uuid.uuid4(), email="fm@example.com",
                     organization_id=uuid.UUID(org) if org else uuid.uuid4(), role=role,
                     can_ingest=True, building_ids=None)


def test_db_exposes_the_names_m1_uses():
    from src.trace_lab import db
    assert db.SCHEMA == "plenum_tracelab"
    assert callable(db.ready)
    assert inspect.iscoroutinefunction(db.require_ready)
    assert callable(db.require_trace_store)
    assert callable(db.require_reviewer) and callable(db.scope_sql)


def test_require_reviewer_follows_section_5_1():
    from src.trace_lab import db
    admin = _principal("admin")
    assert db.require_reviewer(admin, None) == str(admin.organization_id)
    with pytest.raises(HTTPException) as e:
        db.require_reviewer(_principal("user"), None)
    assert e.value.status_code == 403 and e.value.detail["reason"] == "reviewers_only"
    with pytest.raises(HTTPException) as e:
        db.require_reviewer(admin, str(uuid.uuid4()))
    assert e.value.status_code == 403
    sa = Principal(user_id=uuid.uuid4(), email="sa@example.com", organization_id=None, role="superadmin",
                   can_ingest=True, building_ids=None)
    with pytest.raises(HTTPException) as e:
        db.require_reviewer(sa, None)
    assert e.value.status_code == 400 and e.value.detail["reason"] == "name_a_company"
    other = str(uuid.uuid4())
    assert db.require_reviewer(sa, other) == other


def test_scope_sql_is_trace_scope_qualified_by_alias():
    from src.trace_lab import db
    admin = _principal("admin")
    where, params = db.scope_sql(admin, str(admin.organization_id), alias="tt")
    assert "tt.organization_id = CAST(:org AS uuid)" in where and params["org"] == str(admin.organization_id)
    assert "user_id" not in where


def test_provenance_exposes_the_read_time_classifier():
    from src.trace_lab import provenance
    assert tuple(provenance.CHANNELS) == ("chat", "ui_action", "support", "upload", "correction", "report", "workflow", "golden")
    assert provenance.LEGACY_CLASSIFIER == "legacy-1"
    sql = provenance.inferred_channel_sql("tt")
    assert sql.strip().upper().startswith("CASE") and "tt.session_id" in sql and "golden" in sql
    out = provenance.infer_channel({"turn_id": "turn-0000000000000001", "session_id": "report-1", "question": "x",
                                    "answer": "y", "route": None})
    assert out["channel"] == "report" and out["channel_source"] == "inferred"
    assert out["classifier_version"] == provenance.LEGACY_CLASSIFIER


def test_turn_meta_carries_the_columns_m1_reads():
    sql = (ROOT / "migrations" / "trace_lab" / "0001_foundation.sql").read_text(encoding="utf-8")
    body = sql.split("CREATE TABLE plenum_tracelab.turn_meta", 1)[1].split(");", 1)[0]
    for col in ("turn_id", "channel", "channel_source", "classifier_version", "origin", "origin_params", "page",
                "context_text", "asker_question", "asker_role", "asker_scope", "rerun_of", "golden_run_id",
                "golden_case_key", "rep"):
        assert f"\n    {col} " in body or f"\n    {col}\t" in body, col


def test_the_scratch_fixture_is_importable():
    import trace_lab_scratch
    assert hasattr(trace_lab_scratch, "scratch_db")
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_m1_preconditions.py`
Expected before M0 is in the tree: `ModuleNotFoundError: No module named 'src.trace_lab'`. With M0 in the tree it should pass; any failure names the missing piece.

- [ ] **Step 3: Implement**

M1 adds nothing: M0 Task 5 defines `require_trace_store`, `require_reviewer` and `scope_sql`, M0 Task 17 defines `inferred_channel_sql`, M0 Task 9 defines `scratch_db`, and a second definition here would break R36. If the suite fails — a missing or differently shaped M0 name (a `db` or `provenance` name, the `scratch_db` fixture or a `turn_meta` column): stop and ask Hussain.

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_m1_preconditions.py`
Expected: `6 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 2: Normalizer constants and redaction

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/normalize.py`
- Test: `apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_trace_lab_redaction.py`

**Interfaces:**
- Consumes: `src.agents.fiix_credential_parse._CRED_LINE_PATTERNS` (fiix_credential_parse.py:16-36, list of `(pattern, field)`).
- Produces: `NORMALIZER_VERSION`, the §3.1 constants, `REDACTED = "[redacted]"`, `sensitive_key(key) -> bool`, `redact_text(text) -> str`, `redact(value) -> Any`.

- [ ] **Step 1: Write the failing test**

```python
"""Trace Lab redaction (spec §"Trace capture" 3.9): keys AND values, everywhere a record leaves the server.
The repo redacted key names only, missed JSON stored as strings and x-api-key/access_key/app_key, and
redacted token counts. Emails stay (reviewers are the company's admins)."""
from __future__ import annotations

import json

from src.trace_lab import normalize as n


def test_credential_keys_are_redacted_everywhere():
    for key in ("Authorization", "x-api-key", "apiKey", "APIKey", "access_key", "app_key", "client_secret",
                "refresh_token", "id_token", "sessionToken", "password", "AccountKey", "connection_string",
                "dsn", "sas_token", "token", "sig", "signature", "private_key", "cookie", "set-cookie"):
        assert n.sensitive_key(key), key
    value = {"headers": {"x-api-key": "abc123", "Authorization": "Bearer abcdefghijkl"},
             "creds": {"app_key": "A", "access_key": "B", "secret_key": "C"},
             "nested": [{"client_secret": {"v": 1}}, {"refresh_token": ["x"]}],
             "empty_password": "", "fiix_credentials_configured": True, "password_changed_at": "2026-10-01"}
    out = n.redact(value)
    assert out["headers"]["x-api-key"] == "[redacted]" and out["headers"]["Authorization"] == "[redacted]"
    assert out["creds"] == {"app_key": "[redacted]", "access_key": "[redacted]", "secret_key": "[redacted]"}
    assert out["nested"] == [{"client_secret": "[redacted]"}, {"refresh_token": "[redacted]"}]
    assert out["empty_password"] == "" and out["fiix_credentials_configured"] is True
    assert out["password_changed_at"] == "2026-10-01"
    assert value["headers"]["x-api-key"] == "abc123"          # the input is never changed


def test_token_counts_are_not_redacted():
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "prompt_tokens", "max_tokens", "token_count",
                "total_tokens"):
        assert not n.sensitive_key(key), key
    assert n.redact({"input_tokens": 900, "max_tokens": 4096}) == {"input_tokens": 900, "max_tokens": 4096}


def test_secret_values_are_redacted_in_free_text():
    text = ("call with Bearer abcdefghijklmnop and sk-ant-api03-ABCDEFGHIJKLMNOPQRST and sk-proj-abcdefghijklmnopqrstu "
            "jwt eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSM aws AKIAABCDEFGHIJKLMNOP gh ghp_" + "a" * 36 + " "
            "DefaultEndpointsProtocol=https;AccountKey=abc/def==;EndpointSuffix=core "
            "https://blob.example/x?sv=1&sig=abcdef&se=2 postgresql://user:pw123@db.example:5432/x "
            "api_key=zzz password: 'hunter2' "
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----")
    out = n.redact_text(text)
    for secret in ("abcdefghijklmnop", "ABCDEFGHIJKLMNOPQRST", "abcdefghijklmnopqrstu", "SflKxwRJSM",
                   "AKIAABCDEFGHIJKLMNOP", "a" * 36, "abc/def==", "sig=abcdef", "pw123", "zzz", "hunter2", "MIIabc"):
        assert secret not in out, secret
    assert "Bearer [redacted]" in out and "AccountKey=[redacted]" in out and "sig=[redacted]" in out
    assert "postgresql://user:[redacted]@db.example" in out and "[redacted private key]" in out


def test_fiix_credential_lines_are_redacted():
    text = "Subdomain: acme\nApp Key: app-123\nAPI Access Key = acc-456\nSecret: sec-789\n"
    out = n.redact_text(text)
    assert "Subdomain: acme" in out
    for secret in ("app-123", "acc-456", "sec-789"):
        assert secret not in out


def test_json_inside_strings_and_truncated_json_are_redacted():
    inner = json.dumps({"query": "boilers", "api_key": "sk-live-1", "rows": [{"access_token": "t0k"}]})
    out = n.redact({"output": inner})
    parsed = json.loads(out["output"])
    assert parsed["api_key"] == "[redacted]" and parsed["rows"][0]["access_token"] == "[redacted]"
    assert parsed["query"] == "boilers"
    cut = {"_truncated": '{"system_prompt": "You are...", "client_secret": "s3cr3t-value", "x": "y", "password": "abc'}
    red = n.redact(cut)["_truncated"]
    assert "s3cr3t-value" not in red and "abc" not in red.split('"password"')[1]
    assert '"system_prompt": "You are..."' in red


def test_emails_are_kept():
    assert n.redact_text("Ask ops@acme.co.uk or fm@example.com") == "Ask ops@acme.co.uk or fm@example.com"
    assert n.redact({"email": "fm@example.com"}) == {"email": "fm@example.com"}
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_redaction.py`
Expected: `ImportError: cannot import name 'normalize' from 'src.trace_lab'` (6 errors).

- [ ] **Step 3: Implement**

Create `src/trace_lab/normalize.py`:

```python
"""Trace Lab normalizer: one plenum_cafm.agent_trace_turns row and its agent_trace_spans become a Trace
Lab record - a timeline of messages, model-call cards, groups, usage and flags.

Spec: docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md, "Trace capture, normalizer and
conversations" §3 and §4. Pure functions: no database, no clock, no mutation of the inputs. The loaders
live in conversations.py.

One reading departs from the letter of §3.4, deliberately: an `agent` span has no message of its own, so
it is transparent for ordering - its children sort among the agent's siblings by seq. The orchestrator
loop's model calls hang under an "orchestrator loop" agent span while its tools hang from the root
(trace.py:343, :519); a strict depth-first walk would list every model call before every tool. `group`
still names the agent on every descendant.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

from ..agents.fiix_credential_parse import _CRED_LINE_PATTERNS

NORMALIZER_VERSION = "norm-1"
ROLES = ("user", "model_call", "tool_call", "tool_result", "db_query", "plan", "step", "gate", "assistant", "system_note")
RESULT_COLLAPSE_CHARS = 240       # repo rule: longer results start collapsed
DB_ROWS_SHOWN = 10                # rows shown of the <=50 a db span keeps (trace.py:477, 494-498)
TEXT_FLATTEN_CHARS = 2000         # per message in `text`
ALWAYS_UNAVAILABLE = ("finish_reason", "response_id", "provider", "time_to_first_token",
                      "tool_call_ids", "cache_write_tokens", "reasoning_tokens")
GATE_LABELS = {"el_udr": "Answer check", "contract_compose": "Vendor answer rewrite"}
CHECK_STEP = re.compile(r"^(verify claims|verify figures|check s\d+|step gates)$")
COT_BOUNDARY = ("Model reasoning is not shown. Each model card shows what the model was sent and what it returned, "
                "as recorded. Hoist does not keep a model's private reasoning and stores no provider summary of it, "
                "so a missing reasoning transcript is not a missing step. The plan, routing decisions, tool calls "
                "and their results are the evidence.")
COT_CARD_NOTE = "Private reasoning: not recorded (not a missing step)."
PUBLISHED_SUMMARY_LABEL = "Provider-published summary — not raw chain-of-thought"   # repo string; only if a span carries one

#: Between the page context and the asker's words on the REST path (orchestrator.py:914).
QUESTION_MARKER = "\n\n---\n\n**Current user message:**\n"
#: Characters of a question the trace keeps (trace.py:328).
QUESTION_CHARS = 4000
REDACTED = "[redacted]"
REASONING_HIDDEN = "[reasoning not shown]"
#: EL-UDR's footnotes and refusal text (udr_response_evaluator.py:371-406), for answers with no gate span.
GATE_FOOTNOTES = (("degraded", "_Automated accuracy check: flagged for a quick human verification"),
                  ("not_evaluated", "_The automated accuracy check could not run on this answer"))
GATE_BLOCKED_PREFIX = "I found related data but couldn't fully verify an answer ("

_MAX_DEPTH = 64
_JSON_PARSE_CHARS = 200_000
_NOT_JSON = object()

# ── redaction (§3.9) ─────────────────────────────────────────────────────────────────────────────

_SAFE_KEYS = frozenset({"password_changed_at", "fiix_credentials_configured", "max_tokens", "token_count"})
_KEY_RE = re.compile(
    r"(^|_)(authorization|proxy_authorization|cookie|set_cookie|password|passwd|pwd|secret|client_secret|"
    r"private_key|credentials?|connection_string|conn_str|dsn)($|_)"
    r"|(^|_)(api|app|access|secret|client|subscription|account|master|storage)_?key($|_)"
    r"|(^|_)(access|refresh|id|auth|bearer|session|sas|csrf|xsrf|api)_?token($|_)"
    r"|^(token|jwt|sig|signature)$")

_VALUE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}=*"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), "[redacted token]"),
    (re.compile(r"\bsk-(?:ant-|proj-|svcacct-|admin-)?[A-Za-z0-9_-]{16,}"), "[redacted key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[redacted key]"),
    (re.compile(r"(?i)\b(AccountKey|SharedAccessKey|SharedAccessSignature)=[^;\s\"']+"), r"\1=[redacted]"),
    (re.compile(r"(?i)([?&](?:sig|signature|token|access_token|api_key|apikey)=)[^&\s\"']+"), r"\1[redacted]"),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^/\s@]+@"), r"\1:[redacted]@"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|app[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|password|passwd|pwd"
                r"|access[_-]?token|refresh[_-]?token)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1\2[redacted]"),
)
#: The Fiix credential lines (app, access and secret keys; the subdomain line is kept).
_FIIX_SECRET_LINES = tuple(p for p, field in _CRED_LINE_PATTERNS if field in ("app_key", "access_key", "secret_key"))
#: A "key": "value" pair inside text that did not parse as JSON (a cut payload); the value may run to the end.
_QUOTED_PAIR = re.compile(r'"((?:[^"\\]|\\.){1,80})"\s*:\s*"(?:[^"\\]|\\.)*(?:"|$)')


def _norm_key(key: Any) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[-.\s]+", "_", s).lower()


def sensitive_key(key: Any) -> bool:
    k = _norm_key(key)
    return k not in _SAFE_KEYS and _KEY_RE.search(k) is not None


def _redact_group1(m: re.Match[str]) -> str:
    whole, start = m.group(0), m.start(0)
    return whole[: m.start(1) - start] + REDACTED + whole[m.end(1) - start:]


def _value_rules(text: str) -> str:
    out = text
    for rx, repl in _VALUE_RULES:
        out = rx.sub(repl, out)
    for rx in _FIIX_SECRET_LINES:
        out = rx.sub(_redact_group1, out)
    return out


def _keys_in_text(text: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return f'"{m.group(1)}": "{REDACTED}"' if sensitive_key(m.group(1)) else m.group(0)
    return _QUOTED_PAIR.sub(sub, text)


def redact_text(text: str) -> str:
    """Secret values out of one string. A string that is JSON is parsed, redacted as a structure and written
    back; a cut JSON prefix gets the value rules plus the key-in-text rule."""
    if not isinstance(text, str) or not text:
        return text
    if text.lstrip()[:1] in ("{", "["):
        if len(text) <= _JSON_PARSE_CHARS:
            try:
                parsed: Any = json.loads(text)
            except (ValueError, RecursionError):
                parsed = _NOT_JSON
            if isinstance(parsed, (dict, list)):
                return json.dumps(redact(parsed), ensure_ascii=False)
        return _keys_in_text(_value_rules(text))
    return _value_rules(text)


def redact(value: Any, _depth: int = 0) -> Any:
    """A copy of `value` with sensitive keys' values (non-empty strings, objects, arrays) replaced and every
    string run through redact_text. Booleans, numbers and nulls under a sensitive key are kept."""
    if _depth > _MAX_DEPTH:
        return "[cut: nested too deep]"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if sensitive_key(k) and ((isinstance(v, str) and v != "") or isinstance(v, (Mapping, list, tuple))):
                out[str(k)] = REDACTED
            else:
                out[str(k)] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_redaction.py`
Expected: `6 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 3: Tree pick, walk and the reasoning strip

**Files:**
- Modify: `src/trace_lab/normalize.py` (append to the end)
- Test: `tests/test_trace_lab_normalize.py` (create; later tasks append)

**Interfaces:**
- Consumes: Task 2's constants, `_JSON_PARSE_CHARS`, `_NOT_JSON`, `REASONING_HIDDEN`, `PUBLISHED_SUMMARY_LABEL`.
- Produces: `strip_reasoning(value)`, `pick_tree(turn, spans) -> (spans, flags)`, `walk(spans, root_id) -> [(span, depth, group)]`; private helpers `_ts`, `_iso`, `_sid`, `_i`, `_f`, `_list`, `_seq`, `_sibling_key` used by Tasks 4–6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_lab_normalize.py`:

```python
"""Trace Lab normalizer (spec §"Trace capture" 3): tree, order, roles, model-call cards, flags, purity."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.trace_lab import normalize as n

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
ORG = "11111111-1111-4111-8111-111111111111"
USER = "33333333-3333-4333-8333-333333333333"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def span(id, parent, seq, kind, name, *, agent="orchestrator", start=0.0, end=None, input=None, output=None,
         model=None, ok=True, error=None, tin=None, tout=None, tcache=None, cost=None, latency=None):
    return {"id": id, "turn_id": "turn-1", "parent_id": parent, "seq": seq, "kind": kind, "name": name, "agent": agent,
            "model": model, "started_at": at(start), "ended_at": at(start + 1 if end is None else end),
            "latency_ms": latency, "input": input, "output": output, "input_tokens": tin, "output_tokens": tout,
            "cache_read_tokens": tcache, "cost_usd": cost, "ok": ok, "error": error}


def turn(**kw):
    row = {"turn_id": "turn-1", "session_id": "s1", "organization_id": ORG, "user_id": USER, "email": "fm@example.com",
           "question": "How many work orders at B-301 in August?", "answer": "758 work orders.", "route": "single:wo",
           "started_at": at(0), "ended_at": at(10), "latency_ms": 10000, "llm_calls": 2, "tool_calls": 1,
           "cost_usd": Decimal("0.050000"), "cost_complete": True, "input_tokens": 2000, "output_tokens": 100,
           "cache_read_tokens": 0, "models": ["claude-sonnet-5"], "tools": ["answer_from_records"], "ok": True,
           "error": None, "feedback_rating": None, "feedback_comment": None, "feedback_by": None,
           "feedback_at": None, "created_at": at(11)}
    row.update(kw)
    return row


LOOP_IN_1 = {"messages": 3, "last_human_message": "How many work orders at B-301 in August?", "tool_results_since_last_human": 0}
LOOP_IN_2 = {"messages": 5, "last_human_message": "How many work orders at B-301 in August?", "tool_results_since_last_human": 1}


def loop_tree():
    """The shape trace.py writes for a stream-loop turn: model calls under an 'orchestrator loop' agent span
    (trace.py:519), the tool on the root (trace.py:343), its query under the tool (trace.py:490)."""
    return [
        span("root", None, 0, "turn", "turn", start=0, end=10, input={"message": "How many…", "mode": "stream"},
             output={"answer": "758 work orders."}, latency=10000),
        span("prompt", "root", 1, "stage", "prompt assembled", start=0.1,
             input={"user_message": "How many…", "system_prompt": "SYSTEM", "page_context": None},
             output={"system_prompt_chars": 6}),
        span("loop", "root", 2, "agent", "orchestrator loop", agent="orchestrator loop", start=0.2, end=9, cost=0.9),
        span("m1", "loop", 3, "llm", "model", agent="orchestrator loop", start=0.2, end=2, model="claude-sonnet-5",
             input=LOOP_IN_1, output={"content": "", "tool_calls": [{"name": "answer_from_records", "args": {"q": "wo"}}]},
             tin=900, tout=40, tcache=0, cost=0.004, latency=1800),
        span("t1", "root", 4, "tool", "answer_from_records", agent="tool:wo_engine", start=2, end=5,
             input={"question": "work orders B-301 August"}, output={"total": 758}),
        span("q1", "t1", 5, "db", "records: work_orders", agent="tool:wo_engine", start=3, end=3.1,
             input={"sql": "SELECT count(*) FROM work_orders", "params": {}, "label": "records: work_orders"},
             output={"row_count": 1, "rows": [{"count": 758}]}),
        span("m2", "loop", 6, "llm", "model", agent="orchestrator loop", start=5, end=9, model="claude-sonnet-5",
             input=LOOP_IN_2, output={"content": "758 work orders.", "tool_calls": []},
             tin=1100, tout=60, tcache=0, cost=0.005, latency=4000),
    ]


def ids(walked):
    return [s["id"] for s, _, _ in walked]


def roles(record):
    return [m["role"] for m in record["messages"]]


def test_agent_groups_are_transparent_for_order():
    walked = n.walk(loop_tree(), "root")
    assert ids(walked) == ["root", "prompt", "loop", "m1", "t1", "q1", "m2"]
    depth = {s["id"]: d for s, d, _ in walked}
    group = {s["id"]: g for s, _, g in walked}
    assert depth["m1"] == depth["t1"] == depth["m2"] == 1 and depth["q1"] == 2
    assert group["m1"] == group["m2"] == "orchestrator loop" and group["t1"] is None


def test_order_is_depth_first_by_seq_not_by_timestamp():
    spans = loop_tree()
    for s in spans:
        if s["id"] == "m2":
            s["started_at"] = at(0.05)       # back-computed from latency, earlier than everything (trace.py:204-206)
    assert ids(n.walk(spans, "root")) == ["root", "prompt", "loop", "m1", "t1", "q1", "m2"]


def test_repeated_seq_breaks_ties_by_start_then_id():
    spans = [span("root", None, 0, "turn", "turn"), span("b", "root", 4, "tool", "x", start=2),
             span("a", "root", 4, "tool", "x", start=3), span("c", "root", 4, "tool", "x", start=2)]
    assert ids(n.walk(spans, "root")) == ["root", "b", "c", "a"]


def test_an_orphan_and_a_cycle_hang_from_the_root():
    spans = [span("root", None, 0, "turn", "turn"), span("o", "gone", 3, "tool", "x"),
             span("c1", "c2", 4, "tool", "y"), span("c2", "c1", 5, "tool", "z")]
    walked = n.walk(spans, "root")
    assert ids(walked)[:2] == ["root", "o"] and set(ids(walked)) == {"root", "o", "c1", "c2"}
    assert all(d == 1 for s, d, _ in walked if s["id"] != "root")


def test_two_trees_keep_the_one_matching_the_turn_row():
    spans = [span("ra", None, 0, "turn", "turn", start=0, end=10), span("ka", "ra", 1, "tool", "a"),
             span("rb", None, 0, "turn", "turn", start=0, end=20), span("kb", "rb", 1, "tool", "b"),
             span("orphan", "gone", 2, "tool", "c")]
    kept, flags = n.pick_tree(turn(ended_at=at(10)), spans)
    assert {s["id"] for s in kept} == {"ra", "ka", "orphan"} and flags == ["duplicate_trees"]
    kept, _ = n.pick_tree(turn(ended_at=at(99)), spans)
    assert {s["id"] for s in kept} == {"rb", "kb", "orphan"}   # no match: the latest root


def test_reasoning_is_stripped_from_structures_json_and_python_reprs():
    payload = {"content": [{"type": "thinking", "thinking": "secret chain", "signature": "sigval123"},
                           {"type": "text", "text": "Two expire."}],
               "encrypted_content": "enc-xyz",
               "summary": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Looked up certs"}]}],
               "repr": "[{'type': 'thinking', 'thinking': 'hidden plan', 'signature': 'abc'}, {'type': 'text', 'text': 'ok'}]",
               "json": json.dumps([{"type": "redacted_thinking", "data": "zzz"}, {"type": "text", "text": "fine"}])}
    out = n.strip_reasoning(payload)
    text = json.dumps(out)
    for leaked in ("secret chain", "sigval123", "enc-xyz", "hidden plan", "zzz"):
        assert leaked not in text, leaked
    assert out["content"][0] == n.REASONING_HIDDEN and out["content"][1]["text"] == "Two expire."
    assert out["summary"][0]["label"] == n.PUBLISHED_SUMMARY_LABEL
    assert out["summary"][0]["summary"][0]["text"] == "Looked up certs"
    assert n.strip_reasoning("plain text") == "plain text"
    assert payload["encrypted_content"] == "enc-xyz"
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_normalize.py`
Expected: `AttributeError: module 'src.trace_lab.normalize' has no attribute 'walk'` (and `pick_tree`, `strip_reasoning`) — 6 failed.

- [ ] **Step 3: Implement**

Append to the end of `src/trace_lab/normalize.py`:

```python
# ── small helpers ────────────────────────────────────────────────────────────────────────────────

_FAR = datetime.max.replace(tzinfo=timezone.utc)
_NEAR = datetime.min.replace(tzinfo=timezone.utc)


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.isoformat()
    return None if v is None else str(v)


def _sid(v: Any) -> str | None:
    return None if v is None else str(v)


def _i(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(v: Any) -> list[Any]:
    return list(v) if isinstance(v, (list, tuple)) else []


def _seq(s: Mapping[str, Any]) -> int:
    return _i(s.get("seq")) or 0


def _sibling_key(s: Mapping[str, Any]) -> tuple[int, datetime, str]:
    """Siblings sort by (seq, started_at, id): seq is the causal order, and it can repeat after ledger
    absorption (trace.py:397-401)."""
    return (_seq(s), _ts(s.get("started_at")) or _FAR, str(s.get("id")))


# ── reasoning (§3.8) ─────────────────────────────────────────────────────────────────────────────

_THINKING_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
_THINKING_KEYS = frozenset({"encrypted_content", "signature"})


def _strip_text(s: str) -> str:
    t = s.lstrip()
    if len(s) > _JSON_PARSE_CHARS or t[:1] not in ("[", "{"):
        return s
    parsed: Any = _NOT_JSON
    try:
        parsed = json.loads(s)
    except (ValueError, RecursionError):
        if t.startswith(("[{'", "{'")):
            try:
                parsed = ast.literal_eval(t)      # a content list the trace stored as its Python repr (trace.py:543)
            except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
                parsed = _NOT_JSON
    if not isinstance(parsed, (list, dict)):
        return s
    stripped = strip_reasoning(parsed)
    return s if stripped == parsed else json.dumps(stripped, ensure_ascii=False, default=str)


def strip_reasoning(value: Any) -> Any:
    """Model reasoning out of a model payload: thinking / redacted_thinking / reasoning blocks and the
    encrypted_content and signature keys become "[reasoning not shown]". A provider-published reasoning
    summary is kept, under PUBLISHED_SUMMARY_LABEL, never as a timeline role."""
    if isinstance(value, str):
        return _strip_text(value)
    if isinstance(value, (list, tuple)):
        return [strip_reasoning(v) for v in value]
    if isinstance(value, Mapping):
        kind = value.get("type")
        if isinstance(kind, str) and kind in _THINKING_TYPES:
            if kind == "reasoning" and value.get("summary"):
                return {"label": PUBLISHED_SUMMARY_LABEL, "summary": strip_reasoning(value.get("summary"))}
            return REASONING_HIDDEN
        return {str(k): (REASONING_HIDDEN if k in _THINKING_KEYS else strip_reasoning(v)) for k, v in value.items()}
    return value


# ── the span tree (§3.4) ─────────────────────────────────────────────────────────────────────────

def _top(span: Mapping[str, Any], by_id: Mapping[str | None, Mapping[str, Any]]) -> str | None:
    """The id of the parentless span a span's chain ends at; None for a broken chain or a cycle."""
    seen: set[str | None] = set()
    cur: Mapping[str, Any] | None = span
    while cur is not None:
        sid = _sid(cur.get("id"))
        if sid in seen:
            return None
        seen.add(sid)
        parent = _sid(cur.get("parent_id"))
        if parent is None:
            return sid
        cur = by_id.get(parent)
    return None


def pick_tree(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[str]]:
    """A second Turn object for one turn_id writes a second tree (the turn insert only upserts the turn row,
    trace.py:752-754). Keep the tree whose root ended_at equals the turn row's, else the latest root."""
    items = [s for s in spans if s.get("id") is not None]
    roots = [s for s in items if s.get("kind") == "turn" and not s.get("parent_id")]
    if len(roots) <= 1:
        return items, []
    want = _ts(turn.get("ended_at"))
    match = [r for r in roots if want is not None and _ts(r.get("ended_at")) == want]
    chosen = match[0] if match else max(roots, key=lambda r: (_ts(r.get("ended_at")) or _NEAR,
                                                               _ts(r.get("started_at")) or _NEAR, str(r.get("id"))))
    by_id = {_sid(s.get("id")): s for s in items}
    others = {_sid(r.get("id")) for r in roots} - {_sid(chosen.get("id"))}
    return [s for s in items if _top(s, by_id) not in others], ["duplicate_trees"]


def _children(spans: Sequence[Mapping[str, Any]], root_id: str | None):
    by_id = {_sid(s.get("id")): s for s in spans if s.get("id") is not None}
    kids: dict[str | None, list[Mapping[str, Any]]] = {}
    for sid, s in by_id.items():
        if sid == root_id:
            continue
        parent = _sid(s.get("parent_id"))
        if parent is None or parent == sid or parent not in by_id:
            parent = root_id                 # a span with no parent on record hangs from the root (tracesPage.js:42-63)
        kids.setdefault(parent, []).append(s)
    return kids, by_id


def _expanded(node_id: str | None, group: str | None, kids: Mapping[str | None, list[Mapping[str, Any]]]):
    """node_id's children, with every agent child's own children merged in (recursively), sorted together:
    agent spans have no message, so their children sort among the agent's siblings."""
    out: list[tuple[Mapping[str, Any], str | None]] = []
    stack: list[tuple[str | None, str | None]] = [(node_id, group)]
    seen: set[str | None] = set()
    while stack:
        nid, g = stack.pop()
        for k in kids.get(nid, []):
            kid = _sid(k.get("id"))
            if kid in seen:
                continue
            seen.add(kid)
            out.append((k, g))
            if k.get("kind") == "agent":
                stack.append((kid, str(k.get("name") or g or "")))
    out.sort(key=lambda e: _sibling_key(e[0]))
    return out


def walk(spans: Sequence[Mapping[str, Any]], root_id: str | None) -> list[tuple[Mapping[str, Any], int, str | None]]:
    """Depth-first from the root, siblings by (seq, started_at, id), agent spans transparent. Each entry is
    (span, depth, group): depth counts non-agent ancestors, group names the nearest agent span above."""
    rid = _sid(root_id)
    kids, by_id = _children(spans, rid)
    out: list[tuple[Mapping[str, Any], int, str | None]] = []
    seen: set[str | None] = set()
    root = by_id.get(rid) if rid is not None else None
    stack: list[tuple[Mapping[str, Any], int, str | None]] = []
    if root is not None:
        stack.append((root, 0, None))
    else:
        stack.extend((k, 1, g) for k, g in reversed(_expanded(rid, None, kids)))
    while stack:
        s, depth, group = stack.pop()
        sid = _sid(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        out.append((s, depth, group))
        if s.get("kind") == "agent" and s is not root:
            continue                         # its children were merged into its parent's list
        stack.extend((k, depth + 1, g) for k, g in reversed(_expanded(sid, group, kids)))
    for sid, s in by_id.items():
        if sid not in seen:                  # unreachable from the root (a parent cycle): shown under it
            seen.add(sid)
            out.append((s, 1, None))
    return out
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_normalize.py tests/test_trace_lab_redaction.py`
Expected: `12 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 4: Model-call cards

**Files:**
- Modify: `src/trace_lab/normalize.py` (append to the end)
- Test: `tests/test_trace_lab_normalize.py` (append)

**Interfaces:**
- Consumes: Task 2 `redact`, `redact_text`; Task 3 `strip_reasoning`, `_i`, `_f`, `_iso`, `_sid`, `_list`.
- Produces: `request_kind(inp) -> "exchange"|"loop_summary"|"none"|"other"`, `call_label(kind, name, request_kind, loop_output, requested_tools) -> str`, `model_call_record(span, *, turn_id, group, prompt_ref, withheld=False) -> dict` (keys: `span_id, turn_id, call_kind, group, model, provider, operation, label, request_kind, request, request_complete, prompt_ref, response, model_parameters, usage{input_tokens,output_tokens,cache_read_tokens,total_tokens}, latency_s, total_cost, cost_status, status, status_message, raw, unavailable, withheld, cot_note, started_at, ended_at, truncated`), `_is_truncated(v) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trace_lab_normalize.py`:

```python
def test_model_card_lists_unavailable_fields_and_never_invents_them():
    bare = span("x", "root", 1, "llm", "analyst", model=None, input={"system_prompt": "S", "user_message": "U"},
                output={"model_output": "ok"})
    card = n.model_call_record(bare, turn_id="turn-1", group=None, prompt_ref=None)
    assert card["provider"] is None and card["model"] is None and card["total_cost"] is None
    for field in n.ALWAYS_UNAVAILABLE + ("model", "model_parameters", "input_tokens", "output_tokens",
                                         "cache_read_tokens", "latency", "total_cost"):
        assert field in card["unavailable"], field
    assert card["cost_status"] == "unavailable" and card["usage"]["total_tokens"] is None
    unpriced = dict(bare, model="mystery-model", input_tokens=10, output_tokens=2)
    assert n.model_call_record(unpriced, turn_id="turn-1", group=None, prompt_ref=None)["cost_status"] == "unpriced"
    priced = dict(unpriced, cost_usd=Decimal("0.001"), latency_ms=900, cache_read_tokens=0,
                  input={"system_prompt": "S", "user_message": "U", "params": {"max_tokens": 900}})
    p = n.model_call_record(priced, turn_id="turn-1", group=None, prompt_ref=None)
    assert p["cost_status"] == "priced" and p["total_cost"] == 0.001 and p["latency_s"] == 0.9
    assert p["usage"] == {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 0, "total_tokens": 12}
    assert p["model_parameters"] == {"max_tokens": 900} and "model_parameters" not in p["unavailable"]
    assert p["request_kind"] == "exchange" and p["request_complete"] is True and p["label"] == "analyst"


def test_loop_calls_are_marked_summary_only_and_point_at_the_prompt_note():
    m1 = next(s for s in loop_tree() if s["id"] == "m1")
    card = n.model_call_record(m1, turn_id="turn-1", group="orchestrator loop", prompt_ref="prompt")
    assert card["request_kind"] == "loop_summary" and card["request_complete"] is False
    assert card["prompt_ref"] == "prompt" and card["label"] == "tool decision"
    m2 = next(s for s in loop_tree() if s["id"] == "m2")
    assert n.model_call_record(m2, turn_id="turn-1", group="orchestrator loop", prompt_ref="prompt")["label"] == "answer synthesis"


def test_ledger_only_calls_have_no_request_or_response():
    ledger = span("l", "t1", 7, "llm", "planner", agent="tool:wo_engine", model="claude-sonnet-5", tin=13000, tout=200,
                  cost=0.029, latency=2500)
    card = n.model_call_record(ledger, turn_id="turn-1", group=None, prompt_ref="prompt")
    assert card["request_kind"] == "none" and card["request"] is None and card["response"] is None
    assert card["label"] == "planner" and card["prompt_ref"] is None
    assert {"request", "response"} <= set(card["unavailable"])


def test_a_router_is_a_routing_decision_model_call():
    router = span("r", "root", 1, "router", "router", agent="agent_router", model="claude-haiku-4-5",
                  input={"user_message": "x"}, output={"agent": "compliance", "also": [], "reason": "certs"})
    card = n.model_call_record(router, turn_id="turn-1", group=None, prompt_ref=None)
    assert card["call_kind"] == "router" and card["label"] == "routing decision"
    assert card["response"] == {"agent": "compliance", "also": [], "reason": "certs"}


def test_a_withheld_card_keeps_the_request_but_not_the_reply():
    judge = span("j", "g", 9, "llm", "judge", agent="el_udr", model="claude-sonnet-5",
                 input={"system_prompt": "Judge", "user_message": "candidate"}, output={"model_output": {"grounded": False}})
    card = n.model_call_record(judge, turn_id="turn-1", group=None, prompt_ref=None, withheld=True)
    assert card["response"] is None and card["raw"] is None and card["withheld"] == ["response", "raw"]
    assert card["request"]["system_prompt"] == "Judge" and "response" not in card["unavailable"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_normalize.py`
Expected: 5 new tests fail with `AttributeError: module 'src.trace_lab.normalize' has no attribute 'model_call_record'`.

- [ ] **Step 3: Implement**

Append to the end of `src/trace_lab/normalize.py`:

```python
# ── model-call cards (§3.5) ──────────────────────────────────────────────────────────────────────

_TRUNC_RE = re.compile(r"… \[\d+ more chars\]")


def _is_truncated(v: Any) -> bool:
    """A payload the trace bound cut (trace.py:166, 171, 175-176) or the activity log shortened."""
    if v is None:
        return False
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return bool(_TRUNC_RE.search(s)) or '"_truncated"' in s


def request_kind(inp: Any) -> str:
    if isinstance(inp, Mapping):
        if "system_prompt" in inp or "user_message" in inp:
            return "exchange"              # activity exchange (activity_log.py:87-123)
        if "last_human_message" in inp:
            return "loop_summary"          # loop / sub-agent model span (trace.py:651-656)
        return "other"
    return "none" if inp is None else "other"


def call_label(kind: str | None, name: str | None, request_kind: str, loop_output: bool, requested_tools: int) -> str:
    if kind == "router":
        return "routing decision"
    if request_kind == "loop_summary" or loop_output:
        return "tool decision" if requested_tools else "answer synthesis"
    return str(name or "model")


def _model_payload(v: Any) -> Any:
    return None if v is None else redact(strip_reasoning(v))


def model_call_record(span: Mapping[str, Any], *, turn_id: str, group: str | None, prompt_ref: str | None,
                      withheld: bool = False) -> dict[str, Any]:
    """One llm or router span as a card. Missing fields are null and named in `unavailable`; nothing is
    estimated, and the provider is never derived from the model name (R22). `withheld` (a gate's own call
    while its verdict is hidden, R23) drops the reply and the raw copy, not the request."""
    inp, out = span.get("input"), span.get("output")
    kind = "router" if span.get("kind") == "router" else "llm"
    rk = request_kind(inp)
    loop_output = isinstance(out, Mapping) and "tool_calls" in out and "content" in out
    requested = _list(out.get("tool_calls")) if loop_output else []
    if rk == "exchange":
        request: Any = {"system_prompt": inp.get("system_prompt"), "user_message": inp.get("user_message"),
                        "params": inp.get("params")}
    elif rk == "loop_summary":
        request = {"messages": inp.get("messages"), "last_human_message": inp.get("last_human_message"),
                   "tool_results_since_last_human": inp.get("tool_results_since_last_human")}
    elif rk == "other":
        request = inp
    else:
        request = None
    params = inp.get("params") if rk == "exchange" and isinstance(inp.get("params"), Mapping) else None
    model = span.get("model") or None
    tin, tout, tcache = _i(span.get("input_tokens")), _i(span.get("output_tokens")), _i(span.get("cache_read_tokens"))
    present = [x for x in (tin, tout, tcache) if x is not None]
    latency = _i(span.get("latency_ms"))
    cost = _f(span.get("cost_usd"))
    cost_status = "priced" if cost is not None else ("unpriced" if model and present else "unavailable")
    missing = {"model": model is None, "request": request is None, "response": out is None,
               "model_parameters": params is None, "input_tokens": tin is None, "output_tokens": tout is None,
               "cache_read_tokens": tcache is None, "latency": latency is None, "total_cost": cost is None}
    return {
        "span_id": _sid(span.get("id")), "turn_id": turn_id, "call_kind": kind, "group": group,
        "model": model, "provider": None,
        "operation": f"{span.get('agent') or 'orchestrator'} › {span.get('name') or 'model'}",
        "label": call_label(kind, span.get("name"), rk, loop_output, len(requested)),
        "request_kind": rk, "request": _model_payload(request),
        "request_complete": rk in ("exchange", "other"),
        "prompt_ref": prompt_ref if rk == "loop_summary" else None,
        "response": None if withheld else _model_payload(out),
        "model_parameters": None if params is None else redact(params),
        "usage": {"input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                  "total_tokens": sum(present) if present else None},
        "latency_s": None if latency is None else latency / 1000,
        "total_cost": cost, "cost_status": cost_status,
        "status": "error" if span.get("ok") is False else "ok",
        "status_message": redact_text(str(span.get("error"))) if span.get("error") else None,
        "raw": None if withheld else {"input": _model_payload(inp), "output": _model_payload(out)},
        "unavailable": list(ALWAYS_UNAVAILABLE) + [k for k, miss in missing.items() if miss],
        "withheld": ["response", "raw"] if withheld else [],
        "cot_note": COT_CARD_NOTE,
        "started_at": _iso(span.get("started_at")), "ended_at": _iso(span.get("ended_at")),
        "truncated": _is_truncated(inp) or _is_truncated(out),
    }
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_normalize.py`
Expected: `11 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 5: Grouping, question repair, turn headers and attempts

**Files:**
- Modify: `src/trace_lab/normalize.py` (append to the end)
- Test: `tests/test_trace_lab_grouping.py` (create)

**Interfaces:**
- Consumes: Task 2 `redact_text`, `QUESTION_MARKER`, `QUESTION_CHARS`; Task 3 helpers.
- Produces: `session_kind(session_id, *, thread_exists) -> str`, `group_key(session_id, turn_id) -> str`, `session_label(kind, session_id, *, title, golden) -> str`, `repair_question(turn, meta, activity_input) -> (str, list[str])`, `turn_header(turn, meta, *, turn_index, attempt) -> dict` (reads `turn["inferred_channel"]` for rows without turn_meta), `attempts(turns, metas, activity_inputs=None) -> {turn_id: {attempt, attempt_source, retry_of}}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_lab_grouping.py`:

```python
"""Conversation grouping (spec §"Trace capture" 4): session kinds, keys, labels, attempts, headers, and the
read-time question repair (§2.2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.trace_lab import normalize as n

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
FIRST = {"attempt": 1, "attempt_source": "recorded", "retry_of": None}


def row(tid, sid="s1", q="How many boilers?", minutes=0, **kw):
    r = {"turn_id": tid, "session_id": sid, "question": q, "answer": "Four.", "started_at": T0 + timedelta(minutes=minutes),
         "ok": True, "models": ["claude-sonnet-5"], "tool_calls": 1, "cost_usd": 0.01, "latency_ms": 900}
    r.update(kw)
    return r


def test_conversations_prefer_the_real_session_and_flag_inferred_groups():
    assert n.session_kind("3b1f-thread", thread_exists=True) == "observed"
    assert n.session_kind("3b1f-thread", thread_exists=False) == "inferred"
    assert n.session_kind(None, thread_exists=False) == "inferred"
    assert n.session_kind("   ", thread_exists=True) == "inferred"
    assert n.session_kind("\t", thread_exists=False) == "inferred"          # btrim() trims spaces only
    assert n.group_key("3b1f-thread", "turn-1") == "s:3b1f-thread"
    assert n.group_key(None, "turn-1") == "t:turn-1" and n.group_key("  ", "turn-2") == "t:turn-2"
    assert n.group_key("\t", "turn-3") == "s:\t"
    assert n.session_label("observed", "3b1f", title="Boiler certificates", golden=None) == "Boiler certificates"
    assert n.session_label("observed", "3b1f", title="x" * 100, golden=None) == "x" * 80
    assert n.session_label("observed", "3b1f", title=None, golden=None) == "Untitled conversation"
    assert n.session_label("inferred", "3b1f", title="Boiler certificates", golden=None) == "Session not in chat history"
    assert n.session_label("inferred", None, title=None, golden=None) == "Single turn, no session recorded"


def test_synthetic_sessions_are_labelled_and_never_observed():
    cases = {"golden-run1-case-2-rep1": "Golden run · B-07 · rep 2", "report-1a2b-1759300000": "Report card run",
             "docs-9": "Document upload", "ing-4": "Document upload", "skilllab-x": "Skill lab replay",
             "rerun": "Correction re-run", "rerun-abc": "Correction re-run"}
    for sid, label in cases.items():
        assert n.session_kind(sid, thread_exists=True) == "synthetic", sid
        assert n.session_label("synthetic", sid, title="ignored", golden={"case_key": "B-07", "rep": 2}) == label, sid


def test_the_shared_rerun_session_is_never_one_conversation():
    assert n.group_key("rerun", "turn-a") == "r:turn-a"
    assert n.group_key("rerun", "turn-b") == "r:turn-b"
    assert n.group_key("rerun-123", "turn-c") == "r:turn-c"


def test_attempts_follow_recorded_then_client_then_inferred_evidence():
    turns = [row("t1", minutes=0), row("t2", q="Re-run with corrections (1): How many boilers?", minutes=1),
             row("t3", q="How   many BOILERS?", minutes=2), row("t4", q="Something else", minutes=3),
             row("t5", q="Edited question", minutes=4), row("t6", q="Corrected", minutes=5)]
    metas = {"t1": None,
             "t2": {"origin": "correction.rerun", "origin_params": {"rerun_of": "t1", "corrections": 1}},
             "t3": None, "t4": None,
             "t5": {"origin": "chat.edit", "origin_params": {"replaces_turn": "t4"}},
             "t6": {"origin": "correction.reanswer", "origin_params": {"corrects_turn": "turn-elsewhere"}}}
    out = n.attempts(list(reversed(turns)), metas)
    assert out["t1"] == FIRST
    assert out["t2"] == {"attempt": 2, "attempt_source": "recorded", "retry_of": "t1"}
    assert out["t3"] == {"attempt": 3, "attempt_source": "inferred", "retry_of": "t2"}   # the latest same question
    assert out["t4"]["attempt"] == 1
    assert out["t5"] == {"attempt": 2, "attempt_source": "client", "retry_of": "t4"}
    assert out["t6"] == {"attempt": 2, "attempt_source": "client", "retry_of": "turn-elsewhere"}  # target elsewhere: 2


def test_a_legacy_rerun_is_read_from_the_activity_row():
    turns = [row("t1"), row("t2", q="Different words", minutes=1)]
    out = n.attempts(turns, {"t1": None, "t2": None}, {"t2": {"payload": {"message": "Different words", "rerun_of": "t1"}}})
    assert out["t2"] == {"attempt": 2, "attempt_source": "inferred", "retry_of": "t1"}


def test_turn_headers_are_short_redacted_and_carry_provenance():
    wrapped = "ctx " * 50 + n.QUESTION_MARKER + "Which certificates expire? Bearer abcdefghijklmnop"
    h = n.turn_header(row("t1", q=wrapped, answer="Two. " * 100, inferred_channel="chat"), None, turn_index=1,
                      attempt=FIRST)
    assert h["question"] == "Which certificates expire? Bearer [redacted]"
    assert len(h["answer"]) <= 300 and h["channel"] == "chat" and h["channel_source"] == "inferred"
    assert h["flags"] == ["legacy_provenance"] and h["turn_index"] == 1 and h["tool_count"] == 1
    meta = {"channel": "ui_action", "channel_source": "client", "origin": "ui.matrix_cell:risk"}
    h2 = n.turn_header(row("t2", ok=False, answer="gate_interrupt"), meta, turn_index=2, attempt=FIRST)
    assert (h2["channel"], h2["channel_source"], h2["origin"]) == ("ui_action", "client", "ui.matrix_cell:risk")
    assert h2["flags"] == ["failed", "workflow_stop"]


def test_the_question_is_repaired_from_the_strongest_evidence():
    assert n.repair_question({"question": "wrapped"}, {"asker_question": "Own words"}, None) == ("Own words", [])
    assert n.repair_question({"question": "wrapped"}, None, {"payload": {"message": "Typed"}}) == ("Typed", ["question_repaired"])
    marked = "ctx" + n.QUESTION_MARKER + "a" + n.QUESTION_MARKER + "b"
    assert n.repair_question({"question": marked}, None, None) == ("b", ["question_repaired"])
    assert n.repair_question({"question": "x" * 4000}, None, None) == ("x" * 4000, ["question_wrapped"])
    assert n.repair_question({"question": "x" * 4000}, {"channel": "chat"}, None) == ("x" * 4000, [])
    assert n.repair_question({"question": "plain"}, None, None) == ("plain", [])
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_grouping.py`
Expected: 7 failed, `AttributeError: module 'src.trace_lab.normalize' has no attribute 'session_kind'`.

- [ ] **Step 3: Implement**

Append to the end of `src/trace_lab/normalize.py`:

```python
# ── conversations (§4) and the asker's words (§2.2) ──────────────────────────────────────────────

_SYNTHETIC_PREFIXES = ("golden-", "report-", "docs-", "ing-", "rerun", "skilllab-")
_RERUN_PREFIX = re.compile(r"^re-run with corrections \(.*?\):\s*", re.S)


def _blank(session_id: Any) -> bool:
    return session_id is None or str(session_id).strip(" ") == ""      # as Postgres btrim(): spaces only


def session_kind(session_id: str | None, *, thread_exists: bool) -> str:
    """Mirror of conversations.SESSION_KIND_SQL (a scratch-DB test pins parity)."""
    if _blank(session_id):
        return "inferred"
    if str(session_id).startswith(_SYNTHETIC_PREFIXES):
        return "synthetic"
    return "observed" if thread_exists else "inferred"


def group_key(session_id: str | None, turn_id: str) -> str:
    """Mirror of conversations.GROUP_KEY_SQL. The shared 'rerun' session is never one conversation."""
    if _blank(session_id):
        return "t:" + str(turn_id)
    sid = str(session_id)
    if sid.startswith("rerun"):
        return "r:" + str(turn_id)
    return "s:" + sid


def session_label(kind: str, session_id: str | None, *, title: str | None, golden: Mapping[str, Any] | None) -> str:
    """The conversation's name. For an observed session the caller passes the thread title, else the first
    question; synthetic and inferred groups are always labelled as such (repo rule; D4)."""
    sid = "" if session_id is None else str(session_id)
    if _blank(session_id):
        return "Single turn, no session recorded"
    if sid.startswith("rerun"):
        return "Correction re-run"
    if sid.startswith("golden-"):
        g = golden or {}
        return f"Golden run · {g.get('case_key') or '—'} · rep {g.get('rep') or '—'}"
    if sid.startswith("report-"):
        return "Report card run"
    if sid.startswith(("docs-", "ing-")):
        return "Document upload"
    if sid.startswith("skilllab-"):
        return "Skill lab replay"
    if kind == "observed":
        return str(title or "").strip()[:80] or "Untitled conversation"
    return "Session not in chat history"


def repair_question(turn: Mapping[str, Any], meta: Mapping[str, Any] | None,
                    activity_input: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """The asker's own words. turn_meta.asker_question wins; then the earliest activity row's message; then
    the text after the last wrapped-prompt marker. A legacy row whose marker fell past the 4,000-char cut
    keeps its text and says so."""
    stored = str(turn.get("question") or "")
    if meta and isinstance(meta.get("asker_question"), str) and meta.get("asker_question"):
        return meta["asker_question"], []
    payload = activity_input.get("payload") if isinstance(activity_input, Mapping) else None
    typed = payload.get("message") if isinstance(payload, Mapping) else None
    if isinstance(typed, str) and typed.strip() and typed != stored:
        return typed[:QUESTION_CHARS], ["question_repaired"]
    if QUESTION_MARKER in stored:
        return stored.rsplit(QUESTION_MARKER, 1)[1], ["question_repaired"]
    if meta is None and len(stored) >= QUESTION_CHARS:
        return stored, ["question_wrapped"]
    return stored, []


def turn_header(turn: Mapping[str, Any], meta: Mapping[str, Any] | None, *, turn_index: int,
                attempt: Mapping[str, Any]) -> dict[str, Any]:
    question, _ = repair_question(turn, meta, None)
    if meta:
        channel, source, origin = meta.get("channel") or "chat", meta.get("channel_source") or "inferred", meta.get("origin")
    else:
        channel, source, origin = turn.get("inferred_channel") or "chat", "inferred", None
    flags = []
    if meta is None:
        flags.append("legacy_provenance")
    if turn.get("ok") is False:
        flags.append("failed")
    if str(turn.get("answer") or "") == "gate_interrupt":
        flags.append("workflow_stop")
    return {"turn_id": str(turn.get("turn_id")), "turn_index": turn_index, "started_at": _iso(turn.get("started_at")),
            "question": redact_text(question)[:300], "answer": redact_text(str(turn.get("answer") or ""))[:300],
            "ok": turn.get("ok") is not False, "channel": channel, "channel_source": source, "origin": origin,
            "attempt": attempt.get("attempt", 1), "attempt_source": attempt.get("attempt_source", "recorded"),
            "retry_of": attempt.get("retry_of"), "models": [str(m) for m in _list(turn.get("models"))],
            "tool_count": _i(turn.get("tool_calls")) or 0, "cost_usd": _f(turn.get("cost_usd")),
            "latency_ms": _i(turn.get("latency_ms")), "flags": flags}


def _norm_question(q: str) -> str:
    return _RERUN_PREFIX.sub("", re.sub(r"\s+", " ", (q or "").strip().lower()))


def attempts(turns: Sequence[Mapping[str, Any]], metas: Mapping[str, Mapping[str, Any] | None],
             activity_inputs: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """attempt / attempt_source / retry_of for every turn of one group (§4.3). The target's attempt plus 1
    when the target is in the group, else 2."""
    acts = activity_inputs or {}
    order = sorted(turns, key=lambda t: (_ts(t.get("started_at")) or _FAR, str(t.get("turn_id"))))
    in_group = {str(t.get("turn_id")) for t in order}
    out: dict[str, dict[str, Any]] = {}
    latest_by_question: dict[str, str] = {}
    for t in order:
        tid = str(t.get("turn_id"))
        meta = metas.get(tid)
        params = meta.get("origin_params") if meta and isinstance(meta.get("origin_params"), Mapping) else {}
        origin = str((meta or {}).get("origin") or "")
        base = origin.split(":", 1)[0]
        target: str | None = None
        source = "recorded"
        if origin == "correction.rerun" and (params.get("rerun_of") or (meta or {}).get("rerun_of")):
            target, source = str(params.get("rerun_of") or meta.get("rerun_of")), "recorded"
        elif base == "chat.edit" and params.get("replaces_turn"):
            target, source = str(params["replaces_turn"]), "client"
        elif base == "correction.reanswer" and params.get("corrects_turn"):
            target, source = str(params["corrects_turn"]), "client"
        else:
            payload = acts.get(tid, {}).get("payload") if isinstance(acts.get(tid), Mapping) else None
            if isinstance(payload, Mapping) and payload.get("rerun_of"):
                target, source = str(payload["rerun_of"]), "inferred"
        question, _ = repair_question(t, meta, acts.get(tid))
        normalized = _norm_question(question)
        if target is None and normalized and normalized in latest_by_question:
            target, source = latest_by_question[normalized], "inferred"
        if target is None:
            out[tid] = {"attempt": 1, "attempt_source": "recorded", "retry_of": None}
        else:
            prev = out.get(target) if target in in_group else None
            out[tid] = {"attempt": prev["attempt"] + 1 if prev else 2, "attempt_source": source, "retry_of": target}
        if normalized:
            latest_by_question[normalized] = tid
    return out
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_grouping.py tests/test_trace_lab_normalize.py`
Expected: `18 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 6: `normalize_turn` — the timeline, the record and its flags

**Files:**
- Modify: `src/trace_lab/normalize.py` (one import line; append to the end)
- Test: `tests/test_trace_lab_normalize.py` (append)

**Interfaces:**
- Consumes: Tasks 2–5; M0 `provenance.infer_channel(turn, *, activity_input)`, `provenance.LEGACY_CLASSIFIER`.
- Produces: `normalize_turn(turn, spans, *, meta=None, activity_input=None, conversation=None, reveal_gate_verdicts=False) -> dict` (record of §3.2: `id, trace_id, normalizer_version, timestamp, ended_at, created_at, models, tools, input, output, ok, error, messages, model_calls, groups, usage, features, meta, feedback, text, flags, cot_boundary, permalink`); message roles and extra fields exactly as §3.2's table, plus on `gate` messages `recorded`, `verdict_hidden`, `anchors`, and on every message `block`; `features(turn, messages=None)`, `flatten_text(messages)`; `block_text(span, field) -> str` (spec Review 2.2, R36: one span field's annotatable text exactly as the timeline shows it, `field` in `input`/`output`, else `ValueError`). A message's `block` is `block_text(span, "output")` on span messages anchored on `output`, `""` on a withheld gate call (R23), `None` on the question, answer, tool-call and gate messages (M2 reads `m["block"]`; M4 calls `block_text`). `conversation` keys read: `key, session_kind, session_label, turn_index, turn_count, attempt, attempt_source, retry_of, run_id, thread_deleted`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trace_lab_normalize.py`:

```python
def test_trace_id_is_the_turn_id():
    rec = n.normalize_turn(turn(), loop_tree(), meta={"channel": "chat", "channel_source": "client"})
    assert rec["id"] == rec["trace_id"] == "turn-1" and rec["normalizer_version"] == n.NORMALIZER_VERSION


def test_a_simple_turn_reads_user_tool_call_tool_result_assistant():
    spans = [span("root", None, 0, "turn", "turn"), span("t", "root", 1, "tool", "list_certificates", output={"rows": 2})]
    assert roles(n.normalize_turn(turn(), spans)) == ["user", "tool_call", "tool_result", "assistant"]


def test_a_loop_turn_reads_in_the_order_it_ran():
    rec = n.normalize_turn(turn(), loop_tree(), meta={"channel": "chat", "channel_source": "client", "origin": "chat.typed"})
    assert roles(rec) == ["user", "system_note", "model_call", "tool_call", "tool_result", "db_query", "model_call", "assistant"]
    assert [m["label"] for m in rec["messages"] if m["role"] == "model_call"] == ["tool decision", "answer synthesis"]
    assert rec["messages"][0]["text"] == "How many work orders at B-301 in August?"
    assert rec["messages"][-1]["text"] == "758 work orders." and rec["messages"][-1]["subtype"] == "answer"
    assert rec["groups"][0]["name"] == "orchestrator loop" and rec["groups"][0]["cost_is_whole_run"] is True


def test_every_span_kind_maps_to_its_role():
    spans = [span("root", None, 0, "turn", "turn"),
             span("r", "root", 1, "router", "router", agent="agent_router", model="claude-haiku-4-5", output={"agent": "wo"}),
             span("p", "root", 2, "plan", "plan: 1 step", input={"source": "router", "question": "q"},
                  output={"plan": {"steps": [{"target": "wo"}]}, "rejected": None}),
             span("s", "p", 3, "step", "verify claims", output={"ok": True}),
             span("s2", "p", 4, "step", "wo", output={"ok": True}),
             span("st", "root", 5, "stage", "fetch", agent="compliance", output={"rows": 12}),
             span("odd", "root", 6, "mystery", "thing"),
             span("g", "root", 7, "gate", "el_udr", agent="el_udr", input={"answer_before": "A"},
                  output={"outcome": "passed", "changed": False}),
             span("gj", "g", 8, "llm", "judge", agent="el_udr", model="claude-sonnet-5",
                  input={"system_prompt": "J", "user_message": "c"}, output={"model_output": "{}"})]
    rec = n.normalize_turn(turn(), spans)
    got = [(m["role"], m.get("subtype"), m.get("call_kind")) for m in rec["messages"]]
    assert got == [("user", None, None), ("model_call", None, "router"), ("plan", None, None), ("step", "check", None),
                   ("step", "step", None), ("system_note", "stage", None), ("system_note", "unknown_kind", None),
                   ("gate", None, None), ("model_call", None, "llm"), ("assistant", "answer", None)]
    assert rec["messages"][6]["label"] == "mystery: thing"
    gate, judge = rec["messages"][7], rec["messages"][8]
    assert judge["depth"] == gate["depth"] + 1 and gate["model_call_span_ids"] == ["gj"]


def test_a_tool_call_and_its_result_share_one_span():
    rec = n.normalize_turn(turn(), loop_tree())
    i = next(k for k, m in enumerate(rec["messages"]) if m["role"] == "tool_call")
    call, result = rec["messages"][i], rec["messages"][i + 1]
    assert result["role"] == "tool_result" and call["span_id"] == result["span_id"] == "t1"
    assert call["anchor"] is None and result["anchor"] == {"turn_id": "turn-1", "span_id": "t1", "field": "output"}
    assert call["arguments"] == {"question": "work orders B-301 August"} and result["content"] == {"total": 758}


def test_tool_requests_link_to_tool_spans_by_name_and_order():
    spans = loop_tree()
    m1 = next(s for s in spans if s["id"] == "m1")
    m1["output"] = {"content": "", "tool_calls": [{"name": "answer_from_records", "args": {}},
                                                  {"name": "list_vendor_accreditations", "args": {}}]}
    rec = n.normalize_turn(turn(), spans)
    call = next(m for m in rec["messages"] if m["role"] == "tool_call")
    assert call["requested_by"] == "m1" and "unexecuted_tool_requests" in rec["flags"]
    assert "unexecuted_tool_requests" not in n.normalize_turn(turn(), loop_tree())["flags"]


def test_concurrent_same_name_tools_are_flagged():
    spans = [span("root", None, 0, "turn", "turn"), span("a", "root", 1, "tool", "search", start=1, end=3),
             span("b", "root", 2, "tool", "search", start=2, end=4)]
    assert "concurrent_same_name_tools" in n.normalize_turn(turn(), spans)["flags"]


def test_loop_model_messages_carry_cards_pointing_at_the_prompt_note():
    rec = n.normalize_turn(turn(), loop_tree())
    first = next(m for m in rec["messages"] if m["role"] == "model_call")
    assert first["model_call"]["prompt_ref"] == "prompt" and first["model_call"]["request_complete"] is False
    assert [c["span_id"] for c in rec["model_calls"]] == ["m1", "m2"]
    note = rec["messages"][1]
    assert note["subtype"] == "prompt" and note["label"] == "What the model was given" and note["collapse"] is True


def test_agent_group_cost_is_never_added_to_its_children():
    rec = n.normalize_turn(turn(cost_usd=Decimal("0.050000")), loop_tree())
    assert rec["usage"]["cost_usd"] == 0.05 and rec["groups"][0]["cost_usd"] == 0.9
    assert [c["total_cost"] for c in rec["model_calls"]] == [0.004, 0.005]
    assert rec["usage"]["total_tokens"] == 2100 and rec["usage"]["llm_calls"] == 2


def test_the_reasoning_boundary_is_stated_on_every_record():
    rec = n.normalize_turn(turn(), loop_tree())
    assert rec["cot_boundary"] == n.COT_BOUNDARY
    assert all(m["cot_note"] == n.COT_CARD_NOTE for m in rec["messages"] if m["role"] == "model_call")
    assert n.normalize_turn(turn(), [])["cot_boundary"] == n.COT_BOUNDARY


def test_thinking_blocks_never_reach_the_timeline():
    spans = [span("root", None, 0, "turn", "turn"),
             span("a", "root", 1, "llm", "analyst", agent="compliance", model="claude-sonnet-5",
                  input={"system_prompt": "S", "user_message": "U"},
                  output=[{"type": "thinking", "thinking": "secret chain", "signature": "sigval123"},
                          {"type": "text", "text": "Two expire."}]),
             span("m", "root", 2, "llm", "model", model="claude-sonnet-5", input=LOOP_IN_1,
                  output={"content": "[{'type': 'thinking', 'thinking': 'hidden plan', 'signature': 'abc'}, "
                                     "{'type': 'text', 'text': 'ok'}]", "tool_calls": [], "encrypted_content": "enc-xyz"})]
    text = json.dumps(n.normalize_turn(turn(), spans), default=str)
    for leaked in ("secret chain", "sigval123", "hidden plan", "enc-xyz"):
        assert leaked not in text, leaked
    assert n.REASONING_HIDDEN in text


def test_results_over_240_chars_start_collapsed():
    spans = [span("root", None, 0, "turn", "turn"), span("long", "root", 1, "tool", "a", output="x" * 241),
             span("short", "root", 2, "tool", "b", output="y" * 240)]
    results = [m for m in n.normalize_turn(turn(), spans)["messages"] if m["role"] == "tool_result"]
    assert [m["collapse"] for m in results] == [True, False] and results[0]["chars"] == 241


def test_a_purged_turn_still_reads_question_and_answer():
    rec = n.normalize_turn(turn(), [])
    assert roles(rec) == ["user", "assistant"] and "spans_purged" in rec["flags"]
    assert rec["messages"][0]["anchor"]["field"] == "question" and rec["model_calls"] == []


def test_gate_message_resolves_answer_after_from_answer_before_when_unchanged():
    kept = [span("root", None, 0, "turn", "turn"),
            span("g", "root", 1, "gate", "el_udr", agent="el_udr", input={"answer_before": "Four boilers."},
                 output={"outcome": "passed", "changed": False, "answer_after": None, "verdict": {"grounded": True}})]
    g = next(m for m in n.normalize_turn(turn(), kept)["messages"] if m["role"] == "gate")
    assert g["answer_after"] == g["answer_before"] == "Four boilers." and g["changed"] is False and g["label"] == "Answer check"
    changed = copy.deepcopy(kept)
    changed[1]["output"] = {"outcome": "corrected", "changed": True, "answer_after": "Three boilers.", "verdict": {"grounded": False}}
    rec = n.normalize_turn(turn(answer="Three boilers."), changed)
    g = next(m for m in rec["messages"] if m["role"] == "gate")
    assert g["answer_after"] == "Three boilers." and "gate_changed" in rec["flags"]
    assert rec["messages"][-1]["rewritten_by"] == "el_udr"
    open_gate = [kept[0], span("g", "root", 1, "gate", "contract_compose", agent="contract_analyst", input={"answer_before": "A"})]
    g = next(m for m in n.normalize_turn(turn(), open_gate)["messages"] if m["role"] == "gate")
    assert g["recorded"] is False and g["answer_after"] == "A" and g["label"] == "Vendor answer rewrite"


def test_gate_verdict_and_its_model_calls_stay_hidden():
    spans = [span("root", None, 0, "turn", "turn"),
             span("g", "root", 1, "gate", "el_udr", agent="el_udr", input={"answer_before": "A"},
                  output={"outcome": "degraded", "changed": True, "answer_after": "A + note", "verdict": {"issues": ["count off"]}}),
             span("j", "g", 2, "llm", "judge", agent="el_udr", model="claude-sonnet-5",
                  input={"system_prompt": "Judge", "user_message": "c"}, output={"model_output": {"issues": ["count off"]}})]
    hidden = n.normalize_turn(turn(), spans)
    g = next(m for m in hidden["messages"] if m["role"] == "gate")
    assert g["verdict"] is None and g["outcome"] is None and g["verdict_hidden"] is True and g["changed"] is True
    assert "count off" not in json.dumps(hidden, default=str)
    card = hidden["model_calls"][0]
    assert card["response"] is None and card["raw"] is None and card["withheld"] == ["response", "raw"]
    shown = n.normalize_turn(turn(), spans, reveal_gate_verdicts=True)
    g = next(m for m in shown["messages"] if m["role"] == "gate")
    assert g["outcome"] == "degraded" and g["verdict"] == {"issues": ["count off"]} and g["verdict_hidden"] is False
    assert shown["model_calls"][0]["response"] == {"model_output": {"issues": ["count off"]}}


def test_a_footnote_without_a_gate_span_is_an_inferred_gate():
    note = "Four boilers.\n\n_Automated accuracy check: flagged for a quick human verification — count off_"
    assert "gate_inferred:degraded" in n.normalize_turn(turn(answer=note), [])["flags"]
    blocked = "I found related data but couldn't fully verify an answer (no rows). Try naming the table"
    assert "gate_inferred:blocked" in n.normalize_turn(turn(answer=blocked), [])["flags"]
    na = "Four.\n\n_The automated accuracy check could not run on this answer, so it has not been independently verified_"
    assert "gate_inferred:not_evaluated" in n.normalize_turn(turn(answer=na), [])["flags"]


def test_legacy_wrapped_question_is_repaired_and_flagged():
    wrapped = "## Page\nBuildings list" + n.QUESTION_MARKER + "Which buildings are at risk?"
    rec = n.normalize_turn(turn(question=wrapped), [])
    assert rec["input"] == "Which buildings are at risk?" and "question_repaired" in rec["flags"]
    assert "legacy_provenance" in rec["flags"] and rec["meta"]["channel_source"] == "inferred"
    long = n.normalize_turn(turn(question="This is a Hoistra support session. " + "g" * 4000), [])
    assert "question_wrapped" in long["flags"]
    by_activity = n.normalize_turn(turn(question=wrapped), [], activity_input={"payload": {"message": "Typed words"}})
    assert by_activity["input"] == "Typed words"


def test_truncation_markers_are_flagged():
    spans = [span("root", None, 0, "turn", "turn"), span("a", "root", 1, "tool", "a", output="rows… [1200 more chars]"),
             span("b", "root", 2, "tool", "b", input={"_truncated": '{"q": "x'})]
    rec = n.normalize_turn(turn(), spans)
    assert "truncated" in rec["flags"]
    assert [m["truncated"] for m in rec["messages"] if m["role"] == "tool_result"] == [True, True]


def test_normalize_never_mutates_its_inputs():
    t, spans = turn(), loop_tree()
    meta = {"channel": "chat", "channel_source": "client", "origin_params": {"api_key": "x"}}
    before = copy.deepcopy((t, spans, meta))
    n.normalize_turn(t, spans, meta=meta)
    assert (t, spans, meta) == before


def test_meta_feedback_flags_and_permalink():
    meta = {"channel": "ui_action", "channel_source": "client", "classifier_version": "prov-1",
            "origin": "ui.matrix_cell:risk", "origin_params": {"building": "B-301"}, "page": "cc",
            "context_text": "api_key=abc123 page", "asker_role": "superadmin", "asker_scope": {"view_as": True}}
    conv = {"key": "s:s1", "session_kind": "observed", "session_label": "Boilers", "turn_index": 2, "turn_count": 3,
            "attempt": 1, "attempt_source": "recorded", "retry_of": None, "run_id": "live", "thread_deleted": True}
    rec = n.normalize_turn(turn(feedback_rating="down", feedback_comment="Wrong period", feedback_by=USER,
                                cost_complete=False, ok=False, error="boom"), [], meta=meta, conversation=conv)
    m = rec["meta"]
    assert (m["channel"], m["origin"], m["page"], m["turn_index"], m["turn_count"]) == ("ui_action", "ui.matrix_cell:risk", "cc", 2, 3)
    assert m["context_text"] == "api_key=[redacted] page" and m["asker"]["role"] == "superadmin"
    assert m["session_label"] == "Boilers" and m["conversation_key"] == "s:s1" and m["run_id"] == "live"
    assert rec["feedback"] == {"rating": "down", "comment": "Wrong period", "by_asker": True, "at": None}
    for f in ("view_as", "thread_deleted", "cost_incomplete", "failed"):
        assert f in rec["flags"], f
    assert "legacy_provenance" not in rec["flags"]
    assert rec["permalink"] == "?tl=conversations&run=live&conv=s%3As1&turn=turn-1"
    assert rec["messages"][-1]["subtype"] == "error"


def test_features_come_from_the_turn_row():
    f = n.features(turn(tools=["retrieve_docs", "answer_from_records"], input_tokens=10, output_tokens=5, cache_read_tokens=1))
    assert f == {"turn_count": 2, "tool_call_count": 1, "distinct_tools": 2, "has_retrieval": 1, "tokens": 16,
                 "llm_calls": 2, "cost_usd": 0.05, "latency_ms": 10000}
    assert n.features(turn(tools=["answer_from_records"]))["has_retrieval"] == 0
    rec = n.normalize_turn(turn(), loop_tree())
    assert rec["features"]["has_retrieval"] == 1            # a db span counts
    assert rec["text"].startswith("user: How many work orders") and "\nassistant: 758 work orders." in rec["text"]


def test_span_messages_carry_the_block_text_the_timeline_shows():
    import pytest

    spans = loop_tree() + [
        span("p", "root", 7, "plan", "plan: 1 step", input={"source": "router", "question": "q"},
             output={"plan": {"steps": [{"target": "wo", "api_key": "k-1"}]}, "rejected": None}),
        span("s", "p", 8, "step", "wo", output={"ok": True, "note": "Bearer abcdefghijklmnop"}),
        span("g", "root", 9, "gate", "el_udr", agent="el_udr", input={"answer_before": "A"},
             output={"outcome": "degraded", "changed": True, "answer_after": "A + note",
                     "verdict": {"issues": ["count off"]}}),
        span("j", "g", 10, "llm", "judge", agent="el_udr", model="claude-sonnet-5",
             input={"system_prompt": "Judge", "user_message": "c"}, output={"model_output": {"issues": ["count off"]}})]
    by_id = {s["id"]: s for s in spans}
    rec = n.normalize_turn(turn(), spans)
    block = {(m["role"], m["span_id"]): m["block"] for m in rec["messages"]}
    for role, sid in (("tool_result", "t1"), ("db_query", "q1"), ("plan", "p"), ("step", "s"),
                      ("model_call", "m1"), ("model_call", "m2")):
        assert block[(role, sid)] == n.block_text(by_id[sid], "output"), sid
    assert block[("tool_result", "t1")] == '{\n  "total": 758\n}'
    assert block[("db_query", "q1")] == '[\n  {\n    "count": 758\n  }\n]'
    assert "k-1" not in block[("plan", "p")] and "abcdefghijklmnop" not in block[("step", "s")]
    assert block[("model_call", "j")] == ""            # the gate's own call while its verdict is hidden (R23)
    assert block[("gate", "g")] is None and block[("tool_call", "t1")] is None
    assert block[("user", "root")] is None and block[("assistant", "root")] is None
    assert "count off" not in json.dumps([m["block"] for m in rec["messages"]])
    shown = n.normalize_turn(turn(), spans, reveal_gate_verdicts=True)
    judge = next(m for m in shown["messages"] if m["span_id"] == "j")
    assert judge["block"] == n.block_text(by_id["j"], "output") and "count off" in judge["block"]
    assert n.block_text(span("e", "root", 11, "tool", "x"), "output") == ""
    with pytest.raises(ValueError):
        n.block_text(by_id["t1"], "question")
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_normalize.py`
Expected: the 22 new tests fail with `AttributeError: module 'src.trace_lab.normalize' has no attribute 'normalize_turn'` (or `features`).

- [ ] **Step 3: Implement**

In `src/trace_lab/normalize.py` replace the import line:

old:
```python
from ..agents.fiix_credential_parse import _CRED_LINE_PATTERNS
```
new:
```python
from ..agents.fiix_credential_parse import _CRED_LINE_PATTERNS
from . import provenance
```

Append to the end of `src/trace_lab/normalize.py`:

```python
# ── the record (§3.2, §3.3, §3.6, §3.7, §3.10) ───────────────────────────────────────────────────

def _chars(v: Any) -> int:
    if v is None:
        return 0
    return len(v) if isinstance(v, str) else len(json.dumps(v, ensure_ascii=False, default=str))


def block_text(span: Mapping[str, Any], field: str) -> str:
    """One span field's annotatable text exactly as the timeline shows it (spec Review 2.2): the value
    normalize_turn gives that span's message, redacted (3.9); strings as they are, anything else as
    indented JSON. M2's annotation blocks, M4's label quotes and evidence_text read this (R36)."""
    if field not in ("input", "output"):
        raise ValueError(f"not a span field: {field!r}")
    kind = str(span.get("kind") or "")
    raw = span.get(field)
    o_ = raw if isinstance(raw, Mapping) else {}
    if field == "output" and kind in ("llm", "router"):
        value = _model_payload(raw)
    elif field == "output" and kind == "db":
        value = redact(_list(o_.get("rows"))[:50])
    elif field == "output" and kind == "plan":
        value = redact(o_.get("plan"))
    else:
        value = redact(raw)
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _msg(turn_id: str, span: Mapping[str, Any] | None, role: str, part: str, depth: int, group: str | None,
         field: str | None) -> dict[str, Any]:
    sid = _sid(span.get("id")) if span is not None else None
    return {"id": f"{turn_id}:{sid or 'turn'}:{part}", "role": role, "turn_id": turn_id, "span_id": sid,
            "parent_span_id": _sid(span.get("parent_id")) if span is not None else None, "depth": depth,
            "seq": _i(span.get("seq")) if span is not None else None, "group": group, "label": None,
            "started_at": _iso(span.get("started_at")) if span is not None else None,
            "ended_at": _iso(span.get("ended_at")) if span is not None else None,
            "latency_ms": _i(span.get("latency_ms")) if span is not None else None,
            "ok": span.get("ok") is not False if span is not None else True,
            "error": redact_text(str(span.get("error"))) if span is not None and span.get("error") else None,
            "anchor": {"turn_id": turn_id, "span_id": sid, "field": field} if field else None,
            "block": block_text(span, field) if span is not None and field in ("input", "output") else None,
            "collapse": False,
            "truncated": span is not None and (_is_truncated(span.get("input")) or _is_truncated(span.get("output")))}


def _scope(span: Mapping[str, Any], by_id: Mapping[str | None, Mapping[str, Any]], root_id: str | None) -> str | None:
    """The nearest non-agent ancestor: where a model's tool request and the tool span it ran as both live."""
    seen: set[str] = set()
    parent = _sid(span.get("parent_id"))
    while parent is not None and parent not in seen:
        seen.add(parent)
        p = by_id.get(parent)
        if p is None:
            return root_id
        if p.get("kind") != "agent":
            return parent
        parent = _sid(p.get("parent_id"))
    return root_id


def _provenance(turn: Mapping[str, Any], meta: Mapping[str, Any] | None,
                activity_input: Mapping[str, Any] | None) -> dict[str, Any]:
    if meta:
        return {"channel": meta.get("channel") or "chat", "channel_source": meta.get("channel_source") or "inferred",
                "classifier_version": meta.get("classifier_version"), "origin": meta.get("origin"),
                "origin_params": redact(meta.get("origin_params")) if meta.get("origin_params") is not None else None,
                "page": meta.get("page"),
                "context_text": redact_text(meta.get("context_text")) if meta.get("context_text") else None,
                "asker_role": meta.get("asker_role"),
                "asker_scope": redact(meta.get("asker_scope")) if meta.get("asker_scope") is not None else None}
    guess = provenance.infer_channel(turn, activity_input=activity_input)
    return {"channel": guess.get("channel") or "chat", "channel_source": "inferred",
            "classifier_version": guess.get("classifier_version") or provenance.LEGACY_CLASSIFIER,
            "origin": guess.get("origin"), "origin_params": None, "page": None, "context_text": None,
            "asker_role": None, "asker_scope": None}


def features(turn: Mapping[str, Any], messages: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Sampling features (§3.2), from the turn row's totals, never from summing spans."""
    tools = [str(t) for t in _list(turn.get("tools"))]
    has_db = any(m.get("role") == "db_query" for m in (messages or []))
    retrieval = has_db or any(t.startswith("retrieve_") or "search" in t or "doc_rag" in t for t in tools)
    tin, tout, tcache = (_i(turn.get(k)) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    return {"turn_count": 2, "tool_call_count": _i(turn.get("tool_calls")) or 0, "distinct_tools": len(set(tools)),
            "has_retrieval": 1 if retrieval else 0, "tokens": tin + tout + tcache,
            "llm_calls": _i(turn.get("llm_calls")) or 0, "cost_usd": _f(turn.get("cost_usd")) or 0.0,
            "latency_ms": _i(turn.get("latency_ms")) or 0}


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def _flat(m: Mapping[str, Any]) -> str:
    role = m.get("role")
    if role in ("user", "assistant"):
        return str(m.get("text") or "")
    if role == "model_call":
        return f"{m.get('label') or ''} {_as_text((m.get('model_call') or {}).get('response'))}".strip()
    if role == "tool_call":
        return f"{m.get('name')} {_as_text(m.get('arguments'))}"
    if role == "tool_result":
        return _as_text(m.get("content"))
    if role == "db_query":
        return f"{m.get('label')} · {m.get('row_count')} rows"
    if role == "plan":
        return _as_text(m.get("plan"))
    if role == "step":
        return f"{m.get('name')} {_as_text(m.get('output'))}"
    if role == "gate":
        return f"{m.get('label')} · changed: {bool(m.get('changed'))}"
    return str(m.get("label") or "")


def flatten_text(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"{m.get('role')}: {_flat(m)[:TEXT_FLATTEN_CHARS]}" for m in messages)


def normalize_turn(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]], *,
                   meta: Mapping[str, Any] | None = None,
                   activity_input: Mapping[str, Any] | None = None,
                   conversation: Mapping[str, Any] | None = None,
                   reveal_gate_verdicts: bool = False) -> dict[str, Any]:
    """turn = one agent_trace_turns row; spans = its agent_trace_spans rows in any order; meta = its turn_meta
    row or None; activity_input = the earliest orchestrator/turn/input activity row (legacy rows only);
    conversation = {key, session_kind, session_label, turn_index, turn_count, attempt, attempt_source,
    retry_of, run_id, thread_deleted}. reveal_gate_verdicts stays False until the reviewer has saved a verdict
    on this turn (R23; M2). Returns the record of §3.2."""
    turn_id = str(turn.get("turn_id") or "")
    flags: list[str] = []

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    tree, tree_flags = pick_tree(turn, spans) if spans else ([], [])
    for f in tree_flags:
        flag(f)
    if not spans:
        flag("spans_purged")
    root = next((s for s in tree if s.get("kind") == "turn" and not s.get("parent_id")), None)
    root_id = _sid(root.get("id")) if root is not None else None
    by_id = {_sid(s.get("id")): s for s in tree}
    if any(s is not root and s.get("parent_id") is not None and _sid(s.get("parent_id")) not in by_id for s in tree):
        flag("orphan_spans")
    walked = walk(tree, root_id) if tree else []

    question, q_flags = repair_question(turn, meta, activity_input)
    for f in q_flags:
        flag(f)
    prov = _provenance(turn, meta, activity_input)
    if meta is None:
        flag("legacy_provenance")
    answer = str(turn.get("answer") or "")

    def gate_above(s: Mapping[str, Any]) -> str | None:
        seen: set[str] = set()
        parent = _sid(s.get("parent_id"))
        while parent is not None and parent not in seen:
            seen.add(parent)
            p = by_id.get(parent)
            if p is None:
                return None
            if p.get("kind") == "gate":
                return parent
            parent = _sid(p.get("parent_id"))
        return None

    prompt_ref = next((_sid(s.get("id")) for s in tree if s.get("kind") == "stage" and s.get("name") == "prompt assembled"), None)
    kids_count: dict[str | None, int] = {}
    for s in tree:
        p = _sid(s.get("parent_id"))
        kids_count[p] = kids_count.get(p, 0) + 1

    messages: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    tool_calls_by_span: dict[str | None, dict[str, Any]] = {}
    last_changed_gate: str | None = None

    user = _msg(turn_id, root, "user", "user", 0, None, "question")
    user.update(text=redact_text(question), origin=prov["origin"], channel=prov["channel"], latency_ms=None,
                ok=True, error=None, truncated=False)
    messages.append(user)
    for s, depth, group in walked:
        if s is root:
            continue
        kind = str(s.get("kind") or "")
        name = str(s.get("name") or "")
        sid = _sid(s.get("id"))
        inp, out = s.get("input"), s.get("output")
        i_ = inp if isinstance(inp, Mapping) else {}
        o_ = out if isinstance(out, Mapping) else {}
        if kind == "agent":
            groups.append({"span_id": sid, "name": name, "parent_id": _sid(s.get("parent_id")),
                           "cost_usd": _f(s.get("cost_usd")), "cost_is_whole_run": True,
                           "latency_ms": _i(s.get("latency_ms")), "input_tokens": _i(s.get("input_tokens")),
                           "output_tokens": _i(s.get("output_tokens")), "child_count": kids_count.get(sid, 0)})
            continue
        if kind in ("llm", "router"):
            rec = model_call_record(s, turn_id=turn_id, group=group,
                                    prompt_ref=prompt_ref if group == "orchestrator loop" else None,
                                    withheld=gate_above(s) is not None and not reveal_gate_verdicts)
            model_calls.append(rec)
            m = _msg(turn_id, s, "model_call", "call", depth, group, "output")
            m.update(call_kind=rec["call_kind"], label=rec["label"], model_call=rec, cot_note=COT_CARD_NOTE)
            if rec["withheld"]:
                m["block"] = ""                  # the gate's own call while its verdict is hidden (R23)
            messages.append(m)
        elif kind == "tool":
            call = _msg(turn_id, s, "tool_call", "call", depth, group, None)
            call.update(name=name, label=name, arguments=redact(inp), requested_by=None)
            content = redact(out)
            chars = _chars(content)
            result = _msg(turn_id, s, "tool_result", "result", depth, group, "output")
            result.update(name=name, label=name, content=content, chars=chars, collapse=chars > RESULT_COLLAPSE_CHARS)
            tool_calls_by_span[sid] = call
            messages.extend([call, result])
        elif kind == "db":
            rows = redact(_list(o_.get("rows"))[:50])
            m = _msg(turn_id, s, "db_query", "query", depth, group, "output")
            m.update(sql=redact_text(str(i_.get("sql") or "")), params=redact(i_.get("params") or {}),
                     label=str(i_.get("label") or name), row_count=_i(o_.get("row_count")), rows=rows,
                     rows_shown=DB_ROWS_SHOWN, rows_truncated=_i(o_.get("truncated")) or 0,
                     collapse=_chars(rows) > RESULT_COLLAPSE_CHARS)
            messages.append(m)
        elif kind == "plan":
            m = _msg(turn_id, s, "plan", "plan", depth, group, "output")
            m.update(label=name, source=i_.get("source"), plan=redact(o_.get("plan")), rejected=o_.get("rejected"))
            messages.append(m)
        elif kind == "step":
            output = redact(out)
            m = _msg(turn_id, s, "step", "step", depth, group, "output")
            m.update(name=name, label=name, subtype="check" if CHECK_STEP.match(name) else "step",
                     input=redact(inp), output=output, collapse=_chars(output) > RESULT_COLLAPSE_CHARS)
            messages.append(m)
        elif kind == "gate":
            recorded = isinstance(out, Mapping)
            before = i_.get("answer_before")
            changed = bool(recorded and o_.get("changed"))
            after = o_.get("answer_after") if recorded and o_.get("answer_after") is not None else before
            m = _msg(turn_id, s, "gate", "gate", depth, group, "answer_after")
            m.update(gate=name, label=GATE_LABELS.get(name, name), recorded=recorded, changed=changed,
                     outcome=o_.get("outcome") if recorded and reveal_gate_verdicts else None,
                     verdict=redact(o_.get("verdict")) if recorded and reveal_gate_verdicts else None,
                     verdict_hidden=not reveal_gate_verdicts,
                     answer_before=redact_text(before) if isinstance(before, str) else before,
                     answer_after=redact_text(after) if isinstance(after, str) else after,
                     model_call_span_ids=[_sid(x.get("id")) for x, _, _ in walked
                                          if x.get("kind") in ("llm", "router") and gate_above(x) == sid],
                     anchors=[{"turn_id": turn_id, "span_id": sid, "field": "answer_before"},
                              {"turn_id": turn_id, "span_id": sid, "field": "answer_after"}])
            if changed:
                flag("gate_changed")
                last_changed_gate = name
            messages.append(m)
        elif kind == "stage" and name == "prompt assembled":
            m = _msg(turn_id, s, "system_note", "note", depth, group, "output")
            m.update(subtype="prompt", name=name, label="What the model was given", input=redact(inp),
                     output=redact(out), collapse=True)
            messages.append(m)
        else:
            body = {"input": redact(inp), "output": redact(out)}
            m = _msg(turn_id, s, "system_note", "note", depth, group, "output")
            m.update(subtype="stage" if kind == "stage" else "unknown_kind", name=name,
                     label=name if kind == "stage" else f"{kind}: {name}", input=body["input"],
                     output=body["output"], collapse=_chars(body) > RESULT_COLLAPSE_CHARS)
            messages.append(m)

    # A model's requested tools (names only, no ids) take the next unclaimed same-name tool span with a later
    # seq in the same scope (§3.6; agent spans are transparent there too).
    tools = [s for s, _, _ in walked if s.get("kind") == "tool"]
    claimed: set[str | None] = set()
    for s, _, _ in walked:
        if s.get("kind") != "llm":
            continue
        o_ = s.get("output")
        for req in (_list(o_.get("tool_calls")) if isinstance(o_, Mapping) else []):
            want = req.get("name") if isinstance(req, Mapping) else None
            if not want:
                continue
            here = _scope(s, by_id, root_id)
            cands = [t for t in tools if t.get("name") == want and _sid(t.get("id")) not in claimed
                     and _seq(t) > _seq(s) and _scope(t, by_id, root_id) == here]
            if not cands:
                flag("unexecuted_tool_requests")
                continue
            pick = min(cands, key=_sibling_key)
            claimed.add(_sid(pick.get("id")))
            tool_calls_by_span[_sid(pick.get("id"))]["requested_by"] = _sid(s.get("id"))
    # Activity-row tools pair by name (trace.py:346): two same-name tools overlapping under one parent may
    # carry each other's outputs.
    same: dict[tuple[str | None, str], list[Mapping[str, Any]]] = {}
    for t in tools:
        same.setdefault((_sid(t.get("parent_id")), str(t.get("name"))), []).append(t)
    for group_tools in same.values():
        for i, a in enumerate(group_tools):
            for b in group_tools[i + 1:]:
                a0, a1, b0, b1 = (_ts(a.get("started_at")), _ts(a.get("ended_at")),
                                  _ts(b.get("started_at")), _ts(b.get("ended_at")))
                if a0 and a1 and b0 and b1 and a0 < b1 and b0 < a1:
                    flag("concurrent_same_name_tools")

    stop = answer == "gate_interrupt"
    assistant = _msg(turn_id, root, "assistant", "answer", 0, None, "answer")
    assistant.update(text="Stopped at an approval gate" if stop else redact_text(answer),
                     subtype="workflow_stop" if stop else ("error" if turn.get("ok") is False else "answer"),
                     rewritten_by=last_changed_gate, ok=turn.get("ok") is not False,
                     error=redact_text(str(turn.get("error"))) if turn.get("error") else None,
                     latency_ms=_i(turn.get("latency_ms")), truncated=False)
    messages.append(assistant)

    if any(m.get("truncated") for m in messages):
        flag("truncated")
    if turn.get("cost_complete") is False:
        flag("cost_incomplete")
    if turn.get("ok") is False:
        flag("failed")
    if stop:
        flag("workflow_stop")
    if not any(s.get("kind") == "gate" and s.get("name") == "el_udr" for s in tree):
        for outcome, mark in GATE_FOOTNOTES:
            if mark in answer:
                flag(f"gate_inferred:{outcome}")
        if answer.startswith(GATE_BLOCKED_PREFIX):
            flag("gate_inferred:blocked")
    asker_scope = prov.get("asker_scope") if isinstance(prov.get("asker_scope"), Mapping) else {}
    if asker_scope.get("view_as") is True:
        flag("view_as")
    conv = dict(conversation or {})
    if conv.get("thread_deleted"):
        flag("thread_deleted")

    session_id = turn.get("session_id")
    golden = None
    if meta and meta.get("golden_run_id"):
        golden = {"run_id": str(meta.get("golden_run_id")), "case_key": meta.get("golden_case_key"),
                  "rep": _i(meta.get("rep"))}
    kind_ = conv.get("session_kind") or session_kind(session_id, thread_exists=False)
    key = conv.get("key") or group_key(session_id, turn_id)
    run_id = golden["run_id"] if golden else str(conv.get("run_id") or "live")
    label = conv.get("session_label") or session_label(kind_, session_id, title=None, golden=golden)
    tin, tout, tcache = (_i(turn.get(k)) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    feedback = None
    if turn.get("feedback_rating") or turn.get("feedback_comment"):
        by = turn.get("feedback_by")
        feedback = {"rating": turn.get("feedback_rating"),
                    "comment": redact_text(turn.get("feedback_comment")) if turn.get("feedback_comment") else None,
                    "by_asker": by is not None and _sid(by) == _sid(turn.get("user_id")),
                    "at": _iso(turn.get("feedback_at"))}
    return {
        "id": turn_id, "trace_id": turn_id, "normalizer_version": NORMALIZER_VERSION,
        "timestamp": _iso(turn.get("started_at")), "ended_at": _iso(turn.get("ended_at")),
        "created_at": _iso(turn.get("created_at")),
        "models": [str(x) for x in _list(turn.get("models"))], "tools": [str(x) for x in _list(turn.get("tools"))],
        "input": redact_text(question), "output": redact_text(answer),
        "ok": turn.get("ok") is not False,
        "error": redact_text(str(turn.get("error"))) if turn.get("error") else None,
        "messages": messages, "model_calls": model_calls, "groups": groups,
        "usage": {"input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                  "total_tokens": tin + tout + tcache, "cost_usd": _f(turn.get("cost_usd")) or 0.0,
                  "cost_complete": turn.get("cost_complete") is not False,
                  "llm_calls": _i(turn.get("llm_calls")) or 0, "tool_calls": _i(turn.get("tool_calls")) or 0,
                  "latency_ms": _i(turn.get("latency_ms")) or 0},
        "features": features(turn, messages),
        "meta": {"session_id": session_id, "session_kind": kind_, "session_label": label, "conversation_key": key,
                 "run_id": run_id, "case_key": golden["case_key"] if golden else None,
                 "rep": golden["rep"] if golden else None, "golden": golden,
                 "channel": prov["channel"], "channel_source": prov["channel_source"],
                 "classifier_version": prov["classifier_version"], "origin": prov["origin"],
                 "origin_params": prov["origin_params"], "page": prov["page"], "context_text": prov["context_text"],
                 "turn_index": conv.get("turn_index", 1), "turn_count": conv.get("turn_count", 1),
                 "attempt": conv.get("attempt", 1), "attempt_source": conv.get("attempt_source", "recorded"),
                 "retry_of": conv.get("retry_of"), "route": turn.get("route"),
                 "asker": {"user_id": _sid(turn.get("user_id")), "email": turn.get("email"),
                           "role": prov["asker_role"], "scope": prov["asker_scope"]},
                 "organization_id": _sid(turn.get("organization_id"))},
        "feedback": feedback,
        "text": flatten_text(messages),
        "flags": flags,
        "cot_boundary": COT_BOUNDARY,
        "permalink": "?tl=conversations&run=" + quote(run_id, safe="") + "&conv=" + quote(key, safe="")
                     + "&turn=" + quote(turn_id, safe=""),
    }
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_normalize.py tests/test_trace_lab_grouping.py tests/test_trace_lab_redaction.py`
Expected: `46 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 7: Conversation filters, cursors and SQL fragments

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/src/trace_lab/conversations.py`
- Test: `tests/test_trace_lab_conversations.py` (create; Tasks 8–9 append)

**Interfaces:**
- Consumes: M0 `db.SCHEMA`, `provenance.CHANNELS`, `provenance.inferred_channel_sql("tt")`; `trace.TURNS`, `trace.SPANS` (trace.py:44-45).
- Produces: `BadRequest(reason, error)`, `TurnFilter`, `ModelCallFilter`, `make_filter(**query, calls=False, now=None)`, `encode_cursor`, `decode_cursor`, `GROUP_KEY_SQL`, `SESSION_KIND_SQL`, `UPDATE_LOOKBACK_MINUTES`; private `_like`, `_base_sql(scope)`, `_run_sql(f, p, *, channel, golden)`, `_deleted_sql(f)`, `_hit_sql(f, p)`, `_call_filters_sql(f, p, *, channel, source)`, `_read()`, `_meta`, `_as_list`, `_num`, `_iso` used by Task 8.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_lab_conversations.py`:

```python
"""Trace Lab conversations without a database: filters, cursors, the SQL the loaders build and bind, the response
shapes, and the routes' auth, readiness and error envelope (spec §"Trace capture" 5)."""
from __future__ import annotations

import importlib.machinery
import re
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from src.agents import trace
from src.services.principal import Principal
from src.trace_lab import conversations as cv
from src.trace_lab import db

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ORG = "11111111-1111-4111-8111-111111111111"


def test_the_default_window_is_thirty_days_back_and_one_day_ahead():
    f = cv.make_filter(now=NOW)
    assert f.since == NOW - timedelta(days=30) and f.until == NOW + timedelta(days=1)
    assert f.run == "live" and f.reviewed == "any" and not f.include_deleted


def test_filters_are_validated_and_parsed():
    run = str(uuid.uuid4()).upper()
    f = cv.make_filter(now=NOW, run=run, channel="chat, upload,chat", channel_source="client", session_kind="observed",
                       status="error", model=" gpt-5.6-terra ", q="  boilers ", user="ada@", asker_role="admin,unknown",
                       deleted="include")
    assert f.run == run.lower() and f.channels == ("chat", "upload") and f.channel_sources == ("client",)
    assert f.session_kinds == ("observed",) and f.status == "error" and f.model == "gpt-5.6-terra"
    assert f.q == "boilers" and f.user == "ada@" and f.asker_roles == ("admin", "unknown") and f.include_deleted
    calls = cv.make_filter(now=NOW, calls=True, agent="orchestrator loop", kind="router")
    assert isinstance(calls, cv.ModelCallFilter) and calls.kind == "router" and calls.agent == "orchestrator loop"


@pytest.mark.parametrize("kw,reason", [
    ({"since": NOW - timedelta(days=400)}, "window_too_wide"),
    ({"since": NOW, "until": NOW - timedelta(days=1)}, "bad_window"),
    ({"run": "yesterday"}, "bad_run"),
    ({"channel": "chat,email"}, "bad_channel"),
    ({"session_kind": "shared"}, "bad_session_kind"),
    ({"reviewed": "mine"}, "reviewed_filter_unavailable"),
    ({"reviewed": "maybe"}, "bad_reviewed"),
    ({"calls": True, "kind": "tool"}, "bad_kind"),
])
def test_bad_filters_are_refused_with_a_reason(kw, reason):
    with pytest.raises(cv.BadRequest) as e:
        cv.make_filter(now=NOW, **kw)
    assert e.value.reason == reason and e.value.error


def test_cursors_round_trip_and_bad_ones_are_refused():
    c = cv.encode_cursor(NOW, "s:3b1f")
    assert "=" not in c and cv.decode_cursor(c) == (NOW, "s:3b1f")
    assert cv.decode_cursor(None) is None and cv.decode_cursor("") is None
    for bad in ("%%%", "bm90IGpzb24", cv.encode_cursor(NOW, "k")[:-3] + "!!!", "x" * 401):
        with pytest.raises(cv.BadRequest) as e:
            cv.decode_cursor(bad)
        assert e.value.reason == "bad_cursor"


def test_search_text_is_escaped_for_ilike():
    assert cv._like("50%_off\\") == "%50\\%\\_off\\\\%"


def test_turn_filters_bind_every_value():
    p: dict = {}
    f = cv.make_filter(now=NOW, channel="chat", channel_source="client", session_kind="observed", status="ok",
                       model="m", q="boilers", user="ada", asker_role="admin")
    sql = cv._hit_sql(f, p)
    assert sql.count(" AND ") == 7
    assert p == {"f_channels": ["chat"], "f_sources": ["client"], "f_kinds": ["observed"], "f_model": "m",
                 "f_q": "%boilers%", "f_q_exact": "boilers", "f_user": "%ada%", "f_roles": ["admin"]}
    assert cv._hit_sql(cv.make_filter(now=NOW), {}) == "TRUE"
    p2: dict = {}
    assert cv._run_sql(cv.make_filter(now=NOW), p2) == "channel <> 'golden'" and p2 == {}
    run = str(uuid.uuid4())
    assert cv._run_sql(cv.make_filter(now=NOW, run=run), p2) == "golden_run_id = CAST(:f_run AS uuid)" and p2["f_run"] == run
    assert cv._run_sql(cv.make_filter(now=NOW, run="all"), {}) == "TRUE"
    assert cv._deleted_sql(cv.make_filter(now=NOW)) == "thread_deleted_at IS NULL"
    assert cv._deleted_sql(cv.make_filter(now=NOW, deleted="include")) == "TRUE"


def test_the_group_key_and_session_kind_sql_carry_the_section_4_1_rules():
    for piece in ("'t:' || tt.turn_id", "LIKE 'rerun%' THEN 'r:' || tt.turn_id", "'s:' || tt.session_id"):
        assert piece in cv.GROUP_KEY_SQL
    for prefix in ("golden-%", "report-%", "docs-%", "ing-%", "rerun%", "skilllab-%"):
        assert f"LIKE '{prefix}'" in cv.SESSION_KIND_SQL
    assert "th.id IS NOT NULL THEN 'observed'" in cv.SESSION_KIND_SQL
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_conversations.py`
Expected: collection error `ImportError: cannot import name 'conversations' from 'src.trace_lab'`.

- [ ] **Step 3: Implement**

Create `src/trace_lab/conversations.py`:

```python
"""Trace Lab conversations: company-scoped, read-only SQL over the trace tables, grouped into conversations
(spec docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md, "Trace capture, normalizer and
conversations" §4-§5).

Every read runs in a transaction that starts with SET TRANSACTION READ ONLY, so Postgres itself refuses any
write, and SET LOCAL statement_timeout = '10s', because the database is shared. Every route-facing query is
company-scoped with db.scope_sql. load_turn(s) are unscoped and exist for Trace Lab jobs only; never call
them from a route.
"""
from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import text

from ..agents import trace
from . import db, normalize, provenance

TURNS = trace.TURNS
SPANS = trace.SPANS
THREADS = "plenum_cafm.chat_threads"
ACTIVITY = "plenum_cafm.agent_activity_log"
TURN_META = f"{db.SCHEMA}.turn_meta"
UPDATE_LOOKBACK_MINUTES = 30
MAX_WINDOW_DAYS = 366
MAX_TURN_HEADERS = 500
CHANNEL_SOURCES = ("recorded", "client", "inferred")
SESSION_KINDS = ("observed", "synthetic", "inferred")
ASKER_ROLES = ("user", "admin", "superadmin", "unknown")
REVIEWED = ("any", "reviewed", "unreviewed", "mine")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_KEY_RE = re.compile(r"^[str]:[^\x00-\x1f]{1,200}$")

#: §4.1, over agent_trace_turns tt. normalize.group_key / session_kind implement the same rules.
GROUP_KEY_SQL = ("CASE WHEN tt.session_id IS NULL OR btrim(tt.session_id) = '' THEN 't:' || tt.turn_id "
                 "WHEN tt.session_id LIKE 'rerun%' THEN 'r:' || tt.turn_id "
                 "ELSE 's:' || tt.session_id END")
#: Needs `th` = LEFT JOIN plenum_cafm.chat_threads th ON th.id = tt.session_id.
SESSION_KIND_SQL = ("CASE WHEN tt.session_id IS NULL OR btrim(tt.session_id) = '' THEN 'inferred' "
                    "WHEN tt.session_id LIKE 'golden-%' OR tt.session_id LIKE 'report-%' OR tt.session_id LIKE 'docs-%' "
                    "OR tt.session_id LIKE 'ing-%' OR tt.session_id LIKE 'rerun%' OR tt.session_id LIKE 'skilllab-%' "
                    "THEN 'synthetic' WHEN th.id IS NOT NULL THEN 'observed' ELSE 'inferred' END")
#: The asker's words out of a legacy wrapped REST question: the text after the last marker (orchestrator.py:914).
_ASKER_WORDS_SQL = r"regexp_replace(coalesce(tt.question, ''), '^.*\n\n---\n\n\*\*Current user message:\*\*\n', '')"
_SOURCE_SQL = "CASE WHEN tm.turn_id IS NOT NULL THEN tm.channel_source ELSE 'inferred' END"


class BadRequest(ValueError):
    """A query the routes answer with 400 {ok: false, error, reason}."""

    def __init__(self, reason: str, error: str) -> None:
        super().__init__(error)
        self.reason = reason
        self.error = error


@dataclass(frozen=True)
class TurnFilter:
    since: datetime
    until: datetime
    run: str = "live"                         # "live" | "all" | golden run uuid
    channels: tuple[str, ...] = ()
    channel_sources: tuple[str, ...] = ()
    session_kinds: tuple[str, ...] = ()
    status: str | None = None                 # "ok" | "error"
    model: str | None = None
    q: str | None = None
    user: str | None = None
    asker_roles: tuple[str, ...] = ()
    reviewed: str = "any"                     # "any" | "reviewed" | "unreviewed" | "mine"
    include_deleted: bool = False


@dataclass(frozen=True)
class ModelCallFilter(TurnFilter):
    agent: str | None = None
    kind: str | None = None                   # "llm" | "router"


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.isoformat()
    return None if v is None else str(v)


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return list(v) if isinstance(v, (list, tuple)) else []


def _meta(v: Any) -> dict[str, Any] | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return dict(v) if isinstance(v, Mapping) else None


def _csv(value: str | None, allowed: Sequence[str], name: str) -> tuple[str, ...]:
    if not value:
        return ()
    items = tuple(dict.fromkeys(x.strip() for x in str(value).split(",") if x.strip()))
    bad = [x for x in items if x not in allowed]
    if bad:
        raise BadRequest(f"bad_{name}", f"Unknown {name.replace('_', ' ')}: {bad[0][:40]}.")
    return items


def make_filter(*, since: datetime | None = None, until: datetime | None = None, run: str | None = "live",
                channel: str | None = None, channel_source: str | None = None, session_kind: str | None = None,
                status: str | None = None, model: str | None = None, q: str | None = None, user: str | None = None,
                asker_role: str | None = None, reviewed: str = "any", deleted: str = "hide",
                agent: str | None = None, kind: str | None = None, calls: bool = False,
                now: datetime | None = None) -> TurnFilter:
    """The route's query parameters as a filter (§5.3). Raises BadRequest with a reason the route returns."""
    now = _aware(now) or datetime.now(timezone.utc)
    start = _aware(since) or now - timedelta(days=30)
    end = _aware(until) or now + timedelta(days=1)
    if end <= start:
        raise BadRequest("bad_window", "The end of the period must be after its start.")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise BadRequest("window_too_wide", "Choose a period of at most 366 days.")
    run = (run or "live").strip()
    if run not in ("live", "all") and not _UUID_RE.match(run):
        raise BadRequest("bad_run", "That run is not known.")
    if reviewed not in REVIEWED:
        raise BadRequest("bad_reviewed", "Unknown review filter.")
    if reviewed != "any":
        # M2 replaces this with annotations.reviewed_predicate (§5.3).
        raise BadRequest("reviewed_filter_unavailable", "Filtering by review needs open coding, which is not installed yet.")
    if status not in (None, "", "ok", "error"):
        raise BadRequest("bad_status", "Status is ok or error.")
    if deleted not in ("hide", "include"):
        raise BadRequest("bad_deleted", "Deleted is hide or include.")
    fields = dict(since=start, until=end, run=run if run in ("live", "all") else run.lower(),
                  channels=_csv(channel, tuple(provenance.CHANNELS), "channel"),
                  channel_sources=_csv(channel_source, CHANNEL_SOURCES, "channel_source"),
                  session_kinds=_csv(session_kind, SESSION_KINDS, "session_kind"),
                  status=status or None, model=(model or "").strip() or None, q=(q or "").strip() or None,
                  user=(user or "").strip() or None, asker_roles=_csv(asker_role, ASKER_ROLES, "asker_role"),
                  reviewed=reviewed, include_deleted=deleted == "include")
    if calls:
        if kind not in (None, "", "llm", "router"):
            raise BadRequest("bad_kind", "Kind is llm or router.")
        return ModelCallFilter(**fields, agent=(agent or "").strip() or None, kind=kind or None)
    return TurnFilter(**fields)


def encode_cursor(t: datetime, k: str) -> str:
    raw = json.dumps({"t": _aware(t).isoformat(), "k": k}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        if len(cursor) > 400:
            raise ValueError("cursor too long")
        d = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        t, k = datetime.fromisoformat(d["t"]), d["k"]
        if not isinstance(k, str) or not k or len(k) > 300:
            raise ValueError("bad key")
        return _aware(t), k
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise BadRequest("bad_cursor", "That page link is no longer valid. Reload the list.") from exc


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _channel_sql() -> str:
    return f"CASE WHEN tm.turn_id IS NOT NULL THEN tm.channel ELSE {provenance.inferred_channel_sql('tt')} END"


def _base_sql(scope: str) -> str:
    """One row per turn in the window and in scope: its channel (turn_meta first, the legacy classifier second),
    group key, session kind and thread. Needs :since and :until plus the scope's own parameters."""
    return f"""
    SELECT tt.turn_id, tt.session_id, tt.started_at, tt.created_at, tt.ok, tt.cost_usd, tt.email, tt.models,
           tt.tool_calls, tt.question, tt.answer, left({_ASKER_WORDS_SQL}, 300) AS q300,
           {_channel_sql()} AS channel, {_SOURCE_SQL} AS channel_source,
           (tm.turn_id IS NULL) AS legacy, tm.golden_run_id, tm.golden_case_key, tm.rep,
           coalesce(tm.asker_role, 'unknown') AS asker_role,
           {GROUP_KEY_SQL} AS group_key, {SESSION_KIND_SQL} AS session_kind,
           th.title AS thread_title, th.deleted_at AS thread_deleted_at
      FROM {TURNS} tt
      LEFT JOIN {TURN_META} tm ON tm.turn_id = tt.turn_id
      LEFT JOIN {THREADS} th ON th.id = tt.session_id
     WHERE {scope} AND tt.started_at >= :since AND tt.started_at < :until"""


def _run_sql(f: TurnFilter, p: dict[str, Any], *, channel: str = "channel", golden: str = "golden_run_id") -> str:
    if f.run == "all":
        return "TRUE"
    if f.run == "live":
        return f"{channel} <> 'golden'"
    p["f_run"] = f.run
    return f"{golden} = CAST(:f_run AS uuid)"


def _deleted_sql(f: TurnFilter) -> str:
    return "TRUE" if f.include_deleted else "thread_deleted_at IS NULL"


def _hit_sql(f: TurnFilter, p: dict[str, Any]) -> str:
    """The AND of the turn filters over _base_sql's columns (TRUE when there are none)."""
    parts: list[str] = []
    if f.channels:
        parts.append("channel = ANY(CAST(:f_channels AS text[]))")
        p["f_channels"] = list(f.channels)
    if f.channel_sources:
        parts.append("channel_source = ANY(CAST(:f_sources AS text[]))")
        p["f_sources"] = list(f.channel_sources)
    if f.session_kinds:
        parts.append("session_kind = ANY(CAST(:f_kinds AS text[]))")
        p["f_kinds"] = list(f.session_kinds)
    if f.status == "ok":
        parts.append("ok")
    elif f.status == "error":
        parts.append("NOT ok")
    if f.model:
        parts.append("models ? :f_model")
        p["f_model"] = f.model
    if f.q:
        parts.append("(question ILIKE :f_q OR answer ILIKE :f_q OR turn_id = :f_q_exact OR session_id = :f_q_exact)")
        p["f_q"], p["f_q_exact"] = _like(f.q), f.q
    if f.user:
        parts.append("email ILIKE :f_user")
        p["f_user"] = _like(f.user)
    if f.asker_roles:
        parts.append("asker_role = ANY(CAST(:f_roles AS text[]))")
        p["f_roles"] = list(f.asker_roles)
    return " AND ".join(parts) or "TRUE"


def _call_filters_sql(f: ModelCallFilter, p: dict[str, Any], *, channel: str, source: str) -> str:
    parts: list[str] = []
    if f.channels:
        parts.append(f"{channel} = ANY(CAST(:f_channels AS text[]))")
        p["f_channels"] = list(f.channels)
    if f.channel_sources:
        parts.append(f"{source} = ANY(CAST(:f_sources AS text[]))")
        p["f_sources"] = list(f.channel_sources)
    if f.status == "ok":
        parts.append("s.ok")
    elif f.status == "error":
        parts.append("NOT s.ok")
    if f.model:
        parts.append("s.model = :f_model")
        p["f_model"] = f.model
    if getattr(f, "agent", None):
        parts.append("s.agent = :f_agent")
        p["f_agent"] = f.agent
    if getattr(f, "kind", None):
        parts.append("s.kind = :f_kind")
        p["f_kind"] = f.kind
    if f.q:
        parts.append("(tt.question ILIKE :f_q OR tt.answer ILIKE :f_q OR tt.turn_id = :f_q_exact OR tt.session_id = :f_q_exact)")
        p["f_q"], p["f_q_exact"] = _like(f.q), f.q
    return " AND ".join(parts) or "TRUE"


@asynccontextmanager
async def _read() -> AsyncIterator[Any]:
    """A read-only transaction on the shared engine (database.py:34-50), bounded at 10 s."""
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_conversations.py`
Expected: `14 passed` (6 tests + 8 parametrized cases).

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 8: Conversation, update, facet, run and model-call loaders

**Files:**
- Modify: `src/trace_lab/conversations.py` (append to the end)
- Test: `tests/test_trace_lab_conversations.py` (append)

**Interfaces:**
- Consumes: Task 7; Tasks 2–6 normalize functions; M0 `db.scope_sql`.
- Produces: `list_conversations`, `conversation_updates`, `conversation_facets`, `get_conversation`, `get_turn`, `list_runs`, `list_model_calls`, `get_model_call`, `load_turn`, `load_turns` (signatures under Interfaces). Response shapes as spec §5.4, plus: list rows' `flags` may hold `legacy_provenance`, `inferred_session`, `synthetic_session`, `thread_deleted`; facets = `{ok, as_of, channels:[{value,turns}], channel_sources:[{value,turns}], session_kinds:[{value,conversations}], models:[{value,turns}]}`; model-call rows add `withheld`, `question_excerpt`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trace_lab_conversations.py`:

```python
ADMIN = Principal(user_id=uuid.uuid4(), email="ada@example.com", organization_id=uuid.UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)
BIND = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]


class FakeConn:
    """Answers each execute with the next canned row list and records the SQL and parameters it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _Result(self.answers.pop(0) if self.answers else [])

    @asynccontextmanager
    async def begin_nested(self):
        yield self


def use(monkeypatch, conn):
    @asynccontextmanager
    async def fake_read():
        yield conn
    monkeypatch.setattr(cv, "_read", fake_read)


def assert_bound(conn):
    for sql, params in conn.calls:
        missing = set(BIND.findall(sql)) - set(params)
        assert not missing, (missing, sql[:300])


def conv_row(key, sid, last, **kw):
    r = {"group_key": key, "session_kind": "observed", "session_id": sid, "title": None, "first_turn_at": last,
         "last_turn_at": last, "turns_in_window": 2, "matching_turns": 1, "failed_turns": 0, "cost_usd": Decimal("0.031"),
         "channels": ["chat"], "channel_sources": ["client"], "first_question": "How many boilers?",
         "asker_email": "fm@example.com", "asker_role": "admin", "legacy": False, "thread_deleted_at": None,
         "golden_run_id": None, "golden_case_key": None, "golden_rep": None, "matched_turn_id": None, "total": 3}
    r.update(kw)
    return r


def turn_row(tid, minutes, meta=None, **kw):
    r = {"turn_id": tid, "session_id": "a", "organization_id": uuid.UUID(ORG), "user_id": uuid.uuid4(),
         "email": "fm@example.com", "question": "How many boilers?", "answer": "Four.", "route": None,
         "started_at": NOW + timedelta(minutes=minutes), "ended_at": NOW + timedelta(minutes=minutes, seconds=5),
         "latency_ms": 5000, "llm_calls": 1, "tool_calls": 0, "cost_usd": Decimal("0.01"), "cost_complete": True,
         "input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 0, "models": ["claude-sonnet-5"], "tools": [],
         "ok": True, "error": None, "feedback_rating": None, "feedback_comment": None, "feedback_by": None,
         "feedback_at": None, "created_at": NOW, "inferred_channel": "chat", "meta": meta,
         "thread_title": "Boiler certificates", "thread_deleted_at": None, "thread_exists": True}
    r.update(kw)
    return r


def sp(id, parent, seq, kind, name, **kw):
    r = {"id": id, "turn_id": "turn-1", "parent_id": parent, "seq": seq, "kind": kind, "name": name, "agent": "orchestrator",
         "model": None, "started_at": NOW, "ended_at": NOW, "latency_ms": 1, "input": None, "output": None,
         "input_tokens": None, "output_tokens": None, "cache_read_tokens": None, "cost_usd": None, "ok": True, "error": None}
    r.update(kw)
    return r


async def test_the_conversation_list_pages_and_shapes_its_rows(monkeypatch):
    clock = {"as_of": NOW, "watermark": NOW - timedelta(seconds=2)}
    rows = [conv_row("s:a", "a", NOW, title="Boiler certificates"),
            conv_row("s:report-1", "report-1", NOW - timedelta(minutes=5), session_kind="synthetic", legacy=True),
            conv_row("t:turn-9", None, NOW - timedelta(minutes=9), session_kind="inferred")]
    conn = FakeConn([clock], rows, [{"group_key": "s:a", "models": ["claude-sonnet-5"], "tool_calls": 2},
                                    {"group_key": "s:a", "models": ["gpt-5.6-terra"], "tool_calls": 1}])
    use(monkeypatch, conn)
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(now=NOW, q="boilers", channel="chat"), cursor=None, limit=2)
    assert_bound(conn)
    assert out["total"] == 3 and out["watermark"] == (NOW - timedelta(seconds=2)).isoformat()
    assert cv.decode_cursor(out["next_cursor"]) == (NOW - timedelta(minutes=5), "s:report-1")
    a, b = out["conversations"]
    assert a["session_label"] == "Boiler certificates" and a["models"] == ["claude-sonnet-5", "gpt-5.6-terra"]
    assert a["tool_count"] == 3 and a["cost_usd"] == 0.031 and a["flags"] == [] and a["run_id"] == "live"
    assert b["session_label"] == "Report card run" and b["flags"] == ["legacy_provenance", "synthetic_session"]
    page_sql, page_params = conn.calls[1]
    assert "tt.organization_id = CAST(:org AS uuid)" in page_sql and page_params["limit_plus_one"] == 3
    assert "channel = ANY(CAST(:f_channels AS text[]))" in page_sql and page_params["f_q"] == "%boilers%"
    assert conn.calls[2][1]["keys"] == ["s:a", "s:report-1"]


async def test_a_conversation_reads_every_header_and_normalizes_only_the_active_answer(monkeypatch):
    meta = {"turn_id": "turn-2", "channel": "chat", "channel_source": "client", "origin": "chat.typed",
            "classifier_version": "prov-1"}
    turns = [turn_row("turn-1", 0), turn_row("turn-2", 1, meta=meta, question="And at B-302?")]
    spans = [sp(uuid.uuid4(), None, 0, "turn", "turn", turn_id="turn-2")]
    conn = FakeConn(turns, spans)
    use(monkeypatch, conn)
    out = await cv.get_conversation(ADMIN, ORG, "s:a", turn_id="turn-2")
    assert_bound(conn)
    assert len(conn.calls) == 2                              # no activity read: the active answer has turn_meta
    assert out["key"] == "s:a" and out["session_kind"] == "observed" and out["session_label"] == "Boiler certificates"
    assert [h["turn_index"] for h in out["turns"]] == [1, 2] and out["turn_count"] == 2 and not out["turns_truncated"]
    assert out["turns"][0]["flags"] == ["legacy_provenance"] and out["turns"][1]["channel_source"] == "client"
    act = out["active"]
    assert act["id"] == "turn-2" and act["meta"]["turn_index"] == 2 and act["meta"]["conversation_key"] == "s:a"
    assert act["input"] == "And at B-302?" and out["run"] == {"run_id": "live", "kind": "live"}
    assert "tt.session_id = :sid" in conn.calls[0][0] and conn.calls[0][1]["key"] == "s:a"
    assert await cv.get_conversation(ADMIN, ORG, "x:bad") is None


async def test_a_legacy_active_answer_reads_its_earliest_activity_row(monkeypatch):
    turns = [turn_row("turn-1", 0, question="ctx\n\n---\n\n**Current user message:**\nWrapped?")]
    conn = FakeConn(turns, [], [{"payload": {"message": "Typed words"}, "created_at": NOW}])
    use(monkeypatch, conn)
    out = await cv.get_conversation(ADMIN, ORG, "s:a")
    assert_bound(conn)
    assert "agent_activity_log" in conn.calls[2][0] and out["active"]["input"] == "Typed words"
    assert "question_repaired" in out["active"]["flags"] and "spans_purged" in out["active"]["flags"]


async def test_a_deleted_thread_is_hidden_unless_asked(monkeypatch):
    turns = [turn_row("turn-1", 0, thread_deleted_at=NOW, meta={"channel": "chat", "channel_source": "client"})]
    use(monkeypatch, FakeConn(list(turns)))
    assert await cv.get_conversation(ADMIN, ORG, "s:a") is None
    use(monkeypatch, FakeConn(list(turns), []))
    out = await cv.get_conversation(ADMIN, ORG, "s:a", include_deleted=True)
    assert out["thread_deleted"] is True and "thread_deleted" in out["flags"]


async def test_updates_and_facets_build_bound_queries(monkeypatch):
    conn = FakeConn([{"as_of": NOW, "watermark": NOW - timedelta(seconds=2)}],
                    [{"new_turns": 3, "new_conversations": 2, "latest": NOW}])
    use(monkeypatch, conn)
    out = await cv.conversation_updates(ADMIN, ORG, cv.make_filter(now=NOW), watermark=NOW - timedelta(minutes=1))
    assert_bound(conn)
    assert (out["new_turns"], out["new_conversations"]) == (3, 2)
    assert conn.calls[1][1]["since"] == NOW - timedelta(minutes=1 + cv.UPDATE_LOOKBACK_MINUTES)
    assert "created_at > CAST(:wm AS timestamptz)" in conn.calls[1][0]
    conn = FakeConn([{"as_of": NOW, "watermark": NOW}],
                    [{"facet": "channel", "value": "chat", "n": 5}, {"facet": "channel", "value": "upload", "n": 9},
                     {"facet": "session_kind", "value": "observed", "n": 2},
                     {"facet": "model", "value": "claude-sonnet-5", "n": 4},
                     {"facet": "channel_source", "value": "inferred", "n": 14}])
    use(monkeypatch, conn)
    out = await cv.conversation_facets(ADMIN, ORG, cv.make_filter(now=NOW))
    assert_bound(conn)
    assert out["channels"] == [{"value": "upload", "turns": 9}, {"value": "chat", "turns": 5}]
    assert out["session_kinds"] == [{"value": "observed", "conversations": 2}]
    assert out["models"] == [{"value": "claude-sonnet-5", "turns": 4}]
    assert out["channel_sources"] == [{"value": "inferred", "turns": 14}]


async def test_model_call_rows_are_finished_redacted_and_withheld_under_a_gate(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    base = {"turn_id": "turn-1", "kind": "llm", "name": "model", "agent": "orchestrator loop", "model": "claude-sonnet-5",
            "started_at": NOW, "latency_ms": 1800, "input_tokens": 900, "output_tokens": 40, "cache_read_tokens": 0,
            "cost_usd": Decimal("0.004"), "ok": True, "error": None, "request_kind": "loop_summary",
            "response_preview": "Use Bearer abcdefghijklmnop to call", "loop_output": True, "tool_call_requests": 1,
            "under_gate": False, "question_excerpt": "How many boilers?", "conversation_key": "s:a"}
    rows = [dict(base, span_id=uuid.uuid4()),
            dict(base, span_id=uuid.uuid4(), name="judge", agent="el_udr", request_kind="exchange", loop_output=False,
                 tool_call_requests=0, under_gate=True, response_preview='{"issues": ["count off"]}')]
    agg = {"calls": 2, "errors": 0, "latency_p50_ms": 1800.0, "latency_p95_ms": 1800.0, "input_tokens": 1800,
           "output_tokens": 80, "cache_read_tokens": 0, "cost_usd": Decimal("0.008"), "unpriced_calls": 0}
    by_model = [{"model": "claude-sonnet-5", "calls": 2, "errors": 0, "latency_p50_ms": 1800.0, "cost_usd": Decimal("0.008")}]
    conn = FakeConn([{"as_of": NOW, "watermark": NOW}], rows, [agg], by_model)
    use(monkeypatch, conn)
    out = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(now=NOW, calls=True), cursor=None, limit=100)
    assert_bound(conn)
    first, judge = out["calls"]
    assert first["label"] == "tool decision" and first["response_preview"] == "Use Bearer [redacted] to call"
    assert judge["withheld"] is True and judge["response_preview"] is None and judge["label"] == "judge"
    assert out["aggregate"]["total_tokens"] == 1880 and out["aggregate"]["cost_usd"] == 0.008
    assert out["aggregate"]["by_model"][0]["model"] == "claude-sonnet-5" and out["next_cursor"] is None
    assert "s.kind IN ('llm', 'router')" in conn.calls[1][0] and out["retention_days"] == 90
    with pytest.raises(cv.BadRequest):
        await cv.list_model_calls(ADMIN, ORG, cv.make_filter(now=NOW, calls=True), cursor=cv.encode_cursor(NOW, "s:a"), limit=10)


async def test_one_model_call_is_read_from_its_normalized_turn(monkeypatch):
    root, gate, judge = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    head = turn_row("turn-1", 0, conversation_key="s:a")
    spans = [sp(root, None, 0, "turn", "turn"), sp(gate, root, 1, "gate", "el_udr", agent="el_udr"),
             sp(judge, gate, 2, "llm", "judge", agent="el_udr", model="claude-sonnet-5",
                input={"system_prompt": "J", "user_message": "c"}, output={"model_output": "x"})]
    conn = FakeConn([head], spans)
    use(monkeypatch, conn)
    out = await cv.get_model_call(ADMIN, ORG, str(judge).upper())
    assert_bound(conn)
    assert out["turn_id"] == "turn-1" and out["conversation_key"] == "s:a"
    assert out["model_call"]["span_id"] == str(judge) and out["model_call"]["withheld"] == ["response", "raw"]
    assert await cv.get_model_call(ADMIN, ORG, "not-a-uuid") is None


async def test_runs_list_live_traffic_and_golden_runs_only_when_m3_exists(monkeypatch):
    # M3 updates the first half of this test when src/trace_lab/golden.py lands.
    live = {"started_at": NOW - timedelta(days=1), "ended_at": NOW, "conversation_count": 4, "turn_count": 9,
            "cost_usd": Decimal("0.5"), "models": ["claude-sonnet-5"]}
    use(monkeypatch, FakeConn([live]))
    monkeypatch.delitem(sys.modules, "src.trace_lab.golden", raising=False)
    out = await cv.list_runs(ADMIN, ORG, since=NOW - timedelta(days=30), until=NOW)
    assert out["golden_available"] is False and [r["run_id"] for r in out["runs"]] == ["live"]
    assert out["runs"][0]["name"] == "All live traffic" and out["runs"][0]["turn_count"] == 9
    assert out["runs"][0]["recorded"] is False and out["runs"][0]["cost_usd"] == 0.5
    mod = types.ModuleType("src.trace_lab.golden")
    mod.__spec__ = importlib.machinery.ModuleSpec("src.trace_lab.golden", None)

    async def run_summaries(principal, org, since, until):
        return [{"run_id": str(uuid.uuid4()), "kind": "golden", "name": "run-20261015T101500Z-1a2b3c4d", "recorded": True}]

    mod.run_summaries = run_summaries
    monkeypatch.setitem(sys.modules, "src.trace_lab.golden", mod)
    use(monkeypatch, FakeConn([live]))
    out = await cv.list_runs(ADMIN, ORG, since=NOW - timedelta(days=30), until=NOW)
    assert out["golden_available"] is True and [r["kind"] for r in out["runs"]] == ["live", "golden"]


async def test_load_turns_keeps_the_order_and_skips_missing_ids(monkeypatch):
    rows = [turn_row("turn-2", 1, conversation_key="s:a"), turn_row("turn-1", 0, conversation_key="s:a")]
    conn = FakeConn(rows, [])
    use(monkeypatch, conn)
    out = await cv.load_turns(["turn-1", "turn-x", "turn-2"])
    assert_bound(conn)
    assert [r["id"] for r in out] == ["turn-1", "turn-2"] and conn.calls[0][1]["ids"] == ["turn-1", "turn-x", "turn-2"]
    assert ":org" not in conn.calls[0][0]                    # unscoped: for jobs only
    use(monkeypatch, FakeConn([], []))
    assert await cv.load_turn("turn-x") is None
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_conversations.py`
Expected: the 9 new tests fail with `AttributeError: module 'src.trace_lab.conversations' has no attribute 'list_conversations'` (and the other loaders).

- [ ] **Step 3: Implement**

Append to the end of `src/trace_lab/conversations.py`:

```python
# ── shaping ──────────────────────────────────────────────────────────────────────────────────────

_CLOCK_SQL = "SELECT now() AS as_of, now() - interval '2 seconds' AS watermark"


def _golden_of(r: Mapping[str, Any]) -> dict[str, Any] | None:
    if not r.get("golden_run_id"):
        return None
    return {"run_id": str(r["golden_run_id"]), "case_key": r.get("golden_case_key"), "rep": r.get("golden_rep")}


def _conversation_row(r: Mapping[str, Any], extra: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    kind = r["session_kind"]
    golden = _golden_of(r)
    first_q = normalize.redact_text(r["first_question"] or "")
    flags: list[str] = []
    if r["legacy"]:
        flags.append("legacy_provenance")
    if kind == "inferred":
        flags.append("inferred_session")
    elif kind == "synthetic":
        flags.append("synthetic_session")
    if r["thread_deleted_at"] is not None:
        flags.append("thread_deleted")
    e = extra.get(r["group_key"], {"models": set(), "tool_count": 0})
    return {"key": r["group_key"], "session_id": r["session_id"], "session_kind": kind,
            "session_label": normalize.session_label(kind, r["session_id"], title=r["title"] or first_q, golden=golden),
            "title": r["title"], "run_id": golden["run_id"] if golden else "live", "golden": golden,
            "first_turn_at": _iso(r["first_turn_at"]), "last_turn_at": _iso(r["last_turn_at"]),
            "turns_in_window": int(r["turns_in_window"]), "matching_turns": int(r["matching_turns"]),
            "failed_turns": int(r["failed_turns"]), "first_question": first_q,
            "channels": sorted(r["channels"] or []), "channel_sources": sorted(r["channel_sources"] or []),
            "models": sorted(e["models"]), "tool_count": int(e["tool_count"]), "cost_usd": _num(r["cost_usd"]) or 0.0,
            "asker": {"email": r["asker_email"], "role": r["asker_role"]}, "flags": flags, "reviewed_count": None}


# ── conversations (§5.4, §5.5) ───────────────────────────────────────────────────────────────────

async def list_conversations(principal: Any, org: str, f: TurnFilter, *, cursor: str | None, limit: int) -> dict[str, Any]:
    pos = decode_cursor(cursor)
    limit = max(1, min(int(limit), 100))
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(since=f.since, until=f.until, limit_plus_one=limit + 1,
             ct=pos[0] if pos else None, ck=pos[1] if pos else None)
    hit = _hit_sql(f, p)
    matched = "max(CASE WHEN turn_id = :f_q_exact THEN turn_id END)" if f.q else "CAST(NULL AS text)"
    sql = f"""
    WITH base AS ({_base_sql(scope)}),
    scoped AS (SELECT b.*, ({hit}) AS hit FROM base b WHERE {_run_sql(f, p)} AND {_deleted_sql(f)}),
    g AS (
      SELECT group_key, max(session_kind) AS session_kind, max(session_id) AS session_id, max(thread_title) AS title,
             min(started_at) AS first_turn_at, max(started_at) AS last_turn_at, count(*) AS turns_in_window,
             count(*) FILTER (WHERE hit) AS matching_turns, count(*) FILTER (WHERE NOT ok) AS failed_turns,
             coalesce(sum(cost_usd), 0) AS cost_usd, array_agg(DISTINCT channel) AS channels,
             array_agg(DISTINCT channel_source) AS channel_sources,
             (array_agg(q300 ORDER BY started_at, turn_id))[1] AS first_question,
             (array_agg(email ORDER BY started_at, turn_id))[1] AS asker_email,
             (array_agg(asker_role ORDER BY started_at, turn_id))[1] AS asker_role,
             bool_or(legacy) AS legacy, max(thread_deleted_at) AS thread_deleted_at,
             max(CAST(golden_run_id AS text)) AS golden_run_id, max(golden_case_key) AS golden_case_key,
             max(rep) AS golden_rep, {matched} AS matched_turn_id
        FROM scoped GROUP BY group_key HAVING count(*) FILTER (WHERE hit) > 0
    ), g2 AS (SELECT g.*, count(*) OVER () AS total FROM g)
    SELECT * FROM g2
     WHERE CAST(:ct AS timestamptz) IS NULL
        OR (last_turn_at, group_key) < (CAST(:ct AS timestamptz), CAST(:ck AS text))
     ORDER BY last_turn_at DESC, group_key DESC
     LIMIT :limit_plus_one"""
    extra: dict[str, dict[str, Any]] = {}
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]
        page = rows[:limit]
        if page:
            # Phase 2: models and tool counts for this page's conversations only.
            scope2, p2 = db.scope_sql(principal, org, alias="tt")
            p2.update(since=f.since, until=f.until, keys=[r["group_key"] for r in page])
            sql2 = f"""
            WITH base AS ({_base_sql(scope2)})
            SELECT group_key, models, tool_calls FROM base
             WHERE {_run_sql(f, p2)} AND {_deleted_sql(f)} AND group_key = ANY(CAST(:keys AS text[]))"""
            for r in (await conn.execute(text(sql2), p2)).mappings().all():
                e = extra.setdefault(r["group_key"], {"models": set(), "tool_count": 0})
                e["models"].update(str(m) for m in _as_list(r["models"]))
                e["tool_count"] += int(r["tool_calls"] or 0)
    nxt = encode_cursor(page[-1]["last_turn_at"], page[-1]["group_key"]) if len(rows) > limit else None
    total = int(rows[0]["total"]) if rows else (0 if pos is None else None)
    return {"ok": True, "as_of": _iso(clock["as_of"]), "watermark": _iso(clock["watermark"]), "total": total,
            "next_cursor": nxt,
            "matched_turn_id": next((r["matched_turn_id"] for r in rows if r.get("matched_turn_id")), None),
            "conversations": [_conversation_row(r, extra) for r in page]}


async def conversation_updates(principal: Any, org: str, f: TurnFilter, *, watermark: datetime) -> dict[str, Any]:
    """New turns since a watermark. Turn rows land at flush, so created_at is the watermark; the started_at
    look-back keeps the query on ix_trace_turns_org_time (trace.py:107). Advisory: Refresh shows the truth."""
    wm = _aware(watermark)
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(since=wm - timedelta(minutes=UPDATE_LOOKBACK_MINUTES), until=f.until, wm=wm)
    hit = _hit_sql(f, p)
    sql = f"""
    WITH base AS ({_base_sql(scope)})
    SELECT count(*) AS new_turns, count(DISTINCT group_key) AS new_conversations, max(created_at) AS latest
      FROM base
     WHERE created_at > CAST(:wm AS timestamptz) AND {_run_sql(f, p)} AND {_deleted_sql(f)} AND ({hit})"""
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        r = (await conn.execute(text(sql), p)).mappings().one()
    return {"ok": True, "new_turns": int(r["new_turns"] or 0), "new_conversations": int(r["new_conversations"] or 0),
            "latest": _iso(r["latest"]), "watermark": _iso(clock["watermark"]), "as_of": _iso(clock["as_of"])}


async def conversation_facets(principal: Any, org: str, f: TurnFilter) -> dict[str, Any]:
    """Counts by channel, channel source, session kind and model over the window (run and deleted applied)."""
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(since=f.since, until=f.until)
    sql = f"""
    WITH base AS ({_base_sql(scope)}),
    scoped AS (SELECT * FROM base WHERE {_run_sql(f, p)} AND {_deleted_sql(f)})
    SELECT 'channel' AS facet, channel AS value, count(*) AS n FROM scoped GROUP BY channel
    UNION ALL SELECT 'channel_source', channel_source, count(*) FROM scoped GROUP BY channel_source
    UNION ALL SELECT 'session_kind', session_kind, count(DISTINCT group_key) FROM scoped GROUP BY session_kind
    UNION ALL SELECT 'model', m.model, count(*) FROM scoped
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  CASE WHEN jsonb_typeof(scoped.models) = 'array' THEN scoped.models ELSE CAST('[]' AS jsonb) END) AS m(model)
              GROUP BY m.model"""
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = (await conn.execute(text(sql), p)).mappings().all()
    names = {"channel": ("channels", "turns"), "channel_source": ("channel_sources", "turns"),
             "session_kind": ("session_kinds", "conversations"), "model": ("models", "turns")}
    out: dict[str, list[dict[str, Any]]] = {"channels": [], "channel_sources": [], "session_kinds": [], "models": []}
    for r in rows:
        bucket, unit = names[r["facet"]]
        out[bucket].append({"value": r["value"], unit: int(r["n"])})
    for bucket in out.values():
        bucket.sort(key=lambda x: (-int(x.get("turns", x.get("conversations", 0))), str(x["value"])))
    return {"ok": True, "as_of": _iso(clock["as_of"]), **out}


async def _activity_input(conn: Any, session_id: str, turn_id: str) -> dict[str, Any] | None:
    """The earliest orchestrator/turn/input activity row of a legacy turn, read on the (session_id, turn_id,
    created_at) index (activity_log.py:224). A hint only: the activity routes are unauthenticated
    (workflow.py:1128-1196), and the log may be off (ACTIVITY_LOG_ENABLED), so a failure reads as None."""
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text(f"""
                SELECT payload, created_at FROM {ACTIVITY}
                 WHERE session_id = :sid AND turn_id = :tid AND agent = 'orchestrator' AND stage = 'turn'
                   AND direction = 'input'
                 ORDER BY created_at ASC LIMIT 1"""), {"sid": session_id[:120], "tid": turn_id[:64]})).mappings().first()
    except Exception:  # noqa: BLE001 - optional evidence; the savepoint keeps the read transaction usable
        return None
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return {"payload": payload if isinstance(payload, Mapping) else {}, "created_at": _iso(row["created_at"])}


async def get_conversation(principal: Any, org: str, key: str, *, turn_id: str | None = None,
                           include_deleted: bool = False) -> dict[str, Any] | None:
    """One conversation: every turn header (at most 500) and the active turn normalized in full."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return None
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(key=key, limit=MAX_TURN_HEADERS + 1)
    if key[0] == "s":
        p["sid"] = key[2:]
        narrow = "tt.session_id = :sid"
    else:
        p["tid"] = key[2:]
        narrow = "tt.turn_id = :tid"
    sql = f"""
    SELECT tt.*, {provenance.inferred_channel_sql('tt')} AS inferred_channel,
           CASE WHEN tm.turn_id IS NULL THEN NULL ELSE to_jsonb(tm) END AS meta,
           th.title AS thread_title, th.deleted_at AS thread_deleted_at, (th.id IS NOT NULL) AS thread_exists
      FROM {TURNS} tt
      LEFT JOIN {TURN_META} tm ON tm.turn_id = tt.turn_id
      LEFT JOIN {THREADS} th ON th.id = tt.session_id
     WHERE {scope} AND {narrow} AND {GROUP_KEY_SQL} = :key
     ORDER BY tt.started_at, tt.turn_id
     LIMIT :limit"""
    async with _read() as conn:
        rows = [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]
        if not rows:
            return None
        deleted = rows[0]["thread_deleted_at"] is not None
        if deleted and not include_deleted:
            return None
        truncated = len(rows) > MAX_TURN_HEADERS
        rows = rows[:MAX_TURN_HEADERS]
        active = next((r for r in rows if r["turn_id"] == turn_id), None) if turn_id else rows[0]
        if active is None:
            return None
        spans = [dict(s) for s in (await conn.execute(
            text(f"SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": active["turn_id"]})).mappings().all()]
        metas = {r["turn_id"]: _meta(r.pop("meta")) for r in rows}
        activity = None
        if metas[active["turn_id"]] is None and active.get("session_id"):
            activity = await _activity_input(conn, str(active["session_id"]), str(active["turn_id"]))
    att = normalize.attempts(rows, metas, {active["turn_id"]: activity} if activity else None)
    first = rows[0]
    kind = normalize.session_kind(first.get("session_id"), thread_exists=bool(first.get("thread_exists")))
    golden_meta = next((m for m in metas.values() if m and m.get("golden_run_id")), None)
    golden = ({"run_id": str(golden_meta["golden_run_id"]), "case_key": golden_meta.get("golden_case_key"),
               "rep": golden_meta.get("rep")} if golden_meta else None)
    first_q, _ = normalize.repair_question(first, metas[first["turn_id"]], None)
    label = normalize.session_label(kind, first.get("session_id"),
                                    title=first.get("thread_title") or normalize.redact_text(first_q), golden=golden)
    headers = [normalize.turn_header(r, metas[r["turn_id"]], turn_index=i + 1, attempt=att[r["turn_id"]])
               for i, r in enumerate(rows)]
    index = next(i for i, r in enumerate(rows) if r is active)
    run_id = golden["run_id"] if golden else "live"
    record = normalize.normalize_turn(active, spans, meta=metas[active["turn_id"]], activity_input=activity,
                                      conversation={"key": key, "session_kind": kind, "session_label": label,
                                                    "turn_index": index + 1, "turn_count": len(rows),
                                                    **att[active["turn_id"]], "run_id": run_id,
                                                    "thread_deleted": deleted})
    flags: list[str] = []
    if any("legacy_provenance" in h["flags"] for h in headers):
        flags.append("legacy_provenance")
    if kind == "inferred":
        flags.append("inferred_session")
    elif kind == "synthetic":
        flags.append("synthetic_session")
    if deleted:
        flags.append("thread_deleted")
    return {"key": key, "session_id": first.get("session_id"), "session_kind": kind, "session_label": label,
            "title": first.get("thread_title"), "thread_deleted": deleted,
            "run": {"run_id": run_id, "kind": "golden" if golden else "live"}, "turn_count": len(rows),
            "turns_truncated": truncated, "turns": headers, "active": record, "flags": flags,
            "normalizer_version": normalize.NORMALIZER_VERSION}


async def get_turn(principal: Any, org: str, turn_id: str) -> dict[str, Any] | None:
    """One normalized turn and its conversation key. A deleted thread's turn is still readable by its id
    (the Overview links to every turn); the record carries the thread_deleted flag."""
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 80:
        return None
    scope, p = db.scope_sql(principal, org, alias="tt")
    p["tid"] = turn_id
    async with _read() as conn:
        row = (await conn.execute(text(f"SELECT {GROUP_KEY_SQL} AS group_key FROM {TURNS} tt "
                                       f"WHERE {scope} AND tt.turn_id = :tid"), p)).mappings().first()
    if row is None:
        return None
    conv = await get_conversation(principal, org, row["group_key"], turn_id=turn_id, include_deleted=True)
    if conv is None:
        return None
    return {"trace": conv["active"], "conversation_key": row["group_key"]}


# ── runs (§5.4) ──────────────────────────────────────────────────────────────────────────────────

async def list_runs(principal: Any, org: str, *, since: datetime, until: datetime) -> dict[str, Any]:
    """'All live traffic' over the window, plus golden runs once M3's trace_lab.golden exists."""
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(since=since, until=until)
    sql = f"""
    WITH base AS ({_base_sql(scope)}), live AS (SELECT * FROM base WHERE channel <> 'golden')
    SELECT (SELECT min(started_at) FROM live) AS started_at, (SELECT max(started_at) FROM live) AS ended_at,
           (SELECT count(DISTINCT group_key) FROM live) AS conversation_count,
           (SELECT count(*) FROM live) AS turn_count, (SELECT coalesce(sum(cost_usd), 0) FROM live) AS cost_usd,
           (SELECT array_agg(DISTINCT m.model) FROM live
                   CROSS JOIN LATERAL jsonb_array_elements_text(
                       CASE WHEN jsonb_typeof(live.models) = 'array' THEN live.models ELSE CAST('[]' AS jsonb) END) AS m(model)
           ) AS models"""
    async with _read() as conn:
        r = (await conn.execute(text(sql), p)).mappings().one()
    runs: list[dict[str, Any]] = [{
        "run_id": "live", "kind": "live", "name": "All live traffic", "recorded": False, "status": "live",
        "started_at": _iso(r["started_at"]), "ended_at": _iso(r["ended_at"]),
        "conversation_count": int(r["conversation_count"] or 0), "turn_count": int(r["turn_count"] or 0),
        "models": sorted(str(m) for m in (r["models"] or [])), "cost_usd": _num(r["cost_usd"]) or 0.0}]
    golden_available = False
    if importlib.util.find_spec(f"{__package__}.golden") is not None:
        from . import golden  # M3: golden.run_summaries(principal, org, since, until) -> list of run rows (§5.4)

        golden_available = True
        runs.extend(await golden.run_summaries(principal, org, since, until))
    return {"ok": True, "golden_available": golden_available, "runs": runs}


# ── model calls (§5.4, §5.5) ─────────────────────────────────────────────────────────────────────

def _retention_days() -> int:
    try:
        return int(os.environ.get("TRACE_RETENTION_DAYS") or trace.DEFAULT_RETENTION_DAYS)
    except ValueError:
        return trace.DEFAULT_RETENTION_DAYS


def _calls_from(scope: str) -> str:
    return f"""
      FROM {SPANS} s
      JOIN {TURNS} tt ON tt.turn_id = s.turn_id
      LEFT JOIN {TURN_META} tm ON tm.turn_id = tt.turn_id
      LEFT JOIN {SPANS} ps ON ps.id = s.parent_id
     WHERE s.kind IN ('llm', 'router') AND {scope} AND tt.started_at >= :since AND tt.started_at < :until"""


_CALL_COLUMNS = f"""
       s.id AS span_id, s.turn_id, s.kind, s.name, s.agent, s.model, s.started_at, s.latency_ms,
       s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cost_usd, s.ok, left(s.error, 600) AS error,
       CASE WHEN jsonb_typeof(s.input) = 'object' AND (s.input ? 'system_prompt' OR s.input ? 'user_message') THEN 'exchange'
            WHEN jsonb_typeof(s.input) = 'object' AND s.input ? 'last_human_message' THEN 'loop_summary'
            WHEN s.input IS NULL THEN 'none' ELSE 'other' END AS request_kind,
       left(CASE WHEN jsonb_typeof(s.output) = 'object'
                 THEN coalesce(s.output->>'content', s.output->>'model_output', CAST(s.output AS text))
                 ELSE s.output #>> '{{}}' END, 2000) AS response_preview,
       coalesce(jsonb_typeof(s.output) = 'object' AND s.output ? 'tool_calls' AND s.output ? 'content', false) AS loop_output,
       CASE WHEN jsonb_typeof(s.output) = 'object' AND jsonb_typeof(s.output->'tool_calls') = 'array'
            THEN jsonb_array_length(s.output->'tool_calls') ELSE 0 END AS tool_call_requests,
       coalesce(ps.kind = 'gate', false) AS under_gate,
       left({_ASKER_WORDS_SQL}, 600) AS question_excerpt,
       {GROUP_KEY_SQL} AS conversation_key"""


def _call_row(r: Mapping[str, Any]) -> dict[str, Any]:
    withheld = bool(r["under_gate"])
    preview = r["response_preview"]
    return {"span_id": str(r["span_id"]), "turn_id": r["turn_id"], "conversation_key": r["conversation_key"],
            "started_at": _iso(r["started_at"]), "kind": r["kind"], "name": r["name"], "agent": r["agent"],
            "label": normalize.call_label(r["kind"], r["name"], r["request_kind"], bool(r["loop_output"]),
                                          int(r["tool_call_requests"] or 0)),
            "model": r["model"], "latency_ms": r["latency_ms"], "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"], "cache_read_tokens": r["cache_read_tokens"],
            "cost_usd": _num(r["cost_usd"]), "ok": bool(r["ok"]),
            "error": normalize.redact_text(r["error"])[:300] if r["error"] else None,
            "request_kind": r["request_kind"],
            "response_preview": None if withheld or preview is None else normalize.redact_text(preview)[:200],
            "withheld": withheld,
            "question_excerpt": normalize.redact_text(r["question_excerpt"] or "")[:160]}


async def list_model_calls(principal: Any, org: str, f: ModelCallFilter, *, cursor: str | None, limit: int) -> dict[str, Any]:
    """llm and router spans only: agent group spans hold a whole sub-agent run's cost (trace.py:416-428) and
    would double-count. Spans older than the retention window are gone (trace.py:133-146)."""
    pos = decode_cursor(cursor)
    if pos is not None and not _UUID_RE.match(pos[1]):
        raise BadRequest("bad_cursor", "That page link is no longer valid. Reload the list.")
    limit = max(1, min(int(limit), 200))
    scope, p = db.scope_sql(principal, org, alias="tt")
    p.update(since=f.since, until=f.until)
    channel = _channel_sql()
    where = (f"{_run_sql(f, p, channel=channel, golden='tm.golden_run_id')} AND "
             f"{_call_filters_sql(f, p, channel=channel, source=_SOURCE_SQL)}")
    page_sql = f"""SELECT {_CALL_COLUMNS} {_calls_from(scope)} AND {where}
       AND (CAST(:ct AS timestamptz) IS NULL OR (s.started_at, s.id) < (CAST(:ct AS timestamptz), CAST(:ck AS uuid)))
     ORDER BY s.started_at DESC, s.id DESC
     LIMIT :limit_plus_one"""
    agg_sql = f"""SELECT count(*) AS calls, count(*) FILTER (WHERE NOT s.ok) AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p95_ms,
           coalesce(sum(s.input_tokens), 0) AS input_tokens, coalesce(sum(s.output_tokens), 0) AS output_tokens,
           coalesce(sum(s.cache_read_tokens), 0) AS cache_read_tokens, coalesce(sum(s.cost_usd), 0) AS cost_usd,
           count(*) FILTER (WHERE s.cost_usd IS NULL) AS unpriced_calls {_calls_from(scope)} AND {where}"""
    by_model_sql = f"""SELECT s.model, count(*) AS calls, count(*) FILTER (WHERE NOT s.ok) AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p50_ms,
           coalesce(sum(s.cost_usd), 0) AS cost_usd {_calls_from(scope)} AND {where}
     GROUP BY s.model ORDER BY count(*) DESC, s.model"""
    page_params = dict(p, ct=pos[0] if pos else None, ck=pos[1] if pos else None, limit_plus_one=limit + 1)
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = [dict(r) for r in (await conn.execute(text(page_sql), page_params)).mappings().all()]
        agg = dict((await conn.execute(text(agg_sql), p)).mappings().one())
        by_model = [dict(r) for r in (await conn.execute(text(by_model_sql), p)).mappings().all()]
    days = _retention_days()
    page = rows[:limit]
    nxt = (encode_cursor(page[-1]["started_at"], str(page[-1]["span_id"]))
           if len(rows) > limit and page[-1]["started_at"] is not None else None)
    tin, tout, tcache = (int(agg[k] or 0) for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    return {"ok": True, "as_of": _iso(clock["as_of"]), "next_cursor": nxt, "retention_days": days,
            "spans_available_since": _iso(clock["as_of"] - timedelta(days=days)),
            "aggregate": {"calls": int(agg["calls"] or 0), "errors": int(agg["errors"] or 0),
                          "latency_p50_ms": _num(agg["latency_p50_ms"]), "latency_p95_ms": _num(agg["latency_p95_ms"]),
                          "input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                          "total_tokens": tin + tout + tcache, "cost_usd": _num(agg["cost_usd"]) or 0.0,
                          "unpriced_calls": int(agg["unpriced_calls"] or 0),
                          "by_model": [{"model": m["model"], "calls": int(m["calls"]), "errors": int(m["errors"]),
                                        "latency_p50_ms": _num(m["latency_p50_ms"]), "cost_usd": _num(m["cost_usd"]) or 0.0}
                                       for m in by_model]},
            "calls": [_call_row(r) for r in page]}


async def get_model_call(principal: Any, org: str, span_id: str) -> dict[str, Any] | None:
    """One model-call card, read from its turn normalized (so the group, the prompt note and a gate's
    withholding are exactly what the Conversations view shows)."""
    if not isinstance(span_id, str) or not _UUID_RE.match(span_id):
        return None
    scope, p = db.scope_sql(principal, org, alias="tt")
    p["sid"] = span_id
    async with _read() as conn:
        head = (await conn.execute(text(f"""
            SELECT tt.*, CASE WHEN tm.turn_id IS NULL THEN NULL ELSE to_jsonb(tm) END AS meta,
                   {GROUP_KEY_SQL} AS conversation_key
              FROM {SPANS} s
              JOIN {TURNS} tt ON tt.turn_id = s.turn_id
              LEFT JOIN {TURN_META} tm ON tm.turn_id = tt.turn_id
             WHERE s.id = CAST(:sid AS uuid) AND s.kind IN ('llm', 'router') AND {scope}"""), p)).mappings().first()
        if head is None:
            return None
        turn = dict(head)
        spans = [dict(s) for s in (await conn.execute(
            text(f"SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": turn["turn_id"]})).mappings().all()]
    meta = _meta(turn.pop("meta"))
    record = normalize.normalize_turn(turn, spans, meta=meta, conversation={"key": turn["conversation_key"]})
    want = span_id.lower()
    card = next((m for m in record["model_calls"] if m["span_id"] == want), None)
    if card is None:                         # the span sits in a discarded duplicate tree
        span = next((s for s in spans if str(s.get("id")).lower() == want), None)
        if span is None:
            return None
        parent = next((s for s in spans if s.get("id") == span.get("parent_id")), None)
        card = normalize.model_call_record(span, turn_id=str(turn["turn_id"]), group=None, prompt_ref=None,
                                           withheld=bool(parent and parent.get("kind") == "gate"))
    return {"model_call": card, "turn_id": turn["turn_id"], "conversation_key": turn["conversation_key"]}


# ── unscoped loaders, for Trace Lab jobs (M4-M6) ─────────────────────────────────────────────────

async def load_turns(turn_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Normalized records for these turn ids, in the order given; missing ids are skipped. Unscoped: the
    job that calls this is already scoped to its company. Callers chunk large lists."""
    ids = [str(t) for t in turn_ids if t]
    if not ids:
        return []
    async with _read() as conn:
        rows = [dict(r) for r in (await conn.execute(text(f"""
            SELECT tt.*, {provenance.inferred_channel_sql('tt')} AS inferred_channel,
                   CASE WHEN tm.turn_id IS NULL THEN NULL ELSE to_jsonb(tm) END AS meta,
                   (th.id IS NOT NULL) AS thread_exists, th.deleted_at AS thread_deleted_at,
                   {GROUP_KEY_SQL} AS conversation_key
              FROM {TURNS} tt
              LEFT JOIN {TURN_META} tm ON tm.turn_id = tt.turn_id
              LEFT JOIN {THREADS} th ON th.id = tt.session_id
             WHERE tt.turn_id = ANY(CAST(:ids AS text[]))"""), {"ids": ids})).mappings().all()]
        spans = [dict(s) for s in (await conn.execute(text(
            f"SELECT * FROM {SPANS} WHERE turn_id = ANY(CAST(:ids AS text[])) ORDER BY turn_id, seq"),
            {"ids": ids})).mappings().all()]
    by_turn: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        by_turn.setdefault(str(s["turn_id"]), []).append(s)
    found = {str(r["turn_id"]): r for r in rows}
    out: list[dict[str, Any]] = []
    for tid in ids:
        r = found.get(tid)
        if r is None:
            continue
        meta = _meta(r.pop("meta"))
        out.append(normalize.normalize_turn(r, by_turn.get(tid, []), meta=meta, conversation={
            "key": r["conversation_key"],
            "session_kind": normalize.session_kind(r.get("session_id"), thread_exists=bool(r.get("thread_exists"))),
            "thread_deleted": r.get("thread_deleted_at") is not None}))
    return out


async def load_turn(turn_id: str) -> dict[str, Any] | None:
    out = await load_turns([turn_id])
    return out[0] if out else None
```

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_conversations.py`
Expected: `23 passed`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 9: The routes and their registration

**Files:**
- Create: `apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/trace_lab_conversations.py`
- Modify: `src/api/main.py:40` (import), `src/api/main.py:255` (include)
- Test: `tests/test_trace_lab_conversations.py` (append)

**Interfaces:**
- Consumes: Task 7–8 loaders and `BadRequest`; M0 `db.require_reviewer`, `db.require_ready`, `db.require_trace_store()` (503 `trace_store_unavailable`, `retryable: true`, while `trace.ready()` is False); `current_principal` (principal.py:130).
- Produces: `router` (8 GET routes under `/api/trace-lab`, declared updates/facets before `{key:path}`), `reviewer` dependency, `Reviewer(principal, org)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trace_lab_conversations.py`:

```python
PATHS = ["/api/trace-lab/conversations", "/api/trace-lab/conversations/updates?watermark=2026-10-15T12:00:00Z",
         "/api/trace-lab/conversations/facets", "/api/trace-lab/conversations/s%3Aabc", "/api/trace-lab/turns/turn-1",
         "/api/trace-lab/runs", "/api/trace-lab/model-calls", "/api/trace-lab/model-calls/" + str(uuid.uuid4())]


def principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email=f"{role}@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def client(monkeypatch, who, *, ready=True, store=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import trace_lab_conversations as routes
    from src.services.principal import current_principal

    async def fake_ready():
        if not ready:
            raise HTTPException(status_code=503, detail={"ok": False, "available": False,
                                                         "error": "Trace Lab is not available right now. The chat is not affected."})

    monkeypatch.setattr(db, "require_ready", fake_ready)
    monkeypatch.setattr(trace, "_ready", store)
    app = FastAPI()
    app.include_router(routes.router)
    if who is not None:
        app.dependency_overrides[current_principal] = lambda: who
    return TestClient(app)


def fake_loaders(monkeypatch):
    seen = {}

    async def ok(name, *a, **kw):
        seen[name] = (a, kw)
        return {"ok": True, "name": name}

    for name in ("list_conversations", "conversation_updates", "conversation_facets", "list_runs", "list_model_calls"):
        monkeypatch.setattr(cv, name, lambda *a, _n=name, **kw: ok(_n, *a, **kw))

    async def conv(*a, **kw):
        seen["get_conversation"] = (a, kw)
        return {"key": a[2]}

    async def turn(*a, **kw):
        return {"trace": {"id": a[2]}, "conversation_key": "s:abc"}

    async def call(*a, **kw):
        return {"model_call": {"span_id": a[2]}, "turn_id": "turn-1", "conversation_key": "s:abc"}

    monkeypatch.setattr(cv, "get_conversation", conv)
    monkeypatch.setattr(cv, "get_turn", turn)
    monkeypatch.setattr(cv, "get_model_call", call)
    return seen


def test_every_route_is_a_get_behind_the_reviewer_check():
    from src.api.routes import trace_lab_conversations as routes
    paths = set()
    for r in routes.router.routes:
        assert r.methods == {"GET"}, r.path
        assert routes.reviewer in [d.call for d in r.dependant.dependencies], r.path
        paths.add(r.path)
    assert paths == {"/api/trace-lab/conversations", "/api/trace-lab/conversations/updates",
                     "/api/trace-lab/conversations/facets", "/api/trace-lab/conversations/{key:path}",
                     "/api/trace-lab/turns/{turn_id}", "/api/trace-lab/runs", "/api/trace-lab/model-calls",
                     "/api/trace-lab/model-calls/{span_id}"}


def test_routes_need_a_signed_in_caller(monkeypatch):
    c = client(monkeypatch, None)
    assert all(c.get(p).status_code == 401 for p in PATHS)


def test_plain_users_get_403(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(monkeypatch, principal("user"))
    for p in PATHS:
        r = c.get(p)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only", p


def test_naming_another_company_is_403_unless_superadmin(monkeypatch):
    seen = fake_loaders(monkeypatch)
    other = str(uuid.uuid4())
    assert client(monkeypatch, principal("admin")).get(f"/api/trace-lab/conversations?organization_id={other}").status_code == 403
    r = client(monkeypatch, principal("superadmin")).get(f"/api/trace-lab/conversations?organization_id={other}")
    assert r.status_code == 200 and seen["list_conversations"][0][1] == other


def test_a_platform_superadmin_must_name_a_company(monkeypatch):
    fake_loaders(monkeypatch)
    r = client(monkeypatch, principal("superadmin", org=None)).get("/api/trace-lab/conversations")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"


def test_routes_answer_503_when_the_trace_lab_store_is_down(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(monkeypatch, principal("admin"), ready=False)
    for p in PATHS:
        r = c.get(p)
        assert r.status_code == 503 and r.json()["detail"]["ok"] is False, p


def test_routes_answer_503_when_the_trace_store_is_down(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(monkeypatch, principal("admin"), store=False)
    for p in PATHS:
        r = c.get(p)
        assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_store_unavailable", p
        assert r.json()["detail"]["retryable"] is True, p


def test_query_errors_are_400_with_a_reason(monkeypatch):
    c = client(monkeypatch, principal("admin"))
    assert c.get("/api/trace-lab/conversations?reviewed=mine").json()["detail"]["reason"] == "reviewed_filter_unavailable"
    assert c.get("/api/trace-lab/conversations?since=2024-01-01T00:00:00Z").json()["detail"]["reason"] == "window_too_wide"
    r = c.get("/api/trace-lab/conversations?cursor=not-a-cursor")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_cursor"
    assert c.get("/api/trace-lab/model-calls?channel=email").json()["detail"]["reason"] == "bad_channel"
    assert c.get("/api/trace-lab/conversations?status=maybe").status_code == 422


def test_the_list_route_hands_the_parsed_filter_and_company_to_the_loader(monkeypatch):
    seen = fake_loaders(monkeypatch)
    who = principal("admin")
    r = client(monkeypatch, who).get("/api/trace-lab/conversations?channel=chat,upload&q=boilers&limit=25&cursor=abc")
    assert r.status_code == 200
    (p, org, f), kw = seen["list_conversations"]
    assert p is who and org == ORG and f.channels == ("chat", "upload") and f.q == "boilers"
    assert kw == {"cursor": "abc", "limit": 25}
    r = client(monkeypatch, who).get("/api/trace-lab/model-calls?kind=router&agent=agent_router&limit=200")
    (_, _, mf), kw = seen["list_model_calls"]
    assert r.status_code == 200 and isinstance(mf, cv.ModelCallFilter) and mf.kind == "router" and kw["limit"] == 200


def test_updates_and_facets_are_not_read_as_a_conversation_key(monkeypatch):
    seen = fake_loaders(monkeypatch)
    c = client(monkeypatch, principal("admin"))
    assert c.get("/api/trace-lab/conversations/updates?watermark=2026-10-15T12:00:00Z").json()["name"] == "conversation_updates"
    assert c.get("/api/trace-lab/conversations/facets").json()["name"] == "conversation_facets"
    assert c.get("/api/trace-lab/conversations/updates").status_code == 422      # the watermark is required
    assert c.get("/api/trace-lab/conversations/s%3Aa%2Fb?turn=turn-2").json()["conversation"] == {"key": "s:a/b"}
    assert seen["get_conversation"][1] == {"turn_id": "turn-2", "include_deleted": False}


def test_unknown_conversation_turn_and_call_are_404(monkeypatch):
    async def none(*a, **kw):
        return None

    for name in ("get_conversation", "get_turn", "get_model_call"):
        monkeypatch.setattr(cv, name, none)
    c = client(monkeypatch, principal("admin"))
    assert c.get("/api/trace-lab/conversations/s%3Anope").json()["detail"]["reason"] == "conversation_not_found"
    assert c.get("/api/trace-lab/turns/turn-nope").json()["detail"]["reason"] == "turn_not_found"
    assert c.get("/api/trace-lab/model-calls/" + str(uuid.uuid4())).json()["detail"]["reason"] == "model_call_not_found"
```

- [ ] **Step 2: Run it and see it fail**

Run: `tl_da_test tests/test_trace_lab_conversations.py`
Expected: the 11 new tests fail with `ModuleNotFoundError: No module named 'src.api.routes.trace_lab_conversations'`.

- [ ] **Step 3: Implement**

Create `src/api/routes/trace_lab_conversations.py`:

```python
"""Trace Lab: conversations, runs and model calls, read only (spec docs/superpowers/specs/2026-10-15-hoist-trace-
lab-design.md, "Trace capture, normalizer and conversations" §5).

    GET /api/trace-lab/conversations              filtered, cursor-paged conversation list with a total
    GET /api/trace-lab/conversations/updates      new turns since a watermark (the 45 s check)
    GET /api/trace-lab/conversations/facets       counts by channel, model, session kind, channel source
    GET /api/trace-lab/conversations/{key}        one conversation: every turn header + the active turn
    GET /api/trace-lab/turns/{turn_id}            one normalized turn + its conversation key
    GET /api/trace-lab/runs                       "All live traffic" + golden runs (M3)
    GET /api/trace-lab/model-calls                model-call rows + the aggregate for the same filter
    GET /api/trace-lab/model-calls/{span_id}      one model-call card

Every route: a signed-in reviewer of the company (db.require_reviewer: an admin, or a superadmin acting for
the company, D7), Trace Lab verified (db.require_ready, else 503) and the trace store up (else 503). Errors
use the house envelope {ok: false, error, reason}. Nothing here writes or spends model money.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...services.principal import Principal, current_principal
from ...trace_lab import conversations, db

router = APIRouter(prefix="/api/trace-lab", tags=["Trace Lab"])


@dataclass(frozen=True)
class Reviewer:
    principal: Principal
    org: str


async def reviewer(organization_id: str | None = Query(None, max_length=64),
                   principal: Principal = Depends(current_principal)) -> Reviewer:
    org = db.require_reviewer(principal, organization_id)
    await db.require_ready()
    db.require_trace_store()
    return Reviewer(principal, org)


def _bad(exc: conversations.BadRequest) -> HTTPException:
    return HTTPException(status_code=400, detail={"ok": False, "error": exc.error, "reason": exc.reason})


def _missing(error: str, reason: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"ok": False, "error": error, "reason": reason})


@router.get("/conversations")
async def list_conversations(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), channel: str | None = Query(None, max_length=200),
        channel_source: str | None = Query(None, max_length=60), session_kind: str | None = Query(None, max_length=60),
        status: str | None = Query(None, pattern="^(ok|error)$"), model: str | None = Query(None, max_length=120),
        q: str | None = Query(None, max_length=200), user: str | None = Query(None, max_length=200),
        asker_role: str | None = Query(None, max_length=60), reviewed: str = Query("any", max_length=20),
        deleted: str = Query("hide", pattern="^(hide|include)$"), cursor: str | None = Query(None, max_length=400),
        limit: int = Query(50, ge=1, le=100), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, channel=channel, channel_source=channel_source,
                                      session_kind=session_kind, status=status, model=model, q=q, user=user,
                                      asker_role=asker_role, reviewed=reviewed, deleted=deleted)
        return await conversations.list_conversations(who.principal, who.org, f, cursor=cursor, limit=limit)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/updates")
async def conversation_updates(
        watermark: datetime = Query(...), until: datetime | None = Query(None), run: str = Query("live", max_length=40),
        channel: str | None = Query(None, max_length=200), status: str | None = Query(None, pattern="^(ok|error)$"),
        model: str | None = Query(None, max_length=120), q: str | None = Query(None, max_length=200),
        user: str | None = Query(None, max_length=200), asker_role: str | None = Query(None, max_length=60),
        deleted: str = Query("hide", pattern="^(hide|include)$"), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(until=until, run=run, channel=channel, status=status, model=model, q=q,
                                      user=user, asker_role=asker_role, deleted=deleted)
        return await conversations.conversation_updates(who.principal, who.org, f, watermark=watermark)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/facets")
async def conversation_facets(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), deleted: str = Query("hide", pattern="^(hide|include)$"),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, deleted=deleted)
        return await conversations.conversation_facets(who.principal, who.org, f)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/{key:path}")
async def get_conversation(key: str, turn: str | None = Query(None, max_length=80),
                           deleted: str = Query("hide", pattern="^(hide|include)$"),
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    conv = await conversations.get_conversation(who.principal, who.org, key, turn_id=turn,
                                                include_deleted=deleted == "include")
    if conv is None:
        raise _missing("That conversation is not in this company's records.", "conversation_not_found")
    return {"ok": True, "conversation": conv}


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await conversations.get_turn(who.principal, who.org, turn_id)
    if out is None:
        raise _missing("That answer is not in this company's records.", "turn_not_found")
    return {"ok": True, **out}


@router.get("/runs")
async def list_runs(since: datetime | None = Query(None), until: datetime | None = Query(None),
                    who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until)
        return await conversations.list_runs(who.principal, who.org, since=f.since, until=f.until)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/model-calls")
async def list_model_calls(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), channel: str | None = Query(None, max_length=200),
        channel_source: str | None = Query(None, max_length=60), status: str | None = Query(None, pattern="^(ok|error)$"),
        model: str | None = Query(None, max_length=120), agent: str | None = Query(None, max_length=60),
        kind: str | None = Query(None, pattern="^(llm|router)$"), q: str | None = Query(None, max_length=200),
        cursor: str | None = Query(None, max_length=400), limit: int = Query(100, ge=1, le=200),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, channel=channel, channel_source=channel_source,
                                      status=status, model=model, q=q, agent=agent, kind=kind, calls=True)
        return await conversations.list_model_calls(who.principal, who.org, f, cursor=cursor, limit=limit)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/model-calls/{span_id}")
async def get_model_call(span_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await conversations.get_model_call(who.principal, who.org, span_id)
    if out is None:
        raise _missing("That model call is not in this company's records.", "model_call_not_found")
    return {"ok": True, **out}
```

In `src/api/main.py`:

old:
```python
from .routes.traces import router as traces_router
```
new:
```python
from .routes.traces import router as traces_router
from .routes.trace_lab_conversations import router as trace_lab_conversations_router
```

old:
```python
app.include_router(traces_router)
```
new:
```python
app.include_router(traces_router)
app.include_router(trace_lab_conversations_router)
```

(If M0 already added its own Trace Lab router lines next to these, the anchors above are still unique; keep M0's lines.)

- [ ] **Step 4: Run it and see it pass**

Run: `tl_da_test tests/test_trace_lab_conversations.py tests/test_trace.py tests/test_rest_turns_are_traced.py`
Expected: the 34 Trace Lab conversation tests pass and the existing trace tests still pass (no failures).

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 10: Scratch-DB SQL tests (the acceptance conversation and the paging contract)

**Files:**
- Test: `apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_trace_lab_conversations_sql.py` (create)

**Interfaces:**
- Consumes: M0 fixture `scratch_db` (tests/trace_lab_scratch.py); Task 7–8 loaders; `chat_threads.ensure_tables` (chat_threads.py:96); `activity_log.ensure_table` (activity_log.py:230).
- Produces: nothing new — proves the SQL against a real Postgres.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_lab_conversations_sql.py`:

```python
"""Trace Lab conversations against a scratch Postgres (TRACE_LAB_TEST_DB_URL): the M1 acceptance conversation,
grouping and labels, paging, totals, the update watermark, the channel filter, model calls, company scope,
deleted threads and SQL/Python parity. Rows are written only to the throwaway database M0's fixture guards."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.agents import trace
from src.services import chat_threads
from src.services.principal import Principal
from src.trace_lab import conversations as cv
from src.trace_lab import normalize as n
from trace_lab_scratch import scratch_db  # noqa: F401  (M0 fixture, spec §"Data model" 1.8)

pytestmark = pytest.mark.skipif(not os.environ.get("TRACE_LAB_TEST_DB_URL"), reason="needs a scratch Postgres")

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
ADMIN = Principal(user_id=uuid.UUID(USER), email="ada@example.com", organization_id=uuid.UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
async def seeded(scratch_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(chat_threads, "_ready", False)
    assert await chat_threads.ensure_tables()
    async with scratch_db.begin() as conn:
        await conn.execute(text("TRUNCATE plenum_cafm.chat_turns, plenum_cafm.chat_threads"))
        await conn.execute(text("TRUNCATE plenum_tracelab.turn_meta"))
        await conn.execute(text(f"TRUNCATE {trace.TURNS}, {trace.SPANS}"))
    return scratch_db


async def add_turn(conn, turn_id, *, session_id, base, minutes_ago=10, created_minutes_ago=None, org=ORG,
                   question="How many boilers?", answer="Four.", ok=True, models=("claude-sonnet-5",), tools=(),
                   cost=0.01, llm_calls=1, tool_calls=0):
    started = base - timedelta(minutes=minutes_ago)
    created = base - timedelta(minutes=created_minutes_ago) if created_minutes_ago is not None else started + timedelta(seconds=5)
    await conn.execute(text(f"""
        INSERT INTO {trace.TURNS} (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at,
                                   ended_at, latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens,
                                   output_tokens, cache_read_tokens, models, tools, ok, error, created_at)
        VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), 'fm@example.com', :q, :a, NULL, :t0, :t1, 1000, :llm,
                :tn, :usd, true, 100, 20, 0, CAST(:models AS jsonb), CAST(:tools AS jsonb), :ok, NULL, :created)"""),
        {"tid": turn_id, "sid": session_id, "org": org, "uid": USER, "q": question, "a": answer, "t0": started,
         "t1": started + timedelta(seconds=1), "llm": llm_calls, "tn": tool_calls, "usd": cost,
         "models": json.dumps(list(models)), "tools": json.dumps(list(tools)), "ok": ok, "created": created})


async def add_span(conn, turn_id, span_id, parent, seq, kind, name, *, base, agent="orchestrator", model=None, inp=None,
                   out=None, start=0.0, cost=None, tin=None, tout=None, ok=True, org=ORG):
    t0 = base - timedelta(minutes=10) + timedelta(seconds=start)
    await conn.execute(text(f"""
        INSERT INTO {trace.SPANS} (id, turn_id, session_id, organization_id, parent_id, seq, kind, name, agent, model,
                                   started_at, ended_at, latency_ms, input, output, input_tokens, output_tokens,
                                   cache_read_tokens, cost_usd, ok, error)
        VALUES (CAST(:id AS uuid), :tid, NULL, CAST(:org AS uuid), CAST(:parent AS uuid), :seq, :kind, :name, :agent,
                :model, :t0, :t1, 500, CAST(:inp AS jsonb), CAST(:out AS jsonb), :tin, :tout, 0, :usd, :ok, NULL)"""),
        {"id": span_id, "tid": turn_id, "org": org, "parent": parent, "seq": seq, "kind": kind, "name": name,
         "agent": agent, "model": model, "t0": t0, "t1": t0 + timedelta(seconds=0.5),
         "inp": json.dumps(inp) if inp is not None else None, "out": json.dumps(out) if out is not None else None,
         "tin": tin, "tout": tout, "usd": cost, "ok": ok})


async def add_meta(conn, turn_id, channel, *, source="client", origin=None, golden_run_id=None, case_key=None, rep=None,
                   org=ORG):
    await conn.execute(text("""
        INSERT INTO plenum_tracelab.turn_meta (turn_id, organization_id, channel, channel_source, classifier_version,
                                               origin, golden_run_id, golden_case_key, rep)
        VALUES (:tid, CAST(:org AS uuid), :ch, :src, 'prov-1', :origin, CAST(:run AS uuid), :case_key, :rep)"""),
        {"tid": turn_id, "org": org, "ch": channel, "src": source, "origin": origin, "run": golden_run_id,
         "case_key": case_key, "rep": rep})


async def add_thread(conn, session_id, title, *, deleted=False, org=ORG):
    await conn.execute(text("""
        INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title, deleted_at)
        VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), :title, CASE WHEN :deleted THEN now() ELSE NULL END)"""),
        {"id": session_id, "org": org, "uid": USER, "title": title, "deleted": deleted})


async def keys_of(**kw):
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(**kw), cursor=None, limit=50)
    return [c["key"] for c in out["conversations"]]


async def test_a_real_multi_turn_chat_reads_as_one_conversation(seeded):
    B = _now()
    s = {k: str(uuid.uuid4()) for k in ("root", "prompt", "loop", "m1", "tool", "m2")}
    loop1 = {"messages": 3, "last_human_message": "How many…", "tool_results_since_last_human": 0}
    loop2 = {"messages": 5, "last_human_message": "How many…", "tool_results_since_last_human": 1}
    async with seeded.begin() as conn:
        await add_thread(conn, "chat-1", "Boiler certificates")
        await add_turn(conn, "turn-a1", session_id="chat-1", base=B, minutes_ago=20, question="Which boilers are at B-301?")
        await add_turn(conn, "turn-a2", session_id="chat-1", base=B, minutes_ago=10,
                       question="How many work orders there in August?", answer="758 work orders.",
                       tools=("answer_from_records",), tool_calls=1, llm_calls=2)
        await add_meta(conn, "turn-a2", "chat", origin="chat.typed")
        await add_span(conn, "turn-a2", s["root"], None, 0, "turn", "turn", base=B, inp={"message": "How many…"},
                       out={"answer": "758 work orders."})
        await add_span(conn, "turn-a2", s["prompt"], s["root"], 1, "stage", "prompt assembled", base=B, start=0.1,
                       inp={"user_message": "How many…", "system_prompt": "S"}, out={"system_prompt_chars": 1})
        await add_span(conn, "turn-a2", s["loop"], s["root"], 2, "agent", "orchestrator loop", base=B, start=0.2,
                       agent="orchestrator loop")
        await add_span(conn, "turn-a2", s["m1"], s["loop"], 3, "llm", "model", base=B, start=0.2, agent="orchestrator loop",
                       model="claude-sonnet-5", inp=loop1,
                       out={"content": "", "tool_calls": [{"name": "answer_from_records", "args": {}}]},
                       tin=900, tout=40, cost=0.004)
        await add_span(conn, "turn-a2", s["tool"], s["root"], 4, "tool", "answer_from_records", base=B, start=2,
                       agent="tool:wo_engine", inp={"q": "wo"}, out={"total": 758})
        await add_span(conn, "turn-a2", s["m2"], s["loop"], 5, "llm", "model", base=B, start=5, agent="orchestrator loop",
                       model="claude-sonnet-5", inp=loop2, out={"content": "758 work orders.", "tool_calls": []},
                       tin=1100, tout=60, cost=0.005)
    listed = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=50)
    assert listed["total"] == 1
    row = listed["conversations"][0]
    assert (row["key"], row["session_kind"], row["session_label"], row["turns_in_window"]) == \
        ("s:chat-1", "observed", "Boiler certificates", 2)
    assert row["first_question"] == "Which boilers are at B-301?" and row["flags"] == ["legacy_provenance"]
    assert row["models"] == ["claude-sonnet-5"] and row["tool_count"] == 1
    conv = await cv.get_conversation(ADMIN, ORG, "s:chat-1", turn_id="turn-a2")
    assert [h["turn_id"] for h in conv["turns"]] == ["turn-a1", "turn-a2"] and conv["active"]["meta"]["turn_index"] == 2
    order = [m["role"] for m in conv["active"]["messages"] if m["role"] != "system_note"]
    assert order == ["user", "model_call", "tool_call", "tool_result", "model_call", "assistant"]
    assert conv["active"]["meta"]["channel"] == "chat" and conv["active"]["meta"]["origin"] == "chat.typed"
    one = await cv.get_turn(ADMIN, ORG, "turn-a2")
    assert one["conversation_key"] == "s:chat-1" and one["trace"]["id"] == "turn-a2"


async def test_synthetic_and_inferred_groups_are_labelled_and_the_shared_rerun_split(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-s1", session_id="report-1a2b-1759300000", base=B, minutes_ago=10)
        await add_turn(conn, "turn-s2", session_id="rerun", base=B, minutes_ago=11)
        await add_turn(conn, "turn-s3", session_id="rerun", base=B, minutes_ago=12)
        await add_turn(conn, "turn-s4", session_id=None, base=B, minutes_ago=13)
        await add_turn(conn, "turn-s5", session_id="hs-no-thread", base=B, minutes_ago=14)
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=10)
    got = {c["key"]: (c["session_kind"], c["session_label"]) for c in out["conversations"]}
    assert got == {"s:report-1a2b-1759300000": ("synthetic", "Report card run"),
                   "r:turn-s2": ("synthetic", "Correction re-run"), "r:turn-s3": ("synthetic", "Correction re-run"),
                   "t:turn-s4": ("inferred", "Single turn, no session recorded"),
                   "s:hs-no-thread": ("inferred", "Session not in chat history")}


async def test_channel_filter_uses_recorded_then_inferred(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-c1", session_id="c-1", base=B, minutes_ago=10,
                       question="Why is the gas cert at B-1 lapsed or blocked, and what should I do next?")
        await add_meta(conn, "turn-c1", "chat", origin="chat.typed")        # recorded beats the template
        await add_turn(conn, "turn-c2", session_id="c-2", base=B, minutes_ago=11, question="Migrate B-101 workbook")
        await add_turn(conn, "turn-c3", session_id="c-3", base=B, minutes_ago=12, question="Who services the lifts?")
    assert await keys_of(channel="chat") == ["s:c-1", "s:c-3"]
    assert await keys_of(channel="upload") == ["s:c-2"]
    assert await keys_of(channel="ui_action") == []
    facets = await cv.conversation_facets(ADMIN, ORG, cv.make_filter())
    assert {f["value"]: f["turns"] for f in facets["channels"]} == {"chat": 2, "upload": 1}
    assert {f["value"]: f["turns"] for f in facets["channel_sources"]} == {"client": 1, "inferred": 2}


async def test_cursor_pages_without_gaps_or_repeats(seeded):
    B = _now()
    async with seeded.begin() as conn:
        for i in range(7):
            await add_turn(conn, f"turn-p{i}", session_id=f"page-{i}", base=B, minutes_ago=10 + (5 if i == 6 else i))
    seen, cursor, pages = [], None, 0
    while True:
        out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=cursor, limit=3)
        seen += [c["key"] for c in out["conversations"]]
        pages += 1
        assert out["total"] == 7
        cursor = out["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 7 and len(set(seen)) == 7      # page-5 and page-6 tie on time


async def test_total_is_exact_and_comes_from_the_same_grouping(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-t1", session_id="t-1", base=B, minutes_ago=30, question="Gas certificates at B-101?")
        await add_turn(conn, "turn-t2", session_id="t-1", base=B, minutes_ago=29, question="And the boilers?")
        await add_turn(conn, "turn-t3", session_id="t-1", base=B, minutes_ago=28, question="Boilers due a service?")
        await add_turn(conn, "turn-t4", session_id="t-2", base=B, minutes_ago=20, question="Boilers at B-102?")
        await add_turn(conn, "turn-t5", session_id="t-3", base=B, minutes_ago=10, question="Lifts?")
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="boiler"), cursor=None, limit=1)
    assert out["total"] == 2 and [c["key"] for c in out["conversations"]] == ["s:t-2"] and out["next_cursor"]
    nxt = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="boiler"), cursor=out["next_cursor"], limit=1)
    row = nxt["conversations"][0]
    assert row["key"] == "s:t-1" and row["turns_in_window"] == 3 and row["matching_turns"] == 2
    exact = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="turn-t5"), cursor=None, limit=5)
    assert exact["matched_turn_id"] == "turn-t5" and [c["key"] for c in exact["conversations"]] == ["s:t-3"]


async def test_updates_count_turns_written_after_the_watermark(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-u0", session_id="u-0", base=B, minutes_ago=5, created_minutes_ago=4)
    listed = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=10)
    wm = datetime.fromisoformat(listed["watermark"])
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-u1", session_id="u-0", base=B, minutes_ago=20, created_minutes_ago=-1)
        await add_turn(conn, "turn-u2", session_id="u-2", base=B, minutes_ago=1, created_minutes_ago=-1)
        await add_turn(conn, "turn-u3", session_id="u-3", base=B, minutes_ago=120, created_minutes_ago=-1)  # past the look-back
    out = await cv.conversation_updates(ADMIN, ORG, cv.make_filter(), watermark=wm)
    assert (out["new_turns"], out["new_conversations"]) == (2, 2)


async def test_model_call_aggregate_counts_llm_and_router_spans_only(seeded):
    B = _now()
    root, group, llm, router, tool = (str(uuid.uuid4()) for _ in range(5))
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-m1", session_id="m-1", base=B)
        await add_span(conn, "turn-m1", root, None, 0, "turn", "turn", base=B)
        await add_span(conn, "turn-m1", group, root, 1, "agent", "compliance", base=B, agent="compliance", cost=0.5)
        await add_span(conn, "turn-m1", llm, group, 2, "llm", "analyst", base=B, agent="compliance", model="claude-sonnet-5",
                       cost=0.1, tin=1000, tout=100, inp={"system_prompt": "S", "user_message": "U"},
                       out={"model_output": "Use Bearer abcdefghijklmnop now"})
        await add_span(conn, "turn-m1", router, root, 3, "router", "router", base=B, agent="agent_router",
                       model="claude-haiku-4-5", cost=0.01, tin=50, tout=5)
        await add_span(conn, "turn-m1", tool, root, 4, "tool", "list_certificates", base=B, agent="compliance")
    out = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=100)
    agg = out["aggregate"]
    assert agg["calls"] == 2 and round(agg["cost_usd"], 6) == 0.11 and agg["input_tokens"] == 1050
    assert sorted(m["model"] for m in agg["by_model"]) == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert {c["kind"] for c in out["calls"]} == {"llm", "router"}
    analyst = next(c for c in out["calls"] if c["kind"] == "llm")
    assert analyst["response_preview"] == "Use Bearer [redacted] now" and analyst["conversation_key"] == "s:m-1"
    assert next(c for c in out["calls"] if c["kind"] == "router")["label"] == "routing decision"


async def test_model_call_rows_are_redacted(seeded):
    B = _now()
    root, llm = str(uuid.uuid4()), str(uuid.uuid4())
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-x1", session_id="x-1", base=B, question="My key is sk-ant-api03-ABCDEFGHIJKLMNOPQRST, why?")
        await add_span(conn, "turn-x1", root, None, 0, "turn", "turn", base=B)
        await add_span(conn, "turn-x1", llm, root, 1, "llm", "model", base=B, model="claude-sonnet-5",
                       inp={"messages": 1, "last_human_message": "x", "tool_results_since_last_human": 0, "api_key": "k-1"},
                       out={"content": "token eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSM here", "tool_calls": []})
    row = (await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=10))["calls"][0]
    assert row["response_preview"] == "token [redacted token] here"
    assert "ABCDEFGHIJKLMNOPQRST" not in row["question_excerpt"]
    card = (await cv.get_model_call(ADMIN, ORG, llm))["model_call"]
    assert "k-1" not in json.dumps(card) and card["label"] == "answer synthesis"


async def test_gate_children_are_withheld_in_the_model_call_list(seeded):
    B = _now()
    root, gate, judge = (str(uuid.uuid4()) for _ in range(3))
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-g1", session_id="g-1", base=B)
        await add_span(conn, "turn-g1", root, None, 0, "turn", "turn", base=B)
        await add_span(conn, "turn-g1", gate, root, 1, "gate", "el_udr", base=B, agent="el_udr", inp={"answer_before": "A"},
                       out={"outcome": "degraded", "changed": True, "answer_after": "A2", "verdict": {"issues": ["count off"]}})
        await add_span(conn, "turn-g1", judge, gate, 2, "llm", "judge", base=B, agent="el_udr", model="claude-sonnet-5",
                       inp={"system_prompt": "J", "user_message": "c"}, out={"model_output": "count off"},
                       tin=10, tout=2, cost=0.001)
    row = (await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=100))["calls"][0]
    assert row["withheld"] is True and row["response_preview"] is None
    one = await cv.get_model_call(ADMIN, ORG, judge)
    assert one["model_call"]["response"] is None and one["model_call"]["withheld"] == ["response", "raw"]
    conv = await cv.get_conversation(ADMIN, ORG, "s:g-1")
    assert "count off" not in json.dumps(conv, default=str)


async def test_deleted_threads_are_hidden_unless_asked(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_thread(conn, "d-1", "Old chat", deleted=True)
        await add_turn(conn, "turn-d1", session_id="d-1", base=B)
        await add_turn(conn, "turn-d2", session_id="d-2", base=B, minutes_ago=5)
    assert await keys_of() == ["s:d-2"]
    shown = await cv.list_conversations(ADMIN, ORG, cv.make_filter(deleted="include"), cursor=None, limit=10)
    row = next(c for c in shown["conversations"] if c["key"] == "s:d-1")
    assert "thread_deleted" in row["flags"] and row["session_label"] == "Old chat"
    assert await cv.get_conversation(ADMIN, ORG, "s:d-1") is None
    assert (await cv.get_conversation(ADMIN, ORG, "s:d-1", include_deleted=True))["thread_deleted"] is True


async def test_session_kind_sql_and_python_agree(seeded):
    B = _now()
    sessions = [None, "", "   ", "\t", "rerun", "rerun-x", "golden-1", "report-1", "docs-1", "ing-1", "skilllab-1",
                "thread-1", "other"]
    async with seeded.begin() as conn:
        await add_thread(conn, "thread-1", "A thread")
        await add_thread(conn, "golden-1", "Still synthetic")
        for i, sid in enumerate(sessions):
            await add_turn(conn, f"turn-k{i}", session_id=sid, base=B, minutes_ago=10 + i)
        rows = (await conn.execute(text(f"""
            SELECT tt.turn_id, tt.session_id, {cv.GROUP_KEY_SQL} AS k, {cv.SESSION_KIND_SQL} AS kind,
                   (th.id IS NOT NULL) AS th
              FROM {trace.TURNS} tt LEFT JOIN plenum_cafm.chat_threads th ON th.id = tt.session_id"""))).mappings().all()
    assert len(rows) == len(sessions)
    for r in rows:
        assert r["k"] == n.group_key(r["session_id"], r["turn_id"]), dict(r)
        assert r["kind"] == n.session_kind(r["session_id"], thread_exists=r["th"]), dict(r)


async def test_runs_keep_recorded_golden_runs_separate_from_live_traffic(seeded):
    B = _now()
    run = str(uuid.uuid4())
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-live", session_id="live-1", base=B, cost=0.02)
        await add_turn(conn, "turn-gold", session_id="golden-x", base=B, cost=0.5)
        await add_meta(conn, "turn-gold", "golden", source="recorded", origin="golden.case", golden_run_id=run,
                       case_key="B-07", rep=1)
    assert await keys_of() == ["s:live-1"]
    only = await cv.list_conversations(ADMIN, ORG, cv.make_filter(run=run), cursor=None, limit=10)
    row = only["conversations"][0]
    assert row["key"] == "s:golden-x" and row["golden"] == {"run_id": run, "case_key": "B-07", "rep": 1}
    assert row["session_label"] == "Golden run · B-07 · rep 1" and row["run_id"] == run
    assert len(await keys_of(run="all")) == 2
    runs = await cv.list_runs(ADMIN, ORG, since=B - timedelta(days=1), until=B + timedelta(days=1))
    assert runs["golden_available"] is False
    assert runs["runs"][0]["turn_count"] == 1 and runs["runs"][0]["cost_usd"] == 0.02


async def test_scope_never_reads_another_company(seeded):
    B = _now()
    root, llm = str(uuid.uuid4()), str(uuid.uuid4())
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-o1", session_id="o-1", base=B, org=OTHER)
        await add_span(conn, "turn-o1", root, None, 0, "turn", "turn", base=B, org=OTHER)
        await add_span(conn, "turn-o1", llm, root, 1, "llm", "analyst", base=B, model="claude-sonnet-5", org=OTHER)
    assert await keys_of() == []
    assert await cv.get_conversation(ADMIN, ORG, "s:o-1") is None and await cv.get_turn(ADMIN, ORG, "turn-o1") is None
    assert (await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=10))["calls"] == []
    assert await cv.get_model_call(ADMIN, ORG, llm) is None


async def test_a_legacy_answer_is_refined_by_its_earliest_activity_row(seeded):
    from src.agents import activity_log

    assert await activity_log.ensure_table()
    B = _now()
    async with seeded.begin() as conn:
        await conn.execute(text("TRUNCATE plenum_cafm.agent_activity_log"))
        await add_turn(conn, "turn-l1", session_id="l-1", base=B, question="ctx" + n.QUESTION_MARKER + "Wrapped words")
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_activity_log (id, session_id, turn_id, agent, stage, direction, payload)
            VALUES (CAST(:id AS uuid), 'l-1', 'turn-l1', 'orchestrator', 'turn', 'input', CAST(:p AS jsonb))"""),
            {"id": str(uuid.uuid4()), "p": json.dumps({"message": "Typed words", "mode": "rest"})})
    conv = await cv.get_conversation(ADMIN, ORG, "s:l-1")
    assert conv["active"]["input"] == "Typed words" and "question_repaired" in conv["active"]["flags"]
```

- [ ] **Step 2: Run it and see it fail**

Start the scratch Postgres (once): `docker run -d --rm --name tl-scratch-pg -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=tracelab_scratch -p 127.0.0.1:55432:5432 postgres:16-alpine` (M0's scratch guard accepts only the database name `tracelab_scratch`).
Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch tl_da_test tests/test_trace_lab_conversations_sql.py`
Expected first run: the tests execute against real SQL; any failure here is a real SQL defect in Task 7–8 (for example a type error in `CAST(:ct AS timestamptz)` or a missing column). With Task 8 correct they pass — this task's "failing" state is the run without the URL, where all 14 report `SKIPPED (needs a scratch Postgres)`; confirm that first with `tl_da_test tests/test_trace_lab_conversations_sql.py` → `14 skipped`.

- [ ] **Step 3: Implement**

No new production code. If a test fails, fix the SQL in `conversations.py` (Task 7–8 code), never the test's expectation, unless the test contradicts the spec — then stop and report.

- [ ] **Step 4: Run it and see it pass**

Run: `TRACE_LAB_TEST_DB_URL=postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch tl_da_test tests/test_trace_lab_conversations_sql.py tests/test_trace_lab_conversations.py tests/test_trace_lab_normalize.py tests/test_trace_lab_grouping.py tests/test_trace_lab_redaction.py tests/test_trace_lab_m1_preconditions.py`
Expected: `100 passed` (14 SQL + 34 conversations + 33 normalize + 7 grouping + 6 redaction + 6 preconditions), 0 failed, 0 skipped. Then stop the container: `docker stop tl-scratch-pg`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 11: The Trace Lab API client

**Files:**
- Create: `apps/frontend/src/api/traceLab.js`
- Modify: `apps/frontend/src/api/deepAgents.js:53` (export `orgOverride`)
- Test: `apps/frontend/test/traceLabCompanyScope.test.mjs`

**Interfaces:**
- Consumes: `apiFetch`, `BASES` (src/api/client.js:20, :129); `orgOverride()` (src/api/deepAgents.js:53).
- Produces: `traceLabApi.{conversations(query), conversationUpdates(query), conversationFacets(query), conversation(key, query), turn(turnId), runs(query), modelCalls(query), modelCall(spanId)}` — each a GET returning the route's JSON; `export function orgOverride()` from deepAgents.js.

- [ ] **Step 1: Write the failing test**

Create `apps/frontend/test/traceLabCompanyScope.test.mjs`:

```js
// Trace Lab's API calls name the company only through orgOverride() — a superadmin's view-as and nothing
// else — never currentOrgId() (api/deepAgents.js:46-53). Mutation-checked: dropping the override from any
// call, or sending the build's default company, fails here.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' } };
let calls = [];
globalThis.fetch = async (url, opts) => {
  calls.push({ url: new URL(String(url)), method: (opts && opts.method) || 'GET' });
  return { ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}', headers: { get: () => null } };
};
const { traceLabApi } = await import('../src/api/traceLab.js');
const { setActingOrg } = await import('../src/api/client.js');

const B = '/backend/deep-agents/api/trace-lab';
const CALLS = [
  ['conversations', () => traceLabApi.conversations({ run: 'live' }), B + '/conversations'],
  ['conversationUpdates', () => traceLabApi.conversationUpdates({ watermark: 'w' }), B + '/conversations/updates'],
  ['conversationFacets', () => traceLabApi.conversationFacets({}), B + '/conversations/facets'],
  ['conversation', () => traceLabApi.conversation('s:a/b', { turn: 't1' }), B + '/conversations/s%3Aa%2Fb'],
  ['turn', () => traceLabApi.turn('turn-1'), B + '/turns/turn-1'],
  ['runs', () => traceLabApi.runs({}), B + '/runs'],
  ['modelCalls', () => traceLabApi.modelCalls({ limit: 50 }), B + '/model-calls'],
  ['modelCall', () => traceLabApi.modelCall('abc'), B + '/model-calls/abc']
];

beforeEach(() => { calls = []; setActingOrg(null); });

test('every Trace Lab call is a GET on its route and carries the view-as company, and only that', async () => {
  assert.deepEqual(Object.keys(traceLabApi).sort(), CALLS.map((c) => c[0]).sort());
  for (const [name, call, path] of CALLS) {
    setActingOrg('org-viewed');
    calls = [];
    await call();
    assert.equal(calls.length, 1, name);
    assert.equal(calls[0].method, 'GET', name);
    assert.equal(calls[0].url.pathname, path, name);
    assert.equal(calls[0].url.searchParams.get('organization_id'), 'org-viewed', name);
    setActingOrg(null);
    calls = [];
    await call();
    assert.equal(calls[0].url.searchParams.has('organization_id'), false, name + ' sends no company of its own');
  }
});

test('the client never reaches for currentOrgId and every call goes through orgOverride', () => {
  const src = readFileSync(new URL('../src/api/traceLab.js', import.meta.url), 'utf8');
  assert.equal(/currentOrgId|ORG_ID/.test(src), false);
  const fns = src.match(/^ {2}\w+: \(.*$/gm) || [];
  assert.equal(fns.length, CALLS.length);
  for (const line of fns) assert.ok(line.includes('q('), line);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabCompanyScope.test.mjs`
Expected: `ERR_MODULE_NOT_FOUND ... src/api/traceLab.js`.

- [ ] **Step 3: Implement**

In `apps/frontend/src/api/deepAgents.js`:

old:
```js
function orgOverride() { const o = getActingOrg(); return o ? { organization_id: o } : {}; }
```
new:
```js
export function orgOverride() { const o = getActingOrg(); return o ? { organization_id: o } : {}; }
```

Create `apps/frontend/src/api/traceLab.js`:

```js
// api/traceLab — Hoist Trace Lab's read routes (svc-deepagents src/api/routes/trace_lab_conversations.py, prefix
// /api/trace-lab), mounted behind the gateway with the rest of the assistant's routes. Every call names the
// company only through orgOverride() — a superadmin's view-as and nothing else, never the build's default
// company id: the routes 403 a non-superadmin who names any company but their own (test/traceLabCompanyScope.test.mjs).
import { BASES, apiFetch } from './client.js';
import { orgOverride } from './deepAgents.js';

const B = BASES.deepAgents;
const P = '/api/trace-lab';
const q = (extra) => Object.assign({}, extra || {}, orgOverride());
const enc = encodeURIComponent;

export const traceLabApi = {
  conversations: (query) => apiFetch(B, P + '/conversations', { query: q(query), timeoutMs: 20000 }),
  conversationUpdates: (query) => apiFetch(B, P + '/conversations/updates', { query: q(query), timeoutMs: 12000 }),
  conversationFacets: (query) => apiFetch(B, P + '/conversations/facets', { query: q(query), timeoutMs: 15000 }),
  conversation: (key, query) => apiFetch(B, P + '/conversations/' + enc(key), { query: q(query), timeoutMs: 20000 }),
  turn: (turnId) => apiFetch(B, P + '/turns/' + enc(turnId), { query: q(), timeoutMs: 20000 }),
  runs: (query) => apiFetch(B, P + '/runs', { query: q(query), timeoutMs: 15000 }),
  modelCalls: (query) => apiFetch(B, P + '/model-calls', { query: q(query), timeoutMs: 20000 }),
  modelCall: (spanId) => apiFetch(B, P + '/model-calls/' + enc(spanId), { query: q(), timeoutMs: 15000 })
};
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabCompanyScope.test.mjs test/noInternalNames.test.mjs`
Expected: all pass (the comment's service name is stripped before the internal-names scan).

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 12: Timeline cards (pure)

**Files:**
- Create: `apps/frontend/src/logic/traceLabTimeline.js`
- Test: `apps/frontend/test/traceLabTimeline.test.mjs`

**Interfaces:**
- Consumes: `scrubInternal` (src/logic/publicText.js), `fmtMs`, `fmtTok`, `fmtUsd` (src/logic/tracesPage.js:16-27); the record shape of Task 6.
- Produces: `toCards(record) -> Card[]` where `Card.kind ∈ user | note | model | cot | tool | db | plan | step | gate | answer` and every card has `id, kind, role, depth, when`; model cards from `modelCallCard(rec, id)` carry `purpose, model, latency, tokens, cost, header, ok, error, cotNote, withheld, panels[4]{id, title, body|rows|raw, note}`; tool cards `title, latency, args, result{text, full, long, chars}, collapsed, ok, error, requested, raw`; db cards `title, rowCount, rows(≤10), moreRows, raw{sql, params, label}`; gate cards `title, headline, changed, diff, verdictHidden, hiddenNote, outcome, verdict, raw`; step/note cards `title, body, collapsed, raw`; `labelFor(kind, name)`, `humanize(name)`, `collapse(text, n = 240)`, `pretty(v)`, `wordDiff(a, b) -> [{op: same|add|del, text}]`, constants `COLLAPSE_CHARS, DB_ROWS_SHOWN, UNAVAILABLE, PURPOSES`.

- [ ] **Step 1: Write the failing test**

Create `apps/frontend/test/traceLabTimeline.test.mjs`:

```js
// traceLabTimeline — a normalized record becomes timeline cards: every role has its card, a tool call and its
// result are one card, long results collapse past 240 characters, model calls say what they were for, missing
// provider fields read "unavailable", the record's reasoning notice follows every group of model calls, and no
// card title carries an internal name.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toCards, labelFor, humanize, collapse, wordDiff, modelCallCard, UNAVAILABLE } from '../src/logic/traceLabTimeline.js';

const COT = 'Model reasoning is not shown. Each model card shows what the model was sent and what it returned, as recorded.';
const NOTE = 'Private reasoning: not recorded (not a missing step).';
const UNAV = ['finish_reason', 'response_id', 'provider', 'time_to_first_token', 'tool_call_ids', 'cache_write_tokens', 'reasoning_tokens'];
const loopCard = (label, over) => Object.assign({ span_id: 'm', model: 'claude-sonnet-5', label, latency_s: 1.8,
  usage: { input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940 }, total_cost: 0.004, cost_status: 'priced',
  request_kind: 'loop_summary', request: { messages: 3 }, response: { content: '', tool_calls: [{ name: 'answer_from_records' }] },
  unavailable: UNAV, withheld: [], cot_note: NOTE, status: 'ok', operation: 'orchestrator loop › model', raw: { input: {}, output: {} } }, over || {});
const REC = {
  id: 'turn-1', cot_boundary: COT,
  messages: [
    { id: 'turn-1:root:user', role: 'user', depth: 0, text: 'How many work orders?', anchor: { turn_id: 'turn-1', span_id: 'root', field: 'question' } },
    { id: 'turn-1:p:note', role: 'system_note', subtype: 'prompt', depth: 1, name: 'prompt assembled', label: 'What the model was given', input: { system_prompt: 'S' }, output: null, collapse: true },
    { id: 'turn-1:m1:call', role: 'model_call', depth: 1, label: 'tool decision', model_call: loopCard('tool decision') },
    { id: 'turn-1:t1:call', role: 'tool_call', depth: 1, span_id: 't1', name: 'answer_from_records', arguments: { q: 'wo' }, requested_by: 'm1', ok: true, latency_ms: 3000 },
    { id: 'turn-1:t1:result', role: 'tool_result', depth: 1, span_id: 't1', name: 'answer_from_records', content: 'x'.repeat(241), chars: 241, collapse: true, ok: true, anchor: { turn_id: 'turn-1', span_id: 't1', field: 'output' } },
    { id: 'turn-1:q1:query', role: 'db_query', depth: 2, label: 'records: work_orders', sql: 'SELECT count(*) FROM work_orders', params: {}, row_count: 14, rows: Array.from({ length: 12 }, (_, i) => ({ n: i })), rows_truncated: 2, collapse: true, ok: true },
    { id: 'turn-1:m2:call', role: 'model_call', depth: 1, label: 'answer synthesis', model_call: loopCard('answer synthesis', { model: null, latency_s: null, usage: { input_tokens: null, output_tokens: null, cache_read_tokens: null, total_tokens: null }, total_cost: null, cost_status: 'unavailable', request_kind: 'none', request: null, response: null }) },
    { id: 'turn-1:g:gate', role: 'gate', depth: 1, gate: 'el_udr', label: 'Answer check', recorded: true, changed: true, answer_before: 'Four boilers at B-301.', answer_after: 'Three boilers at B-301.', verdict_hidden: true, outcome: null, verdict: null },
    { id: 'turn-1:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: '758 work orders.', rewritten_by: 'el_udr' }
  ]
};

test('every message role becomes its card, in order, with the reasoning notice after each group of model calls', () => {
  assert.deepEqual(toCards(REC).map((c) => c.kind), ['user', 'note', 'model', 'cot', 'tool', 'db', 'model', 'cot', 'gate', 'answer']);
  assert.deepEqual(toCards({ messages: [] }), []);
  assert.deepEqual(toCards(null), []);
});

test('a tool call and its result are one card, and a long result starts collapsed', () => {
  const tool = toCards(REC).find((c) => c.kind === 'tool');
  assert.equal(tool.id, 'turn-1:t1:call');
  assert.equal(tool.title, 'Records query');
  assert.ok(tool.args.includes('"q": "wo"'));
  assert.equal(tool.collapsed, true);
  assert.equal(tool.result.long, true);
  assert.equal(tool.result.text, 'x'.repeat(240) + '…');
  assert.equal(tool.result.chars, 241);
  assert.equal(tool.requested, true);
  assert.equal(tool.raw.name, 'answer_from_records');
});

test('the 240-character rule', () => {
  assert.equal(collapse('y'.repeat(240)).long, false);
  assert.equal(collapse('y'.repeat(241)).long, true);
  assert.equal(collapse(null).text, '');
  assert.equal(collapse('abcdef', 3).text, 'abc…');
});

test('model calls say what they were for: the record label first, the order of the turn as a fallback', () => {
  assert.deepEqual(toCards(REC).filter((c) => c.kind === 'model').map((c) => c.purpose), ['tool decision', 'answer synthesis']);
  const unlabelled = { messages: [
    { id: 'a', role: 'model_call', model_call: {} },
    { id: 'b', role: 'tool_result', span_id: 't', content: 'ok' },
    { id: 'c', role: 'model_call', model_call: {} }] };
  assert.deepEqual(toCards(unlabelled).filter((c) => c.kind === 'model').map((c) => c.purpose), ['tool decision', 'answer synthesis']);
  assert.equal(modelCallCard({ label: 'analyst' }, 'x').purpose, 'Analyst');
});

test('a missing provider field reads "unavailable", never 0 or a guess', () => {
  const [first, second] = toCards(REC).filter((c) => c.kind === 'model');
  assert.equal(second.header, 'Model call · unavailable · unavailable · unavailable · unavailable');
  const usage = second.panels.find((p) => p.title === 'Usage');
  assert.deepEqual(usage.rows.find((r) => r[0] === 'Provider'), ['Provider', UNAVAILABLE]);
  assert.deepEqual(usage.rows.find((r) => r[0] === 'Input tokens'), ['Input tokens', UNAVAILABLE]);
  assert.equal(second.panels.find((p) => p.title === 'Request').body, UNAVAILABLE);
  assert.equal(first.header, 'Model call · claude-sonnet-5 · 1.80 s · 900 in · 40 out · $0.0040');
  assert.match(first.panels.find((p) => p.title === 'Request').note, /full request is not recorded/);
  assert.equal(modelCallCard({ model: 'm', usage: { input_tokens: 5, output_tokens: 1 }, cost_status: 'unpriced' }, 'u').cost, 'unpriced');
});

test('the reasoning notice is the record\'s own text, and every model card carries its note', () => {
  const cards = toCards(REC);
  assert.deepEqual(cards.filter((c) => c.kind === 'cot').map((c) => c.text), [COT, COT]);
  assert.ok(cards.filter((c) => c.kind === 'model').every((c) => c.cotNote === NOTE));
  assert.equal(toCards({ messages: REC.messages }).some((c) => c.kind === 'cot'), false);
});

test('a database query shows its first ten rows and says how many more there are; the SQL goes to the raw panel', () => {
  const db = toCards(REC).find((c) => c.kind === 'db');
  assert.equal(db.title, 'Records: work orders');
  assert.equal(db.rows.length, 10);
  assert.equal(db.moreRows, 4);
  assert.equal(db.rowCount, 14);
  assert.equal(db.raw.sql, 'SELECT count(*) FROM work_orders');
});

test('a gate shows the change as a word diff and keeps its verdict hidden', () => {
  const gate = toCards(REC).find((c) => c.kind === 'gate');
  assert.equal(gate.title, 'Answer check');
  assert.equal(gate.headline, 'Answer changed after checking');
  assert.deepEqual(gate.diff, [{ op: 'del', text: 'Four' }, { op: 'add', text: 'Three' }, { op: 'same', text: ' boilers at B-301.' }]);
  assert.equal(gate.verdictHidden, true);
  assert.ok(gate.hiddenNote.length > 0);
  assert.equal(gate.verdict, '');
  assert.equal(toCards(REC).find((c) => c.kind === 'answer').rewritten, 'Rewritten by Answer check');
  const kept = toCards({ messages: [{ id: 'g', role: 'gate', gate: 'contract_compose', recorded: false, changed: false, verdict_hidden: true }] })[0];
  assert.equal(kept.headline, 'No outcome recorded');
  assert.equal(kept.title, 'Vendor answer rewrite');
});

test('labelFor never returns an internal name', () => {
  const internal = /svc-[a-z]|plenum_cafm|\budr\b|\brag\b|doc[-_ ]?rag|deep-?agents/i;
  for (const name of ['udr_agent', 'svc-udr', 'doc_rag_search', 'deepagents', 'plenum_cafm.sites', 'UDR lookup', 'compliance_router',
    'tool:wo_engine', 'agent_router', 'el_udr', 'tool:udr_query', 'rag_lookup', 'deep-agents-loop']) {
    const out = labelFor('tool', name);
    assert.equal(internal.test(out), false, name + ' → ' + out);
    assert.ok(out.length > 0, name);
  }
  assert.equal(labelFor('db', ''), 'Database query');
  assert.equal(labelFor('tool', 'udr_agent'), 'Data lookup');
  assert.equal(humanize('tool:wo_engine'), 'Wo engine');
});

test('the word diff keeps unchanged words and falls back to a whole replacement for very long answers', () => {
  assert.deepEqual(wordDiff('same words', 'same words'), [{ op: 'same', text: 'same words' }]);
  assert.deepEqual(wordDiff('a '.repeat(2000), 'b '.repeat(2000)).map((d) => d.op), ['del', 'add']);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabTimeline.test.mjs`
Expected: `ERR_MODULE_NOT_FOUND ... src/logic/traceLabTimeline.js`.

- [ ] **Step 3: Implement**

Create `apps/frontend/src/logic/traceLabTimeline.js`:

```js
// traceLabTimeline — pure: a normalized Trace Lab record (spec "Trace capture, normalizer and conversations"
// §3.2) becomes the cards the Conversations timeline draws (spec "Frontend: the Trace Lab" §3). No state and no
// requests. A card's title never carries an internal agent, tool or table name: titles go through labelFor(),
// and the raw values reach only screens/traceLab/RawPanel.jsx.
import { scrubInternal } from './publicText.js';
import { fmtMs, fmtTok, fmtUsd } from './tracesPage.js';

export const COLLAPSE_CHARS = 240;
export const DB_ROWS_SHOWN = 10;
export const UNAVAILABLE = 'unavailable';
export const PURPOSES = ['tool decision', 'answer synthesis', 'routing decision'];
const DIFF_CELLS = 1000000;

// Names the trace records, as a reviewer reads them. Anything else falls back to humanize().
const NAME_LABELS = {
  orchestrator: 'Assistant', 'orchestrator loop': 'Assistant', 'sub-agent': 'Helper', turn: 'Answer',
  compliance: 'Compliance', compliance_router: 'Compliance routing', agent_router: 'Routing', router: 'Routing decision',
  udr_agent: 'Data lookup', el_udr: 'Answer check', contract_compose: 'Vendor answer rewrite',
  contract_analyst: 'Vendor answer rewrite', answer_from_records: 'Records query', task: 'Hand-off to a helper',
  analyst: 'Analyst', planner: 'Planner', plan: 'Planning', review: 'Review', summary: 'Summary', judge: 'Answer check',
  recheck: 'Answer re-check', fetch: 'Fetching records', 'prompt assembled': 'What the model was given'
};
const KIND_LABELS = { db: 'Database query', tool: 'Tool', llm: 'Model call', router: 'Routing decision', plan: 'Plan',
  step: 'Plan step', gate: 'Answer check', stage: 'Step', agent: 'Helper', turn: 'Answer' };
const ALWAYS_ROWS = ['Provider', 'Finish reason', 'Response id', 'Time to first token', 'Tool call ids', 'Cache write tokens', 'Reasoning tokens'];

const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const nil = (v) => v === null || v === undefined;
const two = (n) => String(n).padStart(2, '0');
function timeOf(iso) {
  const t = Date.parse(iso || '');
  if (isNaN(t)) return '';
  const d = new Date(t);
  return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
}

// A recorded name as words, with the platform's own names taken out (logic/publicText.js plus the bare forms
// a name can take once its underscores are spaces).
export function humanize(name) {
  let s = scrubInternal(String(name || '').replace(/^(?:tool|agent|engine):/i, ''));
  s = s.replace(/[_-]+/g, ' ')
    .replace(/\bdoc rag\b/gi, 'document search')
    .replace(/\bdeep ?agents\b/gi, 'assistant')
    .replace(/\budr\b/gi, 'data')
    .replace(/\brag\b/gi, 'search')
    .replace(/\bsvc\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

export function labelFor(kind, name) {
  const key = String(name || '').trim();
  if (key && has(NAME_LABELS, key)) return NAME_LABELS[key];
  if (key && has(NAME_LABELS, key.toLowerCase())) return NAME_LABELS[key.toLowerCase()];
  return humanize(key) || KIND_LABELS[kind] || 'Step';
}

export function collapse(text, n) {
  const max = typeof n === 'number' ? n : COLLAPSE_CHARS;
  const s = nil(text) ? '' : String(text);
  return s.length > max ? { text: s.slice(0, max) + '…', full: s, long: true, chars: s.length }
    : { text: s, full: s, long: false, chars: s.length };
}

export function pretty(v) {
  if (nil(v)) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
}

// Words added and removed between two answers (longest common subsequence over words and the spaces between
// them). Past DIFF_CELLS the answers are shown as one removal and one addition rather than freezing the tab.
export function wordDiff(a, b) {
  const A = String(nil(a) ? '' : a).split(/(\s+)/).filter((x) => x !== '');
  const B = String(nil(b) ? '' : b).split(/(\s+)/).filter((x) => x !== '');
  const out = [];
  const push = (op, text) => { const last = out[out.length - 1]; if (last && last.op === op) last.text += text; else out.push({ op, text }); };
  if (A.length * B.length > DIFF_CELLS) {
    if (A.length) push('del', A.join(''));
    if (B.length) push('add', B.join(''));
    return out;
  }
  const n = A.length;
  const m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push('same', A[i]); i += 1; j += 1; } else if (dp[i + 1][j] >= dp[i][j + 1]) { push('del', A[i]); i += 1; } else { push('add', B[j]); j += 1; }
  }
  while (i < n) { push('del', A[i]); i += 1; }
  while (j < m) { push('add', B[j]); j += 1; }
  return out;
}

// One model call (a ModelCallRecord, spec §3.5) as a card: "Model call · model · latency · tokens · cost" and
// four collapsed panels. A missing field reads "unavailable", never 0 or a guess.
export function modelCallCard(rec, id) {
  const r = rec || {};
  const u = r.usage || {};
  const model = r.model || UNAVAILABLE;
  const latency = typeof r.latency_s === 'number' ? fmtMs(r.latency_s * 1000) : UNAVAILABLE;
  const tokens = nil(u.input_tokens) && nil(u.output_tokens) ? UNAVAILABLE : fmtTok(u.input_tokens) + ' in · ' + fmtTok(u.output_tokens) + ' out';
  const cost = typeof r.total_cost === 'number' ? fmtUsd(r.total_cost) : (r.cost_status === 'unpriced' ? 'unpriced' : UNAVAILABLE);
  const withheld = Array.isArray(r.withheld) && r.withheld.length > 0;
  const val = (v) => (nil(v) ? UNAVAILABLE : String(v));
  return {
    id, kind: 'model', role: 'Model call', purpose: PURPOSES.indexOf(r.label) > -1 ? r.label : labelFor('llm', r.label),
    model, latency, tokens, cost, header: 'Model call · ' + model + ' · ' + latency + ' · ' + tokens + ' · ' + cost,
    ok: r.status !== 'error', error: r.status_message || '', cotNote: r.cot_note || '', withheld,
    panels: [
      { id: id + ':request', title: 'Request', body: nil(r.request) ? UNAVAILABLE : pretty(r.request),
        note: r.request_kind === 'loop_summary' ? 'Summary only: the full request is not recorded. "What the model was given" above shows the prompt.' : '' },
      { id: id + ':response', title: 'Response', note: '',
        body: withheld ? 'Hidden so the check does not shape your review.' : (nil(r.response) ? UNAVAILABLE : pretty(r.response)) },
      { id: id + ':usage', title: 'Usage', note: '',
        rows: [['Input tokens', val(u.input_tokens)], ['Output tokens', val(u.output_tokens)], ['Cache read tokens', val(u.cache_read_tokens)],
          ['Total tokens', val(u.total_tokens)], ['Cost', cost], ['Latency', latency],
          ['Model settings', r.model_parameters ? pretty(r.model_parameters) : UNAVAILABLE]].concat(ALWAYS_ROWS.map((label) => [label, UNAVAILABLE])) },
      { id: id + ':raw', title: 'Raw metadata', raw: { operation: r.operation || null, request_kind: r.request_kind || null, raw: withheld ? null : (r.raw || null) } }
    ]
  };
}

function planSteps(plan) {
  const steps = plan && Array.isArray(plan.steps) ? plan.steps : [];
  return steps.map((s, i) => ({ n: i + 1, text: typeof s === 'string' ? s
    : String((s && (s.question || s.why || s.purpose)) || labelFor('step', s && (s.target || s.name)) || 'Step') }));
}

function gateCard(m, depth, when) {
  const changed = !!m.changed;
  const shown = m.verdict_hidden === false;
  return { id: m.id, kind: 'gate', role: 'Answer check', depth, when, title: labelFor('gate', m.gate || m.label),
    headline: !m.recorded ? 'No outcome recorded' : (changed ? 'Answer changed after checking' : 'Answer checked — not changed'),
    changed, diff: changed ? wordDiff(m.answer_before, m.answer_after) : [], verdictHidden: !shown,
    hiddenNote: shown ? '' : "The check's own verdict stays hidden so it does not shape your review.",
    outcome: shown ? (m.outcome || '') : '', verdict: shown ? pretty(m.verdict) : '',
    raw: { gate: m.gate || null, outcome: shown ? m.outcome : null, verdict: shown ? m.verdict : null }, anchor: m.anchor || null };
}

export function toCards(record) {
  const msgs = record && Array.isArray(record.messages) ? record.messages : [];
  const cards = [];
  let sawResult = false;
  for (let i = 0; i < msgs.length; i += 1) {
    const m = msgs[i] || {};
    const depth = Math.max(0, Number(m.depth) || 0);
    const when = timeOf(m.started_at);
    if (m.role === 'tool_call' || m.role === 'tool_result') {
      const call = m.role === 'tool_call' ? m : null;
      let res = m.role === 'tool_result' ? m : null;
      const next = msgs[i + 1];
      if (call && next && next.role === 'tool_result' && next.span_id === m.span_id) { res = next; i += 1; }
      if (res) sawResult = true;
      const head = call || res;
      const result = collapse(res ? pretty(res.content) : '', COLLAPSE_CHARS);
      cards.push({ id: head.id, kind: 'tool', role: 'Tool', depth, when, title: labelFor('tool', head.name), latency: fmtMs(head.latency_ms),
        args: call ? pretty(call.arguments) : '', result, collapsed: !!(res && res.collapse) || result.long,
        ok: head.ok !== false && (!res || res.ok !== false), error: (res && res.error) || (call && call.error) || '',
        requested: !!(call && call.requested_by),
        raw: { name: head.name || null, arguments: call ? call.arguments : null, content: res ? res.content : null },
        anchor: res ? res.anchor || null : null });
      continue;
    }
    if (m.role === 'model_call') {
      const rec = Object.assign({}, m.model_call || {});
      if (!rec.label) rec.label = m.label || (sawResult ? 'answer synthesis' : 'tool decision');
      cards.push(Object.assign(modelCallCard(rec, m.id), { depth, when, anchor: m.anchor || null }));
      continue;
    }
    if (m.role === 'user') {
      cards.push({ id: m.id, kind: 'user', role: 'Question', depth, when, text: m.text || '', anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'assistant') {
      const role = m.subtype === 'workflow_stop' ? 'Stopped at an approval gate' : (m.subtype === 'error' ? 'Answer (failed)' : 'Answer');
      cards.push({ id: m.id, kind: 'answer', role, depth, when, text: m.text || '', error: m.error || '',
        rewritten: m.rewritten_by ? 'Rewritten by ' + labelFor('gate', m.rewritten_by) : '', anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'db_query') {
      const rows = Array.isArray(m.rows) ? m.rows : [];
      cards.push({ id: m.id, kind: 'db', role: 'Database query', depth, when, title: labelFor('db', m.label || m.name),
        rowCount: nil(m.row_count) ? null : Number(m.row_count), rows: rows.slice(0, DB_ROWS_SHOWN),
        moreRows: Math.max(0, rows.length - DB_ROWS_SHOWN) + (Number(m.rows_truncated) || 0), collapsed: !!m.collapse,
        ok: m.ok !== false, error: m.error || '', raw: { sql: m.sql || '', params: m.params || {}, label: m.label || null }, anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'plan') {
      cards.push({ id: m.id, kind: 'plan', role: 'Plan', depth, when, steps: planSteps(m.plan), rejected: m.rejected || '',
        raw: { source: m.source || null, plan: m.plan || null } });
      continue;
    }
    if (m.role === 'gate') { cards.push(gateCard(m, depth, when)); continue; }
    if (m.role === 'step') {
      const body = collapse(pretty(m.output), COLLAPSE_CHARS);
      cards.push({ id: m.id, kind: 'step', role: m.subtype === 'check' ? 'Check' : 'Plan step', depth, when, title: labelFor('step', m.name),
        body, collapsed: !!m.collapse || body.long, ok: m.ok !== false, error: m.error || '',
        raw: { name: m.name || null, input: m.input, output: m.output } });
      continue;
    }
    // system_note: the prompt note, a stage, or a span kind this build does not know
    const prompt = m.subtype === 'prompt';
    const body = collapse(pretty(prompt || nil(m.output) ? m.input : m.output), COLLAPSE_CHARS);
    cards.push({ id: m.id, kind: 'note', role: prompt ? 'What the model was given' : (m.subtype === 'unknown_kind' ? 'Recorded step' : 'Step'),
      depth, when, title: prompt ? '' : labelFor('stage', m.name), body, collapsed: prompt || !!m.collapse || body.long,
      ok: m.ok !== false, error: m.error || '', raw: { name: m.name || null, label: m.label || null, input: m.input, output: m.output } });
  }
  const text = record && record.cot_boundary;
  if (!text) return cards;
  const out = [];
  cards.forEach((card, k) => {
    out.push(card);
    if (card.kind === 'model' && !(cards[k + 1] && cards[k + 1].kind === 'model')) {
      out.push({ id: card.id + ':cot', kind: 'cot', role: 'About model calls', depth: card.depth, when: '', text });
    }
  });
  return out;
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabTimeline.test.mjs test/noInternalNames.test.mjs`
Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 13: The Trace Lab shell logic

**Files:**
- Create: `apps/frontend/src/logic/traceLab.js`
- Modify: `apps/frontend/src/logic/tracesPage.js:65` (export `sinceFor`)
- Test: `apps/frontend/test/traceLabShell.test.mjs` (create; Task 15 appends)

**Interfaces:**
- Consumes: Task 11 `traceLabApi.conversationUpdates`; `isStaleScope` (client.js:53); `canAdmin` (auth.js:99); `RANGES`, `sinceFor` (tracesPage.js:13, :65). Calls these controller methods that Task 14 defines: `tlLoadLists()`, `tlLoadConversations({merge})`, `tlLoadRuns()`, `tlOpenConversation(key, turnId)`, `tlStepConversation(delta)`, `tlStepTurn(delta)`; and `tpLoad()` (tracesPage.js:79).
- Produces: `TL_VIEWS`, `TL_TABS`, `TL_POLL_MS = 45000`, `TL_POLLED_VIEWS`, `TL_CHANNELS`, `CHANNEL_LABELS`, `TL_FILTER_DEFAULTS`, `tlResetPatch()`, `tlHasCompany(state)`, `tlTabsFor(state)`, `tlQueryString(search, st|null)`, `tlParseLink(search)`; `traceLabMethods` = `tlCanReview, tlOpen(view?), tlSetView(view), tlReadView, tlSetFilter(patch), tlSetQueryDraft(text), tlSearch, tlSetRun(runId), tlQuery, tlStartPoll, tlStopPoll, tlPoll(force), tlRefresh, tlSyncUrl, tlScrubUrl, tlLinkFromUrl, tlOpenLinked, tlKey(e), tlWrite(fn, {key, retry}), tlRetryWrite(key), tlToggleShortcuts, tlResetPatch` (the method bumps `this._tlSeq` and stops the poll); `traceLabVals(c, now)` (keys: `isTraceLab, tlTabs, tlView, tlIsOverview, tlHasCompany, tlRanges, tlChannelOptions, tlChannel, tlSetChannel, tlStatuses, tlRunOptions, tlRunId, tlSetRun, tlQueryDraft, tlSetQueryDraft, tlSearch, tlUpdateLine, tlPolled, tlPollFailed, tlRefresh, tlKey, tlShortcutsOpen, tlToggleShortcuts, tlShortcutRows, tlWriteState(key)`). State keys owned: every key in `tlResetPatch()`.

- [ ] **Step 1: Write the failing test**

Create `apps/frontend/test/traceLabShell.test.mjs`:

```js
// traceLab shell — the tabs a role sees, the URL and its scrub, the 45-second update check, the keyboard that
// ignores typing, and the write helper that never fails silently (spec "Frontend: the Trace Lab" §1-§2). The
// controller-level checks (mount, reset, reload slice, sign-in link) are at the end of this file.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
const store = (m) => ({ getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; } });
const replaced = [];
globalThis.window = {
  location: { origin: 'http://test.local', pathname: '/', search: '' },
  history: { replaceState: (s, t, url) => { replaced.push(url); const u = new URL(url, 'http://test.local'); globalThis.window.location.pathname = u.pathname; globalThis.window.location.search = u.search; } },
  scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: store(mem), sessionStorage: store(tabMem)
};
globalThis.document = { visibilityState: 'visible' };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  requests.push({ method, url: u });
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u) : (h || [404, { detail: 'not mocked' }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const { traceLabMethods, traceLabVals, tlResetPatch, tlTabsFor, tlQueryString, tlParseLink, TL_POLL_MS } = await import('../src/logic/traceLab.js');
const P = '/backend/deep-agents/api/trace-lab';
const tick = () => new Promise((r) => setTimeout(r, 0));
const ADMIN = { id: 'u-1', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin', status: 'active', organization_id: 'org-1' };
const hm = (ms) => { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

function ctl(state) {
  const c = { calls: [] };
  c.state = Object.assign(tlResetPatch(), { signedIn: true, view: 'traces', account: ADMIN }, state || {});
  c.setState = (patch) => { const p = typeof patch === 'function' ? patch(c.state) : patch; c.state = Object.assign({}, c.state, p); };
  Object.assign(c, traceLabMethods);
  ['tpLoad', 'tlLoadLists', 'tlLoadConversations', 'tlLoadRuns', 'tlOpenConversation', 'tlStepConversation', 'tlStepTurn']
    .forEach((m) => { c[m] = (...a) => c.calls.push([m, ...a]); });
  return c;
}

beforeEach(() => {
  handlers = {}; requests = []; replaced.length = 0;
  window.location.search = ''; window.location.pathname = '/';
  document.visibilityState = 'visible';
});

test('tabs per role: an admin and a superadmin viewing a company see all four; a platform superadmin and a plain user see the Overview', () => {
  const keys = (s) => tlTabsFor(s).map((t) => t.key);
  assert.deepEqual(keys({ account: { role: 'admin' } }), ['overview', 'conversations', 'runs', 'models']);
  assert.deepEqual(keys({ account: { role: 'superadmin' }, viewOrgId: 'org-2' }), ['overview', 'conversations', 'runs', 'models']);
  assert.deepEqual(keys({ account: { role: 'superadmin' } }), ['overview']);
  assert.deepEqual(keys({ account: { role: 'user' } }), ['overview']);
  assert.deepEqual(keys({}), ['overview']);
  const v = traceLabVals(ctl({ tlView: 'runs' }), Date.now());
  assert.deepEqual(v.tlTabs.map((t) => [t.label, t.on]), [['Overview', false], ['Conversations', false], ['Runs', true], ['Model calls', false]]);
  assert.equal(traceLabVals(ctl({ tlView: 'runs', account: { role: 'superadmin' } }), Date.now()).tlView, 'overview');
});

test('the URL carries view, run, conversation and answer, keeps other parameters, and parses back strictly', () => {
  assert.equal(tlQueryString('?x=1', { view: 'conversations', run: 'live', conv: 's:a/b', turn: 'turn-1' }), '?x=1&tl=conversations&run=live&conv=s%3Aa%2Fb&turn=turn-1');
  assert.equal(tlQueryString('?tl=runs&run=live&x=1', null), '?x=1');
  assert.equal(tlQueryString('?tl=runs', null), '');
  assert.deepEqual(tlParseLink('?tl=conversations&run=live&conv=s%3Aabc&turn=turn-1'), { view: 'conversations', run: 'live', conv: 's:abc', turn: 'turn-1' });
  const run = '3fa2c1d0-1111-4111-8111-111111111111';
  assert.equal(tlParseLink('?tl=runs&run=' + run).run, run);
  assert.equal(tlParseLink('?tl=runs&run=yesterday').run, 'live');
  assert.equal(tlParseLink('?tl=conversations&conv=x:evil').conv, null);
  assert.equal(tlParseLink('?tl=judges'), null);
  assert.equal(tlParseLink(''), null);
});

test('the page writes its state to the address bar while open and scrubs it on the way out', () => {
  const c = ctl({ tlView: 'conversations', tlConvKey: 's:abc', tlTurnId: 'turn-1' });
  window.location.search = '?keep=1';
  c.tlSyncUrl();
  assert.equal(window.location.search, '?keep=1&tl=conversations&run=live&conv=s%3Aabc&turn=turn-1');
  c.setState({ tlView: 'runs' });
  c.tlSyncUrl();
  assert.equal(window.location.search, '?keep=1&tl=runs&run=live');
  c.tlScrubUrl();
  assert.equal(window.location.search, '?keep=1');
  const away = ctl({ view: 'home' });
  replaced.length = 0;
  away.tlSyncUrl();
  assert.equal(replaced.length, 0);
});

test('opening a tab reads what it shows; a tab the role lacks falls back to the Overview', () => {
  const c = ctl({ tlView: 'conversations', tlConvKey: 's:abc', tlTurnId: 'turn-2' });
  c.tlReadView();
  assert.deepEqual(c.calls, [['tlLoadLists'], ['tlOpenConversation', 's:abc', 'turn-2']]);
  assert.ok(c._tlPoll);
  c.calls = [];
  c.tlSetView('overview');
  assert.deepEqual(c.calls, [['tpLoad']]);
  assert.equal(c._tlPoll, null);
  const sa = ctl({ account: { role: 'superadmin' }, tlView: 'conversations' });
  sa.tlOpen();
  assert.equal(sa.state.tlView, 'overview');
  assert.deepEqual(sa.calls, [['tpLoad']]);
  c.tlStopPoll(); sa.tlStopPoll();
});

test('the update check runs only on a visible Conversations or Runs tab, merges news on page one, and shows a failure with its time', async () => {
  const c = ctl({ tlView: 'conversations', tlWatermark: '2026-10-15T12:00:00Z', tlUpdatedAt: Date.parse('2026-10-15T12:00:00Z') });
  handlers[P + '/conversations/updates'] = (u) => { assert.equal(u.searchParams.get('watermark'), '2026-10-15T12:00:00Z'); return [200, { ok: true, new_turns: 3, new_conversations: 2 }]; };
  await c.tlPoll(false);
  assert.deepEqual(c.state.tlUpdates, { newTurns: 3, newConversations: 2 });
  assert.deepEqual(c.calls, [['tlLoadConversations', { merge: true }]]);
  assert.equal(traceLabVals(c, Date.now()).tlUpdateLine, 'Updated ' + hm(c.state.tlUpdatedAt) + ' · 3 new');
  c.calls = [];
  c.setState({ tlConvsPage: 2 });
  await c.tlPoll(false);
  assert.deepEqual(c.calls, []);                       // not on page one: the line says so, Refresh reads page one
  document.visibilityState = 'hidden';
  requests = [];
  await c.tlPoll(false);
  assert.equal(requests.length, 0);
  document.visibilityState = 'visible';
  c.setState({ tlView: 'models' });
  await c.tlPoll(false);
  assert.equal(requests.length, 0);
  c.setState({ tlView: 'runs' });
  handlers[P + '/conversations/updates'] = [503, { detail: { ok: false, error: 'Trace Lab is not available right now.' } }];
  const before = c.state.tlUpdatedAt;
  await c.tlPoll(true);
  assert.equal(c.state.tlPollErr, 'Trace Lab is not available right now.');
  assert.equal(traceLabVals(c, Date.now()).tlUpdateLine, "Couldn't refresh — retry · last updated " + hm(before));
  c.setState({ view: 'home' });
  c._tlPoll = setInterval(() => {}, TL_POLL_MS);
  await c.tlPoll(true);
  assert.equal(c._tlPoll, null);                       // left the page: the check stops itself
});

test('an update check that lands after a company switch is dropped', async () => {
  const c = ctl({ tlView: 'conversations', tlWatermark: 'w' });
  let release;
  handlers[P + '/conversations/updates'] = () => new Promise((r) => { release = () => r([200, { ok: true, new_turns: 5 }]); });
  const pending = c.tlPoll(true);
  await tick();
  c.setState(c.tlResetPatch());
  release();
  await pending;
  assert.equal(c.state.tlUpdates, null);
  assert.deepEqual(c.calls, []);
});

test('the keyboard ignores typing and modifiers, and walks conversations and answers', () => {
  const c = ctl({ tlView: 'conversations' });
  const key = (k, target, mods) => c.tlKey(Object.assign({ key: k, target: target || { tagName: 'DIV' }, preventDefault: () => {} }, mods || {}));
  key('j', { tagName: 'TEXTAREA' });
  key('j', { tagName: 'input' });
  key('j', { tagName: 'SELECT' });
  key('j', { tagName: 'DIV', isContentEditable: true });
  key('j', { tagName: 'SPAN', closest: (sel) => (sel === '[contenteditable]' ? {} : null) });
  key('j', null, { ctrlKey: true });
  key('k', null, { metaKey: true });
  key('n', null, { altKey: true });
  key('J', null, { shiftKey: true });
  assert.deepEqual(c.calls, []);
  key('j'); key('k'); key('n'); key('p');
  assert.deepEqual(c.calls, [['tlStepConversation', 1], ['tlStepConversation', -1], ['tlStepTurn', 1], ['tlStepTurn', -1]]);
  key('?', null, { shiftKey: true });
  assert.equal(c.state.tlShortcuts, true);
  c.setState({ tlModelCall: { spanId: 'x' } });
  key('Escape');
  assert.equal(c.state.tlShortcuts, false);
  assert.equal(c.state.tlModelCall, null);
  const off = ctl({ view: 'home', tlView: 'conversations' });
  off.tlKey({ key: 'j', target: { tagName: 'DIV' } });
  assert.deepEqual(off.calls, []);
});

test('a failed write keeps a visible error with Retry; a stale base says another tab changed it', async () => {
  const c = ctl();
  let attempts = 0;
  const fn = () => { attempts += 1; return attempts === 1 ? Promise.reject(new Error('network down')) : Promise.resolve({ ok: true }); };
  assert.equal((await c.tlWrite(fn, { key: 'verdict:turn-1' })).ok, false);
  const shown = traceLabVals(c, Date.now()).tlWriteState('verdict:turn-1');
  assert.equal(shown.error, 'Could not save — network down');
  assert.equal(shown.canRetry, true);
  assert.equal((await shown.retry()).ok, true);
  assert.equal(attempts, 2);
  assert.equal(traceLabVals(c, Date.now()).tlWriteState('verdict:turn-1'), null);
  await c.tlWrite(() => Promise.reject(Object.assign(new Error('stale'), { status: 409 })), { key: 'k2' });
  assert.equal(c.state.tlWrites.k2.error, 'Changed in another tab — reload this answer.');
  assert.equal(c.state.tlWrites.k2.canRetry, false);
  const quiet = await c.tlWrite(() => Promise.reject(Object.assign(new Error('moved'), { staleScope: true })), { key: 'k3' });
  assert.equal(quiet.stale, true);
  assert.equal(c.state.tlWrites.k3, undefined);
});

test('a ?tl= link is read at boot and opened after sign-in, for an admin only', () => {
  window.location.search = '?tl=conversations&run=live&conv=s%3Aabc&turn=turn-1';
  const c = ctl({ view: 'home', tlView: 'overview' });
  c.tlLinkFromUrl();
  assert.deepEqual(c._tlLinked, { view: 'conversations', run: 'live', conv: 's:abc', turn: 'turn-1' });
  c.tlOpenLinked();
  assert.equal(c.state.view, 'traces');
  assert.equal(c.state.tlView, 'conversations');
  assert.equal(c.state.tlConvKey, 's:abc');
  assert.deepEqual(c.calls, [['tlLoadLists'], ['tlOpenConversation', 's:abc', 'turn-1']]);
  assert.equal(c._tlLinked, null);
  c.tlStopPoll();
  const user = ctl({ view: 'home', account: { role: 'user' } });
  user.tlLinkFromUrl();
  user.tlOpenLinked();
  assert.equal(user.state.view, 'home');
  assert.equal(window.location.search, '');
});

test('the reset patch is fresh every time and covers the Overview\'s keys too', () => {
  const a = tlResetPatch();
  const b = tlResetPatch();
  assert.notEqual(a.tlFilters, b.tlFilters);
  assert.notEqual(a.tlWrites, b.tlWrites);
  assert.deepEqual(a.tlFilters, { range: '7d', channel: '', status: '', q: '' });
  for (const k of ['tpTurns', 'tpTurn', 'tpStats', 'tpLoadedAt', 'tpErr']) assert.ok(k in a, k);
  assert.equal(a.tlView, 'overview');
  assert.equal(a.tlRunId, 'live');
  const c = ctl();
  const seq = c._tlSeq || 0;
  c.tlResetPatch();
  assert.equal(c._tlSeq, seq + 1);
});

test('the filter bar resets paging and reads the lists again; the search box applies on submit', () => {
  const c = ctl({ tlView: 'conversations', tlConvsPage: 3, tlConvsCursors: [null, 'a', 'b', 'c'] });
  const v = traceLabVals(c, Date.now());
  v.tlRanges.find((r) => r.key === '30d').pick();
  assert.equal(c.state.tlFilters.range, '30d');
  assert.equal(c.state.tlConvsPage, 0);
  assert.deepEqual(c.state.tlConvsCursors, [null]);
  v.tlSetQueryDraft({ target: { value: 'boilers' } });
  assert.equal(c.state.tlFilters.q, '');
  traceLabVals(c, Date.now()).tlSearch();
  assert.equal(c.state.tlFilters.q, 'boilers');
  assert.deepEqual(c.calls, [['tlLoadLists'], ['tlLoadLists']]);
  assert.equal(c.tlQuery().q, 'boilers');
  assert.equal(c.tlQuery().run, 'live');
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabShell.test.mjs`
Expected: `ERR_MODULE_NOT_FOUND ... src/logic/traceLab.js`.

- [ ] **Step 3: Implement**

In `apps/frontend/src/logic/tracesPage.js`:

old:
```js
const sinceFor = (key, now) => {
```
new:
```js
export const sinceFor = (key, now) => {
```

Create `apps/frontend/src/logic/traceLab.js`:

```js
// traceLab — Hoist Trace Lab's shell inside Administration › Hoist Traces: the tabs a role may use, the URL and
// reload state, the 45-second update check, the keyboard, and the one write helper (spec
// docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md, "Frontend: the Trace Lab" §1-§2). Methods are mixed
// into HoistraLogic.prototype; `this` is the controller. The lists, the open conversation and the model calls are
// logic/traceLabConversations.js; the timeline cards are logic/traceLabTimeline.js.
import { traceLabApi } from '../api/traceLab.js';
import { isStaleScope } from '../api/client.js';
import { canAdmin } from './auth.js';
import { RANGES, sinceFor } from './tracesPage.js';

export const TL_VIEWS = ['overview', 'conversations', 'runs', 'models'];
export const TL_TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'conversations', label: 'Conversations' },
  { key: 'runs', label: 'Runs' },
  { key: 'models', label: 'Model calls' }
];
export const TL_POLL_MS = 45000;
export const TL_POLLED_VIEWS = ['conversations', 'runs'];
export const TL_CHANNELS = ['chat', 'ui_action', 'support', 'upload', 'correction', 'report', 'workflow', 'golden'];
export const CHANNEL_LABELS = Object.freeze({
  chat: 'Asked in the chat', ui_action: 'From a page', support: 'Support', upload: 'Upload', correction: 'Correction',
  report: 'Report', workflow: 'Workflow step', golden: 'Golden run'
});
export const TL_FILTER_DEFAULTS = Object.freeze({ range: '7d', channel: '', status: '', q: '' });
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KEY_RE = /^[str]:[^\u0000-\u001f]{1,200}$/;
const TURN_RE = /^[\w.:-]{1,80}$/;
const SHORTCUTS = [['j / k', 'Next / previous conversation'], ['n / p', 'Next / previous answer in this conversation'],
  ['?', 'Show or hide these shortcuts'], ['Esc', 'Close a panel']];

// Every tl* key at its default. A later milestone that adds a tl* key adds it here, or the company-switch test
// (test/traceLabShell.test.mjs) fails.
const TL_SCALARS = {
  tlView: 'overview', tlRunId: 'live', tlConvKey: null, tlTurnId: null, tlQDraft: '',
  tlConvs: null, tlConvsLoading: false, tlConvsErr: '', tlConvsLoadedAt: null, tlConvsTotal: null, tlConvsPage: 0, tlWatermark: null,
  tlFacets: null, tlFacetsErr: '',
  tlConv: null, tlConvLoading: false, tlConvErr: '', tlConvLoadedAt: null,
  tlRuns: null, tlRunsLoading: false, tlRunsErr: '', tlRunsLoadedAt: null, tlGoldenAvailable: false,
  tlModelCalls: null, tlModelCallsAgg: null, tlModelCallsLoading: false, tlModelCallsErr: '', tlModelCallsLoadedAt: null,
  tlModelCallsPage: 0, tlModelCallsRetention: null, tlModelCall: null,
  tlUpdates: null, tlUpdatedAt: null, tlPollErr: '', tlShortcuts: false
};
// The Overview's own keys (logic/tracesPage.js) join the same reset: they leaked across company switches.
const TP_SCALARS = {
  tpStats: null, tpTurn: null, tpSpanId: null, tpLoading: false, tpErr: '', tpLoadedAt: null, tpAvailable: true,
  tpCanManage: false, tpQuery: '', tpStatus: '', tpRange: '7d', tpExporting: false
};

function freshPatch() {
  return Object.assign({}, TL_SCALARS, TP_SCALARS, {
    tlFilters: Object.assign({}, TL_FILTER_DEFAULTS), tlConvsCursors: [null], tlModelCallsCursors: [null],
    tlWrites: {}, tlOpenCards: {}, tpTurns: []
  });
}

export function tlResetPatch() { return freshPatch(); }

// A company to review in: an admin's own, or the one a superadmin is viewing as. Without one the Trace Lab
// routes answer 400 name_a_company, so those tabs are not offered.
export function tlHasCompany(s) {
  const a = s && s.account;
  if (!a) return false;
  if (a.role === 'superadmin') return !!s.viewOrgId;
  return a.role === 'admin';
}

export function tlTabsFor(s) { return TL_TABS.filter((t) => t.key === 'overview' || tlHasCompany(s)); }
const allowed = (s, view) => tlTabsFor(s).some((t) => t.key === view);

export function tlQueryString(search, st) {
  const p = new URLSearchParams(search || '');
  ['tl', 'run', 'conv', 'turn'].forEach((k) => p.delete(k));
  if (st) {
    p.set('tl', st.view);
    p.set('run', st.run || 'live');
    if (st.conv) p.set('conv', st.conv);
    if (st.turn) p.set('turn', st.turn);
  }
  const s = p.toString();
  return s ? '?' + s : '';
}

export function tlParseLink(search) {
  let p;
  try { p = new URLSearchParams(search || ''); } catch (e) { return null; }
  const view = p.get('tl');
  if (!view || TL_VIEWS.indexOf(view) < 0) return null;
  const run = p.get('run') || 'live';
  const conv = p.get('conv');
  const turn = p.get('turn');
  return { view, run: run === 'live' || UUID_RE.test(run) ? run : 'live',
    conv: conv && KEY_RE.test(conv) ? conv : null, turn: turn && TURN_RE.test(turn) ? turn : null };
}

const hhmm = (ms) => { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
const RESET_PAGING = { tlConvsCursors: [null], tlConvsPage: 0, tlModelCallsCursors: [null], tlModelCallsPage: 0, tlUpdates: null };

export const traceLabMethods = {
  tlCanReview() { return tlHasCompany(this.state); },

  // The nav entry and the ?tl= link land here: Hoist Traces on the tab asked for, else the last one open.
  tlOpen(view) {
    const s = this.state;
    const want = view && allowed(s, view) ? view : (allowed(s, s.tlView) ? s.tlView : 'overview');
    if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') window.scrollTo(0, 0);
    this.setState({ view: 'traces', role: 'admin', navOpen: true, detail: null, tlView: want, tpTurn: null, tpSpanId: null });
    this.tlReadView();
  },
  tlSetView(view) {
    if (!allowed(this.state, view) || view === this.state.tlView) return;
    this.setState({ tlView: view, tlModelCall: null });
    this.tlReadView();
  },
  // Reads what the open tab shows. Mount calls it on a reload that lands on Hoist Traces (core.js); it only reads.
  tlReadView() {
    if (!allowed(this.state, this.state.tlView)) this.setState({ tlView: 'overview' });
    const view = this.state.tlView;
    this.tlSyncUrl();
    if (view === 'overview') { this.tlStopPoll(); this.tpLoad(); return; }
    this.tlLoadLists();
    if (view === 'conversations' && this.state.tlConvKey) this.tlOpenConversation(this.state.tlConvKey, this.state.tlTurnId);
    this.tlStartPoll();
  },
  tlSetFilter(patch) {
    const f = Object.assign({}, TL_FILTER_DEFAULTS, this.state.tlFilters || {}, patch || {});
    this.setState(Object.assign({ tlFilters: f, tlQDraft: f.q }, RESET_PAGING));
    this.tlLoadLists();
  },
  tlSetQueryDraft(text) { this.setState({ tlQDraft: String(text || '').slice(0, 200) }); },
  tlSearch() { this.tlSetFilter({ q: String(this.state.tlQDraft || '').trim() }); },
  tlSetRun(runId) {
    const run = runId === 'live' || UUID_RE.test(String(runId || '')) ? String(runId) : 'live';
    this.setState(Object.assign({ tlRunId: run }, RESET_PAGING));
    this.tlSyncUrl();
    this.tlLoadLists();
  },
  tlQuery() {
    const f = Object.assign({}, TL_FILTER_DEFAULTS, this.state.tlFilters || {});
    const q = { since: sinceFor(f.range), run: this.state.tlRunId || 'live' };
    if (f.channel) q.channel = f.channel;
    if (f.status) q.status = f.status;
    if (f.q) q.q = f.q;
    return q;
  },

  // ── the update check: one cheap count every 45 s, never a re-render of what is open ──
  tlStartPoll() {
    if (this._tlPoll) return;
    this._tlPoll = setInterval(() => { this.tlPoll(false); }, TL_POLL_MS);
  },
  tlStopPoll() { clearInterval(this._tlPoll); this._tlPoll = null; },
  async tlPoll(force) {
    const s = this.state;
    if (s.view !== 'traces' || !s.signedIn) { this.tlStopPoll(); return; }
    if (TL_POLLED_VIEWS.indexOf(s.tlView) < 0) return;
    const hidden = typeof document !== 'undefined' && document.visibilityState && document.visibilityState !== 'visible';
    if (hidden && !force) return;
    if (!s.tlWatermark) return;
    const seq = this._tlSeq || 0;
    try {
      const out = await traceLabApi.conversationUpdates(Object.assign(this.tlQuery(), { watermark: s.tlWatermark }));
      if (seq !== (this._tlSeq || 0) || this.state.view !== 'traces') return;
      const fresh = Number(out && out.new_turns) || 0;
      this.setState({ tlUpdates: { newTurns: fresh, newConversations: Number(out && out.new_conversations) || 0 }, tlUpdatedAt: Date.now(), tlPollErr: '' });
      if (!fresh) return;
      // New rows merge in by key on page one; the open conversation, its panels and the side panel are never touched.
      if (this.state.tlView === 'conversations' && !(this.state.tlConvsPage > 0)) this.tlLoadConversations({ merge: true });
      if (this.state.tlView === 'runs') this.tlLoadRuns();
    } catch (e) {
      if (isStaleScope(e) || seq !== (this._tlSeq || 0)) return;
      this.setState({ tlPollErr: (e && e.message) || String(e) });
    }
  },
  tlRefresh() {
    this.setState(Object.assign({}, RESET_PAGING));
    if (this.state.tlView === 'overview') this.tpLoad(); else this.tlLoadLists();
  },

  // ── URL state: ?tl=<view>&run=<run>&conv=<key>&turn=<turn> while the page is open ──
  tlSyncUrl() {
    if (this.state.view !== 'traces') return;
    try {
      const s = this.state;
      const conv = s.tlView === 'conversations';
      const qs = tlQueryString(window.location.search, { view: s.tlView, run: s.tlRunId || 'live', conv: conv ? s.tlConvKey : null, turn: conv ? s.tlTurnId : null });
      if (qs !== (window.location.search || '')) window.history.replaceState(null, '', (window.location.pathname || '/') + qs);
    } catch (e) { /* no history API (tests, old browsers): the reload slice still restores the tab */ }
  },
  tlScrubUrl() {
    try {
      const qs = tlQueryString(window.location.search, null);
      if (qs !== (window.location.search || '')) window.history.replaceState(null, '', (window.location.pathname || '/') + qs);
    } catch (e) { /* nothing to scrub */ }
  },
  // Read once at boot, like the cron link (cronsPage.js cpLinkFromUrl); opened once signed in (auth.js authEnter).
  tlLinkFromUrl() {
    try { this._tlLinked = tlParseLink(window.location.search); } catch (e) { this._tlLinked = null; }
  },
  tlOpenLinked() {
    const link = this._tlLinked;
    if (!link || !this.state.signedIn) return;
    this._tlLinked = null;
    if (!canAdmin(this.state.account)) { this.tlScrubUrl(); return; }
    this.setState({ tlRunId: link.run, tlConvKey: link.conv, tlTurnId: link.turn });
    this.tlOpen(link.view);
  },

  // ── keyboard: never while typing, never with a modifier (the repo's textarea bug) ──
  tlKey(e) {
    if (!e || this.state.view !== 'traces' || this.state.tlView === 'overview') return;
    const t = e.target;
    const tag = t && t.tagName ? String(t.tagName).toUpperCase() : '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (t && (t.isContentEditable || (typeof t.closest === 'function' && t.closest('[contenteditable]')))) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (e.shiftKey && k !== '?') return;
    if (k === '?') { this.tlToggleShortcuts(); return; }
    if (k === 'Escape') {
      if (this.state.tlShortcuts || this.state.tlModelCall) this.setState({ tlShortcuts: false, tlModelCall: null });
      return;
    }
    if (this.state.tlView !== 'conversations') return;
    if (k === 'j' || k === 'k') { if (e.preventDefault) e.preventDefault(); this.tlStepConversation(k === 'j' ? 1 : -1); return; }
    if (k === 'n' || k === 'p') { if (e.preventDefault) e.preventDefault(); this.tlStepTurn(k === 'n' ? 1 : -1); }
  },
  tlToggleShortcuts() { this.setState((p) => ({ tlShortcuts: !p.tlShortcuts })); },

  // ── writes never fail silently: an inline error with Retry; a 409 says another tab changed it (M2 uses this) ──
  async tlWrite(fn, opts) {
    const o = opts || {};
    const key = o.key || 'write';
    const retry = o.retry !== false;
    this._tlWriteFns = this._tlWriteFns || {};
    this._tlWriteFns[key] = { fn, opts: o };
    const seq = this._tlSeq || 0;
    const drop = () => this.setState((p) => { const w = Object.assign({}, p.tlWrites); delete w[key]; return { tlWrites: w }; });
    this.setState((p) => ({ tlWrites: Object.assign({}, p.tlWrites, { [key]: { busy: true, error: '', conflict: false, canRetry: false } }) }));
    try {
      const out = await fn();
      if (seq !== (this._tlSeq || 0)) { drop(); return { ok: false, stale: true }; }
      delete this._tlWriteFns[key];
      drop();
      return { ok: true, out };
    } catch (e) {
      if (isStaleScope(e) || seq !== (this._tlSeq || 0)) { drop(); return { ok: false, stale: true }; }
      const conflict = !!(e && e.status === 409);
      const error = conflict ? 'Changed in another tab — reload this answer.' : 'Could not save — ' + ((e && e.message) || String(e));
      this.setState((p) => ({ tlWrites: Object.assign({}, p.tlWrites, { [key]: { busy: false, error, conflict, canRetry: retry && !conflict } }) }));
      return { ok: false, error, conflict };
    }
  },
  tlRetryWrite(key) {
    const w = this._tlWriteFns && this._tlWriteFns[key];
    return w ? this.tlWrite(w.fn, w.opts) : Promise.resolve({ ok: false });
  },

  // Company switch and sign-out (core.js resetLiveData, auth.js authSignedOut): every tl* and tp* key back to its
  // default, the update check stopped, and any reply still in flight disowned.
  tlResetPatch() {
    this._tlSeq = (this._tlSeq || 0) + 1;
    this.tlStopPoll();
    return freshPatch();
  }
};

export function traceLabVals(c, now) {
  const s = c.state;
  const tabs = tlTabsFor(s);
  const view = tabs.some((t) => t.key === s.tlView) ? s.tlView : 'overview';
  const f = Object.assign({}, TL_FILTER_DEFAULTS, s.tlFilters || {});
  let line = '';
  if (s.tlPollErr) line = "Couldn't refresh — retry" + (s.tlUpdatedAt ? ' · last updated ' + hhmm(s.tlUpdatedAt) : '');
  else if (s.tlUpdatedAt) line = 'Updated ' + hhmm(s.tlUpdatedAt) + (s.tlUpdates && s.tlUpdates.newTurns ? ' · ' + s.tlUpdates.newTurns + ' new' : '');
  const runs = (s.tlRuns || []).filter((r) => r && r.run_id);
  const writes = s.tlWrites || {};
  return {
    isTraceLab: !!s.signedIn && s.view === 'traces',
    tlTabs: tabs.map((t) => ({ key: t.key, label: t.label, on: t.key === view, pick: () => c.tlSetView(t.key) })),
    tlView: view, tlIsOverview: view === 'overview', tlHasCompany: tlHasCompany(s),
    tlRanges: RANGES.map((r) => ({ key: r.key, label: r.label, on: f.range === r.key, pick: () => c.tlSetFilter({ range: r.key }) })),
    tlChannelOptions: [{ value: '', label: 'Every source' }].concat(TL_CHANNELS.map((k) => ({ value: k, label: CHANNEL_LABELS[k] }))),
    tlChannel: f.channel, tlSetChannel: (e) => c.tlSetFilter({ channel: e && e.target ? e.target.value : '' }),
    tlStatuses: [{ key: '', label: 'All' }, { key: 'ok', label: 'Answered' }, { key: 'error', label: 'Failed' }]
      .map((x) => ({ key: x.key, label: x.label, on: f.status === x.key, pick: () => c.tlSetFilter({ status: x.key }) })),
    tlRunOptions: (runs.length ? runs : [{ run_id: 'live', kind: 'live' }])
      .map((r) => ({ value: r.run_id, label: r.kind === 'live' ? 'All live traffic' : String(r.name || r.run_id) })),
    tlRunId: s.tlRunId || 'live', tlSetRun: (e) => c.tlSetRun(e && e.target ? e.target.value : 'live'),
    tlQueryDraft: s.tlQDraft || '', tlSetQueryDraft: (e) => c.tlSetQueryDraft(e && e.target ? e.target.value : ''),
    tlSearch: () => c.tlSearch(),
    tlUpdateLine: line, tlPolled: TL_POLLED_VIEWS.indexOf(view) > -1, tlPollFailed: !!s.tlPollErr, tlRefresh: () => c.tlRefresh(),
    tlKey: c._tlKeyFn || (c._tlKeyFn = (e) => c.tlKey(e)),
    tlShortcutsOpen: !!s.tlShortcuts, tlToggleShortcuts: () => c.tlToggleShortcuts(), tlShortcutRows: SHORTCUTS,
    tlWriteState: (key) => (writes[key] ? Object.assign({}, writes[key], { retry: () => c.tlRetryWrite(key) }) : null)
  };
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabShell.test.mjs test/tracesPage.test.mjs`
Expected: all pass (`sinceFor`'s export changes nothing for the existing page).

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 14: Conversations, runs and model calls — loaders and view models

**Files:**
- Create: `apps/frontend/src/logic/traceLabConversations.js`
- Test: `apps/frontend/test/traceLabConversations.test.mjs`

**Interfaces:**
- Consumes: Task 11 `traceLabApi`; Task 12 `toCards`, `modelCallCard`, `labelFor`, `PURPOSES`, `UNAVAILABLE`; Task 13 `CHANNEL_LABELS`, `TL_FILTER_DEFAULTS`, and controller methods `tlQuery`, `tlSyncUrl`, `tlSetRun`, `tlStartPoll`, `tlReadView`; `fmtMs/fmtTok/fmtUsd/fmtWhen/sinceFor` (tracesPage.js).
- Produces: `TL_PAGE_SIZE = 25`, `TL_CALLS_PAGE_SIZE = 50`, `groupingChip(conv) -> string|null`, `mergeByKey(oldRows, freshRows)`, `FLAG_NOTES`, `flagNotes(flags) -> string[]`; `traceLabConversationsMethods` = `tlLoadLists, tlLoadConversations({merge}?), tlConvsPageStep(delta), tlLoadFacets, tlOpenConversation(key, turnId?), tlOpenTurn(turnId), tlOpenTurnById(turnId), tlStepConversation(delta), tlStepTurn(delta), tlSetCardOpen(id, open), tlLoadRuns, tlPickRun(runId), tlLoadModelCalls, tlModelCallsPageStep(delta), tlOpenModelCall(spanId), tlCloseModelCall`; `traceLabConversationsVals(c, now)` (keys: `tlList, tlListLoading, tlListErr, tlListEmpty, tlTotalLabel, tlPageLabel, tlCanPrev, tlCanNext, tlPrevPage, tlNextPage, tlConvOpen, tlConvLoading, tlConvErr, tlConvView{key, title, chips, answers, truncatedNote, when, who, cost, latency, notes, rating, cards}, tlCardOpen(id, dflt), tlSetCardOpen(id, open), tlRunsView, tlRunsLoading, tlRunsErr, tlGoldenNote, tlCalls, tlCallsHeader, tlCallsNote, tlCallsLoading, tlCallsErr, tlCallsEmpty, tlCallsCanPrev, tlCallsCanNext, tlCallsPrev, tlCallsNext, tlCallPanel{loading, error, card, close, openConversation}`).

- [ ] **Step 1: Write the failing test**

Create `apps/frontend/test/traceLabConversations.test.mjs`:

```js
// traceLabConversations — the list pages by cursor with a total, synthetic and legacy groupings carry their chip,
// the 45 s check merges new rows without closing what is open, replies from before a company switch are dropped,
// an Overview answer opens in its conversation, and model calls read with "unavailable" where nothing was recorded.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} }, scrollTo: () => {} };
globalThis.document = { visibilityState: 'visible' };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  requests.push(u);
  const h = handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u) : (h || [404, { detail: 'not mocked' }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const { traceLabMethods, tlResetPatch } = await import('../src/logic/traceLab.js');
const { traceLabConversationsMethods, traceLabConversationsVals, groupingChip, mergeByKey, flagNotes } = await import('../src/logic/traceLabConversations.js');
const { setActingOrg } = await import('../src/api/client.js');
const P = '/backend/deep-agents/api/trace-lab';
const NOW = Date.parse('2026-10-15T12:00:00Z');
const tick = () => new Promise((r) => setTimeout(r, 0));
const row = (key, over) => Object.assign({ key, session_id: key.slice(2), session_kind: 'observed', session_label: 'Chat ' + key, turns_in_window: 2,
  first_question: 'Question ' + key, last_turn_at: '2026-10-15T11:00:00Z', failed_turns: 0, asker: { email: 'fm@example.com' }, flags: [] }, over || {});

function ctl(state) {
  const c = {};
  c.state = Object.assign(tlResetPatch(), { signedIn: true, view: 'traces', account: { role: 'admin' }, tlView: 'conversations' }, state || {});
  c.setState = (patch) => { const p = typeof patch === 'function' ? patch(c.state) : patch; c.state = Object.assign({}, c.state, p); };
  Object.assign(c, traceLabMethods, traceLabConversationsMethods);
  c.tpLoad = () => {};
  return c;
}

beforeEach(() => { handlers = {}; requests = []; setActingOrg(null); });

test('the list pages by cursor, 25 at a time, and says which page of how many', async () => {
  handlers[P + '/conversations'] = (u) => {
    assert.equal(u.searchParams.get('limit'), '25');
    return u.searchParams.get('cursor') === 'c2'
      ? [200, { ok: true, total: 30, next_cursor: null, watermark: 'w', conversations: Array.from({ length: 5 }, (_, i) => row('s:p2-' + i)) }]
      : [200, { ok: true, total: 30, next_cursor: 'c2', watermark: 'w', conversations: Array.from({ length: 25 }, (_, i) => row('s:p1-' + i)) }];
  };
  const c = ctl();
  await c.tlLoadConversations();
  let v = traceLabConversationsVals(c, NOW);
  assert.equal(v.tlList.length, 25);
  assert.equal(v.tlPageLabel, 'Page 1 of 2');
  assert.equal(v.tlTotalLabel, '30 conversations');
  assert.equal(v.tlCanNext, true);
  assert.equal(v.tlCanPrev, false);
  v.tlNextPage();
  await tick();
  v = traceLabConversationsVals(c, NOW);
  assert.equal(requests.at(-1).searchParams.get('cursor'), 'c2');
  assert.equal(v.tlPageLabel, 'Page 2 of 2');
  assert.equal(v.tlCanNext, false);
  assert.equal(v.tlList[0].question, 'Question s:p2-0');
  v.tlPrevPage();
  await tick();
  assert.equal(requests.at(-1).searchParams.has('cursor'), false);
  assert.equal(traceLabConversationsVals(c, NOW).tlPageLabel, 'Page 1 of 2');
});

test('synthetic and legacy groupings carry a chip; an observed session does not', () => {
  assert.equal(groupingChip({ key: 's:report-1', session_id: 'report-1', session_kind: 'synthetic' }), 'Grouped by report run');
  assert.equal(groupingChip({ key: 'r:turn-1', session_id: 'rerun', session_kind: 'synthetic' }), 'Grouped by re-run');
  assert.equal(groupingChip({ key: 's:golden-x', session_id: 'golden-x', session_kind: 'synthetic' }), 'Grouped by golden run');
  assert.equal(groupingChip({ key: 's:docs-1', session_id: 'docs-1', session_kind: 'synthetic' }), 'Grouped by document upload');
  assert.equal(groupingChip({ key: 's:hs-1', session_id: 'hs-1', session_kind: 'inferred' }), 'Legacy grouping');
  assert.equal(groupingChip({ key: 't:turn-9', session_id: null, session_kind: 'inferred' }), 'Single answer, no conversation recorded');
  assert.equal(groupingChip({ key: 's:abc', session_id: 'abc', session_kind: 'observed' }), null);
  assert.deepEqual(flagNotes(['legacy_provenance', 'gate_inferred:degraded', 'gate_inferred:blocked', 'unknown_flag']).length, 2);
});

test('auto-refresh merges new rows by key and never closes the open conversation or its panels', async () => {
  const open = { key: 's:a', session_label: 'Chat a', turns: [], active: { id: 't1', meta: {}, messages: [], usage: {}, flags: [], cot_boundary: 'x' } };
  const panel = { spanId: 'sp1', loading: false, error: '', card: { label: 'tool decision' } };
  const c = ctl({ tlConvs: [row('s:a'), row('s:b')], tlConvKey: 's:a', tlConv: open, tlOpenCards: { 't1:root:user': true },
    tlModelCall: panel, tlWatermark: 'w0', tlUpdatedAt: NOW });
  handlers[P + '/conversations/updates'] = [200, { ok: true, new_turns: 2, new_conversations: 1 }];
  handlers[P + '/conversations'] = [200, { ok: true, total: 3, next_cursor: null, watermark: 'w1', conversations: [row('s:c'), row('s:a', { turns_in_window: 3 })] }];
  await c.tlPoll(true);
  await tick();
  assert.deepEqual(c.state.tlConvs.map((r) => r.key), ['s:c', 's:a', 's:b']);
  assert.equal(c.state.tlConvs[1].turns_in_window, 3);
  assert.equal(c.state.tlConv, open);
  assert.equal(c.state.tlConvKey, 's:a');
  assert.deepEqual(c.state.tlOpenCards, { 't1:root:user': true });
  assert.equal(c.state.tlModelCall, panel);
  assert.deepEqual(c.state.tlUpdates, { newTurns: 2, newConversations: 1 });
  assert.equal(c.state.tlWatermark, 'w1');
  assert.equal(requests.some((u) => u.pathname.startsWith(P + '/conversations/s')), false, 'the open conversation is not read again');
  assert.deepEqual(mergeByKey([row('s:x')], []).map((r) => r.key), ['s:x']);
});

test('a reply that lands after a company switch is dropped, and a stale scope stays quiet', async () => {
  let release;
  handlers[P + '/conversations'] = () => new Promise((r) => { release = () => r([200, { ok: true, total: 1, conversations: [row('s:old')] }]); });
  const c = ctl();
  const pending = c.tlLoadConversations();
  await tick();
  c.setState(c.tlResetPatch());
  release();
  await pending;
  assert.equal(c.state.tlConvs, null);
  const d = ctl();
  const pending2 = d.tlLoadConversations();
  await tick();
  setActingOrg('org-2');                                // the company changed while the read was out
  release();
  await pending2;
  assert.equal(d.state.tlConvsErr, '');
  assert.equal(d.state.tlConvs, null);
});

test('an answer opened from the Overview lands on its conversation with that answer active', async () => {
  handlers[P + '/turns/turn-7'] = [200, { ok: true, trace: { id: 'turn-7' }, conversation_key: 's:k' }];
  handlers[P + '/conversations'] = [200, { ok: true, total: 0, conversations: [], watermark: 'w' }];
  handlers[P + '/conversations/facets'] = [200, { ok: true, channels: [], channel_sources: [], session_kinds: [], models: [] }];
  handlers[P + '/runs'] = [200, { ok: true, golden_available: false, runs: [] }];
  handlers[P + '/conversations/s%3Ak'] = (u) => {
    assert.equal(u.searchParams.get('turn'), 'turn-7');
    return [200, { ok: true, conversation: { key: 's:k', session_kind: 'observed', session_label: 'Boilers', turns: [
      { turn_id: 'turn-6', turn_index: 1, question: 'First?', ok: true }, { turn_id: 'turn-7', turn_index: 2, question: 'Second?', ok: false }],
      active: { id: 'turn-7', timestamp: '2026-10-15T11:00:00Z', models: ['claude-sonnet-5'], usage: { cost_usd: 0.02, latency_ms: 900, tool_calls: 1 },
        meta: { channel: 'ui_action', channel_source: 'client', turn_index: 2, turn_count: 2, attempt: 2, asker: { email: 'fm@example.com' } },
        feedback: { rating: 'down', comment: 'Wrong period', by_asker: true }, flags: ['legacy_provenance'], cot_boundary: 'B',
        messages: [{ id: 'turn-7:r:user', role: 'user', text: 'Second?' }, { id: 'turn-7:r:answer', role: 'assistant', subtype: 'answer', text: 'Four.' }] } } }];
  };
  const c = ctl({ view: 'home', tlView: 'overview' });
  await c.tlOpenTurnById('turn-7');
  await tick(); await tick();
  try {
    assert.equal(c.state.view, 'traces');
    assert.equal(c.state.tlView, 'conversations');
    assert.equal(c.state.tlConvKey, 's:k');
    const v = traceLabConversationsVals(c, NOW).tlConvView;
    assert.equal(v.title, 'Boilers');
    assert.deepEqual(v.chips, ['From a page', 'Answer 2 of 2', 'Attempt 2', 'claude-sonnet-5', '1 tool']);
    assert.deepEqual(v.answers.map((a) => [a.label, a.on, a.failed]), [['Answer 1', false, false], ['Answer 2', true, true]]);
    assert.equal(v.rating.text, 'The asker rated this answer down');
    assert.equal(v.notes[0], 'Where this question came from is inferred: it was asked before sources were recorded.');
    assert.deepEqual(v.cards.map((k) => k.kind), ['user', 'answer']);
    assert.equal(traceLabConversationsVals(c, NOW).tlCardOpen('nope', true), true);
    c.tlSetCardOpen('turn-7:r:user', false);
    assert.equal(traceLabConversationsVals(c, NOW).tlCardOpen('turn-7:r:user', true), false);
  } finally { c.tlStopPoll(); }
});

test('stepping walks the list and the answers of the open conversation', () => {
  const c = ctl({ tlConvs: [row('s:a'), row('s:b')], tlConvKey: 's:a',
    tlConv: { key: 's:a', turns: [{ turn_id: 't1' }, { turn_id: 't2' }] }, tlTurnId: 't1' });
  const opened = [];
  c.tlOpenConversation = (key, turn) => opened.push([key, turn]);
  c.tlStepConversation(1);
  c.tlStepConversation(-1);                           // from s:a, nothing before it
  c.tlStepTurn(1);
  c.tlStepTurn(-1);
  assert.deepEqual(opened, [['s:b', undefined], ['s:a', 't2']]);
});

test('model calls read with an aggregate header, "unavailable" where nothing was recorded, and open in a side panel', async () => {
  handlers[P + '/model-calls'] = [200, { ok: true, next_cursor: null, retention_days: 90,
    aggregate: { calls: 3120, errors: 14, latency_p50_ms: 2210, latency_p95_ms: 9140, input_tokens: 120000, output_tokens: 3000, cache_read_tokens: 0, total_tokens: 123000, cost_usd: 48.11, unpriced_calls: 6, by_model: [] },
    calls: [
      { span_id: 'sp1', turn_id: 't1', started_at: '2026-10-15T11:00:00Z', model: 'claude-sonnet-5', label: 'tool decision', latency_ms: 1880, input_tokens: 900, output_tokens: 40, cost_usd: 0.0041, ok: true, question_excerpt: 'Boilers?' },
      { span_id: 'sp2', turn_id: 't1', started_at: '2026-10-15T11:00:01Z', model: null, label: 'planner', latency_ms: null, input_tokens: null, output_tokens: null, cost_usd: null, ok: false, question_excerpt: '' }] }];
  handlers[P + '/model-calls/sp2'] = [200, { ok: true, turn_id: 't1', conversation_key: 's:a',
    model_call: { model: null, label: 'planner', latency_s: null, usage: {}, total_cost: null, cost_status: 'unavailable', request: null, response: null, withheld: [], cot_note: 'N' } }];
  const c = ctl({ tlView: 'models' });
  await c.tlLoadModelCalls();
  let v = traceLabConversationsVals(c, NOW);
  assert.equal(v.tlCallsHeader, '3,120 calls · p50 2.21 s · p95 9.14 s · 120k in · 3.0k out · $48.11 · 14 failed · 6 unpriced');
  assert.equal(v.tlCallsNote, 'Model calls are kept for 90 days; older calls are not in these figures.');
  assert.deepEqual([v.tlCalls[0].purpose, v.tlCalls[0].tokens, v.tlCalls[0].cost], ['tool decision', '900 in · 40 out', '$0.0041']);
  assert.deepEqual([v.tlCalls[1].model, v.tlCalls[1].purpose, v.tlCalls[1].latency, v.tlCalls[1].tokens, v.tlCalls[1].cost, v.tlCalls[1].question],
    ['unavailable', 'Planner', 'unavailable', 'unavailable', 'unavailable', '—']);
  v.tlCalls[1].open();
  await tick();
  v = traceLabConversationsVals(c, NOW);
  assert.equal(v.tlCallPanel.card.header, 'Model call · unavailable · unavailable · unavailable · unavailable');
  assert.equal(typeof v.tlCallPanel.openConversation, 'function');
  v.tlCallPanel.close();
  assert.equal(traceLabConversationsVals(c, NOW).tlCallPanel, null);
});

test('the runs list names the live pseudo-run and picking it opens its conversations', async () => {
  handlers[P + '/runs'] = [200, { ok: true, golden_available: false, runs: [{ run_id: 'live', kind: 'live', name: 'All live traffic', recorded: false,
    started_at: '2026-10-14T09:00:00Z', ended_at: '2026-10-15T11:00:00Z', conversation_count: 412, turn_count: 1290, models: ['claude-sonnet-5'], cost_usd: 61.42 }] }];
  const c = ctl({ tlView: 'runs' });
  await c.tlLoadRuns();
  const v = traceLabConversationsVals(c, NOW);
  assert.deepEqual([v.tlRunsView[0].name, v.tlRunsView[0].conversations, v.tlRunsView[0].answers, v.tlRunsView[0].cost], ['All live traffic', '412', '1290', '$61.42']);
  assert.equal(v.tlGoldenNote, 'No golden runs yet.');
  const loaded = [];
  c.tlLoadLists = () => loaded.push(c.state.tlView);
  v.tlRunsView[0].open();
  try {
    assert.equal(c.state.tlView, 'conversations');
    assert.equal(c.state.tlRunId, 'live');
    assert.deepEqual(loaded, ['conversations']);
  } finally { c.tlStopPoll(); }
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabConversations.test.mjs`
Expected: `ERR_MODULE_NOT_FOUND ... src/logic/traceLabConversations.js`.

- [ ] **Step 3: Implement**

Create `apps/frontend/src/logic/traceLabConversations.js`:

```js
// traceLabConversations — the Trace Lab's reads: the conversation list (filtered, cursor-paged, merged on the
// 45 s check), one conversation with its active answer, the runs list and model calls (spec "Frontend: the Trace
// Lab" §4.2-§4.4). Read only. Every loader keeps its old rows on screen while it reads, drops a reply that lands
// after a company switch (this._tlSeq, bumped by tlResetPatch), and stays quiet on a stale scope.
import { traceLabApi } from '../api/traceLab.js';
import { isStaleScope } from '../api/client.js';
import { fmtMs, fmtTok, fmtUsd, fmtWhen, sinceFor } from './tracesPage.js';
import { CHANNEL_LABELS, TL_FILTER_DEFAULTS } from './traceLab.js';
import { labelFor, modelCallCard, PURPOSES, toCards, UNAVAILABLE } from './traceLabTimeline.js';

export const TL_PAGE_SIZE = 25;
export const TL_CALLS_PAGE_SIZE = 50;

const msg = (e) => (e && e.message) || String(e);
const current = (c, seq) => seq === (c._tlSeq || 0);
const nil = (v) => v === null || v === undefined;
const count = (v) => Number(v || 0).toLocaleString('en-GB');

// Synthetic and inferred groupings are always labelled, never shown as a session someone held (repo rule).
export function groupingChip(conv) {
  if (!conv || conv.session_kind === 'observed') return null;
  const key = String(conv.key || '');
  const sid = String(conv.session_id || '');
  if (key.startsWith('r:')) return 'Grouped by re-run';
  if (key.startsWith('t:')) return 'Single answer, no conversation recorded';
  if (sid.startsWith('report-')) return 'Grouped by report run';
  if (sid.startsWith('golden-')) return 'Grouped by golden run';
  if (sid.startsWith('docs-') || sid.startsWith('ing-')) return 'Grouped by document upload';
  if (sid.startsWith('skilllab-')) return 'Grouped by skill lab replay';
  return 'Legacy grouping';
}

// Page one again, merged by conversation key: fresh rows first, the rest kept, the list no longer than it was.
export function mergeByKey(oldRows, freshRows) {
  const fresh = freshRows || [];
  const old = oldRows || [];
  const keys = new Set(fresh.map((r) => r.key));
  return fresh.concat(old.filter((r) => !keys.has(r.key))).slice(0, Math.max(old.length, fresh.length));
}

export const FLAG_NOTES = {
  spans_purged: 'The step-by-step record of this answer has been deleted after the retention period; only the question and the answer remain.',
  duplicate_trees: 'This answer was recorded twice; the record that matches the answer is shown.',
  orphan_spans: 'Some steps lost their parent step and are shown at the top level.',
  truncated: 'Some recorded content was cut to fit the record.',
  question_repaired: "The question shown is the asker's own words, recovered from the record.",
  question_wrapped: 'The question shown still includes the page context it was sent with.',
  cost_incomplete: 'The cost is partial: some model calls have no price.',
  legacy_provenance: 'Where this question came from is inferred: it was asked before sources were recorded.',
  view_as: 'A platform admin asked this while viewing as this company.',
  thread_deleted: 'The asker deleted this conversation.',
  gate_changed: 'An answer check changed the answer.',
  unexecuted_tool_requests: 'The model asked for a tool that did not run.',
  concurrent_same_name_tools: 'Two calls of the same tool ran at once; their results may be swapped.',
  failed: 'This answer failed.',
  workflow_stop: 'This answer stopped at an approval gate.'
};
const GATE_INFERRED_NOTE = 'The answer carries an accuracy-check note, but the check itself was not recorded.';

export function flagNotes(flags) {
  const out = [];
  (flags || []).forEach((f) => {
    const note = String(f).indexOf('gate_inferred:') === 0 ? GATE_INFERRED_NOTE : FLAG_NOTES[f];
    if (note && out.indexOf(note) < 0) out.push(note);
  });
  return out;
}

export const traceLabConversationsMethods = {
  tlLoadLists() {
    const view = this.state.tlView;
    if (view === 'conversations') { this.tlLoadConversations(); this.tlLoadFacets(); this.tlLoadRuns(); } else if (view === 'runs') this.tlLoadRuns();
    else if (view === 'models') this.tlLoadModelCalls();
  },

  async tlLoadConversations(opts) {
    const o = opts || {};
    const s = this.state;
    const page = o.merge ? 0 : (s.tlConvsPage || 0);
    const cursor = (s.tlConvsCursors || [null])[page] || null;
    const seq = this._tlSeq || 0;
    const ask = (this._tlConvsAsk = (this._tlConvsAsk || 0) + 1);
    if (!o.merge) this.setState({ tlConvsLoading: true, tlConvsErr: '' });
    try {
      const out = await traceLabApi.conversations(Object.assign(this.tlQuery(), { limit: TL_PAGE_SIZE }, cursor ? { cursor } : {}));
      if (!current(this, seq) || ask !== this._tlConvsAsk) return;
      const rows = (out && out.conversations) || [];
      const next = (out && out.next_cursor) || null;
      this.setState((p) => {
        const cursors = (p.tlConvsCursors || [null]).slice(0, page + 1);
        if (next) cursors.push(next);
        return {
          tlConvs: o.merge ? mergeByKey(p.tlConvs, rows) : rows,
          tlConvsTotal: out && typeof out.total === 'number' ? out.total : (o.merge ? p.tlConvsTotal : null),
          tlConvsCursors: cursors, tlConvsPage: page, tlConvsLoading: false, tlConvsErr: '', tlConvsLoadedAt: Date.now(),
          tlWatermark: (out && out.watermark) || p.tlWatermark,
          tlUpdatedAt: o.merge ? p.tlUpdatedAt : Date.now(), tlUpdates: o.merge ? p.tlUpdates : null, tlPollErr: o.merge ? p.tlPollErr : ''
        };
      });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq) || ask !== this._tlConvsAsk) return;
      this.setState(o.merge ? { tlPollErr: msg(e) } : { tlConvsLoading: false, tlConvsErr: msg(e) });
    }
  },
  tlConvsPageStep(delta) {
    const s = this.state;
    const page = (s.tlConvsPage || 0) + delta;
    if (page < 0 || (page > 0 && !(s.tlConvsCursors || [])[page])) return;
    this.setState({ tlConvsPage: page });
    this.tlLoadConversations();
  },
  async tlLoadFacets() {
    const seq = this._tlSeq || 0;
    try {
      const out = await traceLabApi.conversationFacets(this.tlQuery());
      if (!current(this, seq)) return;
      this.setState({ tlFacets: out || null, tlFacetsErr: '' });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq)) return;
      this.setState({ tlFacetsErr: msg(e) });
    }
  },

  async tlOpenConversation(key, turnId) {
    if (!key) return;
    const seq = this._tlSeq || 0;
    const ask = (this._tlConvAsk = (this._tlConvAsk || 0) + 1);
    const same = !!(this.state.tlConv && this.state.tlConv.key === key);
    this.setState(Object.assign({ tlConvKey: key, tlTurnId: turnId || null, tlConvLoading: true, tlConvErr: '' }, same ? {} : { tlConv: null, tlOpenCards: {} }));
    this.tlSyncUrl();
    try {
      const out = await traceLabApi.conversation(key, turnId ? { turn: turnId } : {});
      if (!current(this, seq) || ask !== this._tlConvAsk) return;
      const conv = (out && out.conversation) || null;
      this.setState({ tlConv: conv, tlTurnId: conv && conv.active ? conv.active.id : null, tlConvLoading: false, tlConvLoadedAt: Date.now() });
      this.tlSyncUrl();
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq) || ask !== this._tlConvAsk) return;
      this.setState({ tlConvLoading: false, tlConvErr: msg(e) });
    }
  },
  tlOpenTurn(turnId) { if (turnId && this.state.tlConvKey) this.tlOpenConversation(this.state.tlConvKey, turnId); },
  // The Overview's "Review this answer in Conversations": the turn names its conversation, then the tab reads.
  async tlOpenTurnById(turnId) {
    if (!turnId) return;
    const seq = this._tlSeq || 0;
    if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') window.scrollTo(0, 0);
    this.setState({ view: 'traces', tlView: 'conversations', tlConvErr: '', tlConvLoading: true, tpTurn: null, tpSpanId: null });
    try {
      const out = await traceLabApi.turn(turnId);
      if (!current(this, seq)) return;
      const key = out && out.conversation_key;
      if (!key) throw new Error('This answer is not part of a conversation you can open.');
      this.setState({ tlConvKey: key, tlTurnId: turnId });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq)) return;
      this.setState({ tlConvLoading: false, tlConvErr: msg(e), tlConvKey: null, tlTurnId: null });
    }
    this.tlReadView();
  },
  tlStepConversation(delta) {
    const rows = this.state.tlConvs || [];
    if (!rows.length) return;
    const i = rows.findIndex((r) => r.key === this.state.tlConvKey);
    const next = rows[i < 0 ? (delta > 0 ? 0 : rows.length - 1) : i + delta];
    if (next) this.tlOpenConversation(next.key);
  },
  tlStepTurn(delta) {
    const turns = (this.state.tlConv && this.state.tlConv.turns) || [];
    const i = turns.findIndex((t) => t.turn_id === this.state.tlTurnId);
    const next = i > -1 ? turns[i + delta] : null;
    if (next) this.tlOpenTurn(next.turn_id);
  },
  // Which cards and panels are open lives here, so a refresh never closes one (tlPoll touches none of it).
  tlSetCardOpen(id, open) {
    if (!id) return;
    this.setState((p) => ({ tlOpenCards: Object.assign({}, p.tlOpenCards, { [id]: !!open }) }));
  },

  async tlLoadRuns() {
    const seq = this._tlSeq || 0;
    const f = Object.assign({}, TL_FILTER_DEFAULTS, this.state.tlFilters || {});
    this.setState({ tlRunsLoading: true, tlRunsErr: '' });
    try {
      const out = await traceLabApi.runs({ since: sinceFor(f.range) });
      if (!current(this, seq)) return;
      this.setState({ tlRuns: (out && out.runs) || [], tlGoldenAvailable: !!(out && out.golden_available), tlRunsLoading: false, tlRunsLoadedAt: Date.now() });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq)) return;
      this.setState({ tlRunsLoading: false, tlRunsErr: msg(e) });
    }
  },
  tlPickRun(runId) {
    this.setState({ tlView: 'conversations', tlConvKey: null, tlTurnId: null, tlConv: null });
    this.tlSetRun(runId);
    this.tlStartPoll();
  },

  async tlLoadModelCalls() {
    const s = this.state;
    const page = s.tlModelCallsPage || 0;
    const cursor = (s.tlModelCallsCursors || [null])[page] || null;
    const seq = this._tlSeq || 0;
    const ask = (this._tlCallsAsk = (this._tlCallsAsk || 0) + 1);
    this.setState({ tlModelCallsLoading: true, tlModelCallsErr: '' });
    try {
      const out = await traceLabApi.modelCalls(Object.assign(this.tlQuery(), { limit: TL_CALLS_PAGE_SIZE }, cursor ? { cursor } : {}));
      if (!current(this, seq) || ask !== this._tlCallsAsk) return;
      const next = (out && out.next_cursor) || null;
      this.setState((p) => {
        const cursors = (p.tlModelCallsCursors || [null]).slice(0, page + 1);
        if (next) cursors.push(next);
        return { tlModelCalls: (out && out.calls) || [], tlModelCallsAgg: (out && out.aggregate) || null,
          tlModelCallsRetention: out && out.retention_days ? out.retention_days : null, tlModelCallsCursors: cursors,
          tlModelCallsLoading: false, tlModelCallsErr: '', tlModelCallsLoadedAt: Date.now() };
      });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq) || ask !== this._tlCallsAsk) return;
      this.setState({ tlModelCallsLoading: false, tlModelCallsErr: msg(e) });
    }
  },
  tlModelCallsPageStep(delta) {
    const s = this.state;
    const page = (s.tlModelCallsPage || 0) + delta;
    if (page < 0 || (page > 0 && !(s.tlModelCallsCursors || [])[page])) return;
    this.setState({ tlModelCallsPage: page });
    this.tlLoadModelCalls();
  },
  async tlOpenModelCall(spanId) {
    if (!spanId) return;
    const seq = this._tlSeq || 0;
    const still = () => this.state.tlModelCall && this.state.tlModelCall.spanId === spanId;
    this.setState({ tlModelCall: { spanId, loading: true, error: '', card: null } });
    try {
      const out = await traceLabApi.modelCall(spanId);
      if (!current(this, seq) || !still()) return;
      this.setState({ tlModelCall: { spanId, loading: false, error: '', card: (out && out.model_call) || null,
        turnId: (out && out.turn_id) || null, conversationKey: (out && out.conversation_key) || null } });
    } catch (e) {
      if (isStaleScope(e) || !current(this, seq) || !still()) return;
      this.setState({ tlModelCall: { spanId, loading: false, error: msg(e), card: null } });
    }
  },
  tlCloseModelCall() { this.setState({ tlModelCall: null }); }
};

function convView(c, conv, now) {
  const rec = conv.active || null;
  const meta = (rec && rec.meta) || {};
  const headers = conv.turns || [];
  const usage = (rec && rec.usage) || {};
  const tools = Number(usage.tool_calls) || 0;
  const chips = [CHANNEL_LABELS[meta.channel] || CHANNEL_LABELS.chat];
  const chip = groupingChip(conv);
  if (chip) chips.push(chip);
  chips.push('Answer ' + (meta.turn_index || 1) + ' of ' + (meta.turn_count || headers.length || 1));
  if (Number(meta.attempt) > 1) chips.push('Attempt ' + meta.attempt);
  if (meta.channel_source === 'inferred') chips.push('Source inferred');
  ((rec && rec.models) || []).forEach((m) => chips.push(String(m)));
  chips.push(tools + (tools === 1 ? ' tool' : ' tools'));
  const fb = rec && rec.feedback;
  return {
    key: conv.key, title: conv.session_label || '—', chips,
    answers: headers.map((h) => ({ id: h.turn_id, label: 'Answer ' + h.turn_index, question: h.question || '—', failed: h.ok === false,
      on: !!rec && h.turn_id === rec.id, pick: () => c.tlOpenTurn(h.turn_id) })),
    truncatedNote: conv.turns_truncated ? 'Showing the first 500 answers of this conversation.' : '',
    when: rec ? fmtWhen(rec.timestamp, now) : '', who: (meta.asker && meta.asker.email) || '—',
    cost: rec ? fmtUsd(usage.cost_usd) + (usage.cost_complete === false ? ' (partial)' : '') : '—',
    latency: rec ? fmtMs(usage.latency_ms) : '—',
    notes: flagNotes(rec ? rec.flags : []),
    rating: fb ? { text: (fb.by_asker ? 'The asker rated this answer ' : 'Rated, not by the asker: ') + (fb.rating === 'up' ? 'up' : (fb.rating === 'down' ? 'down' : '—')),
      comment: fb.comment || '' } : null,
    cards: rec ? toCards(rec) : []
  };
}

export function traceLabConversationsVals(c, now) {
  const s = c.state;
  const t = typeof now === 'number' ? now : Date.now();
  const rows = s.tlConvs || [];
  const page = s.tlConvsPage || 0;
  const total = typeof s.tlConvsTotal === 'number' ? s.tlConvsTotal : null;
  const pages = total !== null ? Math.max(1, Math.ceil(total / TL_PAGE_SIZE)) : null;
  const conv = s.tlConv && s.tlConv.key === s.tlConvKey ? s.tlConv : null;
  const openCards = s.tlOpenCards || {};
  const calls = s.tlModelCalls || [];
  const callsPage = s.tlModelCallsPage || 0;
  const agg = s.tlModelCallsAgg;
  const mc = s.tlModelCall;
  return {
    tlList: rows.map((r) => ({
      key: r.key, question: r.first_question || '—',
      answers: r.turns_in_window + (r.turns_in_window === 1 ? ' answer' : ' answers'),
      who: (r.asker && r.asker.email) || '—', when: fmtWhen(r.last_turn_at, t), chip: groupingChip(r),
      failed: Number(r.failed_turns) > 0, on: r.key === s.tlConvKey, open: () => c.tlOpenConversation(r.key)
    })),
    tlListLoading: !!s.tlConvsLoading && !s.tlConvsLoadedAt,
    tlListErr: s.tlConvsErr || '',
    tlListEmpty: !!s.tlConvsLoadedAt && !rows.length && !s.tlConvsErr,
    tlTotalLabel: total !== null ? total + (total === 1 ? ' conversation' : ' conversations') : '',
    tlPageLabel: pages ? 'Page ' + (page + 1) + ' of ' + pages : 'Page ' + (page + 1),
    tlCanPrev: page > 0, tlCanNext: !!(s.tlConvsCursors || [])[page + 1],
    tlPrevPage: () => c.tlConvsPageStep(-1), tlNextPage: () => c.tlConvsPageStep(1),
    tlConvOpen: !!s.tlConvKey,
    tlConvLoading: !!s.tlConvLoading && !conv,
    tlConvErr: s.tlConvErr || '',
    tlConvView: conv ? convView(c, conv, t) : null,
    tlCardOpen: (id, dflt) => (Object.prototype.hasOwnProperty.call(openCards, id) ? !!openCards[id] : !!dflt),
    tlSetCardOpen: (id, open) => c.tlSetCardOpen(id, open),
    tlRunsView: (s.tlRuns || []).map((r) => ({
      id: r.run_id, name: r.kind === 'live' ? 'All live traffic' : String(r.name || r.run_id), live: r.kind === 'live',
      period: r.started_at ? fmtWhen(r.started_at, t) + ' – ' + fmtWhen(r.ended_at, t) : '—',
      conversations: nil(r.conversation_count) ? '—' : String(r.conversation_count),
      answers: nil(r.turn_count) ? '—' : String(r.turn_count),
      models: (r.models || []).join(', ') || '—', cost: fmtUsd(r.cost_usd), status: r.kind === 'live' ? 'Live' : String(r.status || '—'),
      on: (s.tlRunId || 'live') === r.run_id, open: () => c.tlPickRun(r.run_id)
    })),
    tlRunsLoading: !!s.tlRunsLoading && !s.tlRunsLoadedAt,
    tlRunsErr: s.tlRunsErr || '',
    tlGoldenNote: s.tlRunsLoadedAt && !s.tlGoldenAvailable ? 'No golden runs yet.' : '',
    tlCallsHeader: agg ? [count(agg.calls) + (Number(agg.calls) === 1 ? ' call' : ' calls'), 'p50 ' + fmtMs(agg.latency_p50_ms),
      'p95 ' + fmtMs(agg.latency_p95_ms), fmtTok(agg.input_tokens) + ' in · ' + fmtTok(agg.output_tokens) + ' out', fmtUsd(agg.cost_usd),
      count(agg.errors) + ' failed'].concat(Number(agg.unpriced_calls) ? [count(agg.unpriced_calls) + ' unpriced'] : []).join(' · ') : '',
    tlCallsNote: s.tlModelCallsRetention ? 'Model calls are kept for ' + s.tlModelCallsRetention + ' days; older calls are not in these figures.' : '',
    tlCallsLoading: !!s.tlModelCallsLoading && !s.tlModelCallsLoadedAt,
    tlCallsErr: s.tlModelCallsErr || '',
    tlCallsEmpty: !!s.tlModelCallsLoadedAt && !calls.length && !s.tlModelCallsErr,
    tlCalls: calls.map((r) => ({
      id: r.span_id, when: fmtWhen(r.started_at, t), model: r.model || UNAVAILABLE,
      purpose: PURPOSES.indexOf(r.label) > -1 ? r.label : labelFor('llm', r.label),
      latency: nil(r.latency_ms) ? UNAVAILABLE : fmtMs(r.latency_ms),
      tokens: nil(r.input_tokens) && nil(r.output_tokens) ? UNAVAILABLE : fmtTok(r.input_tokens) + ' in · ' + fmtTok(r.output_tokens) + ' out',
      cost: nil(r.cost_usd) ? (nil(r.input_tokens) ? UNAVAILABLE : 'unpriced') : fmtUsd(r.cost_usd),
      ok: r.ok !== false, question: r.question_excerpt || '—', withheld: !!r.withheld,
      on: !!(mc && mc.spanId === r.span_id), open: () => c.tlOpenModelCall(r.span_id)
    })),
    tlCallsCanPrev: callsPage > 0, tlCallsCanNext: !!(s.tlModelCallsCursors || [])[callsPage + 1],
    tlCallsPrev: () => c.tlModelCallsPageStep(-1), tlCallsNext: () => c.tlModelCallsPageStep(1),
    tlCallPanel: mc ? {
      loading: !!mc.loading, error: mc.error || '', card: mc.card ? modelCallCard(mc.card, 'panel:' + mc.spanId) : null,
      close: () => c.tlCloseModelCall(), openConversation: mc.turnId ? () => c.tlOpenTurnById(mc.turnId) : null
    } : null
  };
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabConversations.test.mjs test/traceLabShell.test.mjs test/traceLabTimeline.test.mjs`
Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 15: Wire the Trace Lab into the controller (defaults, mount, reset, sign-out, reload slice, nav)

**Files:**
- Modify: `apps/frontend/src/logic/HoistraLogic.js` (imports after :20, state after :73, `setState` before :374, mixins :383)
- Modify: `apps/frontend/src/logic/renderVals.js` (import after :19, spread after :1993)
- Modify: `apps/frontend/src/logic/core.js` (mount :65, unmount :81-82, `resetLiveData` :183)
- Modify: `apps/frontend/src/logic/auth.js` (:178, :446, :560, :573)
- Modify: `apps/frontend/src/logic/session.js` (consts after :55, `readSlice` before :195, `buildSlice` :345)
- Modify: `apps/frontend/src/logic/integrations.js:95`
- Test: `apps/frontend/test/traceLabShell.test.mjs` (append)

**Interfaces:**
- Consumes: Tasks 13–14 (`tlResetPatch`, `traceLabMethods`, `traceLabConversationsMethods`, `traceLabVals`, `traceLabConversationsVals`, `tlReadView`, `tlLinkFromUrl`, `tlOpenLinked`, `tlScrubUrl`, `tlStopPoll`, `tlOpen`).
- Produces: the reload slice keys `tlView`, `tlRunId`, `tlConvKey`, `tlFilters`; the nav entry opening `tlOpen()`; leaving `view === 'traces'` scrubs the URL.

- [ ] **Step 1: Write the failing test**

Append to `apps/frontend/test/traceLabShell.test.mjs`:

```js
// ── the controller: mount, reset, sign-out, the reload slice and the nav ──────────────────────────
const cleanup = (k) => {
  clearInterval(k._orchTick); clearInterval(k._cronTimer); clearInterval(k._frameTimer); clearInterval(k._iotTimer);
  clearTimeout(k._ccRetry); clearTimeout(k._homeRetry); clearTimeout(k._homeRefresh);
  clearTimeout(k._vpRetry); clearTimeout(k._vpRefresh); clearTimeout(k._bldRetry);
  clearTimeout(k._gphRetry); clearTimeout(k._gphRefresh); clearTimeout(k._spRetry);
  clearTimeout(k._enRetry); clearTimeout(k._enPosRetry); clearTimeout(k._asLiveRetry); clearTimeout(k._mxLiveRetry);
  clearTimeout(k._usLiveRetry); clearTimeout(k._usLiveRefresh); clearTimeout(k._auLiveRetry);
  clearTimeout(k._saLiveRetry); clearTimeout(k._saLiveRefresh); clearTimeout(k._tt); clearTimeout(k._qTimer); clearTimeout(k._mgTimer);
  if (typeof k.rpStop === 'function') k.rpStop();
  if (typeof k.authStop === 'function') k.authStop();
  k.tlStopPoll();
};

function liveHandlers() {
  handlers[P + '/conversations'] = [200, { ok: true, total: 1, next_cursor: null, watermark: '2026-10-15T12:00:00Z', conversations: [
    { key: 's:a', session_id: 'a', session_kind: 'observed', session_label: 'Boilers', turns_in_window: 1, first_question: 'Boilers?',
      last_turn_at: '2026-10-15T11:00:00Z', asker: { email: 'fm@example.com' }, flags: [] }] }];
  handlers[P + '/conversations/facets'] = [200, { ok: true, channels: [], channel_sources: [], session_kinds: [], models: [] }];
  handlers[P + '/conversations/updates'] = [200, { ok: true, new_turns: 1, new_conversations: 1, watermark: 'w2' }];
  handlers[P + '/conversations/s%3Aa'] = [200, { ok: true, conversation: { key: 's:a', session_label: 'Boilers', turns: [],
    active: { id: 't1', meta: {}, messages: [], usage: {}, flags: [], cot_boundary: 'x' } } }];
  handlers[P + '/runs'] = [200, { ok: true, golden_available: false, runs: [{ run_id: 'live', kind: 'live', name: 'All live traffic' }] }];
  handlers[P + '/model-calls'] = [200, { ok: true, aggregate: { calls: 1 }, calls: [{ span_id: 'sp1' }], next_cursor: null, retention_days: 90 }];
  handlers[P + '/model-calls/sp1'] = [200, { ok: true, model_call: { label: 'x' }, turn_id: 't1', conversation_key: 's:a' }];
  handlers[P + '/turns/t1'] = [200, { ok: true, trace: {}, conversation_key: 's:a' }];
  handlers['/backend/deep-agents/api/traces/turns'] = [200, { ok: true, available: true, turns: [{ turn_id: 't1' }], can_manage: true }];
  handlers['/backend/deep-agents/api/traces/stats'] = [200, { ok: true, totals: {}, by_day: [], by_model: [], by_tool: [] }];
  handlers['/backend/deep-agents/api/traces/turns/t1'] = [200, { ok: true, turn: { turn_id: 't1', spans: [] } }];
}

test('a reload on Hoist Traces reads its tab, and mounting writes nothing', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  liveHandlers();
  const c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: ADMIN, view: 'traces', tlView: 'conversations', tlConvKey: 's:a' });
  requests = [];
  c.componentDidMount();
  await new Promise((r) => setTimeout(r, 60));
  try {
    const paths = requests.map((q) => q.method + ' ' + q.url.pathname);
    assert.ok(paths.includes('GET ' + P + '/conversations'), paths.join('\n'));
    assert.ok(paths.includes('GET ' + P + '/conversations/s%3Aa'));
    assert.deepEqual(paths.filter((p) => !p.startsWith('GET ')), []);
    assert.equal(c.state.tlConv.key, 's:a');
  } finally { cleanup(c); }
});

test('tlResetPatch clears every tl* and tp* key the page ever sets', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  liveHandlers();
  const c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: ADMIN, view: 'traces' });
  try {
    c.tlOpen('conversations');
    await tick(); await tick();
    await c.tlOpenConversation('s:a', 't1');
    c.tlSetCardOpen('t1:root:user', true);
    c.tlSetView('models');
    await tick();
    await c.tlOpenModelCall('sp1');
    c.setState({ tlView: 'conversations' });
    await c.tlPoll(true);
    await c.tlWrite(() => Promise.reject(new Error('x')), { key: 'k' });
    c.tlToggleShortcuts();
    c.tlSetQueryDraft('boilers');
    await c.tpLoad();
    await c.tpOpenTurn('t1');
    await c.tlOpenTurnById('t1');
    await tick(); await tick();
    const keys = Object.keys(c.state).filter((k) => /^t[lp][A-Z]/.test(k));
    const patch = c.tlResetPatch();
    assert.deepEqual(keys.filter((k) => !(k in patch)), []);
    assert.ok(keys.length > 30);
  } finally { cleanup(c); }
});

test('a company switch and sign-out clear the Trace Lab and the Overview, and sign-out scrubs the link', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const c = new HoistraLogic();
  const dirty = { signedIn: true, role: 'admin', account: ADMIN, view: 'traces', tlView: 'conversations', tlConvKey: 's:a',
    tlConvs: [{ key: 's:a' }], tlConv: { key: 's:a' }, tlModelCall: { spanId: 'x' }, tpTurns: [{ turn_id: 't1' }], tpTurn: { turn_id: 't1' } };
  try {
    c.setState(dirty);
    c.resetLiveData();
    assert.equal(c.state.tlConvKey, null);
    assert.equal(c.state.tlConvs, null);
    assert.equal(c.state.tlModelCall, null);
    assert.deepEqual(c.state.tpTurns, []);
    assert.equal(c.state.tpTurn, null);
    c.setState(dirty);
    window.location.search = '?tl=conversations&run=live&conv=s%3Aa';
    c.authSignedOut('');
    assert.equal(c.state.signedIn, false);
    assert.equal(c.state.tlConvKey, null);
    assert.deepEqual(c.state.tpTurns, []);
    assert.equal(window.location.search, '');
  } finally { cleanup(c); }
});

test('the reload slice keeps the tab, run, conversation and filters, and drops anything malformed', async () => {
  const { loadSession, saveSession, SESSION_KEY } = await import('../src/logic/session.js');
  const run = '3fa2c1d0-1111-4111-8111-111111111111';
  const base = { signedIn: true, refreshToken: 'ref-1', account: ADMIN, view: 'traces' };
  saveSession(Object.assign({}, base, { tlView: 'runs', tlRunId: run, tlConvKey: 's:abc',
    tlFilters: { range: '30d', channel: 'upload', status: 'error', q: 'boilers' } }));
  const s = loadSession();
  assert.equal(s.tlView, 'runs');
  assert.equal(s.tlRunId, run);
  assert.equal(s.tlConvKey, 's:abc');
  assert.deepEqual(s.tlFilters, { range: '30d', channel: 'upload', status: 'error', q: 'boilers' });
  tabMem[SESSION_KEY] = JSON.stringify(Object.assign({}, base, { tlView: 'judges', tlRunId: 'yesterday', tlConvKey: 's:' + 'x'.repeat(300),
    tlFilters: { range: 'year', channel: 'email', status: 'maybe', q: 'y'.repeat(300) } }));
  const bad = loadSession();
  assert.equal(bad.tlView, undefined);
  assert.equal(bad.tlRunId, undefined);
  assert.equal(bad.tlConvKey, undefined);
  assert.deepEqual(bad.tlFilters, { range: '7d', channel: '', status: '', q: 'y'.repeat(200) });
  assert.equal(bad.view, 'traces');
});

test('the nav opens the last tab; a ?tl= link opens once signed in; leaving the page takes the link out of the address bar', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  liveHandlers();
  const c = new HoistraLogic();
  try {
    c.setState({ signedIn: true, role: 'admin', account: ADMIN, view: 'home' });
    window.location.search = '?tl=runs&run=live';
    c.tlLinkFromUrl();
    c.tlOpenLinked();
    assert.equal(c.state.view, 'traces');
    assert.equal(c.state.tlView, 'runs');
    assert.equal(window.location.search, '?tl=runs&run=live');
    c.setState({ view: 'home' });
    assert.equal(window.location.search, '');
    const nav = c.intVals(c.state).navAdmin.find((x) => x.label === 'Hoist Traces');
    nav.click();
    assert.equal(c.state.view, 'traces');
    assert.equal(c.state.tlView, 'runs');
  } finally { cleanup(c); }
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabShell.test.mjs`
Expected: the five new tests fail (for example `TypeError: c.tlOpen is not a function`, `tlConvKey` restored as `undefined`, the URL left at `?tl=…`).

- [ ] **Step 3: Implement**

`apps/frontend/src/logic/HoistraLogic.js` — four exact edits:

old:
```js
import { tracesPageMethods } from './tracesPage.js';
```
new:
```js
import { tracesPageMethods } from './tracesPage.js';
import { tlResetPatch, traceLabMethods } from './traceLab.js';
import { traceLabConversationsMethods } from './traceLabConversations.js';
```

old:
```js
    ...VDL_DEFAULTS,
    view: "home", module: null, answerKey: null, askedQuery: "",
```
new:
```js
    ...VDL_DEFAULTS,
    // Hoist Traces' Trace Lab (logic/traceLab.js): its tab, lists, open conversation and update check, and the
    // Overview's own keys, all at their defaults. The stored slice restores tab, run, conversation and filters.
    ...tlResetPatch(),
    view: "home", module: null, answerKey: null, askedQuery: "",
```

old:
```js
    if (prev.sessions !== this.state.sessions) saveSessions(this.state.sessions, undefined, this.state.sessionId);
```
new:
```js
    // Leaving Hoist Traces takes its ?tl= link out of the address bar, so a reload elsewhere does not reopen it.
    if (prev.view === "traces" && this.state.view !== "traces" && typeof this.tlScrubUrl === "function") this.tlScrubUrl();
    if (prev.sessions !== this.state.sessions) saveSessions(this.state.sessions, undefined, this.state.sessionId);
```

old:
```js
migrationMethods, emailHistoryMethods, renderValsMethods);
```
new:
```js
migrationMethods, emailHistoryMethods, traceLabMethods, traceLabConversationsMethods, renderValsMethods);
```

`apps/frontend/src/logic/renderVals.js`:

old:
```js
import { tracesPageVals } from './tracesPage.js';
```
new:
```js
import { tracesPageVals } from './tracesPage.js';
import { traceLabVals } from './traceLab.js';
import { traceLabConversationsVals } from './traceLabConversations.js';
```

old:
```js
      ...tracesPageVals(this),
```
new:
```js
      ...tracesPageVals(this),
      ...traceLabVals(this, Date.now()),
      ...traceLabConversationsVals(this, Date.now()),
```

`apps/frontend/src/logic/core.js`:

old:
```js
    if (typeof this.crEnsureRun === "function") this.crEnsureRun();
  },

  componentWillUnmount() {
```
new:
```js
    if (typeof this.crEnsureRun === "function") this.crEnsureRun();
    // A reload that lands on Hoist Traces reads the Trace Lab tab it was on (logic/traceLab.js). A ?tl= link is
    // opened after sign-in instead (auth.js authEnter → tlOpenLinked). Reads only (test/mount.test.mjs).
    if (this.state.view === "traces" && this.state.signedIn && !this._tlLinked && typeof this.tlReadView === "function") this.tlReadView();
  },

  componentWillUnmount() {
```

old:
```js
    clearTimeout(this._mgTimer);
    clearTimeout(this._qTimer);
  },
```
new:
```js
    clearTimeout(this._mgTimer);
    clearTimeout(this._qTimer);
    clearInterval(this._tlPoll);
  },
```

old:
```js
    this.setState({
      ccLive: null, ccLoading: false, ccError: "", ccLoadedAt: null, ccLastScan: null,
```
new:
```js
    // Hoist Traces (logic/traceLab.js): one company's conversations and answers never show under the next; any
    // reply still in flight is disowned and the update check stops.
    const tlPatch = typeof this.tlResetPatch === "function" ? this.tlResetPatch() : {};
    this.setState({
      ...tlPatch,
      ccLive: null, ccLoading: false, ccError: "", ccLoadedAt: null, ccLastScan: null,
```

`apps/frontend/src/logic/auth.js`:

old:
```js
    if (typeof this.cpLinkFromUrl === 'function') this.cpLinkFromUrl();
```
new:
```js
    if (typeof this.cpLinkFromUrl === 'function') this.cpLinkFromUrl();
    // A Trace Lab link (?tl=conversations&conv=…): read now, opened once signed in (logic/traceLab.js).
    if (typeof this.tlLinkFromUrl === 'function') this.tlLinkFromUrl();
```

old:
```js
    if (typeof this.cpOpenLinked === 'function') this.cpOpenLinked();
```
new:
```js
    if (typeof this.cpOpenLinked === 'function') this.cpOpenLinked();
    if (typeof this.tlOpenLinked === 'function') this.tlOpenLinked();
```

old:
```js
    const vdlPatch = typeof this.vdlResetPatch === 'function' ? this.vdlResetPatch() : {};
```
new:
```js
    const vdlPatch = typeof this.vdlResetPatch === 'function' ? this.vdlResetPatch() : {};
    // Hoist Traces' Trace Lab and Overview, in the same one write; its link leaves the address bar.
    const tlPatch = typeof this.tlResetPatch === 'function' ? this.tlResetPatch() : {};
    if (typeof this.tlScrubUrl === 'function') this.tlScrubUrl();
```

old:
```js
      ...vdlPatch,
      account: null,
```
new:
```js
      ...vdlPatch,
      ...tlPatch,
      account: null,
```

`apps/frontend/src/logic/session.js`:

old:
```js
const isBuildingList = (v) => Array.isArray(v) && v.every(isBuildingRow);
```
new:
```js
const isBuildingList = (v) => Array.isArray(v) && v.every(isBuildingRow);
// Hoist Traces' Trace Lab (logic/traceLab.js TL_VIEWS, TL_CHANNELS; tracesPage.js RANGES), kept here for the same
// reason as INSTANCE_KEYS. A later milestone that adds a tab adds it to both lists.
const TL_VIEW_KEYS = ['overview', 'conversations', 'runs', 'models'];
const TL_RANGE_KEYS = ['today', '7d', '30d', '90d'];
const TL_CHANNEL_KEYS = ['chat', 'ui_action', 'support', 'upload', 'correction', 'report', 'workflow', 'golden'];
const TL_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function cleanTlFilters(f) {
  if (!f || typeof f !== 'object') return null;
  return {
    range: TL_RANGE_KEYS.indexOf(f.range) > -1 ? f.range : '7d',
    channel: TL_CHANNEL_KEYS.indexOf(f.channel) > -1 ? f.channel : '',
    status: f.status === 'ok' || f.status === 'error' ? f.status : '',
    q: typeof f.q === 'string' ? f.q.slice(0, 200) : ''
  };
}
```

old:
```js
  // Signing out must not leave a restorable page behind.
  if (!out.signedIn) return {};
```
new:
```js
  // Hoist Traces' Trace Lab: the tab, run and conversation that were open, and the filters.
  if (TL_VIEW_KEYS.indexOf(d.tlView) > -1) out.tlView = d.tlView;
  if (isStr(d.tlRunId) && (d.tlRunId === 'live' || TL_UUID_RE.test(d.tlRunId))) out.tlRunId = d.tlRunId;
  if (isStr(d.tlConvKey) && d.tlConvKey.length <= 200 && /^[str]:/.test(d.tlConvKey)) out.tlConvKey = d.tlConvKey;
  const tlFilters = cleanTlFilters(d.tlFilters);
  if (tlFilters) out.tlFilters = tlFilters;

  // Signing out must not leave a restorable page behind.
  if (!out.signedIn) return {};
```

old:
```js
    ccCaseQuestion: state.ccCaseQuestion || ''
  };
}
```
new:
```js
    ccCaseQuestion: state.ccCaseQuestion || '',
    // Hoist Traces' Trace Lab (readSlice validates each one on the way back).
    tlView: state.tlView || 'overview',
    tlRunId: state.tlRunId || 'live',
    tlConvKey: state.tlConvKey || null,
    tlFilters: state.tlFilters || null
  };
}
```

`apps/frontend/src/logic/integrations.js`:

old:
```js
        click: () => this.tpOpen()
```
new:
```js
        click: () => this.tlOpen()
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabShell.test.mjs test/mount.test.mjs test/session.test.mjs test/auth.test.mjs test/dataReset.test.mjs test/companyScopeSwitch.test.mjs test/migration.test.mjs test/integrations.test.mjs`
Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 16: The Overview tab — "Answers", read-only asker thumbs (R14), a link into Conversations

**Files:**
- Modify: `apps/frontend/src/logic/tracesPage.js` (header :5-6, remove :114-136, `tpDetail` :194-195)
- Modify: `apps/frontend/src/screens/Traces.jsx` (header :1-3, `Run` :87-94, intro :170, list :263-265, component :156-163)
- Test: `apps/frontend/test/tracesPage.test.mjs` (modify :46-47, :95-97; append)

**Interfaces:**
- Consumes: controller methods `tlCanReview()` and `tlOpenTurnById(turnId)` (Tasks 13–14).
- Produces: `tracesPageVals(c).tpDetail` loses `rateUp`, `rateDown`, `setComment`, `saveComment`, `commentDirty`; gains `ratedByAsker: boolean`, `reviewInConversations: () => void | null`. `Traces({ vals, tabs })` renders `tabs` under its breadcrumbs. `tpRate`, `tpSetComment`, `tpSaveComment` no longer exist. `deepAgentsApi.traceFeedback` stays (the chat rail's own thumbs, corrections.js:310, :320).

- [ ] **Step 1: Write the failing test**

In `apps/frontend/test/tracesPage.test.mjs`:

old:
```js
  ['tpLoad', 'tpSetRange', 'tpSetStatus', 'tpSetQuery', 'tpSearch', 'tpOpenTurn', 'tpBack', 'tpSelectSpan', 'tpRate', 'tpSetComment', 'tpSaveComment', 'tpExport']
    .forEach((m) => { c[m] = (...a) => calls.push([m, ...a]); });
```
new:
```js
  ['tpLoad', 'tpSetRange', 'tpSetStatus', 'tpSetQuery', 'tpSearch', 'tpOpenTurn', 'tpBack', 'tpSelectSpan', 'tlOpenTurnById', 'tpExport']
    .forEach((m) => { c[m] = (...a) => calls.push([m, ...a]); });
  c.tlCanReview = () => c.state.canReview !== false;
```

old:
```js
  d.rateUp();
  d.tree[1].pick();
  assert.deepEqual(calls, [['tpRate', 't1', 'up'], ['tpSelectSpan', 'r']]);
```
new:
```js
  assert.equal(d.ratedByAsker, true);
  assert.equal(d.rateUp, undefined);
  d.reviewInConversations();
  d.tree[1].pick();
  assert.deepEqual(calls, [['tlOpenTurnById', 't1'], ['tpSelectSpan', 'r']]);
```

Append to the end of the file:

```js
test('the asker\'s rating is read only: nothing on this page writes feedback or teaches the chat (R14)', async () => {
  const { readFileSync } = await import('node:fs');
  for (const f of ['../src/logic/tracesPage.js', '../src/screens/Traces.jsx']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8');
    for (const banned of ['traceFeedback', 'tpRate', 'tpSaveComment', 'tpSetComment', 'Rate up', 'Rate down']) {
      assert.equal(src.includes(banned), false, f + ' still has ' + banned);
    }
  }
});

test('a rating someone else gave is labelled as not the asker\'s, and without a company there is no Conversations link', () => {
  const turn = { turn_id: 't9', user_id: 'u-asker', feedback_by: 'u-admin', feedback_rating: 'down', feedback_comment: 'Wrong building', spans: SPANS, models: [] };
  const d = tracesPageVals(ctl({ tpTurn: turn }).c).tpDetail;
  assert.equal(d.ratedByAsker, false);
  assert.equal(d.comment, 'Wrong building');
  assert.equal(tracesPageVals(ctl({ tpTurn: turn, canReview: false }).c).tpDetail.reviewInConversations, null);
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/tracesPage.test.mjs`
Expected: failures — `d.ratedByAsker` is `undefined`, `reviewInConversations is not a function`, and the R14 scan finds `tpRate` / `traceFeedback` / `Rate up`.

- [ ] **Step 3: Implement**

`apps/frontend/src/logic/tracesPage.js`:

old:
```js
// Two uses: seeing how an answer was produced (observability, cost, delay, failures), and
// labelling turns (thumbs up/down) so the export is training data with a verdict on it.
```
new:
```js
// It is the Trace Lab's Overview tab (screens/TraceLab.jsx): how an answer was produced (observability, cost,
// delay, failures) and the asker's own rating, read only (spec R14) — a reviewer can no longer overwrite it or
// teach the chat from here. Reviewing answers happens in the Conversations tab.
```

old:
```js
  tpBack() { this.setState({ tpTurn: null, tpSpanId: null }); },
  tpSelectSpan(id) { this.setState({ tpSpanId: id }); },
  // The reader's verdict: the label that makes a turn training data. Clicking the same thumb
  // again clears it.
  async tpRate(turnId, rating) {
    const t = this.state.tpTurn;
    const next = t && t.feedback_rating === rating ? null : rating;
    try {
      await deepAgentsApi.traceFeedback(turnId, next, t && t.feedback_comment);
      this.setState((p) => ({
        tpTurn: p.tpTurn && p.tpTurn.turn_id === turnId ? Object.assign({}, p.tpTurn, { feedback_rating: next }) : p.tpTurn,
        tpTurns: (p.tpTurns || []).map((x) => (x.turn_id === turnId ? Object.assign({}, x, { feedback_rating: next }) : x))
      }));
    } catch (e) { this.flash('Could not save the rating: ' + ((e && e.message) || e)); }
  },
  tpSetComment(e) { this.setState((p) => ({ tpTurn: p.tpTurn ? Object.assign({}, p.tpTurn, { feedback_comment: e.target.value, commentDirty: true }) : p.tpTurn })); },
  async tpSaveComment() {
    const t = this.state.tpTurn;
    if (!t || !t.commentDirty) return;
    try {
      await deepAgentsApi.traceFeedback(t.turn_id, t.feedback_rating || null, t.feedback_comment || '');
      this.setState((p) => ({ tpTurn: Object.assign({}, p.tpTurn, { commentDirty: false }) }));
      this.flash('Saved.');
    } catch (e) { this.flash('Could not save the comment: ' + ((e && e.message) || e)); }
  },
  async tpExport(ratedOnly) {
```
new:
```js
  tpBack() { this.setState({ tpTurn: null, tpSpanId: null }); },
  tpSelectSpan(id) { this.setState({ tpSpanId: id }); },
  async tpExport(ratedOnly) {
```

old:
```js
      rating: t.feedback_rating || null, rateUp: () => c.tpRate(t.turn_id, 'up'), rateDown: () => c.tpRate(t.turn_id, 'down'),
      comment: t.feedback_comment || '', setComment: (e) => c.tpSetComment(e), saveComment: () => c.tpSaveComment(), commentDirty: !!t.commentDirty,
```
new:
```js
      // The asker's own rating, read only (R14). Rows rated by someone else before R14 say so.
      rating: t.feedback_rating || null, comment: t.feedback_comment || '',
      ratedByAsker: !t.feedback_by || !t.user_id || String(t.feedback_by) === String(t.user_id),
      reviewInConversations: typeof c.tlCanReview === 'function' && c.tlCanReview() ? () => c.tlOpenTurnById(t.turn_id) : null,
```

`apps/frontend/src/screens/Traces.jsx`:

old:
```js
// Traces — Administration › Hoist Traces (logic/tracesPage.js). The runs the orchestrator
// answered with cost, latency and status; each run as a span tree with a waterfall, and the
// selected span's input and output. Thumbs rate a run for the training export.
```
new:
```js
// Traces — the Overview tab of Administration › Hoist Traces (logic/tracesPage.js; the tab shell is
// screens/TraceLab.jsx). The answers the orchestrator gave, with cost, latency and status; each one as a span
// tree with a waterfall and the selected span's input and output. The asker's own rating shows read only.
```

old:
```jsx
        <div style={{ display: "flex", gap: "10px", alignItems: "center", marginTop: "14px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Was this answer right?"}</span>
          <button type="button" aria-label="Rate up" onClick={d.rateUp} style={{ ...QUIET, borderColor: d.rating === "up" ? "var(--st-ok)" : "var(--color-divider)", color: d.rating === "up" ? "var(--st-ok)" : "var(--color-neutral-400)" }}><i className="ph ph-thumbs-up"></i></button>
          <button type="button" aria-label="Rate down" onClick={d.rateDown} style={{ ...QUIET, borderColor: d.rating === "down" ? "var(--st-risk)" : "var(--color-divider)", color: d.rating === "down" ? "var(--st-risk)" : "var(--color-neutral-400)" }}><i className="ph ph-thumbs-down"></i></button>
          <input aria-label="Comment on this answer" className="input" placeholder="What was right or wrong (kept with the export)…" value={d.comment} onChange={d.setComment} onBlur={d.saveComment}
            style={{ flex: "1 1 320px", fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)" }} />
          {d.commentDirty ? <button type="button" onClick={d.saveComment} style={QUIET}>{"Save"}</button> : null}
        </div>
```
new:
```jsx
        <div style={{ display: "flex", gap: "10px", alignItems: "baseline", marginTop: "14px", flexWrap: "wrap", fontSize: "12.5px" }}>
          <span style={{ color: "var(--color-neutral-500)" }}>{d.ratedByAsker ? "The asker's rating:" : "Rating, not by the asker:"}</span>
          {d.rating ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", color: d.rating === "up" ? "var(--st-ok)" : "var(--st-risk)" }}>
              <i className={"ph ph-thumbs-" + d.rating} aria-hidden="true"></i>{d.rating === "up" ? "Rated up" : "Rated down"}
            </span>
          ) : <span style={{ color: "var(--color-neutral-500)" }}>{"Not rated"}</span>}
          {d.comment ? <span style={{ color: "var(--color-text)" }}>{"“" + d.comment + "”"}</span> : null}
          {d.reviewInConversations ? <button type="button" onClick={d.reviewInConversations} style={{ ...QUIET, marginLeft: "auto" }}>{"Review this answer in Conversations"}</button> : null}
        </div>
```

old:
```jsx
                {"Every turn the chat answered: what it cost, how long it took, which models and tools ran, and whether it failed. Open a run to see the trace — each model and tool call with its input and output — and rate the answer; rated runs are the labelled export."}
```
new:
```jsx
                {"Every answer the chat gave: what it cost, how long it took, which models and tools ran, and whether it failed. Open one to see the trace — each model and tool call with its input and output — and the asker's own rating; rated answers are the labelled export."}
```

old:
```jsx
            <div style={{ ...KICK, padding: "14px 12px 6px" }}>{"Runs"}</div>
            {vals.tpLoading ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the runs…"}</div>
              : vals.tpEmpty ? <div style={{ padding: "22px 18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"No runs in this range. Every chat turn from now on lands here."}</div>
```
new:
```jsx
            <div style={{ ...KICK, padding: "14px 12px 6px" }}>{"Answers"}</div>
            {vals.tpLoading ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the answers…"}</div>
              : vals.tpEmpty ? <div style={{ padding: "22px 18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"No answers in this period. Every chat answer from now on lands here."}</div>
```

old:
```jsx
export default function Traces({ vals }) {
```
new:
```jsx
export default function Traces({ vals, tabs }) {
```

old:
```jsx
      <Crumbs vals={vals} run={inRun ? (vals.tpDetail ? vals.tpDetail.when : "Run") : null} />
```
new:
```jsx
      <Crumbs vals={vals} run={inRun ? (vals.tpDetail ? vals.tpDetail.when : "Answer") : null} />
      {tabs || null}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/tracesPage.test.mjs test/corrections.test.mjs test/noInternalNames.test.mjs`
Expected: all pass (the chat rail's own thumbs in corrections.js still call `traceFeedback`, untouched).

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 17: Timeline components (cards, panels, the raw panel)

**Files:**
- Create: `apps/frontend/src/screens/traceLab/RawPanel.jsx`, `ToolCard.jsx`, `ModelCallCard.jsx`, `Timeline.jsx`
- Modify: `apps/frontend/test/noInternalNames.test.mjs:42` (SKIP `RawPanel.jsx`)
- Test: `apps/frontend/test/traceLabViews.test.mjs` (create; Task 18 appends)

**Interfaces:**
- Consumes: Task 12 card shapes.
- Produces: `RawPanel({ title, data, open, onToggle })`, `ToolCard({ card, isOpen, setOpen })`, `ModelCallCard({ card, isOpen, setOpen })`, `Timeline({ cards, isOpen, setOpen })` where `isOpen(id, dflt) -> boolean` and `setOpen(id, open)` are `vals.tlCardOpen` / `vals.tlSetCardOpen`. Panel ids: `<card id>:request|:response|:usage|:raw|:all|:body`.

- [ ] **Step 1: Write the failing test**

Create `apps/frontend/test/traceLabViews.test.mjs`:

```js
// Trace Lab screens rendered to markup (bundled with esbuild, rendered with react-dom/server, as
// test/vendorDeleteDialog.test.mjs does): the timeline is a list of articles labelled by role, panels start closed,
// "unavailable" is printed where nothing was recorded, the reasoning notice is the record's text, and SQL appears
// only inside the collapsed technical panel.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFiles = [];
async function bundle(rel, name) {
  const res = await build({ entryPoints: [path.join(here, '..', rel)], bundle: true, write: false, format: 'esm', platform: 'node',
    jsx: 'automatic', external: ['react', 'react/*', 'react-dom/*'], logLevel: 'silent' });
  const out = path.join(here, '.' + name + '.bundle.mjs');
  writeFileSync(out, res.outputFiles[0].text);
  outFiles.push(out);
  return import(pathToFileURL(out).href);
}
after(() => outFiles.forEach((f) => rmSync(f, { force: true })));
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { toCards } = await import('../src/logic/traceLabTimeline.js');
const { default: Timeline } = await bundle('src/screens/traceLab/Timeline.jsx', 'trace-lab-timeline');

const COT = 'Model reasoning is not shown. Each model card shows what the model was sent and what it returned, as recorded.';
const NOTE = 'Private reasoning: not recorded (not a missing step).';
const loopCard = (label, over) => Object.assign({ model: 'claude-sonnet-5', label, latency_s: 1.8,
  usage: { input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940 }, total_cost: 0.004, cost_status: 'priced',
  request_kind: 'loop_summary', request: { messages: 3 }, response: { content: '', tool_calls: [{ name: 'answer_from_records' }] },
  withheld: [], cot_note: NOTE, status: 'ok', operation: 'orchestrator loop › model', raw: { input: {}, output: {} } }, over || {});
const REC = {
  id: 'turn-1', cot_boundary: COT,
  messages: [
    { id: 'turn-1:root:user', role: 'user', depth: 0, text: 'How many work orders?' },
    { id: 'turn-1:p:note', role: 'system_note', subtype: 'prompt', depth: 1, name: 'prompt assembled', input: { system_prompt: 'S' }, collapse: true },
    { id: 'turn-1:m1:call', role: 'model_call', depth: 1, label: 'tool decision', model_call: loopCard('tool decision') },
    { id: 'turn-1:t1:call', role: 'tool_call', depth: 1, span_id: 't1', name: 'answer_from_records', arguments: { q: 'wo' }, requested_by: 'm1', ok: true, latency_ms: 3000 },
    { id: 'turn-1:t1:result', role: 'tool_result', depth: 1, span_id: 't1', name: 'answer_from_records', content: 'x'.repeat(241), collapse: true, ok: true },
    { id: 'turn-1:q1:query', role: 'db_query', depth: 2, label: 'records: work_orders', sql: 'SELECT count(*) FROM work_orders', params: {}, row_count: 1, rows: [{ count: 758 }], ok: true },
    { id: 'turn-1:m2:call', role: 'model_call', depth: 1, label: 'answer synthesis', model_call: loopCard('answer synthesis', { model: null, latency_s: null, usage: {}, total_cost: null, cost_status: 'unavailable', request: null, response: null }) },
    { id: 'turn-1:g:gate', role: 'gate', depth: 1, gate: 'el_udr', recorded: true, changed: true, answer_before: 'Four boilers.', answer_after: 'Three boilers.', verdict_hidden: true },
    { id: 'turn-1:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: '758 work orders.' }
  ]
};
const render = (cards, opened) => renderToStaticMarkup(React.createElement(Timeline, {
  cards, isOpen: (id, d) => (opened && id in opened ? opened[id] : !!d), setOpen: () => {} }));

test('the timeline is a list of articles labelled by role, in order', () => {
  const html = render(toCards(REC));
  const labels = [...html.matchAll(/<article aria-label="([^"]*)"/g)].map((m) => m[1].split(' · ')[0]);
  assert.deepEqual(labels, ['Question', 'What the model was given', 'Model call', 'About model calls', 'Tool', 'Database query',
    'Model call', 'About model calls', 'Answer check', 'Answer']);
  assert.match(html, /<ol class="tl-timeline" aria-label="How this answer was produced">/);
});

test('panels start closed, unavailable fields say so, and the reasoning notice is the record text', () => {
  const html = render(toCards(REC));
  assert.equal(/<details[^>]*\sopen/.test(html), false);
  assert.ok(html.includes('unavailable'));
  assert.ok(html.includes(COT));
  assert.ok(html.includes(NOTE));
  assert.ok(html.includes('Show all (241 characters)'));
  assert.ok(html.includes('Answer changed after checking'));
  assert.ok(html.includes('<del>Four</del>') && html.includes('<ins>Three</ins>'));
});

test('SQL appears only inside the technical panel, and card titles use plain labels', () => {
  const html = render(toCards(REC));
  const raws = (html.match(/<details class="tl-raw"[\s\S]*?<\/details>/g) || []).join('');
  assert.ok(raws.includes('SELECT count(*) FROM work_orders'));
  assert.equal(html.replace(/<details class="tl-raw"[\s\S]*?<\/details>/g, '').includes('SELECT'), false);
  const chrome = html.replace(/<details[\s\S]*?<\/details>/g, '').replace(/<pre[\s\S]*?<\/pre>/g, '');
  assert.equal(chrome.includes('answer_from_records'), false);
  assert.ok(chrome.includes('Records query'));
});

test('a panel the reviewer opened is rendered open, because its state is the controller\'s', () => {
  const cards = toCards(REC);
  const model = cards.find((c) => c.kind === 'model');
  assert.match(render(cards, { [model.id + ':request']: true }), /<details class="tl-panel" open="">/);
});

test('an empty timeline says nothing was recorded', () => {
  assert.ok(render([]).includes('Nothing was recorded for this answer.'));
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabViews.test.mjs`
Expected: esbuild error `Could not resolve ".../src/screens/traceLab/Timeline.jsx"`.

- [ ] **Step 3: Implement**

Create `apps/frontend/src/screens/traceLab/RawPanel.jsx`:

```jsx
// RawPanel — the one place the Trace Lab shows raw trace payloads: span, tool and agent names, SQL, and the stored
// input and output. Admin-only and collapsed by default. It is on test/noInternalNames.test.mjs's SKIP list; no
// other Trace Lab file is.
import React from 'react';

const pretty = (v) => {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
};

export default function RawPanel({ title, data, open, onToggle }) {
  const label = title || 'Technical detail';
  return (
    <details className="tl-raw" open={!!open} onToggle={(e) => { if (onToggle && e.currentTarget.open !== !!open) onToggle(e.currentTarget.open); }}>
      <summary>{label}</summary>
      <pre className="tl-pre">{pretty(data)}</pre>
    </details>
  );
}
```

Create `apps/frontend/src/screens/traceLab/ToolCard.jsx`:

```jsx
// ToolCard — one tool call and its result as a single card (one span holds both), or a database query with the
// rows it answered. The tool's own name, its arguments as recorded and the SQL sit in RawPanel.
import React from 'react';
import RawPanel from './RawPanel.jsx';

function Rows({ rows }) {
  if (!rows || !rows.length) return null;
  const cols = Object.keys(rows[0] || {});
  const cell = (v) => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
  return (
    <div className="tl-rows">
      <table>
        <thead><tr>{cols.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} title={cell(r[c])}>{cell(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

const failed = (card) => (card.ok === false ? <span className="tl-bad">{'Failed' + (card.error ? ': ' + card.error : '')}</span> : null);

export default function ToolCard({ card, isOpen, setOpen }) {
  const raw = isOpen(card.id + ':raw', false);
  if (card.kind === 'db') {
    const count = card.rowCount === null || card.rowCount === undefined ? 'rows not recorded' : card.rowCount + (card.rowCount === 1 ? ' row' : ' rows');
    return (
      <>
        <div className="tl-card-head"><span className="tl-role">{card.role}</span><span>{card.title}</span><span className="tl-meta">{count}</span>{failed(card)}</div>
        <Rows rows={card.rows} />
        {card.moreRows ? <div className="tl-meta">{card.moreRows + ' more rows not shown'}</div> : null}
        <RawPanel title="Technical detail" data={card.raw} open={raw} onToggle={(o) => setOpen(card.id + ':raw', o)} />
      </>
    );
  }
  const body = card.result || { text: '', full: '', long: false, chars: 0 };
  const all = isOpen(card.id + ':all', false);
  return (
    <>
      <div className="tl-card-head"><span className="tl-role">{card.role}</span><span>{card.title}</span><span className="tl-meta">{card.latency}</span>{failed(card)}</div>
      {body.full ? (
        <>
          <pre className="tl-pre">{all || !body.long ? body.full : body.text}</pre>
          {body.long ? (
            <button type="button" className="tl-link" aria-expanded={all ? 'true' : 'false'} onClick={() => setOpen(card.id + ':all', !all)}>
              {all ? 'Show less' : 'Show all (' + body.chars.toLocaleString('en-GB') + ' characters)'}
            </button>
          ) : null}
        </>
      ) : <div className="tl-meta">{'No result recorded.'}</div>}
      <RawPanel title="Technical detail" data={card.raw} open={raw} onToggle={(o) => setOpen(card.id + ':raw', o)} />
    </>
  );
}
```

Create `apps/frontend/src/screens/traceLab/ModelCallCard.jsx`:

```jsx
// ModelCallCard — one model call: what it was for, then "model · latency · tokens · cost" (each "unavailable"
// when the trace did not record it), the record's note on private reasoning, and four panels that start closed:
// Request, Response, Usage and Raw metadata.
import React from 'react';
import RawPanel from './RawPanel.jsx';

function Panel({ panel, open, onToggle }) {
  return (
    <details className="tl-panel" open={!!open} onToggle={(e) => { if (e.currentTarget.open !== !!open) onToggle(e.currentTarget.open); }}>
      <summary>{panel.title}</summary>
      {panel.note ? <div className="tl-meta">{panel.note}</div> : null}
      {panel.rows ? (
        <table className="tl-kv"><tbody>{panel.rows.map(([k, v]) => <tr key={k}><th scope="row">{k}</th><td>{v}</td></tr>)}</tbody></table>
      ) : <pre className="tl-pre">{panel.body}</pre>}
    </details>
  );
}

export default function ModelCallCard({ card, isOpen, setOpen }) {
  const [request, response, usage, raw] = card.panels;
  return (
    <>
      <div className="tl-card-head">
        <span className="tl-role">{card.role}</span><span>{card.purpose}</span>
        <span className="tl-meta">{card.model + ' · ' + card.latency + ' · ' + card.tokens + ' · ' + card.cost}</span>
        {card.ok ? null : <span className="tl-bad">{'Failed' + (card.error ? ': ' + card.error : '')}</span>}
      </div>
      {card.cotNote ? <div className="tl-meta">{card.cotNote}</div> : null}
      {[request, response, usage].map((p) => <Panel key={p.id} panel={p} open={isOpen(p.id, false)} onToggle={(o) => setOpen(p.id, o)} />)}
      <RawPanel title={raw.title} data={raw.raw} open={isOpen(raw.id, false)} onToggle={(o) => setOpen(raw.id, o)} />
    </>
  );
}
```

Create `apps/frontend/src/screens/traceLab/Timeline.jsx`:

```jsx
// Timeline — one answer's cards (logic/traceLabTimeline.js toCards) as a list of articles, each labelled with its
// role and time. What is open lives in the controller (tlOpenCards), so the 45-second update check never closes it.
import React from 'react';
import ToolCard from './ToolCard.jsx';
import ModelCallCard from './ModelCallCard.jsx';
import RawPanel from './RawPanel.jsx';

function Body({ card, isOpen, setOpen }) {
  const id = card.id;
  if (card.kind === 'tool' || card.kind === 'db') return <ToolCard card={card} isOpen={isOpen} setOpen={setOpen} />;
  if (card.kind === 'model') return <ModelCallCard card={card} isOpen={isOpen} setOpen={setOpen} />;
  if (card.kind === 'cot') return <p className="tl-cot">{card.text}</p>;
  if (card.kind === 'user' || card.kind === 'answer') {
    return (
      <>
        <div className="tl-card-head"><span className="tl-role">{card.role}</span>{card.rewritten ? <span className="tl-meta">{card.rewritten}</span> : null}</div>
        <div className="tl-text">{card.text || '—'}</div>
        {card.error ? <div className="tl-bad">{card.error}</div> : null}
      </>
    );
  }
  if (card.kind === 'gate') {
    return (
      <>
        <div className="tl-card-head"><span className="tl-role">{card.role}</span><span>{card.title}</span></div>
        <div className="tl-text">{card.headline}</div>
        {card.diff && card.diff.length ? (
          <p className="tl-diff">{card.diff.map((d, i) => (d.op === 'same' ? <span key={i}>{d.text}</span> : d.op === 'add' ? <ins key={i}>{d.text}</ins> : <del key={i}>{d.text}</del>))}</p>
        ) : null}
        {card.hiddenNote ? <div className="tl-meta">{card.hiddenNote}</div> : null}
        {card.verdict ? <pre className="tl-pre">{card.verdict}</pre> : null}
      </>
    );
  }
  if (card.kind === 'plan') {
    return (
      <>
        <div className="tl-card-head"><span className="tl-role">{card.role}</span>{card.rejected ? <span className="tl-bad">{'Rejected: ' + card.rejected}</span> : null}</div>
        {card.steps.length ? <ol className="tl-steps">{card.steps.map((s) => <li key={s.n}>{s.text}</li>)}</ol> : <div className="tl-meta">{'No steps recorded.'}</div>}
        <RawPanel title="Technical detail" data={card.raw} open={isOpen(id + ':raw', false)} onToggle={(o) => setOpen(id + ':raw', o)} />
      </>
    );
  }
  const open = isOpen(id + ':body', !card.collapsed);
  const chars = card.body && card.body.chars ? ' (' + card.body.chars.toLocaleString('en-GB') + ' characters)' : '';
  return (
    <>
      <div className="tl-card-head">
        <span className="tl-role">{card.role}</span>{card.title ? <span>{card.title}</span> : null}
        {card.ok === false ? <span className="tl-bad">{'Failed' + (card.error ? ': ' + card.error : '')}</span> : null}
      </div>
      <button type="button" className="tl-link" aria-expanded={open ? 'true' : 'false'} onClick={() => setOpen(id + ':body', !open)}>{open ? 'Hide' : 'Show' + chars}</button>
      {open ? <pre className="tl-pre">{card.body ? card.body.full : ''}</pre> : null}
      <RawPanel title="Technical detail" data={card.raw} open={isOpen(id + ':raw', false)} onToggle={(o) => setOpen(id + ':raw', o)} />
    </>
  );
}

export default function Timeline({ cards, isOpen, setOpen }) {
  if (!cards || !cards.length) return <div className="tl-meta">{'Nothing was recorded for this answer.'}</div>;
  return (
    <ol className="tl-timeline" aria-label="How this answer was produced">
      {cards.map((card) => (
        <li key={card.id} className={'tl-item tl-' + card.kind} style={{ marginLeft: Math.min(card.depth || 0, 6) * 16 + 'px' }}>
          <article aria-label={card.role + (card.when ? ' · ' + card.when : '')}>
            <Body card={card} isOpen={isOpen} setOpen={setOpen} />
          </article>
        </li>
      ))}
    </ol>
  );
}
```

In `apps/frontend/test/noInternalNames.test.mjs`:

old:
```js
  'src/components/shell/SuperAdminOverlay.jsx', 'src/components/shell/PlatformCostPage.jsx', 'src/components/shell/PlatformCostPanel.jsx'
]);
```
new:
```js
  'src/components/shell/SuperAdminOverlay.jsx', 'src/components/shell/PlatformCostPage.jsx', 'src/components/shell/PlatformCostPanel.jsx',
  // Trace Lab: raw trace payloads, admin-only, collapsed by default. The only Trace Lab file on this list (spec R13).
  'src/screens/traceLab/RawPanel.jsx'
]);
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabViews.test.mjs test/noInternalNames.test.mjs`
Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 18: The Trace Lab screen — tab shell, Conversations, Runs, Model calls; mount it

**Files:**
- Create: `apps/frontend/src/screens/TraceLab.jsx`, `src/screens/traceLab/Conversations.jsx`, `Runs.jsx`, `ModelCalls.jsx`, `src/styles/tracelab.css`
- Modify: `apps/frontend/src/App.jsx:25`, `:85`; `src/styles/base.css` (after `@import './support.css';`); `src/logic/support.js:112`
- Test: `apps/frontend/test/traceLabViews.test.mjs` (append)

**Interfaces:**
- Consumes: Task 13–14 vals (keys listed there), Task 16 `Traces({ vals, tabs })`, Task 17 components, `vals.goHome` (renderVals.js:414).
- Produces: `TraceLab({ vals })` (default export) and `TabBar({ vals })`; `vals.isTraces` (tracesPage.js:159) now mounts `TraceLab`.

- [ ] **Step 1: Write the failing test**

Append to `apps/frontend/test/traceLabViews.test.mjs`:

```js
// ── the whole screen, from the real view models ────────────────────────────────────────────────
const { default: TraceLab } = await bundle('src/screens/TraceLab.jsx', 'trace-lab-shell');
const { traceLabMethods, traceLabVals, tlResetPatch } = await import('../src/logic/traceLab.js');
const { traceLabConversationsMethods, traceLabConversationsVals } = await import('../src/logic/traceLabConversations.js');
const { tracesPageVals } = await import('../src/logic/tracesPage.js');

function screenVals(state) {
  const c = { state: Object.assign(tlResetPatch(), { signedIn: true, view: 'traces', account: { role: 'admin' } }, state || {}) };
  c.setState = () => {};
  Object.assign(c, traceLabMethods, traceLabConversationsMethods);
  const now = Date.parse('2026-10-15T12:00:00Z');
  return Object.assign({ goHome: () => {} }, tracesPageVals(c, now), traceLabVals(c, now), traceLabConversationsVals(c, now));
}
const page = (state) => renderToStaticMarkup(React.createElement(TraceLab, { vals: screenVals(state) }));

test('the shell is a tablist of the views this role may use, with the open one selected', () => {
  const html = page({ tlView: 'runs', tlRunsLoadedAt: 1,
    tlRuns: [{ run_id: 'live', kind: 'live', name: 'All live traffic', conversation_count: 4, turn_count: 9, cost_usd: 0.5 }] });
  assert.match(html, /role="tablist"/);
  const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*>([^<]*)<\/button>/g)].map((m) => [m[2], m[1]]);
  assert.deepEqual(tabs, [['Overview', 'false'], ['Conversations', 'false'], ['Runs', 'true'], ['Model calls', 'false']]);
  assert.ok(html.includes('All live traffic'));
  assert.ok(html.includes('No golden runs yet.'));
});

test('Conversations shows the list with grouping chips and the open conversation as a timeline', () => {
  const conv = { key: 's:chat-1', session_kind: 'observed', session_label: 'Boiler certificates',
    turns: [{ turn_id: 't1', turn_index: 1, question: 'Which boilers?', ok: true }, { turn_id: 't2', turn_index: 2, question: 'How many?', ok: true }],
    active: Object.assign({}, REC, { id: 't2', timestamp: '2026-10-15T11:00:00Z', models: ['claude-sonnet-5'], usage: { cost_usd: 0.03, latency_ms: 9000, tool_calls: 1 },
      meta: { channel: 'chat', channel_source: 'client', turn_index: 2, turn_count: 2, attempt: 1, asker: { email: 'fm@example.com' } },
      flags: ['question_repaired'], feedback: null }) };
  const html = page({ tlView: 'conversations', tlConvKey: 's:chat-1', tlConv: conv, tlConvsLoadedAt: 1, tlConvsTotal: 2,
    tlConvs: [
      { key: 's:chat-1', session_id: 'chat-1', session_kind: 'observed', turns_in_window: 2, first_question: 'Which boilers?', last_turn_at: '2026-10-15T11:00:00Z', asker: { email: 'fm@example.com' } },
      { key: 's:report-1', session_id: 'report-1', session_kind: 'synthetic', turns_in_window: 1, first_question: 'Weekly summary', last_turn_at: '2026-10-15T10:00:00Z', asker: { email: 'cron@example.com' } }] });
  assert.ok(html.includes('Grouped by report run'));
  assert.ok(html.includes('2 conversations'));
  assert.ok(html.includes('Answer 2 of 2'));
  assert.match(html, /aria-pressed="true"[^>]*>Answer 2</);
  assert.ok(html.includes('The question shown is the asker&#x27;s own words, recovered from the record.'));
  assert.ok(html.includes('<article aria-label="Question'));
  assert.match(html, /aria-live="polite"/);
});

test('Model calls is a table with an aggregate line, and a row opens a dialog with the card', () => {
  const html = page({ tlView: 'models', tlModelCallsLoadedAt: 1,
    tlModelCallsAgg: { calls: 2, errors: 0, latency_p50_ms: 1800, latency_p95_ms: 1800, input_tokens: 900, output_tokens: 40, cost_usd: 0.004 },
    tlModelCalls: [{ span_id: 'sp1', started_at: '2026-10-15T11:00:00Z', model: null, label: 'tool decision', latency_ms: null, input_tokens: null, output_tokens: null, cost_usd: null, ok: true, question_excerpt: 'How many?' }],
    tlModelCall: { spanId: 'sp1', loading: false, error: '', turnId: 't2', card: { model: null, label: 'tool decision', usage: {}, cost_status: 'unavailable', withheld: [], cot_note: 'N' } } });
  assert.ok(html.includes('2 calls · p50 1.80 s'));
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Model call"/);
  assert.ok(html.includes('Open in Conversations'));
  assert.ok(html.includes('unavailable'));
});

test('the Overview is the existing page under the tabs: "Answers", and the asker\'s rating is read only', () => {
  const html = page({ tlView: 'overview', tpLoadedAt: 1, tpTurns: [{ turn_id: 't1', started_at: '2026-10-15T11:00:00Z', question: 'How many?', llm_calls: 1, tool_calls: 0, cost_usd: 0.01, latency_ms: 900, ok: true, models: [] }] });
  assert.match(html, /role="tablist"/);
  assert.ok(html.includes('>Answers<'));
  assert.equal(html.includes('Rate up'), false);
  const run = page({ tlView: 'overview', tpTurn: { turn_id: 't1', user_id: 'u1', feedback_by: 'u1', feedback_rating: 'down', feedback_comment: 'Wrong period', spans: [], models: [] } });
  assert.ok(run.includes('The asker&#x27;s rating:'));
  assert.ok(run.includes('Rated down'));
  assert.ok(run.includes('Review this answer in Conversations'));
});

test('a platform superadmin who is not viewing a company sees the Overview alone, with no tabs', () => {
  const html = page({ account: { role: 'superadmin' }, tlView: 'conversations' });
  assert.equal(html.includes('role="tablist"'), false);
  assert.ok(html.includes('>Answers<'));
});

test('the shortcut sheet is a dialog', () => {
  const html = page({ tlView: 'conversations', tlShortcuts: true });
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"/);
  assert.ok(html.includes('Next / previous conversation'));
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd apps/frontend && node --test test/traceLabViews.test.mjs`
Expected: esbuild error `Could not resolve ".../src/screens/TraceLab.jsx"` (the Task 17 tests still pass).

- [ ] **Step 3: Implement**

Create `apps/frontend/src/screens/traceLab/Conversations.jsx`:

```jsx
// Conversations — the list (left) and one conversation (right): its chips, the answers in it, notes about the
// record, the asker's own rating, and the open answer's timeline. Read only in M1; the verdict bar arrives with
// open coding (M2).
import React from 'react';
import Timeline from './Timeline.jsx';

export default function Conversations({ vals }) {
  const v = vals.tlConvView;
  return (
    <div className="tl-split">
      <section className="tl-list" aria-label="Conversations">
        <div className="tl-list-head"><span>{vals.tlTotalLabel || ' '}</span><span className="tl-meta">{vals.tlPageLabel}</span></div>
        {vals.tlListLoading ? <div className="tl-meta">{'Reading conversations…'}</div> : null}
        {vals.tlListErr ? <div className="tl-bad" role="alert">{"Couldn't read the conversations: " + vals.tlListErr}</div> : null}
        {vals.tlListEmpty ? <div className="tl-meta">{'No answers in this period.'}</div> : null}
        <ul className="tl-rows-list">
          {vals.tlList.map((r) => (
            <li key={r.key}>
              <button type="button" className={'tl-row' + (r.on ? ' tl-row-on' : '')} aria-current={r.on ? 'true' : undefined} onClick={r.open}>
                <span className="tl-row-q">{r.question}</span>
                <span className="tl-meta">{r.answers + ' · ' + r.who + ' · ' + r.when + (r.failed ? ' · failed' : '')}</span>
                {r.chip ? <span className="tl-chip">{r.chip}</span> : null}
              </button>
            </li>
          ))}
        </ul>
        <div className="tl-pager">
          <button type="button" className="tl-link" onClick={vals.tlPrevPage} disabled={!vals.tlCanPrev}>{'‹ Previous'}</button>
          <button type="button" className="tl-link" onClick={vals.tlNextPage} disabled={!vals.tlCanNext}>{'Next ›'}</button>
        </div>
      </section>
      <section className="tl-conv" aria-label="Conversation">
        {vals.tlConvErr ? <div className="tl-bad" role="alert">{"Couldn't read this conversation: " + vals.tlConvErr}</div> : null}
        {vals.tlConvLoading ? <div className="tl-meta">{'Reading the conversation…'}</div> : null}
        {!vals.tlConvOpen && !vals.tlConvErr ? <div className="tl-meta">{'Pick a conversation on the left.'}</div> : null}
        {v ? (
          <>
            <h3 className="tl-title" tabIndex={-1}>{v.title}</h3>
            <div className="tl-chips">{v.chips.map((c, i) => <span key={i} className="tl-chip">{c}</span>)}</div>
            <div className="tl-meta">{v.when + ' · ' + v.who + ' · ' + v.cost + ' · ' + v.latency}</div>
            {v.answers.length > 1 ? (
              <div className="tl-answers" role="group" aria-label="Answers in this conversation">
                {v.answers.map((a) => (
                  <button key={a.id} type="button" className={'tl-chip-btn' + (a.on ? ' tl-on' : '')} aria-pressed={a.on ? 'true' : 'false'} title={a.question} onClick={a.pick}>
                    {a.label + (a.failed ? ' · failed' : '')}
                  </button>
                ))}
              </div>
            ) : null}
            {v.truncatedNote ? <div className="tl-meta">{v.truncatedNote}</div> : null}
            {v.notes.map((n) => <div key={n} className="tl-note">{n}</div>)}
            {v.rating ? <div className="tl-meta">{v.rating.text + (v.rating.comment ? ' — “' + v.rating.comment + '”' : '')}</div> : null}
            <Timeline cards={v.cards} isOpen={vals.tlCardOpen} setOpen={vals.tlSetCardOpen} />
          </>
        ) : null}
      </section>
    </div>
  );
}
```

Create `apps/frontend/src/screens/traceLab/Runs.jsx`:

```jsx
// Runs — "All live traffic" over the period (golden runs join this list in M3). Picking a run opens its conversations.
import React from 'react';

export default function Runs({ vals }) {
  return (
    <section className="tl-card" aria-label="Runs">
      {vals.tlRunsLoading ? <div className="tl-meta">{'Reading runs…'}</div> : null}
      {vals.tlRunsErr ? <div className="tl-bad" role="alert">{"Couldn't read the runs: " + vals.tlRunsErr}</div> : null}
      <table className="tl-table">
        <thead><tr>{['Run', 'Period', 'Conversations', 'Answers', 'Models', 'Cost', 'Status'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {vals.tlRunsView.map((r) => (
            <tr key={r.id} className={r.on ? 'tl-row-on' : ''}>
              <td><button type="button" className="tl-link" onClick={r.open}>{r.name}</button></td>
              <td>{r.period}</td><td>{r.conversations}</td><td>{r.answers}</td><td>{r.models}</td><td>{r.cost}</td><td>{r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {vals.tlGoldenNote ? <div className="tl-meta">{vals.tlGoldenNote}</div> : null}
    </section>
  );
}
```

Create `apps/frontend/src/screens/traceLab/ModelCalls.jsx`:

```jsx
// ModelCalls — every model call in the period: the aggregate line, one row per call, and the call's card in a side
// panel. A field the trace did not record reads "unavailable".
import React from 'react';
import ModelCallCard from './ModelCallCard.jsx';

export default function ModelCalls({ vals }) {
  const panel = vals.tlCallPanel;
  return (
    <>
      <section className="tl-card" aria-label="Model calls">
        <div className="tl-list-head"><span>{vals.tlCallsHeader || ' '}</span></div>
        {vals.tlCallsNote ? <div className="tl-meta">{vals.tlCallsNote}</div> : null}
        {vals.tlCallsLoading ? <div className="tl-meta">{'Reading model calls…'}</div> : null}
        {vals.tlCallsErr ? <div className="tl-bad" role="alert">{"Couldn't read the model calls: " + vals.tlCallsErr}</div> : null}
        {vals.tlCallsEmpty ? <div className="tl-meta">{'No model calls in this period.'}</div> : null}
        <table className="tl-table">
          <thead><tr>{['Time', 'Model', 'Purpose', 'Latency', 'Tokens', 'Cost', 'Status', 'Question'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {vals.tlCalls.map((r) => (
              <tr key={r.id} className={r.on ? 'tl-row-on' : ''}>
                <td><button type="button" className="tl-link" onClick={r.open} aria-label={'Open the model call at ' + r.when}>{r.when}</button></td>
                <td>{r.model}</td><td>{r.purpose}</td><td>{r.latency}</td><td>{r.tokens}</td><td>{r.cost}</td>
                <td>{r.ok ? 'ok' : 'failed'}</td><td className="tl-ellipsis" title={r.question}>{r.question}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="tl-pager">
          <button type="button" className="tl-link" onClick={vals.tlCallsPrev} disabled={!vals.tlCallsCanPrev}>{'‹ Previous'}</button>
          <button type="button" className="tl-link" onClick={vals.tlCallsNext} disabled={!vals.tlCallsCanNext}>{'Next ›'}</button>
        </div>
      </section>
      {panel ? (
        <div className="tl-side" role="dialog" aria-modal="true" aria-label="Model call">
          <button type="button" className="tl-close" aria-label="Close" onClick={panel.close}>{'×'}</button>
          {panel.loading ? <div className="tl-meta">{'Reading the model call…'}</div> : null}
          {panel.error ? <div className="tl-bad" role="alert">{"Couldn't read the model call: " + panel.error}</div> : null}
          {panel.card ? <ModelCallCard card={panel.card} isOpen={vals.tlCardOpen} setOpen={vals.tlSetCardOpen} /> : null}
          {panel.openConversation ? <button type="button" className="tl-link" onClick={panel.openConversation}>{'Open in Conversations'}</button> : null}
        </div>
      ) : null}
    </>
  );
}
```

Create `apps/frontend/src/screens/TraceLab.jsx`:

```jsx
// TraceLab — Administration › Hoist Traces as a tab shell (spec "Frontend: the Trace Lab" §1-§2, D13). Overview is
// the existing Traces page; Conversations, Runs and Model calls are M1's read-only views; later milestones add
// their tabs here. State is logic/traceLab.js and logic/traceLabConversations.js.
import React from 'react';
import Traces from './Traces.jsx';
import Conversations from './traceLab/Conversations.jsx';
import Runs from './traceLab/Runs.jsx';
import ModelCalls from './traceLab/ModelCalls.jsx';

export function TabBar({ vals }) {
  const tabs = vals.tlTabs || [];
  if (tabs.length < 2) return null;
  const onKey = (e, i) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    next.pick();
    const el = typeof document !== 'undefined' ? document.getElementById('tl-tab-' + next.key) : null;
    if (el) el.focus();
  };
  return (
    <div role="tablist" aria-label="Trace Lab views" className="tl-tabs">
      {tabs.map((t, i) => (
        <button key={t.key} id={'tl-tab-' + t.key} type="button" role="tab" aria-selected={t.on ? 'true' : 'false'} tabIndex={t.on ? 0 : -1}
          className={'tl-tab' + (t.on ? ' tl-tab-on' : '')} onClick={t.pick} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>
      ))}
    </div>
  );
}

function Crumbs({ vals }) {
  return (
    <div className="tl-crumbs">
      <button type="button" className="tl-link" onClick={vals.goHome}>{'← Home'}</button>
      <span>{'/'}</span><span>{'Administration'}</span><span>{'/'}</span><span>{'Hoist Traces'}</span>
    </div>
  );
}

function FilterBar({ vals }) {
  return (
    <div className="tl-filters">
      {vals.tlRanges.map((r) => <button key={r.key} type="button" className={'tl-chip-btn' + (r.on ? ' tl-on' : '')} aria-pressed={r.on ? 'true' : 'false'} onClick={r.pick}>{r.label}</button>)}
      <label className="tl-field"><span>{'Source'}</span>
        <select value={vals.tlChannel} onChange={vals.tlSetChannel}>{vals.tlChannelOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
      </label>
      <label className="tl-field"><span>{'Run'}</span>
        <select value={vals.tlRunId} onChange={vals.tlSetRun}>{vals.tlRunOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
      </label>
      {vals.tlStatuses.map((x) => <button key={x.key || 'all'} type="button" className={'tl-chip-btn' + (x.on ? ' tl-on' : '')} aria-pressed={x.on ? 'true' : 'false'} onClick={x.pick}>{x.label}</button>)}
      <form className="tl-search" onSubmit={(e) => { e.preventDefault(); vals.tlSearch(); }}>
        <input aria-label="Search questions and answers" placeholder="Search questions and answers…" value={vals.tlQueryDraft} onChange={vals.tlSetQueryDraft} />
        <button type="submit" className="tl-link">{'Search'}</button>
      </form>
    </div>
  );
}

function UpdateLine({ vals }) {
  return (
    <div className="tl-update">
      {vals.tlPolled ? <span aria-live="polite">{vals.tlUpdateLine}</span> : null}
      <button type="button" className="tl-link" onClick={vals.tlRefresh}>{vals.tlPollFailed ? 'Retry' : 'Refresh now'}</button>
    </div>
  );
}

function Shortcuts({ vals }) {
  return (
    <div className="tl-side" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
      <button type="button" className="tl-close" aria-label="Close" onClick={vals.tlToggleShortcuts}>{'×'}</button>
      <table className="tl-kv"><tbody>{vals.tlShortcutRows.map(([k, d]) => <tr key={k}><th scope="row"><kbd>{k}</kbd></th><td>{d}</td></tr>)}</tbody></table>
    </div>
  );
}

export default function TraceLab({ vals }) {
  const onKey = vals.tlKey;
  React.useEffect(() => {
    if (!onKey) return undefined;
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onKey]);
  const tabs = <TabBar vals={vals} />;
  if (vals.tlIsOverview) return <Traces vals={vals} tabs={tabs} />;
  return (
    <div className="tl-page">
      <Crumbs vals={vals} />
      <div className="tl-head">
        <h2>{'Hoist Traces'}</h2>
        <button type="button" className="tl-link" aria-label="Keyboard shortcuts" onClick={vals.tlToggleShortcuts}>{'?'}</button>
      </div>
      {tabs}
      <FilterBar vals={vals} />
      <UpdateLine vals={vals} />
      {vals.tlView === 'conversations' ? <Conversations vals={vals} /> : null}
      {vals.tlView === 'runs' ? <Runs vals={vals} /> : null}
      {vals.tlView === 'models' ? <ModelCalls vals={vals} /> : null}
      {vals.tlShortcutsOpen ? <Shortcuts vals={vals} /> : null}
    </div>
  );
}
```

Create `apps/frontend/src/styles/tracelab.css`:

```css
/* Trace Lab (screens/TraceLab.jsx): tabs, cards, collapsible panels, focus rings. Hoist tokens only. */
.tl-page { width: 100%; max-width: 1280px; min-width: 0; box-sizing: border-box; margin: 0 auto; padding: 0 24px 60px; }
.tl-crumbs { display: flex; align-items: center; gap: 8px; padding: 24px 0 0; font-size: 12px; color: var(--color-neutral-500); }
.tl-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; }
.tl-head h2 { margin: 0; font-size: 24px; }
.tl-tabs { display: flex; gap: 4px; margin-top: 14px; border-bottom: 1px solid var(--color-divider); flex-wrap: wrap; }
.tl-tab { font: inherit; font-size: 13px; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; background: transparent; color: var(--color-neutral-400); cursor: pointer; }
.tl-tab-on { color: var(--color-text); border-bottom-color: var(--color-accent); }
.tl-tab:focus-visible, .tl-link:focus-visible, .tl-row:focus-visible, .tl-chip-btn:focus-visible, .tl-panel summary:focus-visible, .tl-raw summary:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.tl-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 14px; }
.tl-chip-btn { font: inherit; font-size: 12px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--color-divider); background: transparent; color: var(--color-neutral-400); cursor: pointer; white-space: nowrap; }
.tl-on { border-color: var(--color-accent); background: var(--color-accent-900); color: var(--color-accent); }
.tl-field { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; color: var(--color-neutral-500); }
.tl-field select, .tl-search input { font: inherit; font-size: 12.5px; padding: 6px 8px; border-radius: 7px; border: 1px solid var(--color-divider); background: var(--color-surface); color: var(--color-text); }
.tl-search { display: flex; gap: 6px; margin-left: auto; min-width: min(100%, 240px); }
.tl-search input { flex: 1 1 auto; min-width: 0; }
.tl-update { display: flex; gap: 10px; align-items: center; margin-top: 8px; font-size: 12px; color: var(--color-neutral-500); }
.tl-link { font: inherit; font-size: 12px; border: none; background: transparent; color: var(--color-accent); cursor: pointer; padding: 2px 0; }
.tl-link:disabled { color: var(--color-ink-decorative); cursor: default; }
.tl-split { display: grid; grid-template-columns: minmax(0, 320px) minmax(0, 1fr); gap: 14px; margin-top: 14px; align-items: start; }
@media (max-width: 900px) { .tl-split { grid-template-columns: minmax(0, 1fr); } }
.tl-list, .tl-conv, .tl-card { border-radius: 10px; background: var(--color-surface); box-shadow: var(--shadow-sm); padding: 12px 14px; min-width: 0; }
.tl-card { margin-top: 14px; overflow-x: auto; }
.tl-list-head { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; margin-bottom: 8px; }
.tl-rows-list { list-style: none; margin: 0; padding: 0; }
.tl-row { width: 100%; text-align: left; font: inherit; display: flex; flex-direction: column; gap: 3px; padding: 9px 8px; border: none; border-left: 2px solid transparent; background: transparent; color: var(--color-text); cursor: pointer; }
.tl-row-on { background: var(--color-accent-900); border-left-color: var(--color-accent); }
.tl-row-q { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-meta { font-size: 11.5px; color: var(--color-neutral-500); }
.tl-bad { font-size: 12px; color: var(--st-risk); }
.tl-chip { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--color-divider); color: var(--color-neutral-400); width: fit-content; }
.tl-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.tl-answers { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.tl-note { font-size: 12px; color: var(--color-neutral-400); border-left: 2px solid var(--color-divider); padding-left: 8px; margin: 6px 0; }
.tl-title { margin: 0; font-size: 17px; }
.tl-timeline { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.tl-item article { border-radius: 8px; padding: 10px 12px; background: var(--color-surface); border: 1px solid var(--color-divider); border-left-width: 3px; }
.tl-user article { border-left-color: var(--color-text); }
.tl-model article { background: var(--st-dormant-bg); }
.tl-tool article, .tl-db article { background: var(--color-neutral-900); }
.tl-plan article, .tl-step article, .tl-note article { color: var(--color-neutral-400); }
.tl-gate article { background: var(--st-warn-bg); }
.tl-answer article { border-left-color: var(--color-accent); }
.tl-cot article { border: none; background: transparent; padding: 0 12px; }
.tl-cot { font-size: 11.5px; color: var(--color-neutral-500); margin: 0; font-style: italic; }
.tl-card-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; font-size: 12.5px; }
.tl-role { font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase; color: var(--color-neutral-500); }
.tl-text { font-size: 13.5px; line-height: 1.5; margin-top: 4px; white-space: pre-wrap; word-break: break-word; }
.tl-pre { font-family: ui-monospace, monospace; font-size: 11.5px; line-height: 1.5; margin: 6px 0 0; padding: 10px; border-radius: 8px; background: var(--color-bg); color: var(--color-text); white-space: pre-wrap; word-break: break-word; max-height: 420px; overflow: auto; }
.tl-panel, .tl-raw { margin-top: 6px; }
.tl-panel summary, .tl-raw summary { cursor: pointer; font-size: 12px; color: var(--color-neutral-400); }
.tl-kv th { text-align: left; font-weight: 400; color: var(--color-neutral-500); padding: 2px 12px 2px 0; font-size: 12px; }
.tl-kv td { font-size: 12px; font-family: ui-monospace, monospace; }
.tl-rows { overflow-x: auto; margin-top: 6px; }
.tl-rows table, .tl-table { border-collapse: collapse; min-width: 100%; }
.tl-rows th, .tl-rows td, .tl-table th, .tl-table td { padding: 5px 10px; border-bottom: 1px solid var(--color-divider); font-size: 12px; text-align: left; white-space: nowrap; }
.tl-ellipsis { max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
.tl-diff ins { background: var(--st-ok-bg); }
.tl-diff del { background: var(--st-risk-bg); }
.tl-pager { display: flex; justify-content: space-between; margin-top: 8px; }
.tl-side { position: fixed; top: 0; right: 0; bottom: 0; width: min(100%, 560px); box-sizing: border-box; overflow-y: auto; background: var(--color-surface); box-shadow: var(--shadow-lg); padding: 18px; z-index: 40; }
.tl-close { float: right; font-size: 20px; border: none; background: transparent; color: var(--color-neutral-400); cursor: pointer; }
.tl-steps { margin: 6px 0 0; padding-left: 20px; font-size: 12.5px; }
@media (prefers-reduced-motion: reduce) { .tl-page *, .tl-side * { transition: none !important; animation: none !important; } }
```

`apps/frontend/src/styles/base.css`:

old:
```css
@import './support.css';
```
new:
```css
@import './support.css';
@import './tracelab.css';
```

`apps/frontend/src/App.jsx`:

old:
```jsx
import Traces from './screens/Traces.jsx';
```
new:
```jsx
import TraceLab from './screens/TraceLab.jsx';
```

old:
```jsx
      {vals.isTraces ? <Traces vals={vals} /> : null}
```
new:
```jsx
      {vals.isTraces ? <TraceLab vals={vals} /> : null}
```

`apps/frontend/src/logic/support.js` — add one help line after the Admin-view line:

old:
```js
    'Administrators switch between Admin view and User view in the account menu (the initial in the top right). Admin view holds Integrations, Users & access, Hoist Crons, Chat memory, Hoist Traces, Skill lab and Audit trail; User view holds the reports of the chosen instance — on the Full platform: Buildings, Compliance, Vendors, Energy, Assets and Maintenance.',
```
new:
```js
    'Administrators switch between Admin view and User view in the account menu (the initial in the top right). Admin view holds Integrations, Users & access, Hoist Crons, Chat memory, Hoist Traces, Skill lab and Audit trail; User view holds the reports of the chosen instance — on the Full platform: Buildings, Compliance, Vendors, Energy, Assets and Maintenance.',
    'Hoist Traces, in Admin view, has four tabs. Overview lists every answer the chat gave, with its cost, speed, models and tools and the asker\'s own rating. Conversations shows each conversation as a timeline: the question, every model call and tool call with what came back, and the answer. Runs shows all live traffic for the period, and Model calls lists every model call with its time, tokens and cost.',
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd apps/frontend && node --test test/traceLabViews.test.mjs test/noInternalNames.test.mjs test/support.test.mjs test/supportFlow.test.mjs`
Expected: all pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 19: Full suites and the production build

**Files:**
- No new files. Fix only what these runs show is broken by M1's changes.

**Interfaces:**
- Consumes: everything above.
- Produces: green suites; the task log records the counts.

- [ ] **Step 1: Write the failing test**

No new test: this task runs the whole suites, which already contain every M1 test.

- [ ] **Step 2: Run it and see it fail**

Run the frontend suite and build on Node 24.18.1:
```sh
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend
export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"
npm test 2>&1 | tail -25
npm run build 2>&1 | tail -15
```
Run the svc-deepagents suite (unit; SQL tests skip):
```sh
tl_da_test tests/ 2>&1 | tail -25
```
Expected: any failure here is one M1 introduced (or a pre-existing one). Known pre-existing frontend failure: the timezone test noted in memory "Pre-push check 5 Oct" (1444/1445). Record the counts.

- [ ] **Step 3: Implement**

For each failure, decide: (a) caused by an M1 edit — fix the M1 code with an exact-string edit, never by weakening the other test; (b) present without M1 — check by reading the failing test and the code it covers (no git operations); list it in the task log as pre-existing and leave it. Common M1 causes to check first: a test that deep-compares `resetLiveData`'s or `authSignedOut`'s state patch (now also carrying the `tl*`/`tp*` keys); a test that clicks the old "Rate up" button; a bundle-size limit in `scripts/check-bundle.mjs` (if exceeded, report the new size to Hussain rather than raising the limit).

- [ ] **Step 4: Run it and see it pass**

Re-run the three commands from Step 2. Then the SQL suite against the scratch Postgres (Task 10 Step 4). Expected: frontend `npm test` green except any pre-existing failure listed in the log; `npm run build` succeeds; svc-deepagents suite has no failure in a Trace Lab file and no new failure elsewhere; the 14 SQL tests pass.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 20: Stub backend and browser walk

**Files:**
- Create: `~/Desktop/hoist/eval-demo/stub-tracelab.mjs`, `~/Desktop/hoist/eval-demo/start-tracelab.sh`, `~/Desktop/hoist/eval-demo/walks/verify-tracelab-conversations.mjs`

**Interfaces:**
- Consumes: the frontend from Tasks 11–18 served by Vite with `VITE_DEV_PROXY_TARGET` (the recipe in memory "hoistra-ui-verification"; the existing `eval-demo/start-demo.sh` pattern on other ports).
- Produces: a walk that prints `WALK PASSED`, screenshots `tracelab-0{1..4}-*.png`.

- [ ] **Step 1: Write the failing test**

Create `~/Desktop/hoist/eval-demo/walks/verify-tracelab-conversations.mjs`:

```js
// Walks Hoist Traces' Trace Lab (M1) against the stub on :4011 (via Vite on :5181): sign in as the company admin,
// the four tabs, the Overview's "Answers" with the asker's rating read only, "Review this answer in Conversations",
// the conversation read as Question → Model call → Tool → Model call → Answer, the report run's grouping chip, a
// reload landing on the same view, the keyboard (and its silence while typing), Model calls with its side panel,
// and Runs. Every non-GET is logged; only the stub's sign-in may write, and nothing may touch /feedback or /memories.
import { chromium } from 'playwright-core';

const OUT = process.env.WALK_OUT || new URL('.', import.meta.url).pathname;
const BASE = process.env.WALK_BASE || 'http://127.0.0.1:5181/';
const b = await chromium.launch({ channel: 'chrome', headless: true });
const page = await b.newPage({ viewport: { width: 1440, height: 1000 } });
const writes = [];
const errors = [];
page.on('request', (r) => { if (r.method() !== 'GET') writes.push(r.method() + ' ' + new URL(r.url()).pathname); });
page.on('pageerror', (e) => errors.push(String(e)));
const shot = (name) => page.screenshot({ path: OUT + '/tracelab-' + name + '.png', fullPage: true });
const step = (s) => console.log('· ' + s);
const ph = (t) => page.getByPlaceholder(t, { exact: true });
const text = (t) => page.getByText(t, { exact: true });
const tab = (name) => page.getByRole('tab', { name, exact: true });
const roles = async () => (await page.locator('article').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label'))))
  .map((l) => String(l).split(' · ')[0]);
const fail = (m) => { throw new Error(m); };

try {
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await ph('you@portfolio.com').waitFor({ timeout: 30000 });
  await ph('you@portfolio.com').fill('ada@example.com');
  await ph('Password').fill('correct-horse-battery-staple');
  await text('Continue').click();
  { const full = page.getByRole('button', { name: 'Enter Full platform' }); await full.waitFor({ timeout: 8000 }).then(() => full.click(), () => {}); }
  await page.getByTitle('Ada Admin').waitFor({ timeout: 20000 });
  step('signed in as the company admin');

  let nav = text('Hoist Traces').first();
  if (!(await nav.isVisible().catch(() => false))) {
    await page.getByTitle('Ada Admin').click();
    const adminView = text('Admin view');
    if (await adminView.count()) await adminView.first().click();
    nav = text('Hoist Traces').first();
  }
  await nav.click();
  for (const t of ['Overview', 'Conversations', 'Runs', 'Model calls']) await tab(t).waitFor();
  step('four tabs: Overview, Conversations, Runs, Model calls');

  await text('Answers').waitFor();
  if (await page.getByRole('button', { name: 'Rate up' }).count()) fail('the Overview still offers admin thumbs');
  await text('How many work orders there in August?').first().click();
  await text("The asker's rating:").waitFor();
  await text('Rated down').waitFor();
  await shot('01-overview-answer');
  step('Overview: "Answers", and the asker\'s rating read only');

  await page.getByRole('button', { name: 'Review this answer in Conversations' }).click();
  await page.waitForURL(/tl=conversations/);
  if (!/conv=s%3Achat-1/.test(page.url()) || !/turn=t2/.test(page.url())) fail('the URL does not name the conversation and answer: ' + page.url());
  await page.locator('h3.tl-title', { hasText: 'Boiler certificates' }).waitFor();
  await text('Answer 2 of 2').waitFor();
  const order = (await roles()).filter((r) => !['What the model was given', 'About model calls'].includes(r));
  if (order.join(' → ') !== 'Question → Model call → Tool → Model call → Answer') fail('timeline order: ' + order.join(' → '));
  if (!(await page.getByText('unavailable').count())) fail('a missing model field does not read "unavailable"');
  await shot('02-conversation');
  step('the conversation reads Question → Model call → Tool → Model call → Answer');

  await text('Grouped by report run').waitFor();
  step('the report run carries its grouping chip');

  await page.reload({ waitUntil: 'domcontentloaded' });
  await tab('Conversations').waitFor({ timeout: 20000 });
  if ((await tab('Conversations').getAttribute('aria-selected')) !== 'true') fail('the reload did not land on Conversations');
  await text('Answer 2 of 2').waitFor();
  step('a reload lands on the same view and answer');

  await page.getByLabel('Search questions and answers').focus();
  await page.keyboard.press('j');
  await page.waitForTimeout(300);
  if ((await page.locator('h3.tl-title').textContent()) !== 'Boiler certificates') fail('typing j in the search box moved the list');
  await page.locator('h3.tl-title').click();
  await page.keyboard.press('j');
  await page.locator('h3.tl-title', { hasText: 'Report card run' }).waitFor();
  await page.keyboard.press('?');
  await page.getByRole('dialog', { name: 'Keyboard shortcuts' }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Keyboard shortcuts' }).waitFor({ state: 'detached' });
  step('keyboard: j walks the list, typing does not, ? opens the shortcuts and Esc closes them');

  await tab('Model calls').click();
  await page.getByText(/2 calls · p50/).waitFor();
  await page.getByRole('button', { name: /Open the model call at/ }).nth(1).click();
  const dialog = page.getByRole('dialog', { name: 'Model call' });
  await dialog.waitFor();
  if (!(await dialog.getByText('unavailable').count())) fail('the model-call panel does not say "unavailable"');
  await shot('03-model-calls');
  await dialog.getByRole('button', { name: 'Close' }).click();
  step('Model calls: the aggregate line and a side panel');

  await tab('Runs').click();
  await page.getByRole('button', { name: 'All live traffic' }).waitFor();
  await shot('04-runs');
  step('Runs: All live traffic');

  const allowed = new Set(['POST /backend/ops-intelligence/api/auth/login', 'POST /backend/ops-intelligence/api/auth/refresh']);
  const bad = writes.filter((w) => !allowed.has(w));
  if (bad.length) fail('writes the page must not make:\n' + bad.join('\n'));
  if (writes.some((w) => /\/feedback|\/memories/.test(w))) fail('a write reached /feedback or /memories');
  if (errors.length) fail('page errors:\n' + errors.join('\n'));
  step('no write but sign-in; no page errors');
  console.log('WALK PASSED');
} finally {
  await b.close();
}
```

- [ ] **Step 2: Run it and see it fail**

```sh
S=/private/tmp/claude-501/-Users-hussain-Desktop-hoist/$(ls -t /private/tmp/claude-501/-Users-hussain-Desktop-hoist | head -1)/scratchpad/tl-walk
mkdir -p "$S" && cd "$S" && [ -f package.json ] || npm init -y >/dev/null
npm i playwright-core@1 >/dev/null 2>&1
cp ~/Desktop/hoist/eval-demo/walks/verify-tracelab-conversations.mjs "$S/"
export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"
WALK_OUT="$S" node "$S/verify-tracelab-conversations.mjs"
```
(Use the session's own scratchpad directory for `S` when it is known; the walk file is copied next to `node_modules` because ES modules resolve `playwright-core` from the script's folder.)
Expected: fails at `page.goto` with `net::ERR_CONNECTION_REFUSED` — nothing is serving :5181 yet.

- [ ] **Step 3: Implement**

Create `~/Desktop/hoist/eval-demo/stub-tracelab.mjs`:

```js
// A throwaway backend for the Trace Lab M1 browser walk (walks/verify-tracelab-conversations.mjs): sign-in for one
// company admin, the Overview's /api/traces reads, and the eight read-only /api/trace-lab routes, all in memory and
// shaped like the service's records (spec "Trace capture, normalizer and conversations" §3.2, §5.4). Every non-GET
// other than sign-in answers 405 and is logged. Nothing here touches a database.
import http from 'node:http';

const PORT = Number(process.env.STUB_PORT || 4011);
const ADMIN = { id: 'u-admin', email: 'ada@example.com', full_name: 'Ada Admin', organization_id: 'org-1', status: 'active', email_verified: true, role: 'admin', role_label: 'Admin' };
let n = 0;
const tokens = () => { n += 1; return { access_token: 'acc-' + n, refresh_token: 'ref-' + n, token_type: 'Bearer', expires_in: 1800 }; };
const send = (res, status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
const T = (m) => new Date(Date.now() - m * 60000).toISOString();
const COT = "Model reasoning is not shown. Each model card shows what the model was sent and what it returned, as recorded. Hoist does not keep a model's private reasoning and stores no provider summary of it, so a missing reasoning transcript is not a missing step. The plan, routing decisions, tool calls and their results are the evidence.";
const NOTE = 'Private reasoning: not recorded (not a missing step).';
const UNAV = ['finish_reason', 'response_id', 'provider', 'time_to_first_token', 'tool_call_ids', 'cache_write_tokens', 'reasoning_tokens'];
const Q1 = 'Which boilers are at B-301?';
const Q2 = 'How many work orders there in August?';
const A2 = '758 work orders were raised at B-301 in August.';

function card(span, label, over) {
  return Object.assign({ span_id: span, turn_id: 't2', call_kind: 'llm', group: 'orchestrator loop', model: 'claude-sonnet-5', provider: null,
    operation: 'orchestrator loop › model', label, request_kind: 'loop_summary', request_complete: false, prompt_ref: 'p',
    request: { messages: 3, last_human_message: Q2, tool_results_since_last_human: label === 'tool decision' ? 0 : 1 },
    response: label === 'tool decision' ? { content: '', tool_calls: [{ name: 'answer_from_records', args: { q: 'work orders August' } }] } : { content: A2, tool_calls: [] },
    model_parameters: null, usage: { input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940 }, latency_s: 1.8,
    total_cost: 0.004, cost_status: 'priced', status: 'ok', status_message: null, raw: { input: {}, output: {} },
    unavailable: UNAV.concat(['model_parameters']), withheld: [], cot_note: NOTE }, over || {});
}
const msg = (turn, id, role, extra) => Object.assign({ id: turn + ':' + id + ':' + role, role, turn_id: turn, span_id: id, depth: 1, ok: true, collapse: false, truncated: false }, extra);
const USAGE = { input_tokens: 2000, output_tokens: 100, cache_read_tokens: 0, total_tokens: 2100, cost_usd: 0.031, cost_complete: true, llm_calls: 2, tool_calls: 1, latency_ms: 9000 };
const meta = (idx, extra) => Object.assign({ session_id: 'chat-1', session_kind: 'observed', session_label: 'Boiler certificates', conversation_key: 's:chat-1', run_id: 'live',
  channel: 'chat', channel_source: 'client', origin: 'chat.typed', turn_index: idx, turn_count: 2, attempt: 1, attempt_source: 'recorded', retry_of: null,
  asker: { email: 'fm@example.com', role: 'user' } }, extra || {});
const CALL1 = card('m1', 'tool decision');
const CALL2 = card('m2', 'answer synthesis', { model: null, usage: { input_tokens: null, output_tokens: null, cache_read_tokens: null, total_tokens: null }, latency_s: null, total_cost: null, cost_status: 'unavailable' });
const T2 = { id: 't2', trace_id: 't2', normalizer_version: 'norm-1', timestamp: T(10), models: ['claude-sonnet-5'], tools: ['answer_from_records'],
  input: Q2, output: A2, ok: true, error: null, cot_boundary: COT, flags: [], usage: USAGE, meta: meta(2),
  feedback: { rating: 'down', comment: 'It counted cancelled jobs.', by_asker: true, at: T(5) }, model_calls: [CALL1, CALL2],
  messages: [
    msg('t2', 'root', 'user', { depth: 0, text: Q2, anchor: { turn_id: 't2', span_id: 'root', field: 'question' } }),
    msg('t2', 'p', 'system_note', { subtype: 'prompt', name: 'prompt assembled', label: 'What the model was given', input: { system_prompt: 'You answer questions about the portfolio.' }, output: { system_prompt_chars: 40 }, collapse: true }),
    msg('t2', 'm1', 'model_call', { label: 'tool decision', call_kind: 'llm', model_call: CALL1 }),
    msg('t2', 't1', 'tool_call', { name: 'answer_from_records', arguments: { q: 'work orders August' }, requested_by: 'm1', latency_ms: 3000 }),
    msg('t2', 't1', 'tool_result', { name: 'answer_from_records', content: { total: 758, building: 'B-301' }, chars: 32 }),
    msg('t2', 'm2', 'model_call', { label: 'answer synthesis', call_kind: 'llm', model_call: CALL2 }),
    msg('t2', 'root', 'assistant', { depth: 0, subtype: 'answer', text: A2, rewritten_by: null })] };
const T1 = { id: 't1', trace_id: 't1', normalizer_version: 'norm-1', timestamp: T(20), models: ['claude-sonnet-5'], tools: [], input: Q1, output: 'Four boilers.', ok: true, error: null,
  cot_boundary: COT, flags: ['spans_purged', 'legacy_provenance'], usage: Object.assign({}, USAGE, { tool_calls: 0 }), meta: meta(1, { channel_source: 'inferred', origin: null }),
  feedback: null, model_calls: [],
  messages: [msg('t1', 'turn', 'user', { depth: 0, text: Q1 }), msg('t1', 'turn', 'assistant', { depth: 0, subtype: 'answer', text: 'Four boilers.' })] };
const T3 = { id: 't3', trace_id: 't3', normalizer_version: 'norm-1', timestamp: T(60), models: ['claude-sonnet-5'], tools: [], input: 'Weekly compliance summary', output: 'All current.', ok: true, error: null,
  cot_boundary: COT, flags: [], usage: Object.assign({}, USAGE, { tool_calls: 0 }),
  meta: { session_id: 'report-1a2b-1759300000', session_kind: 'synthetic', session_label: 'Report card run', conversation_key: 's:report-1a2b-1759300000', run_id: 'live',
    channel: 'report', channel_source: 'recorded', turn_index: 1, turn_count: 1, attempt: 1, asker: { email: 'cron@example.com', role: 'admin' } },
  feedback: null, model_calls: [],
  messages: [msg('t3', 'turn', 'user', { depth: 0, text: 'Weekly compliance summary' }), msg('t3', 'turn', 'assistant', { depth: 0, subtype: 'answer', text: 'All current.' })] };
const header = (rec, idx) => ({ turn_id: rec.id, turn_index: idx, started_at: rec.timestamp, question: rec.input, answer: rec.output, ok: true,
  channel: rec.meta.channel, channel_source: rec.meta.channel_source, origin: rec.meta.origin || null, attempt: 1, attempt_source: 'recorded', retry_of: null,
  models: rec.models, tool_count: rec.usage.tool_calls, cost_usd: rec.usage.cost_usd, latency_ms: rec.usage.latency_ms, flags: rec.flags });
const CONVS = {
  's:chat-1': { key: 's:chat-1', session_id: 'chat-1', session_kind: 'observed', session_label: 'Boiler certificates', title: 'Boiler certificates', thread_deleted: false,
    run: { run_id: 'live', kind: 'live' }, turn_count: 2, turns_truncated: false, turns: [header(T1, 1), header(T2, 2)], flags: ['legacy_provenance'], normalizer_version: 'norm-1',
    byTurn: { t1: T1, t2: T2 }, first: 't1' },
  's:report-1a2b-1759300000': { key: 's:report-1a2b-1759300000', session_id: 'report-1a2b-1759300000', session_kind: 'synthetic', session_label: 'Report card run', title: null,
    thread_deleted: false, run: { run_id: 'live', kind: 'live' }, turn_count: 1, turns_truncated: false, turns: [header(T3, 1)], flags: ['synthetic_session'], normalizer_version: 'norm-1',
    byTurn: { t3: T3 }, first: 't3' }
};
const ROWS = [
  { key: 's:chat-1', session_id: 'chat-1', session_kind: 'observed', session_label: 'Boiler certificates', title: 'Boiler certificates', run_id: 'live', golden: null,
    first_turn_at: T(20), last_turn_at: T(10), turns_in_window: 2, matching_turns: 2, failed_turns: 0, first_question: Q1, channels: ['chat'], channel_sources: ['client', 'inferred'],
    models: ['claude-sonnet-5'], tool_count: 1, cost_usd: 0.062, asker: { email: 'fm@example.com', role: 'user' }, flags: ['legacy_provenance'], reviewed_count: null },
  { key: 's:report-1a2b-1759300000', session_id: 'report-1a2b-1759300000', session_kind: 'synthetic', session_label: 'Report card run', title: null, run_id: 'live', golden: null,
    first_turn_at: T(60), last_turn_at: T(60), turns_in_window: 1, matching_turns: 1, failed_turns: 0, first_question: 'Weekly compliance summary', channels: ['report'],
    channel_sources: ['recorded'], models: ['claude-sonnet-5'], tool_count: 0, cost_usd: 0.031, asker: { email: 'cron@example.com', role: 'admin' }, flags: ['synthetic_session'], reviewed_count: null }
];
const TURN_ROWS = [{ turn_id: 't2', session_id: 'chat-1', email: 'fm@example.com', question: Q2, answer: A2, started_at: T(10), llm_calls: 2, tool_calls: 1, cost_usd: 0.031,
  cost_complete: true, latency_ms: 9000, ok: true, models: ['claude-sonnet-5'], tools: ['answer_from_records'], feedback_rating: 'down' }];
const CALLS = [
  { span_id: 'm1', turn_id: 't2', conversation_key: 's:chat-1', started_at: T(10), kind: 'llm', name: 'model', agent: 'orchestrator loop', label: 'tool decision', model: 'claude-sonnet-5',
    latency_ms: 1800, input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, cost_usd: 0.004, ok: true, error: null, request_kind: 'loop_summary', response_preview: '', withheld: false, question_excerpt: Q2 },
  { span_id: 'm2', turn_id: 't2', conversation_key: 's:chat-1', started_at: T(9.9), kind: 'llm', name: 'model', agent: 'orchestrator loop', label: 'answer synthesis', model: null,
    latency_ms: null, input_tokens: null, output_tokens: null, cache_read_tokens: null, cost_usd: null, ok: true, error: null, request_kind: 'loop_summary', response_preview: A2, withheld: false, question_excerpt: Q2 }
];
const missing = (res, error, reason) => send(res, 404, { detail: { ok: false, error, reason } });

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const ch of req) chunks.push(ch);
  const raw = Buffer.concat(chunks).toString('utf8');
  const body = raw ? JSON.parse(raw) : {};
  const u = new URL(req.url, 'http://x');
  const p = u.pathname;
  console.log(req.method, p + (u.search || ''));
  const A = '/backend/ops-intelligence/api/auth';
  if (p === A + '/config') return send(res, 200, { ok: true, self_registration: true, password: { min_length: 12 }, otp: { code_length: 6 } });
  if (p === A + '/login' && req.method === 'POST') {
    if (body.email !== ADMIN.email || body.password !== 'correct-horse-battery-staple') return send(res, 401, { detail: { ok: false, reason: 'invalid_credentials', error: 'That email address and password do not match an account.' } });
    return send(res, 200, { ok: true, user: ADMIN, tokens: tokens() });
  }
  if (p === A + '/refresh' && req.method === 'POST') return send(res, 200, { ok: true, user: ADMIN, tokens: tokens() });
  if (p === A + '/me') return send(res, 200, { ok: true, user: ADMIN });
  if (req.method !== 'GET') return send(res, 405, { detail: { ok: false, error: 'The stub is read only.' } });
  const D = '/backend/deep-agents/api';
  if (p === D + '/traces/turns') return send(res, 200, { ok: true, available: true, can_manage: true, turns: TURN_ROWS });
  if (p === D + '/traces/stats') return send(res, 200, { ok: true, totals: { turns: 1, failed: 0, cost_usd: 0.031, llm_calls: 2, tool_calls: 1, users: 1, p50_ms: 9000, p95_ms: 9000, thumbs_up: 0, thumbs_down: 1, cache_read_tokens: 0 }, by_day: [], by_model: [], by_tool: [], by_agent: [] });
  if (p === D + '/traces/turns/t2') return send(res, 200, { ok: true, turn: Object.assign({}, TURN_ROWS[0], { user_id: 'u-fm', feedback_by: 'u-fm', feedback_comment: 'It counted cancelled jobs.',
    input_tokens: 2000, output_tokens: 100, cache_read_tokens: 0, spans: [{ id: 'root', parent_id: null, seq: 0, kind: 'turn', name: 'turn', started_at: T(10), ended_at: T(9.85), latency_ms: 9000, ok: true }] }) });
  const L = D + '/trace-lab';
  if (p === L + '/conversations') return send(res, 200, { ok: true, as_of: new Date().toISOString(), watermark: new Date(Date.now() - 2000).toISOString(), total: ROWS.length, next_cursor: null, matched_turn_id: null, conversations: ROWS });
  if (p === L + '/conversations/updates') {
    const k = Number(process.env.STUB_NEWS || 0);
    return send(res, 200, { ok: true, new_turns: k, new_conversations: k ? 1 : 0, latest: null, watermark: new Date(Date.now() - 2000).toISOString(), as_of: new Date().toISOString() });
  }
  if (p === L + '/conversations/facets') return send(res, 200, { ok: true, as_of: new Date().toISOString(), channels: [{ value: 'chat', turns: 2 }, { value: 'report', turns: 1 }], channel_sources: [], session_kinds: [], models: [] });
  let mm = /^\/backend\/deep-agents\/api\/trace-lab\/conversations\/(.+)$/.exec(p);
  if (mm) {
    const c = CONVS[decodeURIComponent(mm[1])];
    const active = c && c.byTurn[u.searchParams.get('turn') || c.first];
    if (!active) return missing(res, "That conversation is not in this company's records.", 'conversation_not_found');
    const { byTurn, first, ...rest } = c;
    return send(res, 200, { ok: true, conversation: Object.assign({}, rest, { active }) });
  }
  mm = /^\/backend\/deep-agents\/api\/trace-lab\/turns\/(.+)$/.exec(p);
  if (mm) {
    const id = decodeURIComponent(mm[1]);
    const key = Object.keys(CONVS).find((k) => CONVS[k].byTurn[id]);
    if (!key) return missing(res, "That answer is not in this company's records.", 'turn_not_found');
    return send(res, 200, { ok: true, trace: CONVS[key].byTurn[id], conversation_key: key });
  }
  if (p === L + '/runs') return send(res, 200, { ok: true, golden_available: false, runs: [{ run_id: 'live', kind: 'live', name: 'All live traffic', recorded: false, status: 'live',
    started_at: T(60), ended_at: T(10), conversation_count: 2, turn_count: 3, models: ['claude-sonnet-5'], cost_usd: 0.093 }] });
  if (p === L + '/model-calls') return send(res, 200, { ok: true, as_of: new Date().toISOString(), next_cursor: null, retention_days: 90, spans_available_since: T(60 * 24 * 90),
    aggregate: { calls: 2, errors: 0, latency_p50_ms: 1800, latency_p95_ms: 1800, input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940, cost_usd: 0.004, unpriced_calls: 1,
      by_model: [{ model: 'claude-sonnet-5', calls: 1, errors: 0, latency_p50_ms: 1800, cost_usd: 0.004 }] }, calls: CALLS });
  mm = /^\/backend\/deep-agents\/api\/trace-lab\/model-calls\/(.+)$/.exec(p);
  if (mm) {
    const i = ['m1', 'm2'].indexOf(mm[1]);
    if (i < 0) return missing(res, "That model call is not in this company's records.", 'model_call_not_found');
    return send(res, 200, { ok: true, model_call: [CALL1, CALL2][i], turn_id: 't2', conversation_key: 's:chat-1' });
  }
  return send(res, 404, { detail: 'not stubbed' });
}).listen(PORT, '127.0.0.1', () => console.log('trace lab stub on http://127.0.0.1:' + PORT));
```

Create `~/Desktop/hoist/eval-demo/start-tracelab.sh`:

```sh
#!/bin/sh
# Starts the Trace Lab M1 stub (in memory, :4011) and the web app on :5181, built from the repo's working tree.
# Sign in as ada@example.com, password correct-horse-battery-staple. STUB_NEWS=1 makes the 45 s check report news.
D=$(cd "$(dirname "$0")" && pwd)
export PATH="/Users/hussain/.nvm/versions/node/v24.18.1/bin:$PATH"
for port in 4011 5181; do PID=$(lsof -tnP -iTCP:$port -sTCP:LISTEN 2>/dev/null); [ -n "$PID" ] && kill $PID; done
sleep 1
nohup node "$D/stub-tracelab.mjs" > "$D/stub-tracelab.log" 2>&1 &
cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend && VITE_DEV_PROXY_TARGET=http://127.0.0.1:4011 nohup npx vite --port 5181 --strictPort --host 127.0.0.1 > "$D/vite-tracelab.log" 2>&1 &
i=0; until curl -s -o /dev/null http://127.0.0.1:5181/ || [ $i -ge 60 ]; do sleep 0.5; i=$((i+1)); done
echo "Trace Lab stub running: http://127.0.0.1:5181"
```

Then: `chmod +x ~/Desktop/hoist/eval-demo/start-tracelab.sh && sh ~/Desktop/hoist/eval-demo/start-tracelab.sh`. Nothing here reaches :3000 or any database: Vite proxies `/backend/*` to the stub only.

- [ ] **Step 4: Run it and see it pass**

Re-run Step 2's last command. Expected: the eight `·` steps print and the last line is `WALK PASSED`; four screenshots land in `$S`. Look at each screenshot (the conversation should read top to bottom as Question, the prompt note, Model call · tool decision, Tool · Records query, Model call · answer synthesis with "unavailable", Answer). Then stop the servers: `for port in 4011 5181; do PID=$(lsof -tnP -iTCP:$port -sTCP:LISTEN 2>/dev/null); [ -n "$PID" ] && kill $PID; done`.

- [ ] **Step 5: Checkpoint** — leave uncommitted; list the changed files in the task log (no git commit unless Hussain says "commit").

---

### Task 21: Hussain's verification and deploy (not for a Claude session)

**Files:** none changed. Every command below is Hussain's.

- [ ] **Step 1: Prerequisites (stop here if any is not met)**
  - **P1:** hoist-f5's company-boundary fixes are on the branch you will build (they were only in `stash@{0}`). Without them, rebuilding svc-deepagents regresses the 34-floors leak and chat tools read every company's traces.
  - **M0 is deployed and its `0001_foundation.sql` is applied to hoistra_test (P3 for M0)**, and Trace Lab verifies `ready`. M1 adds no migration: there is nothing to `migrate apply` for M1, and none should be run for it.
  - Read-only status check on the running stack (after the M0 rebuild):
    ```sh
    cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
    docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml \
      exec svc-deepagents python -m src.trace_lab.migrate status
    ```
    Expected: status `ready`. If not, the M1 routes will answer 503 and the Trace Lab tabs show "Trace Lab is not available right now"; fix M0 first.

- [ ] **Step 2: Re-run the suites yourself**
  ```sh
  cd /Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend && export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH" && npm test && npm run build
  # svc-deepagents: paste tl_da_test from Global Constraints, then
  tl_da_test tests/test_trace_lab_m1_preconditions.py tests/test_trace_lab_redaction.py tests/test_trace_lab_normalize.py tests/test_trace_lab_grouping.py tests/test_trace_lab_conversations.py tests/test_trace.py tests/test_rest_turns_are_traced.py
  ```
  Optional SQL run against a throwaway Postgres (Task 10 Step 2–4 commands).

- [ ] **Step 3: The stub walk**
  ```sh
  sh ~/Desktop/hoist/eval-demo/start-tracelab.sh
  W=$(mktemp -d /tmp/tl-walk.XXXX) && cd "$W" && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null 2>&1
  cp ~/Desktop/hoist/eval-demo/walks/verify-tracelab-conversations.mjs "$W/" && WALK_OUT="$W" node "$W/verify-tracelab-conversations.mjs"
  ```
  Expected: `WALK PASSED`; screenshots in `$W`. Open http://127.0.0.1:5181 yourself if you want to click around (ada@example.com / correct-horse-battery-staple). Stop the servers afterwards (the `lsof … kill` line from Task 20 Step 4).

- [ ] **Step 4: Before rebuilding, check for other sessions' half-done work** (memory: concurrent sessions share the working tree)
  ```sh
  cd /Users/hussain/Desktop/hoist/Hoistra_Plenum && git status --short && git diff --stat
  ```
  Every changed file should be one this plan lists (File Structure) or one you know about. Ask any other active session before building.

- [ ] **Step 5: Rebuild svc-deepagents and the frontend, then reload the gateway**
  ```sh
  cd /Users/hussain/Desktop/hoist/Hoistra_Plenum
  docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps svc-deepagents frontend-app
  docker ps --format '{{.Names}}' | grep -E 'gateway|svc-deepagents|frontend'
  docker exec hoistra_plenum-gateway-app-1 nginx -s reload
  ```
  Verify the code is really in the container (memory: deployed images drift):
  ```sh
  docker exec hoistra_plenum-svc-deepagents-1 python -c "import src.trace_lab.normalize as n, src.api.routes.trace_lab_conversations as r; print(n.NORMALIZER_VERSION, len(r.router.routes))"
  ```
  Expected: `norm-1 8`. (Use the container name `docker ps` printed if it differs.)

- [ ] **Step 6: Manual checks on the real stack (read only)**
  Signed in as a company admin (or as superadmin viewing a test company):
  1. Administration › Hoist Traces shows tabs Overview, Conversations, Runs, Model calls. As a superadmin not viewing a company: only the Overview, no tabs.
  2. Overview: the table is titled "Answers"; an open answer shows "The asker's rating:" as text, no thumbs buttons, and "Review this answer in Conversations". In DevTools › Network, nothing on this page sends a POST to `/api/traces/turns/…/feedback`.
  3. Pick a real multi-turn chat: it reads as one conversation; its answers switch with the "Answer n" buttons (or `n`/`p`); the timeline order is Question → (prompt note) → Model call · tool decision → Tool → Model call · answer synthesis → Answer.
  4. A report-card run shows "Grouped by report run"; a re-run shows "Grouped by re-run"; older turns show "Where this question came from is inferred…".
  5. Reload the page: the same tab, conversation and answer come back (URL `?tl=conversations&run=live&conv=…&turn=…`).
  6. Model calls: the header's call count and cost are plausible against the Overview's tiles for the same period; a row opens the side panel; missing provider fields read "unavailable".
  7. Wait 45 s on Conversations: "Updated hh:mm" appears; an open conversation and any open panel stay open.
  8. Superadmin: switch the viewed company — the Trace Lab empties and the new company's conversations load; nothing of the previous company remains.
  9. svc-deepagents logs show no `trace_lab` errors: `docker logs --since 10m hoistra_plenum-svc-deepagents-1 2>&1 | grep -i trace_lab | tail -20`.

- [ ] **Step 7: Tell Bala, and leave Azure alone**
  - R14: admins can no longer rate answers from Hoist Traces (the asker's own thumbs in the chat are unchanged).
  - Azure is not redeployed by this plan. Its images only verify; when you decide to deploy, it is the manual all-in-one rebuild, after P1 and after M0's migration is in hoistra_test.
