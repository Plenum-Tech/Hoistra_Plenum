# Expected answers drafted from SPEC.md — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every new live question gets an expected answer drafted from the approved SPEC.md, shown on its turn in the session page with Accept / Edit.

**Architecture:** A new backend module drafts with the coding agent's model path and stores the draft as a `coding_agent` expected answer; the automatic sync and Sync now call it after live questions sync. A new read route returns a turn's expected answer; a new frontend panel on the session page shows it and uses the existing accept and edit routes.

**Tech Stack:** FastAPI + SQLAlchemy async (svc-trace-lab), React (apps/frontend/src/lab), node:test, pytest on the scratch Postgres.

**Spec:** `docs/superpowers/specs/2026-11-14-auto-expected-answers-design.md`

## Global Constraints

- Live answers only (Hussain, 14 Nov 2026): nothing in the Codex flow changes; the panel shows only on live turns.
- Trace Lab only; the live Hoistra app is not changed. No commits: the user says when to commit and push.
- The draft is built from SPEC.md and the question only, never Hoistra's answer.
- A draft is `source='coding_agent'`, counts only after a person accepts it (existing `CURRENT_EXPECTED_WHERE`).
- Candidates: kind `production`, not a scenario turn (`SCENARIO_Q`), asked in the last 7 days, no expected answer of any source; at most 20 per round; none without an approved SPEC.md.
- Off switch `TRACE_LAB_AUTO_EXPECTED=false` (default on).
- Every read bounded to the caller's company (`runs._scope`, `runs._question_row`).
- Copy: "Expected answer", "Drafted from SPEC.md v<n> · rules S-1, S-4 · not accepted yet", "Accept", "Edit", "Add expected answer".

## Review Focus

- The model returns a rule code not in SPEC.md → dropped from the stored rules (test in Task 1).
- The model call fails or times out → nothing stored, tried next round, the sync's other steps still run (Tasks 1, 2).
- Two rounds at once (Sync now during the automatic sync) → one draft per question (re-check under the sync lock, Task 1).
- A person already typed an expected answer before the draft lands → the draft is not stored (Task 1).
- Another company's turn id → not found (Task 3).

---

### Task 1: Draft expected answers (`auto_expected.py`)

**Files:** Create `svc-trace-lab/trace_lab/auto_expected.py`; Test `svc-trace-lab/tests/test_trace_lab_auto_expected_sql.py`

**Interfaces:**
- Produces: `async def draft_for_live(org: str | None = None, *, now: datetime | None = None, limit: int = PER_ROUND) -> dict` returning `{"drafted": int, "failed": int, "skipped": str | None}`; `enabled() -> bool` (env `TRACE_LAB_AUTO_EXPECTED`); constants `PER_ROUND = 20`, `DAYS = 7`, `AGENT_EMAIL = agent.AGENT_EMAIL`.
- Consumes: `agent._ask(system, prompt, schema, max_tokens)`, `loop.approved_spec(conn)`, `loop.rule_codes(spec)`, `runs._insert_expected`, `runs.SCENARIO_Q`, `runs.SYNC_LOCK`, `runs._limits`.

- [ ] Step 1: tests (monkeypatch `agent._ask`; seed an approved spec with rules S-1, S-4 via `loop` helpers or SQL as other loop tests do; live questions via `runs.sync_live` from `test_trace_lab_run_sessions_sql._live_run` / `_old_day`):
  - `test_a_new_live_question_gets_a_draft_from_the_spec`: drafted 1+; row source `coding_agent`, author `trace-lab-agent`, evidence `{"drafted_from": "SPEC.md", "spec_version": n, "rules": ["S-1"]}`; the prompt passed to `_ask` contains the question and the spec rules and not the trace output.
  - `test_unknown_rule_codes_are_dropped` (model returns `["S-1", "S-99"]` → stored `["S-1"]`).
  - `test_no_draft_without_an_approved_spec`, `test_scenario_turns_and_answered_questions_are_skipped`, `test_the_off_switch` (`skipped` says why), `test_a_failed_model_call_stores_nothing_and_is_tried_again`, `test_a_question_is_drafted_once` (second call drafts 0), `test_a_person_s_answer_written_meanwhile_wins` (expected inserted between `_ask` and store → no draft row).
