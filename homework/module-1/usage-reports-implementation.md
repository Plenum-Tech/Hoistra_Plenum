# Milestone 05 — measured usage and manual reports

Specification: canonical0.37.0, USAGE-01…07, NOTIFY-01…06, ACT-05/06,
IDENTITY-04 and ACCESS-01…07. This supplements the historical mapping/checklists;
approved policy, implementation, tests and candidate activation remain distinct.

| Acceptance | Implemented enforcement | Evidence |
|---|---|---|
| source_events_for_provider_ingestion_onboarding_failure_and_backlog | `usage_hw1/service.py`: actual provider receipts, timestamped accepted intake/correction/onboarding history, observed failure events, live proposal backlog; `events.py`: safe immutable failure record and immediate manager alert | Running gate invokes genuine provider, source intake, onboarding and invalid input; verifies independent count deltas and source references |
| unsupported_metrics_explicitly_unavailable | Null full-platform adoption/session counts, subscription/model costs, unconfigured limits and coverage trends; observed population and partial token coverage separately labelled | Actual API null/coverage/freshness checks and deterministic mixed measured/missing-token tests |
| weekly_monthly_period_and_timezone_boundaries | One Asia/Dubai half-open window calculator; Monday weekly start, calendar month day1, explicit UTC boundaries, separate generation time and intended cadence | Actual immutable fixture boundary2/30 and February5/2131 checks, explicitly labelled fictional observations; leap/year regressions |
| four_role_recipient_and_building_scope | General own actor/contact only; manager assigned scope; Officer compliance-only. Contacts must cover the entire report scope. No quota/billing/assignment mutation tools | Actual role/event/contact metadata parity, foreign ID/building/organization denials and narrow-contact regression |
| manual_trigger_immutable_snapshot_and_idempotent_replay | Report-family advisory lock, single-SQL event/backlog/contact snapshot, fingerprint including source IDs/backlog/recipient configuration, immutable versioned report history | Concurrent actual trigger replay, backlog-only new version, exact historical GET; recipient-configuration version regression |
| digest_and_immediate_failure_delivery_outcomes | Existing bounded shared simulated outbox; configured scoped digest, failure/action alerts, safe nonrecursive fallback; no real transport/scheduler | Actual sent1/failed2/unknown1/pending0 records and review-only recovery; report snapshot stays immutable |

`skills/usage/hw1.md` plus five shared instructions are separately compiled and hash-checked
by `build-usage-profile.py`, `skills/usage-manifest.json` and `agents/usage_prompt.py`.
The bounded agent binds only `usage_weekly_report`, `usage_monthly_report` and
`usage_get_report`. Direct typed tools and weekly/monthly routes use the same service.
Sessions and traces remain principal-owned; actual provider receipts retain existing
measured usage capture. Officer communications/ingestion/usage traces are system-labelled
compliance because those roles' guarded tools supply compliance-only content.

The authenticated HTTP boundary records observed adapter failures without request/source
content, and central agent `failed` outcomes also create immediate durable failure events.
One request ID is counted once when both a trace and an HTTP failure record exist.
Errors outside instrumented adapters remain explicitly unavailable, not silently zero.
Provider receipt keys are idempotent. Source/history/audit privileges stay append-only.

Report period metrics are observed events during the stated interval. Backlog and recipient
configuration are snapshot state at generation, not period arrival totals. Report replay
retains its original as-of time and delivery snapshot; current transport recovery is viewed
through the shared scoped outbox, without rewriting report history. Source freshness is
separate from backlog as-of. No model or app role may inject telemetry or claim subscription
prices, quotas or complete platform activity from these adapters.

`setup-usage-fixtures.py` only adds fictional contacts and source-backed boundary observations.
Dedicated `usage_only` contacts do not become unrelated HOI/expiry subscribers. Their
configuration derives from trusted assignments; no users/roles/assignments are changed.
Prior narrower portfolio contacts cannot receive a wider report. Original fixture documents
and prior receipts are retained. Boundary observations have explicit fictional origin and
are separately counted from genuine live provider receipts; they are not real usage claims.

Run `milestones/gates/05-usage-reports.py` for fresh candidate evidence. It returns nonzero
for any failed acceptance check, uses private trusted sessions without printing credentials
or restricted bodies, verifies original source fingerprints and non-owner DB restrictions,
and writes `milestones/evidence/05-usage-reports.json`. Full multi-domain readiness and
active service replacement remain later controller-owned acceptance work.
