# Tasks: Contract Performance Engine (Feature B)

**Input**: Design documents from `/specs/001-contract-performance-engine/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**Context**: The B1/B2/B3 engines are substantially implemented. Tasks close the 7 gaps
identified in the plan's Gap Register and add the FR-039 cross-cutting conflict detection.
No greenfield scaffolding is needed — every task modifies or extends existing files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup — Database Migration

**Purpose**: Add the two new columns required by the gap register. All subsequent tasks
depend on this migration being applied.

- [ ] T001 Create `migrations/phase2_contract_performance_v3.sql` with two idempotent `ALTER TABLE IF EXISTS` statements: (1) `ADD COLUMN IF NOT EXISTS signed_date DATE` on `plenum_cafm.contract_sla_parameters`; (2) `ADD COLUMN IF NOT EXISTS conflict_flag BOOLEAN DEFAULT false` and `ADD COLUMN IF NOT EXISTS conflict_payload JSONB` on `plenum_cafm.work_orders`; include a comment on each column per data-model.md
- [ ] T002 Apply the migration to the development database: `psql $DB_URL -f apps/backend/cafm-connector-service-final/svc-operations-intelligence/migrations/phase2_contract_performance_v3.sql`

**Checkpoint**: `\d plenum_cafm.contract_sla_parameters` shows `signed_date date`. `\d plenum_cafm.work_orders` shows `conflict_flag bool` and `conflict_payload jsonb`.

---

## Phase 2: Foundational — ORM & Schema Sync

**Purpose**: Reflect the new DB columns in the ORM models and API schemas so all
subsequent story tasks can use them. No story implementation can begin until this is done.

⚠️ **CRITICAL**: Blocks all story phases.

- [ ] T003 Add `signed_date: Mapped[date | None] = mapped_column(Date)` to `ContractSlaParameters` in `src/models/contract_performance.py` (after the `contract_ref` column definition)
- [ ] T004 [P] Add `conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)` and `conflict_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)` to `plenum_cafm.work_orders` — add these to the existing `work_orders` ORM model in `cafm-connector-service/src/cafm_connector/models/plenum_cafm.py`
- [ ] T005 [P] Add `signed_date: date | None` to `params_to_dict` return dict in `src/engines/contract_performance/parameters.py` and to the `ContractSlaParametersOut` Pydantic schema in `src/api/schemas/contract_performance.py`

**Checkpoint**: `pytest tests/unit/test_b1_parameters.py -v` still passes with no import errors.

---

## Phase 3: User Story 1 — Contract Parameter Extraction (P1)

**Goal**: A PM uploads a contract; the system extracts and presents terms for review;
the PM can override any field before confirming; overlapping contracts for the same
vendor resolve deterministically to the most recently signed one (FR-035).

**Independent Test**: Upload a contract → confirm extracted table → read back stored record.
Fully testable without any work order data.

### Implementation for User Story 1

- [ ] T006 [US1] Add `"signed_date"` to the `CONTRACT_FIELDS` list in `src/engines/contract_performance/extract.py` and extend the Claude prompt string to request it (format: `YYYY-MM-DD`); add a heuristic regex fallback that matches patterns like `"signed on 15 January 2026"` → `2026-01-15`
- [ ] T007 [P] [US1] Accept `signed_date: date | str | None` in the `extract_contract_parameters` function signature in `src/engines/contract_performance/extract.py` and pass it through to `ingest_contract_parameters`
- [ ] T008 [P] [US1] Accept `signed_date: date | None` in `ingest_contract_parameters` in `src/engines/contract_performance/parameters.py` and write it to `ContractSlaParameters.signed_date` on the new row
- [ ] T009 [US1] Update `_load_confirmed_params` in `src/engines/contract_performance/scoring.py` to order by `signed_date DESC NULLS LAST, confirmed_at DESC` instead of `confirmed_at DESC`; after fetching, if two confirmed rows share the same `signed_date`, call `enqueue_approval` with `item_type="overlapping_contracts_tie"` and return the system-default params with `ok=False` signalling to the caller that scoring is blocked until PM resolves the tie
- [ ] T010 [P] [US1] Add `signed_date: date | None` to the `POST /contracts/extract` request body schema in `src/api/schemas/contract_performance.py` and thread it through the route handler in `src/api/routes/contract_performance.py`
- [ ] T011 [US1] Add a test to `tests/unit/test_b1_parameters.py` that creates two confirmed `ContractSlaParameters` rows for the same `vendor_id` with different `signed_date` values and asserts `_load_confirmed_params` returns the more recently signed one; add a second test that creates two rows with identical `signed_date` and asserts an `enqueue_approval` call is made

**Checkpoint**: `pytest tests/unit/test_b1_parameters.py -v` — all tests pass including T011.

---

## Phase 4: User Story 2 — Vendor Performance Scoring (P1)

**Goal**: Every completed WO is scored against the contract SLA. Monthly scorecard
produced on the 1st with full drill-down, PPM reported separately, and three gaps
closed: per-WO accreditation lapse cap (FR-038), scorecard exclusions (FR-033),
and parameter-set snapshot for immutability (FR-031).

**Independent Test**: With a confirmed contract and ingested WOs, score one vendor for
one month; verify every component and total against the underlying records by hand.

### Implementation for User Story 2

- [X] T012 [US2] Extend `_accreditation_current` in `src/engines/contract_performance/scoring.py` to return `tuple[bool, date | None]` — the second element is the earliest `expiry_date` from any lapsed `ComplianceCertificate` row, or `None` if accreditation is current; update the docstring; update both callers (`score_completed_work_orders`) to unpack the tuple
- [X] T013 [P] [US2] Update `score_work_order_pure` in `src/engines/contract_performance/scoring.py` to accept `lapse_date: date | None = None`; inside the function, compute `wo_raised = _parse_dt(wo.get("reported_at") or wo.get("created_at")); post_lapse = lapse_date is not None and wo_raised is not None and wo_raised.date() >= lapse_date`; pass `vendor_blocked=(vendor_blocked and post_lapse)` to `apply_block_cap`; add `"lapse_cap_applied": post_lapse and capped, "lapse_date": lapse_date.isoformat() if lapse_date else None` to the returned `component_scores` dict
- [X] T014 [P] [US2] Update `score_completed_work_orders` in `src/engines/contract_performance/scoring.py` to unpack `(acc_ok, lapse_date)` from the extended `_accreditation_current` call and pass `lapse_date` to `score_work_order_pure` for each WO in the loop
- [X] T015 [US2] Add exclusion tracking to `score_completed_work_orders` in `src/engines/contract_performance/scoring.py`: before scoring each WO, check for self-contradictory timestamps (`completed_at < reported_at`), missing `vendor_id`, and missing both `attended_at` and `completed_at`; excluded WOs must NOT produce a `VendorWoScore` row; collect exclusions as `{"wo_code": ..., "reason": ...}` dicts; include the exclusions list in the function return dict as `"exclusions": [...]`
- [X] T016 [US2] Add `exclusions` and `weights_snapshot` to `generate_monthly_scorecard` in `src/engines/contract_performance/scoring.py`: (1) pass `exclusions` from `score_completed_work_orders` result into `component_breakdown["exclusions"]`; (2) fetch the current `VendorScoreWeightConfig` and store the five component percentages as `component_breakdown["weights_snapshot"]` before writing the `VendorMonthlyScorecard` row
- [X] T017 [US2] Add tests to `tests/unit/test_b2_scoring.py`: (a) test FR-036: a WO where `detect_recall` fires must produce `first_fix=False`; (b) test FR-038: a WO raised after `lapse_date` must have `capped_by_block=True` while a WO raised before `lapse_date` must not; (c) test FR-033: a WO with `completed_at < reported_at` appears in `exclusions` and is absent from `scores`; (d) test FR-031: `generate_monthly_scorecard` stores a `weights_snapshot` key in `component_breakdown`

**Checkpoint**: `pytest tests/unit/test_b2_scoring.py -v` — all tests pass including T017.

---

## Phase 5: User Story 3 — Invoice Verification (P2)

**Goal**: The PM uploads an invoice; lines are matched and auto-cleared or flagged;
flagged lines >£500 pass through the adversary gate; PM records a decision with reason;
all decisions traceable in the audit trail (FR-034).

**Independent Test**: Upload a control invoice against known WOs; verify split between
cleared and flagged lines and each flag's stated dispute amount.

### Implementation for User Story 3

- [X] T018 [US3] In `src/engines/contract_performance/invoice.py`, in the `_record_pm_decision` function (or wherever PM decisions are persisted), add a call to `write_audit` from `src/shared/approvals.py` with `action_type="invoice_line.pm_decision"`, `source_feature="B"`, `input_payload={"decision": decision, "note": note}`, and `output_payload={"line_id": str(line_id), "invoice_verification_id": str(iv_id)}`; verify this call is absent or incomplete by reading the current implementation first
- [X] T019 [P] [US3] In `src/api/routes/contract_performance.py`, confirm the `POST /contracts/invoices/{verification_id}/lines/{line_id}/decide` route (or equivalent) calls `write_audit` after persisting the PM decision; if the route calls only `update_invoice_line_decision`, move the audit write into that function in `src/engines/contract_performance/invoice.py`
- [X] T020 [P] [US3] Add a test to `tests/unit/test_b3_invoice.py` confirming: (a) a flagged line with `delta_gbp > 500` has `adversary_reviewed=True` after verification; (b) a PM rejection records `pm_decision="rejected"` and a corresponding audit log entry with `action_type="invoice_line.pm_decision"`

**Checkpoint**: `pytest tests/unit/test_b3_invoice.py -v` — all tests pass including T020.

---

## Phase 6: User Story 4 — Admin Scoring Parameter Tuning (P3)

**Goal**: An administrator adjusts component weightings from the admin section;
saves are rejected if components do not total 100 (FR-030); prior scorecards keep
their original parameters (already handled by weights_snapshot added in T016).

**Independent Test**: Change a weighting; confirm next scoring run uses new weights;
confirm prior scorecard `weights_snapshot` is unchanged.

### Implementation for User Story 4

- [X] T021 [US4] In `update_weights` in `src/engines/contract_performance/scoring.py`, replace the soft-validation comment with a hard rejection: after computing `component_sum`, if `round(component_sum, 2) != 100.0`, do NOT call `await session.commit()` and instead return `{"ok": False, "error": "weights_must_total_100", "component_sum": round(component_sum, 2)}`; ensure `row` changes are not flushed (wrap in a check before the flush, or call `await session.rollback()` after the guard)
- [X] T022 [P] [US4] In `src/api/routes/contract_performance.py`, update the `PATCH /contracts/score-weights` handler to return HTTP 422 when `update_weights` returns `ok=False` with `error="weights_must_total_100"`; include `component_sum` in the response body
- [X] T023 [P] [US4] Add a test to `tests/unit/test_b2_scoring.py` (or a new `tests/unit/test_b4_admin.py`) that: (a) calls `update_weights` with a set summing to 95 and asserts `ok=False` and DB row is unchanged; (b) calls `update_weights` with a valid set summing to 100 and asserts `ok=True` and the weight is persisted; (c) re-scores after the change and asserts the new weights appear in the scorecard's `weights_snapshot`

**Checkpoint**: `pytest tests/unit/test_b2_scoring.py tests/unit/test_b4_admin.py -v` — all pass including T023.

---

## Phase 7: User Story 5 — Insights Layer (P3)

**Goal**: The PM reads cost variance for service and parts against contract or budget,
usage variance for labour hours, and vendor performance patterns (FR-028).

**Independent Test**: With several months of scored data, open the insights endpoint and
verify each variance against the underlying records.

### Implementation for User Story 5

- [X] T024 [US5] Implement a `compute_insights` function in `src/engines/contract_performance/scoring.py` (or a new `src/engines/contract_performance/insights.py`) that: queries `vendor_wo_scores` for the given `vendor_id` and date range; computes (a) cost variance = sum(actual - estimated) / sum(estimated) × 100; (b) labour usage variance = sum(invoice labour hours vs attendance hours) if available; (c) flagged-to-matched ratio trend from `invoice_verifications`; returns a dict `{cost_variance_pct, labour_variance_pct, matched_flagged_trend, contributing_wo_ids, period}` — all figures traced to WO IDs (constitution Principle II)
- [X] T025 [P] [US5] Add `GET /contracts/insights` endpoint to `src/api/routes/contract_performance.py` with query params `vendor_id`, `organization_id`, `from_date` (`YYYY-MM-DD`), `to_date` (`YYYY-MM-DD`); call `compute_insights`; return 200 with the insights dict; return `{"ok": true, "message": "No data for the requested period"}` when no WO scores exist for the range (FR-028 / US5-AS3)
- [X] T026 [P] [US5] Add a Pydantic `InsightsResponse` schema to `src/api/schemas/contract_performance.py` with typed fields for `cost_variance_pct`, `labour_variance_pct`, `matched_flagged_trend`, `contributing_wo_ids`, `period`
- [X] T027 [US5] Add tests to `tests/unit/test_b5_insights.py`: (a) with seeded `VendorWoScore` rows, assert `compute_insights` returns the correct `cost_variance_pct`; (b) with zero rows, assert the "no data" response; (c) assert `contributing_wo_ids` lists the WO IDs whose scores drove the variance (constitution Principle II traceability)

**Checkpoint**: `pytest tests/unit/test_b5_insights.py -v` — all pass. `GET /contracts/insights` returns 200 with traced variance figures.

---

## Phase 8: Cross-Cutting — Re-Ingestion Conflict Detection (FR-039)

**Purpose**: When a WO is re-ingested with different values, surface the conflict to the
PM rather than overwriting silently. Applies across B1/B2/B3 because the underlying
work order data feeds all three sub-features.

**Independent Test**: Ingest a WO, re-ingest it with a different `actual_cost`, assert
`conflict_flag=True` on the row and an approval queue item exists; resolve via API; assert
`conflict_flag=False` and the WO is included in the next scoring run.

- [X] T028 Create a `detect_and_flag_wo_conflict` async function in `src/engines/contract_performance/parameters.py` (or a new `src/engines/contract_performance/conflicts.py`): given `wo_code` and incoming field dict, query `plenum_cafm.work_orders` for an existing row with that `wo_code`; if a row exists and any of `actual_cost`, `estimated_cost`, `attended_at`, `completed_at` differs beyond a `None` vs non-None or a >1% numeric difference, set `conflict_flag=True`, write the incoming fields to `conflict_payload`, call `enqueue_approval` with `item_type="work_order_conflict"`, and return `{"conflict": True, "wo_code": wo_code}`; if no conflict, return `{"conflict": False}`
- [X] T029 [P] Wire `detect_and_flag_wo_conflict` into the WO ingestion path — locate where `plenum_cafm.work_orders` rows are written or updated in `src/engines/contract_performance/` (check `scoring.py` `fetch_completed_work_orders_from_udr` entry point and any direct upsert paths); call `detect_and_flag_wo_conflict` before any update to an existing WO row; if conflict detected, skip the update and return early
- [X] T030 [P] Update `fetch_completed_work_orders_from_udr` in `src/engines/contract_performance/scoring.py` to add `AND (wo.conflict_flag IS NOT TRUE)` to the WHERE clause so conflicted WOs are excluded from all scoring runs (FR-039)
- [X] T031 [P] Add `POST /contracts/work-orders/{wo_id}/resolve-conflict` endpoint to `src/api/routes/contract_performance.py`: accept `{"accept": "stored|incoming", "resolved_by": "uuid", "note": "string|null"}`; if `accept="incoming"` apply `conflict_payload` values to the WO row; set `conflict_flag=False`; call `write_audit` with `action_type="work_order_conflict.resolve"`; return `{"ok": true, "wo_code": ..., "conflict_flag": false}`
- [X] T032 Add tests to `tests/unit/test_b_conflicts.py`: (a) first ingest creates no conflict; (b) re-ingest with changed `actual_cost` sets `conflict_flag=True` and calls `enqueue_approval`; (c) `fetch_completed_work_orders_from_udr` excludes `conflict_flag=True` rows; (d) resolve via `accept="incoming"` sets `conflict_flag=False` and audit entry present

**Checkpoint**: `pytest tests/unit/test_b_conflicts.py -v` — all pass. Scoring pipeline excludes conflicted WOs.

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T033 [P] Run the full test suite and confirm zero regressions: `pytest tests/unit/test_b1_parameters.py tests/unit/test_b2_scoring.py tests/unit/test_b3_invoice.py tests/unit/test_b4_admin.py tests/unit/test_b5_insights.py tests/unit/test_b_conflicts.py -v`
- [X] T034 [P] Verify `ruff check src/` and `mypy src/` pass cleanly across all modified files in `src/engines/contract_performance/`, `src/models/contract_performance.py`, `src/api/routes/contract_performance.py`, `src/api/schemas/contract_performance.py`
- [ ] T035 Run the quickstart validation guide (`specs/001-contract-performance-engine/quickstart.md`) end-to-end against the seeded 6-month data: confirm SC-001 through SC-010 acceptance criteria are met; note any that require manual sign-off
- [X] T036 [P] Update `specs/001-contract-performance-engine/spec.md` **Status** field from `Draft` to `Complete` once all acceptance scenario spot-checks pass

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Migration)
  └── Phase 2 (ORM/Schema sync)
        ├── Phase 3 (US1 — contract extraction gaps)
        ├── Phase 4 (US2 — scoring gaps)          ← also needs Phase 3 complete (signed_date in _load_confirmed_params)
        ├── Phase 5 (US3 — invoice audit)          ← can run after Phase 2
        ├── Phase 6 (US4 — weight rejection)       ← can run after Phase 2
        ├── Phase 7 (US5 — insights)               ← can run after Phase 4 (needs VendorWoScore rows)
        └── Phase 8 (conflict detection)           ← can run after Phase 2
              └── Final Phase (polish)
```

