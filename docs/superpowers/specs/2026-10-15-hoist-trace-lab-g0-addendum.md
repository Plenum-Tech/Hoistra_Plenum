# Hoist Trace Lab — Golden Rule addendum (binding; supersedes conflicting parts of the design spec)

**Date:** 2026-10-15
**Status:** approved by Hussain (AskUserQuestion, 15 Oct ~16:50), following the senior's review.
**Applies to:** every Trace Lab plan, task and review from Phase 1 on. Where this addendum and
`2026-10-15-hoist-trace-lab-design.md` disagree, this addendum wins.

## 1. The Golden Rule (G0)

Trace Lab is a **separate module**. Existing application logic is never edited for it, and the existing application
must not be affected in any way: behaviour, data, startup, performance or UI. Existing code is reused only by
**importing and calling it**. The existing app behaves identically whether Trace Lab is absent, disabled, enabled or
broken.

The only edits to existing files allowed are the **approved attachments** in §3. Each is additive, inert while its flag
is off, and pinned by a guard test.

## 2. Architecture

### 2.1 A separate service
- **Folder:** `apps/backend/cafm-connector-service-final/svc-trace-lab/`. It holds the package `trace_lab/`, tests in
  `tests/`, its own `pyproject.toml`, `Dockerfile` and `README.md`.
- **Process:** its own process and container (`trace-lab`), FastAPI app `trace_lab.app:app`, port **8010**,
  `--root-path /backend/trace-lab`.
- **Reuse:** it imports svc-deepagents' `src` package as a library (absolute imports, for example
  `from src.agents import skill_lab`). The image copies svc-deepagents' `src` and `skills` beside it. Nothing in
  svc-deepagents is edited or imported back.
- **Never at start-up:** svc-deepagents' startup is never run. No `ensure_tables` of any existing store (activity_log,
  chat_threads, chat_memories, trace, ingest batches, skill_lab, skill_overlays), no trace purge, no checkpointer setup.

### 2.2 Configuration (environment only; never `.env` files baked into images)
| Variable | Value in the trace-lab container | Why |
|---|---|---|
| `DB_URL` | the main database DSN (as svc-deepagents) | `src.config.settings` needs it at import time; used READ-ONLY |
| `TRACE_LAB_DB_URL` | the separate Trace Lab database DSN | Trace Lab's own tables |
| `TRACE_LAB_SCHEMA` | `verify` (default) \| `apply` | boot never runs DDL on shared databases; `apply` only on scratch/local or by Hussain |
| `TRACE_LAB_ENABLED` | `1` to run (the Azure supervisord program sleeps otherwise) | kill switch |
| `ACTIVITY_LOG_ENABLED` | `false` | no activity rows |
| `MIGRATION_BASE_URL` | `http://127.0.0.1:9` | raw-httpx writer tools can reach nothing |
| `UDR_BASE_URL`, `OPERATIONS_INTELLIGENCE_BASE_URL`, `WO_MANAGEMENT_BASE_URL`, `DOC_RAG_BASE_URL` | `http://127.0.0.1:8010/__proxy/<svc>` | every downstream call goes through Trace Lab's allow-list proxy (§2.5) |
| `TRACE_LAB_UPSTREAM_UDR`, `…_OI`, `…_WOM`, `…_DOCRAG` | the real service URLs | where the proxy forwards allowed reads |
| `OPENAI_MODEL`, `ORCHESTRATOR_LLM_ROUTING`, `COMPLIANCE_SKILL_PATH`, `COMPLIANCE_SELECTIVE_SKILLS`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | same values as svc-deepagents | golden runs measure the same setup as live chat |
| `TRACE_LAB_JUDGE_MODEL` | optional | grader and judge model (else `COMPLIANCE_SUMMARY_MODEL`, default claude-sonnet-5) |