- [ ] Step 2: run → fail (module missing).
- [ ] Step 3: implement. Candidates query outside the lock; one `_ask` per question (schema `{"expected": str, "rules": [str]}`, max_tokens 600); store in `lab_engine().begin()` after `pg_advisory_xact_lock(SYNC_LOCK)` with `NOT EXISTS` re-check; exceptions per question counted in `failed` and logged.
- [ ] Step 4: run → pass.

### Task 2: Run it in the sync

**Files:** Modify `trace_lab/autosync.py` (new step after the live questions, STATUS keys `expected_ok`, `last_new_expected`, `expected_error`), `trace_lab/runs.py` `sync_production` (adds `drafted_expected` and `failed["expected"]`); Test: extend `tests/test_trace_lab_run_sessions_sql.py`.

- [ ] Step 1: tests `test_sync_now_drafts_expected_answers_for_new_live_questions` and `test_the_automatic_round_drafts_them_and_says_so` (monkeypatched `auto_expected.draft_for_live`); a raising draft step leaves `new_questions` intact and reports `failed["expected"]`.
- [ ] Step 2: fail. Step 3: implement. Step 4: pass. Frontend `syncLines` (Runs.jsx) learns `drafted_expected` ("expected answer drafted") and `failed.expected` ("Expected answers could not be drafted: ") with a test in `__tests__/runSessions.test.mjs`.

### Task 3: A turn's expected answer (read route)

**Files:** Modify `trace_lab/runs.py` (`async def turn_expected(m, turn_id: str) -> dict`), `trace_lab/routes_runs.py` (`GET /lab/turns/{turn_id}/expected`), the e2e `NOT_DRIVEN` list; Test: `tests/test_trace_lab_auto_expected_sql.py`.

**Interfaces:** Produces `{"question": code | None, "expected": expected_out row | None, "draft": bool, "decision": "approved" | "rejected" | None, "spec_version": int | None, "rules": [str], "live": bool}` — `live` true only for a hand question of a live day — the newest expected answer of the turn's question (any source); `draft` true for `coding_agent`.

- [ ] Step 1: tests: draft shown with `draft` true and rules; after `loop.decide(..., "approved")` decision `approved`; after `runs.set_expected` the person's version wins; a turn of another company → `NotFound`; a turn with no question → `{"question": None, ...}`.
- [ ] Step 2–4: fail, implement, pass.

### Task 4: The session page panel

**Files:** Create `apps/frontend/src/lab/TurnExpected.jsx`; Modify `SessionPage.jsx` (render `<TurnExpected turnId={trace.id} me={me} />` right after `<WhyFailed …/>`), `labApi.js` (`turnExpected: (id) => call('GET', '/lab/turns/' + enc(id) + '/expected')`); Test `__tests__/turnExpected.test.mjs`.

**Interfaces:** `export function ExpectedView({ data, busy, error, editing, onAccept, onEdit, onSave, onCancel })` (pure, rendered in tests); default export loads and wires `labApi.decideProposal('expected_answer', id, { decision: 'approved' })` and `labApi.setExpected(code, { expected })`.

- [ ] Step 1: tests: a draft shows the text, "Drafted from SPEC.md v3 · rules S-1, S-4 · not accepted yet", Accept and Edit; an accepted one shows no Accept; a person's shows "Expected answer" with Edit only; none shows "Add expected answer"; no question or not `live` (a Codex turn) → renders nothing; the panel is placed after WhyFailed in SessionPage.
- [ ] Step 2–4: fail, implement, pass.

### Task 5: Verify

- [ ] Full svc-trace-lab suite (only known failure `test_trace_lab_g0_audit_script`), `npm test`, both Vite builds, local browser check of a live turn (read-only), save the patch.