### User Story Dependencies

- **US1 (P1)**: After Phase 2. No dependency on other stories.
- **US2 (P1)**: After Phase 2 AND Phase 3 (depends on `_load_confirmed_params` using `signed_date`).
- **US3 (P2)**: After Phase 2. No dependency on US1/US2 for the invoice audit task.
- **US4 (P3)**: After Phase 2. Independent.
- **US5 (P3)**: After Phase 4 (needs scored data in `vendor_wo_scores`).

### Parallel Opportunities Within Phases

**Phase 2** (T003, T004, T005): T004 modifies a different file from T003/T005 → run T004 in parallel.

**Phase 3**: T007 and T008 modify different functions → can run in parallel after T006.

**Phase 4**: T013 and T014 modify the same function sequentially; T015 and T016 can start once T013/T014 are done.

**Phase 7**: T025 (route), T026 (schema) are independent of T024 (engine) → write all three after agreeing on the interface, then integrate.

**Phase 8**: T029, T030, T031 all modify different files → run in parallel after T028.

---

## Parallel Example: Phase 4 (User Story 2)

```bash
# After T012 is done, launch in parallel:
Task T013: "Update score_work_order_pure to accept lapse_date in scoring.py"
Task T014: "Update score_completed_work_orders to unpack lapse_date in scoring.py"
  # Note: T013 and T014 edit the same file — assign to one developer or coordinate

# After T013+T014 complete, launch in parallel:
Task T015: "Add exclusion tracking to score_completed_work_orders in scoring.py"
Task T016: "Add exclusions + weights_snapshot to generate_monthly_scorecard in scoring.py"
  # Note: T015 and T016 edit the same file — assign sequentially or coordinate

# After all implementation tasks:
Task T017: "Add FR-036, FR-038, FR-033, FR-031 tests to test_b2_scoring.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + User Story 2 only)

1. **Phase 1**: Apply migration → verify columns exist
2. **Phase 2**: ORM + schema sync → run existing tests (should all pass)
3. **Phase 3**: US1 gaps (T006–T011) → `pytest test_b1_parameters.py` passes
4. **Phase 4**: US2 gaps (T012–T017) → `pytest test_b2_scoring.py` passes
5. **STOP and VALIDATE**: Run `quickstart.md §3–5` — US1 and US2 acceptance criteria met
6. Deploy/demo: contract extraction with signed_date, per-WO lapse cap, exclusion reporting, and scorecard immutability all working

### Incremental Delivery

- After MVP: US3 (T018–T020) → invoice audit trail complete
- After US3: US4 (T021–T023) → weight rejection enforced
- After US4: US5 (T024–T027) → insights layer live
- After US5: Phase 8 (T028–T032) → conflict detection and PM resolution path live
- Final: T033–T036 polish and quickstart sign-off

### Notes

- Every task modifies an existing file — no new top-level modules needed
- `test_b4_admin.py` and `test_b5_insights.py` and `test_b_conflicts.py` are new test files; all others extend existing suites
- Commit after each phase checkpoint
- Stop at any checkpoint to validate a story independently before continuing

---

## Task Count Summary

| Phase | Tasks | Story |
|-------|-------|-------|
| Phase 1: Migration | 2 (T001–T002) | — |
| Phase 2: ORM/Schema | 3 (T003–T005) | — |
| Phase 3: US1 gaps | 6 (T006–T011) | US1 |
| Phase 4: US2 gaps | 6 (T012–T017) | US2 |
| Phase 5: US3 audit | 3 (T018–T020) | US3 |
| Phase 6: US4 weight | 3 (T021–T023) | US4 |
| Phase 7: US5 insights | 4 (T024–T027) | US5 |
| Phase 8: FR-039 conflicts | 5 (T028–T032) | Cross-cutting |
| Final: Polish | 4 (T033–T036) | — |
| **Total** | **36** | |