### 2.3 Databases
- **Trace Lab's own data:** a **separate Postgres database** (proposed name `hoist_tracelab`), reached with
  `TRACE_LAB_DB_URL`. It never shares a database with `plenum_cafm`, so the chat's generic SQL tools and svc-udr's
  custom SELECT can never read it. Postgres cannot query across databases. The tables live in schema `tracelab`.
  Versioned migrations sit in `svc-trace-lab/migrations/NNNN_name.sql` with a ledger `tracelab.schema_migrations`.
  Boot verifies the schema; it applies migrations only with `TRACE_LAB_SCHEMA=apply`.
- **Main database (read-only):**
  - Before any first use, Trace Lab pre-sets `src.database._engine` to its own engine: `pool_size=2`,
    `max_overflow=0`, `connect_args.server_settings = {"default_transaction_read_only": "on",
    "application_name": "trace-lab"}`. The TLS arguments are built with svc-deepagents' own
    `_strip_sslmode_from_async_url` and `_async_engine_connect_args`, imported.
  - Any write from any code path in this process then fails. A golden case that hits one is marked `degraded`.
  - A DBA-created SELECT-only role is the recommended hardening.
- **Creating the database** is Hussain's or the DBA's action, from SQL prepared in the plan. No Claude session runs it.

### 2.4 Auth and scope
- **Who the caller is:** `src.services.principal.current_principal` (reused) resolves the bearer through ops-intel
  `/api/auth/me`.
