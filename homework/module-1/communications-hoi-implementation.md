# Milestone 04 implementation mapping — specification 0.37.0

The canonical policy remains `specifications.md`. This checkpoint supplements historical
mapping/checklist artifacts; it does not relabel their original receipts or certify production.
Candidate profile: `hw1-communications`, policy `hw1-communications-v1`, six hash-checked
core/shared/domain instruction files. Model tools draft/read only; human API decisions commit.

| Acceptance identifier | Approved policy | Python and instructions | Verification |
|---|---|---|---|
| requested_email_exact_preview_and_confirmation | ACT-05, OUTBOX-01/02/03/06/07, VENDOR-05 | `communications_hw1/contracts.py`, `service.py`, guarded routes; `skills/communications/hw1.md`; exact owner/role, content/attachment digest, target/contact/vendor snapshot and locked confirmation | Actual API exact preview/no-send/rejection/role/hash/concurrent replay checks; independent stale contact/target/vendor tests |
| automatic_scoped_action_and_failure_notifications | ACT-06, NOTIFY-03/06, CORR-03 | Shared `compliance_hw1/outbox.py`, configured ingestion/certificate/Energy hooks, safe immediate manager fallback | Actual accepted correction and manual expiry evaluation; no business approval implied |
| known_failure_retry_unknown_review_missing_contact_pending | OUTBOX-05/07, NOTIFY-05 | Bounded fictional delivery adapter with individual attempts; pending/review/recovery workflow; no transport library | Live fail=2, fail-once=2/success, unknown=1, missing=0; nonrecursive recovery/fallback regressions |
| event_recipient_deduplication_and_recovery | OUTBOX-06, NOTIFY-04/05 | Shared advisory lock, event/recipient key; human manager review/resume of unsent configured rows; immutable issue/recovery history | Repeated actual event drain leaves IDs unchanged; simultaneous configured pending recovery records one attempt |
| canonical_fixed_ten_group_hoi_expected_arithmetic | HOI-01…05, HOI-08 | Fixed `compliance_hw1/hoi.py` hierarchy, source projection checks, duplicate original detection, invoice line evidence, half-up final rounding | Actual source-backed 100→98→100; independent 97.5→98, fivegroups50, 3/5assets6-point and commercial75% tests |
| all_authorized_hoi_changes_trigger_recalculation | HOIST-02/04, CORR-08, HOI-07 | Existing ingestion/fixed-item corrections/version selection/onboarding hooks plus certificate event drain and vendor eligibility unchanged trail | Actual four-domain intake, version replacement, certificate acceptance/correction/restoration, fixed-item correction and vendor-block change checks |
| both_manager_trails_including_unchanged_score | HOIST-03/04, HOI-06/07 | Structured both-manager source/actor/reason/formula/before/after notifications and append-only history, including pending contact outcomes | Actual per-event history and exactly two configured manager notices, unchanged scores retained |
| no_general_or_officer_hoi_leakage | HOI-06, AUTH-04/07, ACCESS-01…07 | Role tools/direct API guards, target and attachment guards, outbox projections, owned sessions; Officer certificate separation | Actual direct/model/outbox/source/recipient and foreign-building denials; genuine configured provider calls and manifest comparison |

Run the executable `milestones/gates/04-communications-hoi.py` for fresh candidate evidence;
it appends fictional evidence and writes `milestones/evidence/04-communications-hoi.json`,
returning nonzero on failure. `tests/test_communications_hoi.py` supplies independent arithmetic,
state/disclosure, stale approvals and audit-failure regressions, in addition to the prior suite.
The setup-only `setup-communications-fixtures.py --building API_CREATED_ID` inserts new fictional
records/originals with `ON CONFLICT DO NOTHING`. Its explicit `--resolve-contact` step only fills
the new dedicated fictional portfolio contact to exercise pending recovery; it never edits sources,
users, roles, historic fixtures or controller state. Runtime tools use the non-owner RLS app role.

Old invoice fixture originals without line evidence stay unchanged; unsupported lines earn no
credit. New fixtures retain lines in their original JSON and projection. Unlinked domain sources
trigger a trail without guessing checklist slots or increasing coverage. Missing assignment/contact
remains pending. These are fictional structured backend adapters, not OCR/legal verification,
production email, a scheduler, role administration or operational work-order control.