- **Roles (design D7 plus the senior's decision 3):**
  - Company admin, or a superadmin acting for the company: review, golden sets, golden runs.
  - Platform superadmin: confirms failure modes and owns judges and monitoring.
  - Company admins may **propose** failure modes.
- **Naming a company:** `organization_id` is honoured only for a superadmin. Every read is scoped to the acting
  company, reusing the pure `src.agents.trace._scope_sql` where it fits.
- **Selected building (R34):** Trace Lab reads the starter's selected building from the `/api/auth/me` payload it
  fetches. It does not edit `principal.py`.

### 2.5 Golden runs: Skill lab extended, its replay switch replaced on purpose
- **The orchestrator:** a Trace Lab-owned `DeepAgentOrchestrator(..., checkpointer=MemorySaver())`, created lazily in
  this process. There are no checkpoint rows, and loop follow-ups keep their context in memory.
- **No REPLAY flag.** Safety comes from everything else in this process:
  - no existing stores are initialised, so there are no memory, thread or trace writes and no recall counters or
    forget;
  - activity logging is off;
  - the main database is read-only;
  - the raw-httpx writers point at a dead port;
  - every downstream service URL points at the **allow-list proxy**.
- **The proxy** is a set of routes mounted in Trace Lab's own app at `/__proxy/{service}/{path}`:
  - It accepts loopback callers only.
  - It forwards GET, HEAD and OPTIONS, plus an explicit table of read-only POSTs (service, method, path pattern and
    body rule; for example svc-udr `tables/query/select` and `{table}/records/search`, and the read POSTs listed in the
    replay-footprint findings).
  - Anything else gets a 403 `refused_by_trace_lab` and is logged against the running case.
  - A refusal marks the answer `degraded`, never `wrong`.
- **The entry point** is `orchestrator.stream()`, the chat UI's path, consumed until `workflow_completed`. The run has a
  fresh session id `golden-<runhex>-<case_key>-r<k>`, the page context from the case or the console default, and the
  starter's principal, company and bearer set in the same ContextVars `skill_lab.replay` sets.
- **Evidence capture:**
  - Trace Lab pre-sets `src.agents.trace`'s current Turn to an in-memory `trace.Turn`, so the normal hooks fill the
    span tree. Nothing writes, because the trace store is not ready.
  - Trace Lab stores the answer, the span tree, the cost ledger entries, latency and the refusal log in its own
    database.
  - These private touch points are pinned by Trace Lab's own tests.
- **Reuse from Skill lab:**
  - `skill_lab._ask_json` for the grader and judges (golden-specific prompts and role names);
  - `skill_lab.WRITE_INTENT` and `usable()` to screen uploaded questions, plus questions starting with "forget" and
    credential-like text;
  - `skill_lab.split_of`.
  - Never `start`, `_store_result`, `_set_run`, `ensure_tables` or its lock.
- **Stepped from the browser** (D9): the page calls `POST /golden-runs/{id}/step` with its fresh bearer, and each step
  runs one case in the background. Concurrency is one case per process. A server-side job acting as the reviewer
  (senior's note 3) needs an auth design in ops-intel, so it is out of scope for G0. The browser-stepped run is the
  first version.
- **Fidelity caveats,** shown on every run:
  - company memories are not recalled;
  - engine-path follow-ups may lack `last_turn` context;
  - approved skill overlays are loaded read-only by Trace Lab (with `_ready` pre-set; no DDL);
  - the contract analyst's and embeddings' spend is unmeasured.

### 2.6 Live-traffic evidence and provenance
- **Read-only:** Trace Lab reads `plenum_cafm.agent_trace_turns`, `agent_trace_spans`, `chat_threads` and
  `agent_activity_log`, and never writes them.
- **Provenance is inferred at read time,** never captured in the chat path: channel and origin come from activity-log
  payloads and text rules, labelled `inferred`. The old D6 capture changes are dropped.
- **Evidence snapshots replace the purge keep-list.** When a turn is sampled, annotated, labelled, judged or run as a
  golden case, Trace Lab copies its spans into its own database, so svc-deepagents' 90-day span purge can never remove
  them.
- **No gate spans.** Post-loop answer rewrites are visible only by read-time comparison of the last loop model output
  with the stored answer, labelled `inferred`.

### 2.7 Frontend: a self-contained module
- **Folder and state:** `apps/frontend/src/tracelab/`, with its own small store (`useSyncExternalStore`). It does not
  use HoistraLogic mixins, renderVals keys or session.js entries.
- **Reuse by import:** `api/client.js` `apiFetch`, `getActingOrg`, `isStaleScope`, `accessToken`. The
  `orgOverride` pattern is re-implemented locally.
- **Loading:** `Mount.jsx` lazy-loads the app only when `import.meta.env.VITE_TRACE_LAB === '1'`. With the flag off,
  the chunk is never fetched and the nav entry is absent.
- **Tests** live in `apps/frontend/src/tracelab/__tests__/` and run with `node --test src/tracelab/__tests__/*.test.mjs`.
  The existing `npm test` suite is unchanged. Module source must pass the existing `noInternalNames` scan, because the
  SKIP list cannot be edited.
- **The page:** "Trace Lab" is its own Administration page. Today's Hoist Traces page is unchanged: the R14 change and
  the "Answers" rename are dropped.

## 3. Approved attachments (the complete list)
| # | File | Exact change |
|---|---|---|
| A1 | `apps/frontend/src/App.jsx` | `import TraceLabMount from './tracelab/Mount.jsx';` after line 37; `{vals.isTraceLab ? <TraceLabMount vals={vals} /> : null}` after line 86 |
| A2 | `apps/frontend/src/logic/integrations.js` | `import { traceLabNav, traceLabActive } from '../tracelab/nav.js';` after line 3; `isTraceLab: traceLabActive(s),` after line 62; `}],` at line 113 → `}, ...traceLabNav(s, this)],` |
| A3 | `apps/frontend/src/components/shell/PlatformCostPage.jsx` | `import EvalSpendLine from '../../tracelab/EvalSpendLine.jsx';` after line 7; `<EvalSpendLine monthLabel={st.showing \|\| month.label} />` before line 276. It renders nothing when the flag is off. When on, it shows "Evaluation $X, not included in the totals above". |
| A4 | `apps/frontend/Dockerfile`, `deploy/allinone/Dockerfile` (frontend stage) | `ARG VITE_TRACE_LAB=0` and `ENV VITE_TRACE_LAB=$VITE_TRACE_LAB` |
| A5 | `apps/frontend/infra/single-url/nginx.conf` | before `location /`: `location /backend/trace-lab/ { set $trace_lab http://trace-lab:8010; rewrite ^/backend/trace-lab/(.*)$ /$1 break; proxy_pass $trace_lab; }`. The variable upstream means only this route 502s when the container is absent. Not added to `depends_on`. |
| A6 | `deploy/allinone/Dockerfile` | `COPY ${BE}/svc-trace-lab/ svc-trace-lab/`. Further Python deps go in a `--system-site-packages` venv, never shared site-packages. |
| A7 | `deploy/allinone/nginx.conf` | `location /backend/trace-lab/ { proxy_pass http://127.0.0.1:8010/; }` |
| A8 | `deploy/allinone/supervisord.conf` | append a `[program:trace-lab]` that `exec sleep infinity` unless `TRACE_LAB_ENABLED=1`, with `ACTIVITY_LOG_ENABLED="false"` and `MIGRATION_BASE_URL="http://127.0.0.1:9"` (no `%(ENV_X)s`) |
| new | `docker-compose.tracelab.yml` (new file, profile `tracelab`) | the trace-lab service; it never edits the existing compose files |
| DB | a new database `hoist_tracelab` | created by Hussain or a DBA from prepared SQL |

A guard test compares every existing file against the base commit and allows only these changes.

## 4. The senior's decisions (binding)
1. **Grader validation** pools human labels across companies and reports results per company.
2. **Judge label minimum** is 30 pass / 30 fail, with the hard floor of 10 per class shown openly. Lower it only after
   counting real traffic.
3. **Failure modes:** only the platform team creates them at first; company admins can propose them, through a
   proposal queue.
4. **Question bank:** the compliance bank is used as question templates only (161 rows, expected answers blank).
   Expected answers come from each company's Excel.
5. **Eval spend** appears on Platform cost as its own line (A3), not added into the totals.

## 5. Superseded parts of the design spec
- **D3 (one database, schema `plenum_tracelab`):** now a separate database, schema `tracelab`.
- **D5 (purge keep-list in trace.py):** now evidence snapshots (§2.6).
- **D6 (capture additions in the chat path):** dropped; read-time inference instead.
- **D10 (replay contract edits in http_client, orchestrator, chat_threads, chat_memories, skill_overlays and agents):**
  now the in-process proxy plus configuration (§2.5).
- **D13 (tabs inside the Hoist Traces page):** now its own "Trace Lab" admin page (§2.7).
- **D15 (guards in udr_agent and svc-udr):** now the separate database.
- **R11 (numpy):** pure Python.
- **R13 / R14:** dropped.
- **R31 (Evaluation cost line through an ops-intel change):** now A3.
- **R34 (edit principal.py):** now read from `/me`.
- **The old M0 tasks** that edit existing files are dropped, and **M1–M6 frontend work** is rebuilt as the
  self-contained module.

## 6. Phase map
| Phase | Ships |
|---|---|
| 1 | Pure modules: guard tests, stats, golden-set reader + templates, value check, code checks (running) |
| 2 | Service foundation: settings, app, both engines, migrations (verify/apply) + 0001, auth/scope, jobs, status; normalizer + conversations API; frontend module shell + A1/A2 + Conversations, Model calls and Runs views; compose file + A5 + A4 |
| 3 | Golden sets (upload, templates), the proxy, the golden runner (`stream()`, capture, refusals), grading, Runs + compare, expected-outcome panel; A3 cost line + spend endpoint |
| 4 | Open coding (annotations, evidence snapshots), sampling, Map, Progress |
| 5 | Failure modes (platform creates, admins propose), labels, AI proposals with audit, requirement register |
| 6 | Judges (30/30 with a visible floor of 10), pooled grader validation reported per company |
| 7 | Watch (monitoring, corrected prevalence), eval cases, release check, automatic-vs-manual trend; Azure A6–A8; final G0 audit |
