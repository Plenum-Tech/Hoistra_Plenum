# Homework 1 — entire orchestrator capability and requirements checklist

## 2026-10-27 — controller full-suite correction passed

**Fresh complete07 pass: all seven checks, all six prior live gates, 523 tests and
65 subtests passed (18 warnings). Total474.05 seconds; controller limit900 seconds.**
Receipt `milestones/evidence/07-readiness.json`, recorded `2026-10-27T04:06:09.863810+00:00`;
independent owner-only timing proof `runtime/readiness/timed-gate-018d2590b35b45e885da5f855b52df33.json` matches receipt
SHA256 `89adc6e0cb28fb1ff46701be3d3c8820652ca3e78ec8bbab9657f78fc43631d8`. Genuine candidate/provider evaluations and active EUI12/owned-trace
verification passed. Controller owns the subsequent independent rerun/advancement.

The controller failure was confined to legacy planner unit fixtures accidentally using
real pooled telemetry across separate unittest event loops. Changed service test file:
`apps/backend/cafm-connector-service-final/svc-deepagents/tests/test_homework_reads.py`.
It now observes telemetry locally for fake models/repositories, strengthens the original
forged-identity and multiple-call rejection assertions, and adds a telemetry-outage test
proving zero partial read execution. All19 planner tests pass with the actual enabled
runtime configuration. Production telemetry, DB usage tests, application authorization,
profiles/skills and genuine-provider gates remain unchanged in this correction.

Active18008 and candidate18009 are healthy on `sha256:76198ead38c552302c302c831fc2d0827c1014ca433797687425d77ebcd7b6dd`;
active start `2026-10-27T04:05:56.857683579Z`. Original rollback image
`sha256:d473d750734cbe355d7318bc8cfe8f10a99901544dc4a52a29a77a9482ecfef1` and its exact private environment remain retained.
Specification0.37.0; project `hoistra-hw1`; profile/policy `hw1-unified`/`hw1-unified-v1`.
The successful receipt identifies active18008 and candidate18009 separately. Fresh
receipts/status mapping and six operational documents are updated; earlier checkpoints
below are preserved history. Runbook commands and fictional/backend-only production
boundaries remain applicable. No private configuration, source/history/audit evidence,
application roles or unrelated projects were modified by this test-fixture correction.


## 2026-10-27 — final timed readiness accepted by the executable gate

**All seven readiness checks passed; all six prior gates were freshly reverified.**
The complete build/provision/verification/activation run took **427.69 seconds**, below
the controller's900-second deadline. Full service suite: **522 tests and65 subtests passed**,
with18 warnings. Gate06 ran13 genuine configured OpenRouter conversations; active
provider EUI12, owned trace/manifest parity and candidate/active four-role matrices passed.
Receipt: `milestones/evidence/07-readiness.json`, recorded `2026-10-27T03:32:49.380774+00:00`.
Independent timing proof: `runtime/readiness/timed-gate-95e1be330664470e8b8a60a1bee7dcad.json` (owner-only),
matching receipt SHA256 `c2a90f78f993691ea96c2f4d3dd5f14e0e5fd131d9862228842f2f8f1b7ae0b7`. Controller owns its independent rerun/advancement.

The fictional backend is healthy and active on **localhost18008**; candidate remains on18009.
Successful final receipt identifies active `api_base`18008 and `candidate_api_base`18009.
Both run `sha256:bac7d4d70ea91eb89552a7d0399eb2ab8c540b6cd8171689c1e716e9eafde2ff`;
active start `2026-10-27T03:32:36.910856994Z`. Rollback image
`sha256:d473d750734cbe355d7318bc8cfe8f10a99901544dc4a52a29a77a9482ecfef1` and exact private original environment/Compose snapshot remain.
Project `hoistra-hw1`; specification0.37.0; unified profile/policy `hw1-unified`/`hw1-unified-v1`.
The original read-only facade and application role/building/content/confirmation controls remain.

Use `READINESS-RUNBOOK.md` for first questions, reports, exact preview/approval, trace
export and deliberate isolated rollback. No declared backend acceptance gap remains in
this checkpoint. Frontend, real transport/appointments, production identity/connectors,
OCR, automatic calendar scheduling, unmeasured pricing/quotas/adoption, course video and
Git publication remain deferred. This is fictional backend acceptance, not legal or
production conformance. Earlier failed, slow and passing checkpoints below are historical.


## 2026-10-27 — fresh readiness passed after retained-history fixes

All seven readiness checks passed at `2026-10-27T00:17:43.904062+00:00`. Gates01–06 were freshly
executed against rebuilt candidate18009; full suite: **507 tests /65 subtests passed**,
18 warnings. Candidate and active role/domain matrices and genuine active provider EUI12
verification passed. Current active/candidate image: `sha256:a7b5d110cf44e52e6243f85f00273cf1c7de3742fc8839a9c6d0c93ad9b2b4f3`.
Active start: `2026-10-27T00:17:28.863441117Z`. Original rollback image/environment retained.
Authoritative fresh receipt: `milestones/evidence/07-readiness.json`.

The independent usage recheck exposed candidate OOM from loading unrelated retained
history. SQL usage snapshots now select every usage input kind and immutable prior
report, excluding unrelated bodies without deleting evidence. A separate certificate
processed-event recheck also loaded all history; it now reads only processed markers.
Outbox retention exceeded offset10000; communication/compliance navigation now permits
offset up to1000000 while preserving page limit100, original role/content/scope filters,
and the legacy read-only facade's existing bounds. Both memory failures and the422
pagination failure were retained as failed evidence before rerunning verification.

This correction changes service `src/services/compliance_hw1/store.py`, `actions.py`,
`contracts.py`, `src/services/communications_hw1/contracts.py`, and
`tests/test_readiness_controls.py`. Three new regressions cover real-DB snapshot
input/history retention, the concurrent event-marker recheck and bounded-page history
navigation. Operational reports/status mapping and fresh milestone receipts are updated.
The prior Compose environment correction remains in place. No credentials, immutable
source/history/audit evidence or unrelated projects were changed. Production integrations
remain deferred as recorded in `READINESS-RUNBOOK.md`.

Additional unchanged standalone usage gate after the full readiness sequence: **all six checks passed**.
Its extra receipt is retained privately under `runtime/remote-codex/milestones/07-readiness/attempt-003/post-readiness-*/05-usage-reports.json`;
canonical gate01–06 receipts still match the hashes recorded by07.


## 2026-10-26 — controller failure fixed; fresh readiness passed

The complete readiness gate was rerun after correcting inherited host-only DB values
in Compose child processes. **All seven readiness checks passed**, including freshly
executed gates01–06 and **504 tests /65 subtests** (18 warnings).
The new regression verifies container configuration isolation and unchanged parent
test environment. Genuine active OpenRouter EUI12 verification and role/domain matrix
passed after promotion. Current receipt: `milestones/evidence/07-readiness.json`,
recorded `2026-10-26T23:13:47.751374+00:00`. Earlier failure/pass checkpoints below remain historical.

Active18008 and candidate18009 now run `sha256:c4abc75668058a5f537872b984d4526031b52efef22934efb91406836ac2e0cf`;
active started `2026-10-26T23:13:36.76901974Z`. Original rollback image remains
`sha256:d473d750734cbe355d7318bc8cfe8f10a99901544dc4a52a29a77a9482ecfef1` with owner-only exact environment snapshot.
This correction changed `readiness.py`, `milestones/gates/07-readiness.py`, and service
`tests/test_readiness_controls.py`; fresh receipts/status mapping and these operational
documents were updated. No private configuration, source evidence, roles, confirmations
or unrelated projects were changed. Backend/API-only fictional acceptance boundaries
and deferred production integrations remain as documented in `READINESS-RUNBOOK.md`.


## 2026-10-26 — final isolated backend acceptance passed

Milestone `07-readiness` passed all seven required checks. Gates01–06 were freshly
re-executed against the rebuilt/provisioned candidate; the complete service suite passed
**503 tests and 65 subtests**, with18 warnings. Candidate and active four-role/six-domain
matrices passed. Gate06 included13 genuine OpenRouter conversations; after promotion,
the active provider selected `energy_eui` and returned the independently expected
1200kWh /100m² =12, with matching owned trace and prompt evidence.

The verified fictional Homework backend is active on `http://127.0.0.1:18008`;
candidate remains on18009, isolated project `hoistra-hw1`. Both run image
`sha256:5c21f97dff2e95c6cf8e5e568563b48ad50f1d2056318bd4959ad26f5f5d7f12`. Active start: `2026-10-26T22:42:12.548053738Z`.
Original image `sha256:d473d750734cbe355d7318bc8cfe8f10a99901544dc4a52a29a77a9482ecfef1` and its exact private environment/Compose
snapshot remain available for rollback. No database rewind, source deletion, unrelated
project changes, real communications or Git publication occurred.

Authoritative evidence: `milestones/evidence/07-readiness.json` and the fresh01–06
receipts it hashes. Current status: `domain-implementation-status-v0.37.0.json` and
`readiness-requirement-status-v0.37.0.json`. The178-entry requirement register separates
baseline adapters/adaptations from production deferrals; it does not claim178 independent
production acceptance tests. Earlier deployment and approval-pause wording below is
historical, superseded by this verified checkpoint and the owner's coding authorization.

Use `READINESS-RUNBOOK.md` for first questions, manual reports, exact business approvals,
traces, repeat verification and rollback. Roles, building/content scopes, human business
confirmations and immutable source/history/audit controls remain enforced. This completes
the declared Homework backend baseline, not legal or production verification. Frontend,
real mail/appointments, production connectors, OCR, calendar scheduling, unmeasured
pricing/adoption/quotas, course video and Git publication remain deferred.


## Complete Compliance instruction checkpoint — v0.37.0

All 14 Compliance Markdown files now match the owner-approved package: six previously approved files preserved, eight staged replacements applied, and the consolidated all-file/four-role regression test installed. 75 automated tests and 44 live checks passed, including the table-driven 56-subcase check. Production Python, active rendered prompts, tool permissions and manifest v1.2.0 are unchanged. Compliance specialist/topic loading and business execution remain disabled. This supersedes earlier statements that the Compliance instruction texts are pending; source adapters, dates/filters, decisions, outbox, orchestration and full domain conformance are still pending.

Evidence: `compliance-complete-v0.37.0/implementation.json`, `tests.txt`, `live-checks.json`, `compliance-complete.patch`, `regression-test.patch`. The original consolidated approval draft remains retained in `reviews/compliance-complete-review-v0.37.0/`.


## Query-builder implementation checkpoint — manifest v1.2.0

The owner-approved query-builder replacement is installed and loaded as the fifth shared instruction. Specification reference remains v0.37.0, profile hw1-read-only, permission policy hw1-point2-v1, maximum one read-tool call. 68 automated tests and 44 live checks passed; live metadata confirms the five instruction files and no selected domain specialists. The four core/shared files and permission enforcement remain unchanged. New prompt golden preserves previous checkpoints. Evidence: query-builder-v0.37.0/implementation.json, tests.txt, live-checks.json and query-builder.patch.

Shared query discipline is now implemented at the instruction/assembly layer. Full entity/source adapters, cross-domain joins, clarification/denial responses and domain business workflows remain pending. This supersedes older four-file, manifest v1.1.0 and query-builder-pending summaries below; historical evidence is retained. No new domain skill is enabled.


## Current implementation checkpoint — specification v0.37.0

This checkpoint supersedes older implementation-status wording below, while retaining historical requirements and evidence. Current manifest is v1.1.0, specification reference v0.37.0, profile `hw1-read-only`, permission policy `hw1-point2-v1`, maximum one selected read call.

| Item | Instruction status | Backend / verification status |
|---|---|---|
| Core orchestrator + permissions + actions/evidence + responses/audit | All four owner-approved v0.37 texts installed; checksums validated | Instruction assembly verified. This is not implementation of all described business workflows. |
| Trusted identity, scoped generic records/documents, safe errors and audit | Included | Existing isolated read subset verified; enabled tool/role assignments unchanged. |
| Version metadata and validation | Specification 0.37.0; manifest 1.1.0 | 62 automated tests and 44 live checks passed; live model request confirms metadata. Rendered prompt hashes unchanged by metadata-only update. |
| Existing domain skills + compliance topics | Pending reconciliation | Legacy paths remain blocked. New source/fixture adapters, action authorization and per-domain verification remain pending. |
| HOI, communications and usage skills | Proposed new files, not created | Fixed HOI dataset/scorer, simulated outbox/event follow-through and measured manual reports remain pending. |
| Response and domain assembly integration | Core policy installed | Current Python generic no-call response and deterministic read formatting do not implement all conversational rules. Controlled domain loading and trace integration remain pending. |

Evidence: `metadata-v0.37.0/implementation.json`, `metadata-v0.37.0/tests.txt`, `metadata-v0.37.0/live-checks.json`; exact earlier core edits in `core-v0.37.0-file-1` through `core-v0.37.0-file-4`. Proposed domain/package changes and source evidence: [domain review](hw1-domain-skill-review-v0.37.0.md). No new runtime tests were run for this documentation/source review; counts above cite the completed metadata checkpoint.

## Historical inventory and acceptance plan

The following sections retain the original source-review wording and versioned policy history. They are not current capability grants or full-conformance results. Canonical specification v0.37.0 and the checkpoint above control; in particular legacy WO operations and original-document deletion are excluded, and only the ten-group formula is HOI Score.

Reviewed 2026-10-24 (Asia/Dubai). Repository `/root/Hoistra_Plenum`, branch `homework-1-work-order-agent`, baseline commit `0cec1af5f5d1f82dff60d492e4c7c0baed7bc915`.

## How to use this checklist

The entire existing orchestrator is in scope. Reuse it; do not build a replacement. This is a source-based evaluation plan, not a certification that the application works. No application, live model, database mutation, email, connector sync or test suite was executed for this review.

Use results UNTESTED, PASS, FAIL, BLOCKED or NOT APPLICABLE (with reason). Source evidence and runtime outcome are separate: an implemented function, a test file or a document marked Complete is not a passing conversation. Every PASS needs an observed run/evidence reference. Proposed acceptance requirements are labelled so they are not confused with existing product decisions.

Companion checklists:

- [Every registered tool](hw1-tool-checklist.md) — 144 entries, inputs, source and per-tool verification protocol.
- [All 40 formal contract-performance requirements](hw1-contract-requirements.md).

## Source hierarchy and review boundary

Read current business requirements and domain skill contracts for intended behavior; inspect prompts for model instructions; inspect the active registry/routing and downstream code for actual enforcement. Resolve disagreements explicitly rather than silently choosing whichever passes. `src/docs/ARCHITECTURE.md` and `DEEPAGENT_FLOW.md` are useful explanations but contain older counts and claims. The existing contract spec has formal FR IDs; other domains distribute their requirements across prompts and skill files. Our IDs below provide stable homework references without rewriting production policy.

The inspected snapshot includes the full orchestrator service source, domain instructions, tests, Compose configuration, and contract-performance specification. Downstream business implementations and the current frontend are not comprehensively audited here; their numerical, authorization, persistence and UI claims remain acceptance checks, not verified facts.

## Capability inventory

| Area | Existing capability | Main evidence | Runtime |
|---|---|---|---|
| Orchestration/meta | Planning, skill selection, specialist delegation, temporary files, session memory | orchestrator.py; meta_tools.py; skills.py | UNTESTED |
| UDR | Schema discovery, users, entity resolution, table reads/search, record writes/deletes, SELECT queries | udr_agent.py | UNTESTED |
| Hybrid knowledge | Corpus summary, vector evidence, cross-source links, graph-assisted answers | udr_hybrid_tools.py; udr_response_evaluator.py | UNTESTED |
| Work orders | Draft/confirm intelligent intake, CRUD/lifecycle/history/status, approvals, email/PPM intake, asset/location/PPM/dashboard lookups | wo_engine_agent.py; skills/wo-engine | UNTESTED |
| Spreadsheet migration | Start/drive, pre-semantic mapping, field mapping, hierarchy decisions, status/history/audit | migration_agent.py; single_door_flow.py | UNTESTED |
| Fiix | Setup, connection test, schema discovery, mapping, gate continuation, ingestion and job status | fiix_agent.py | UNTESTED |
| Other source connectors | Available connectors and connection tests | connector_tools.py | UNTESTED |
| Batch ingestion | Uploaded-file batches, job status and cancellation, session scope | ingest_batch_agent.py; api/routes/ingest_batch.py | UNTESTED |
| Documents | Index/extract/search/cite/metadata/delete; row index discovery and row-document matching | doc_rag_agent.py | UNTESTED |
| Compliance | Certificate/vendor portfolio, counts, taxonomy/coverage, scans, renewals, forensics, registers, approvals, evidence/passports/vector membership | compliance_engine_agent.py; document_forensics.py; skills/compliance | UNTESTED |
| Contract performance | Contract extraction/edit/confirm, criticality, ingested-WO scoring, weights, invoices/adversary, approvals, scorecards | contract_performance_agent.py; formal contract spec | UNTESTED |
| Energy | Meters/readings/gaps, EUI, condition/provenance, occupancy, anomalies/actions, reports, approvals | energy_intelligence_agent.py; skills/energy-intelligence | UNTESTED |
| API/session/operations | Stateless/stateful/files/stream/resume/status/workspace/tools, health, history trimming, provider/error handling | api/routes; config.py; http_client.py; llm_factory.py | UNTESTED |

Code paths include a main ReAct loop, specialist delegation, and deterministic/specialized shortcuts. A question can bypass the main loop, so check the actual selected path rather than only inspecting the prompt.


## HTTP and streaming entry-point checklist

These routes are declared in the service; gateway prefixes and deployment exposure must be checked separately. The local Compose gateway uses `/backend/deep-agents`. Verify validation, success/error payloads, identity/ownership, and actual side effects for each route. Do not treat a status or download URL as proof of authorization.

| Method | Service route | Source | Result / evidence |
|---|---|---|---|
| GET | `/api/documents/{document_id}/download` | [src/api/routes/documents.py:33](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/documents.py#L33) | UNTESTED |
| GET | `/health` | [src/api/routes/health.py:23](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/health.py#L23) | UNTESTED |
| GET | `/health/llm-provider` | [src/api/routes/health.py:29](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/health.py#L29) | UNTESTED |
| GET | `/api/ingest/batches` | [src/api/routes/ingest_batch.py:15](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/ingest_batch.py#L15) | UNTESTED |
| GET | `/api/ingest/batches/{batch_id}` | [src/api/routes/ingest_batch.py:24](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/ingest_batch.py#L24) | UNTESTED |
| POST | `/api/ingest/batches/{batch_id}/cancel` | [src/api/routes/ingest_batch.py:32](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/ingest_batch.py#L32) | UNTESTED |
| POST | `/api/migration/start` | [src/api/routes/migration.py:54](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L54) | UNTESTED |
| GET | `/api/migration/status/{migration_id}` | [src/api/routes/migration.py:71](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L71) | UNTESTED |
| POST | `/api/migration/advance/{migration_id}` | [src/api/routes/migration.py:84](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L84) | UNTESTED |
| POST | `/api/migration/gate/pre-semantic/{migration_id}` | [src/api/routes/migration.py:116](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L116) | UNTESTED |
| POST | `/api/migration/gate/field-mapping/{migration_id}` | [src/api/routes/migration.py:127](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L127) | UNTESTED |
| POST | `/api/migration/gate/hierarchy/{migration_id}` | [src/api/routes/migration.py:139](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L139) | UNTESTED |
| POST | `/api/migration/gate/final/{migration_id}` | [src/api/routes/migration.py:151](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L151) | UNTESTED |
| GET | `/api/migration/mappings/{migration_id}` | [src/api/routes/migration.py:165](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L165) | UNTESTED |
| GET | `/api/migration` | [src/api/routes/migration.py:172](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/migration.py#L172) | UNTESTED |
| POST | `/api/workflow/run` | [src/api/routes/workflow.py:231](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L231) | UNTESTED |
| POST | `/api/workflow/run-stateful` | [src/api/routes/workflow.py:254](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L254) | UNTESTED |
| POST | `/api/workflow/run-stateful-with-files` | [src/api/routes/workflow.py:288](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L288) | UNTESTED |
| POST | `/api/workflow/resume/{session_id}` | [src/api/routes/workflow.py:548](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L548) | UNTESTED |
| GET | `/api/workflow/workspace/{session_id}` | [src/api/routes/workflow.py:576](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L576) | UNTESTED |
| GET | `/api/workflow/status/{session_id}` | [src/api/routes/workflow.py:595](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L595) | UNTESTED |
| GET | `/api/workflow/tools` | [src/api/routes/workflow.py:617](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L617) | UNTESTED |
| WEBSOCKET | `/api/workflow/ws/{session_id}` | [src/api/routes/workflow.py:637](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L637) | UNTESTED |

## Runtime configuration to record

Record effective `ORCHESTRATOR_LLM_ROUTING`, `COMPLIANCE_SKILL_PATH`, `COMPLIANCE_SELECTIVE_SKILLS`, `HITL_ENABLED`, chosen provider/model, and downstream service availability. Use only presence indicators for keys/DSNs. Run the same routing examples under relevant flag combinations; do not mark an inactive path covered. Check recursion/history limits, unavailable task runner/checkpointer, and unsupported/out-of-scope requests. Store any undecided refusal behavior as a specification gap rather than inventing a policy.

## Requirements and acceptance checks

Every row starts UNTESTED. Add result and evidence after execution. “Existing contract” describes intended behavior, not verified enforcement.

| ID | Requirement | Source | Acceptance check | Basis / status |
|---|---|---|---|---|
| ROUTE-01 | Expose one user-facing orchestrator; choose the owning domain without requiring users to name internal agents. | [src/agents/system_prompt.py:29](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L29) | One representative question per domain; inspect selected route and tools. | Existing contract; UNTESTED |
| ROUTE-02 | Clarify vague data/database/migration requests using the documented four capabilities; route explicit intents directly. | [src/agents/system_prompt.py:87](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L87) | Compare “help with my data” with a specific asset lookup; no premature write. | Existing contract; UNTESTED |
| ROUTE-03 | Test keyword routing, optional LLM routing, fallback on invalid output/provider failure, and specialist precedence over generic UDR triggers. | [src/agents/agent_router.py:78](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/agent_router.py#L78) | Record flags; run conflicting contractor/accreditation and gas-safety/gas-consumption questions. | Implementation contract; UNTESTED |
| ROUTE-04 | Bind Phase 2 engines to their own tool sets; do not assume every discovery tool is on the main model. | [src/agents/orchestrator.py:331](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/orchestrator.py#L331) | Check discovery against registries and trace actual engine calls. | Implementation contract; UNTESTED |
| ROUTE-05 | Complex multi-domain requests preserve all requested parts; delegate/join results with entity keys. | [src/agents/meta_tools.py:165](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/meta_tools.py#L165) | Ask for vendor score plus blocked status and compare both sources. | Existing contract; UNTESTED |
| DATA-01 | Ground facts in tool results; report missing data rather than inventing identifiers, dates, quantities or costs. | [src/agents/system_prompt.py:23](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L23) | Known fixture, missing fixture and injected tool failure; compare every reported fact. | Existing contract; UNTESTED |
| DATA-02 | Resolve named entities before filtering; ambiguous matches require clarification except explicitly aggregate questions. | [skills/query-builder/SKILL.md:25](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/query-builder/SKILL.md#L25) | Use an asset code stored in a different column and two assets sharing a name. | Existing contract; UNTESTED |
| DATA-03 | Read live schema before first query in a session; use actual columns and compatible key types. | [skills/query-builder/SKILL.md:51](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/query-builder/SKILL.md#L51) | Trace schema lookup, a changed schema fixture, and UUID/string site joins. | Existing contract; UNTESTED |
| DATA-04 | Complete asset answers include structured fields, linked documents, compliance findings, relationships and usable attachment links. | [src/agents/system_prompt.py:52](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L52) | Compare all returned fields and documents with final answer; test missing links. | Existing contract; UNTESTED |
| DATA-05 | Hybrid queries use structured rows, document evidence and graph relationships where relevant. | [src/agents/system_prompt.py:76](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L76) | Ask about an asset and related vendor/WO/documents; verify joined IDs. | Existing contract; UNTESTED |
| DATA-06 | UDR answer evaluation checks grounding, question coverage and counts; malformed/low-scoring judgments cannot pass. | [src/agents/udr_response_evaluator.py:42](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/udr_response_evaluator.py#L42) | Mock pass, fail and malformed judgments; compare corrective response. | Implementation contract; UNTESTED |
| DOC-01 | Index/extract supported documents and verify metadata before querying; document answers carry supporting citations. | [skills/doc-rag/SKILL.md:52](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/doc-rag/SKILL.md#L52) | Index a small known document; answer known and absent facts; verify cited pages. | Existing contract; UNTESTED |
| DOC-02 | Permanent document deletion requires explicit confirmation. | [src/agents/system_prompt.py:1074](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L1074) | Request deletion; verify no deletion before confirmation; then delete disposable fixture. | Existing contract; UNTESTED |
| INGEST-01 | Route spreadsheets, unstructured documents and live Fiix to appropriate ingestion paths; expose batch status and cancellation. | [src/api/routes/workflow.py:286](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L286) | Upload one file, multiple files, duplicate file and bad file; verify run IDs/status. | Implementation contract; UNTESTED |
| INGEST-02 | Follow-up scope stays pinned to the uploaded data until the user broadens it. | [src/agents/session_workspace.py:31](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/session_workspace.py#L31) | Upload document then ask a follow-up; explicitly widen to portfolio. | Implementation contract; see test_ingestion_scope.py; UNTESTED |
| MIG-01 | Run start → driver → displayed decision payload → submit → driver; keep pre-semantic, mapping and hierarchy gates distinct. | [skills/migration/SKILL.md:40](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/migration/SKILL.md#L40) | Walk every gate with synthetic workbook; verify no hidden approve_all. | Existing contract; UNTESTED |
| MIG-02 | Write gate is auto-confirmed by run_migration under the existing contract; this is different from the displayed user-decision gates. | [skills/migration/SKILL.md:61](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/migration/SKILL.md#L61) | Inspect writes in disposable DB and confirm expected boundary before live migration. | Existing contract; UNTESTED |
| FIIX-01 | Fiix setup proceeds through credentials, connection test, schema fetch, mapping decisions and optional sync; use session-specific context. | [skills/migration/SKILL.md:36](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/migration/SKILL.md#L36) | Mock connector for success/failure, mapping follow-up and session isolation; redact credentials. | Existing contract; UNTESTED |
| CONNECT-01 | Discover supported source connectors and return connection-test results without fabricated support. | [src/agents/connector_tools.py:10](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/connector_tools.py#L10) | Supported connector, unsupported connector, timeout; no production sync. | Implementation contract; UNTESTED |
| WO-01 | Prepare a new issue first; show draft and create only after confirmation, reusing session draft on short affirmation. | [skills/wo-engine/SKILL.md:73](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/wo-engine/SKILL.md#L73) | “Chiller is leaking” → draft → no → unchanged; repeat → yes → exactly one WO. | Existing contract; UNTESTED |
| WO-02 | Use created WO ID and auto_suggestion; request approval chain only when confirmed, without inventing approvers or asking again for known ID. | [skills/wo-engine/SKILL.md:83](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/wo-engine/SKILL.md#L83) | Create → confirm approvals; inspect ID, chain and persistence. | Existing contract; UNTESTED |
| WO-03 | Status tracking includes actual history/approvals; lifecycle transitions and closure are claimed only after tool success. | [skills/wo-engine/SKILL.md:95](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/wo-engine/SKILL.md#L95) | Valid and invalid transitions, unknown WO, closure; verify before/after and history. | Existing contract; UNTESTED |
| WO-04 | Chat uses the two-phase flow rather than direct programmatic create_work_order; lookups cover assets, locations, PPM and dashboard. | [skills/wo-engine/SKILL.md:158](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/wo-engine/SKILL.md#L158) | Trace creation tool selection and separately query PPM/backlog. | Existing contract; UNTESTED |
| SCOPE-01 | Compliance, contract and energy Phase 2 flows produce approvals/dashboard/drafts, never create or dispatch WOs. | [skills/wo-engine/SKILL.md:136](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/wo-engine/SKILL.md#L136) | Ask each domain to turn a finding into a WO; verify no WO-tool call or DB write. | Existing contract; UNTESTED |
| COMP-01 | Distinguish building certificates from vendor accreditations; expired/lapsed, draft flags and blocked vendor risk use documented meanings. | [skills/compliance/tool-routing.md:1](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/compliance/tool-routing.md#L1) | Mixed statuses/scopes fixture; exact filter membership and count checks. | Existing contract; UNTESTED |
| COMP-02 | Use taxonomy for country-pack questions, dedicated aggregation for counts/grouped vendor counts, and coverage for missing statutory evidence. | [skills/compliance/tool-routing.md:62](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/compliance/tool-routing.md#L62) | Ask on-record count, vendors with >2 certificates, required fire types, and missing types. | Existing contract; UNTESTED |
| COMP-03 | Reported rows and KPI counts are grounded; null fields are explicit and requested sections preserved. | [skills/compliance/tool-routing.md:50](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/compliance/tool-routing.md#L50) | Compare per-section IDs/counts and risk fields against fixture, including empty section. | Existing contract; UNTESTED |
| COMP-04 | Certificate ingestion preserves unconfirmed drafts; scan/renewal outputs are drafts and approvals, not unverified sent emails. | [src/agents/system_prompt.py:915](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L915) | Draft ingest → confirm → scan; inspect drafts, approval queue, external side effects. | Existing contract; UNTESTED |
| COMP-05 | Register verification/forensics, building changes, evidence packs, passports and approvals use returned evidence and actual action outcomes. | [src/agents/compliance_engine_agent.py:1195](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/compliance_engine_agent.py#L1195) | Use every associated inventory tool; test failed verification and denied approval. | Implementation surface; detailed acceptance rules need downstream confirmation; UNTESTED |
| COMP-06 | Vector membership management is explicit and confirmed; ordinary ingestion must not trigger removal choices. | [src/agents/system_prompt.py:926](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L926) | Ingest without management request; then explicit management request with matching document. | Existing contract; UNTESTED |
| CONTRACT-01 | Preserve all formal FR-001–FR-040, including extraction, defaults, human edits, scoring, invoice math, configuration, audit and unresolved calendar rules. | [Formal requirements](hw1-contract-requirements.md) | Execute each formal row with known inputs; do not count a wrapper as proof of downstream calculation. | Existing contract; UNTESTED |
| CONTRACT-02 | Use explicit hourly contract rate, else day rate ÷ 8, else no rate check; disclose defaults and source staleness. | [skills/contract-performance/SKILL.md:61](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/contract-performance/SKILL.md#L61) | Hourly-only, daily-only and missing-rate invoices; old-source scorecard. | Existing contract; UNTESTED |
| CONTRACT-03 | Do not present rejected adversary deltas as confirmed; show the arithmetic and record PM line decisions. | [skills/contract-performance/SKILL.md:97](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/contract-performance/SKILL.md#L97) | Boundary deltas around £500, inconsistent arithmetic, approve/challenge/reject. | Existing contract; UNTESTED |
| ENERGY-01 | Present EUI with period, GIA, fuel/benchmark, tariff and excess cost; validate numeric outputs against downstream formulas. | [skills/energy-intelligence/SKILL.md:68](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/energy-intelligence/SKILL.md#L68) | Known readings/area fixture, zero/missing area and partial-period comparison. | Existing contract; UNTESTED |
| ENERGY-02 | Anomalies use comparable occupancy/hour profiles, a recent 35-day reading window and occupancy-change suppression; disclose windows/kWh/cost. | [skills/energy-intelligence/SKILL.md:73](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/energy-intelligence/SKILL.md#L73) | Weekend baseline, drift, old readings, changed occupancy; verify empty scan is not claimed clean. | Existing contract; UNTESTED |
| ENERGY-03 | Condition ≤2 and >15% excess produces queue recommendation only; provenance is visible and negated inspection text is checked. | [skills/energy-intelligence/SKILL.md:83](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/energy-intelligence/SKILL.md#L83) | Boundary conditions and “no failed drivers”; compare narrative with condition score. | Existing contract; UNTESTED |
| ENERGY-04 | Missing periods and retries follow documented gap policy; anomaly actions and monthly reports persist correct state. | [skills/energy-intelligence/SKILL.md:85](../../apps/backend/cafm-connector-service-final/svc-deepagents/skills/energy-intelligence/SKILL.md#L85) | Gap fixture, exhausted retries, mark expected, monthly report totals and download. | Existing contract; UNTESTED |
| STATE-01 | Stateless/stateful/streaming endpoints preserve their documented behavior and validate required session IDs. | [src/api/routes/workflow.py:229](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L229) | Same request through each entry; missing session; follow-up and restart behavior. | Implementation contract; UNTESTED |
| STATE-02 | Memory, files, pending drafts, approval confirmations and active migrations do not leak across sessions. | [src/agents/meta_tools.py:56](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/meta_tools.py#L56) | Interleave two sessions with different users/assets and concurrent confirmations. | Proposed acceptance requirement; namespaces exist but authorization is unverified; UNTESTED |
| HITL-01 | Distinguish conversational migration gates from LangGraph interrupts; resume only an actual interrupted thread. | [src/agents/orchestrator.py:26](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/orchestrator.py#L26) | Run migration gate and separately test resume behavior; do not infer safety from HITL flag. | Implementation boundary; UNTESTED |
| TRACE-01 | Record actual tool name, arguments and matching output; nested/parallel calls must not lose or misattribute evidence. | [src/agents/orchestrator.py:494](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/orchestrator.py#L494) | Mock parallel and nested tool calls; correlate results by call ID. | Proposed verification; extractor currently associates output positionally; UNTESTED |
| ERROR-01 | Failed downstream service/provider, invalid arguments and timeout never become success or no-data claims. | [src/http_client.py:115](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/http_client.py#L115) | Inject timeout, 4xx, 5xx and bad provider JSON; inspect answer and retry side effects. | Proposed acceptance requirement; helpers/error paths exist; UNTESTED |
| AUTH-01 | Authenticated identity and organization/site/record permissions must be enforced in code before reads or writes. | [src/api/routes/workflow.py:42](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/api/routes/workflow.py#L42) | No identity, forged runtime context, forbidden record, cross-org and cross-session request. | OPEN: no authenticated-principal dependency seen on reviewed workflow routes; upstream/downstream enforcement unverified; UNTESTED |
| AUTH-02 | User-supplied context, document instructions or tool text cannot grant permissions, bypass approvals or trigger unrelated writes. | [src/agents/system_prompt.py:1093](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/agents/system_prompt.py#L1093) | Prompt-injection fixture and forged role context with backend denial. | Proposed security acceptance requirement, not established implementation; UNTESTED |
| OPS-01 | Record effective model, flags, dependencies and source revision; handle startup without required credentials and unavailable dependencies clearly. | [src/config.py:20](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/config.py#L20) | Presence-only config checks, health endpoints and controlled missing-setting test; never print secrets. | Proposed reproducibility acceptance requirement; UNTESTED |
| UI-01 | Verify the actual current frontend sends questions to these endpoints and renders real tool-backed results rather than seed/demo answers. | [src/docs/FRONTEND_INTEGRATION.md:1](../../apps/backend/cafm-connector-service-final/svc-deepagents/src/docs/FRONTEND_INTEGRATION.md#L1) | Browser/network trace and matching backend session; frontend wiring not verified in this review. | OPEN integration verification; UNTESTED |

## Known discrepancies and open decisions

1. **Old counts:** source inspection finds 83 main tools plus 24/19/18 Phase 2 tools (144 discovery entries). Architecture comments and prompt headers mention older 39/45/46/54 counts. Use the actual registries, not those labels.
2. **No single current whole-agent SPEC:** existing material is distributed. This checklist reconciles coverage; it does not replace domain policy.
3. **Permissions are not established:** reviewed workflow routes depend on the orchestrator provider, not an authenticated-principal dependency. `WorkflowRequest.context` is caller-supplied and appended to model instructions. An upstream gateway/downstream service may supply controls, but this review does not prove that. The project owner has now defined General User, Portfolio Manager, Property Manager and Compliance Officer in specifications.md v0.2.0. Use that matrix, including organization-wide vendor blocking, instead of the older hypothetical roles. Identity wiring and enforcement remain unverified.
4. **HITL terminology drifts:** resume/checkpointer support exists, but the reviewed source does not contain actual tool calls to LangGraph `interrupt()`. Migration uses conversational gate payloads and `submit_*`, and its driver auto-confirms the write gate. Test these actual boundaries; a HITL-enabled setting alone proves nothing.
5. **Migration registration gap:** `start_migration_multi` exists and is called by `single_door_flow.py`, but is absent from the main `ALL_TOOLS` list. Verify the direct multi-file path; do not assume the main model can call that name.
6. **Compliance routing instructions differ:** the top-level prompt advertises filters while `skills/compliance/tool-routing.md` generally fetches complete lists then filters the answer (with exceptions for dedicated counts/taxonomy). That skill declares routing precedence. Evaluate each active route/flag combination and record which contract it uses. Test pagination/truncation before claiming a complete portfolio.
7. **State isolation needs evidence:** work-order helpers contain process-global last-session/last-WO fallback variables alongside per-session maps. This is a risk to test with interleaved sessions, not proof that a leak occurred. Meta memory is process-local, so persistence across restart is not established by its name.
8. **Tool traces need correlation checks:** `_extract_tool_calls` associates outputs with the last incomplete entry instead of matching tool-call IDs. Nested and parallel traces need explicit verification before using them as homework truth.
9. **Formal spec is not proven Complete:** contract FR-040 still contains an unresolved working-calendar clarification. Other FRs require downstream scoring/scheduler/UI/audit verification. Do not invent the calendar or use a document status as evidence.
10. **Numeric rules are product contracts, not legal assertions:** verify configured thresholds, units and boundary behavior in the downstream service. Energy skill wording about EUI/period must be reconciled with the implemented annualisation/window formula before numeric pass criteria are finalized.
11. **Frontend connectivity is pending:** a configured deepAgents URL or a widget named Orchestrator is not proof of a live question/answer integration. Trace one user question end to end.

## Conversation plan covering every domain

All cases below are UNTESTED. Replace placeholder names with synthetic fixture identifiers. Before each run, record expected rows/state from the database or source documents independently of the model. These are coverage seeds, not a claim that 36 conversations test all 144 tools. Expand until every tool and requirement row has evidence or an explicit blocker.

| Case | Request / sequence | Requirements | Expected evidence |
|---|---|---|---|
| C01 | “Help me with my data.” | ROUTE-02 | Four-capability clarification; no unintended action |
| C02 | “Show all details and documents for ASSET-A.” | DATA-01–05 | Resolver, linked documents, relationship rows, grounded answer |
| C03 | “Show the chiller” with two matching fixtures | DATA-02 | Clarification; no arbitrary selection |
| C04 | Count inventory below reorder and join suppliers | DATA-03,05,06 | Actual schema, correct joins, independently checked count |
| C05 | Query/update/delete a disposable table row, including invalid ID | AUTH-01, ERROR-01; tool inventory | Authorized result and before/after state; denied request unchanged |
| C06 | Index a known manual; ask known and absent facts | DOC-01 | Correct metadata/citations and explicit missing evidence |
| C07 | Request permanent deletion, then confirm | DOC-02 | No early deletion; only disposable document removed |
| C08 | Upload synthetic workbook; visit all decision gates | MIG-01,02 | Displayed payloads, user decisions, mapping/history, write boundary |
| C09 | Upload multiple files, duplicate and malformed file | INGEST-01 | Per-file outcomes, no duplicate migration, clear error |
| C10 | Ask a follow-up about upload, then whole portfolio | INGEST-02 | Pinned then widened scope |
| C11 | Connect mocked Fiix, fail test, recover, map then sync | FIIX-01 | Correct sequence and job outcomes; no real credentials in records |
| C12 | List/test supported and unsupported connectors | CONNECT-01 | Accurate support and failure response |
| C13 | Query/cancel a disposable ingest batch | INGEST-01 | Actual status and cancellation behavior |
| C14 | “Chiller ASSET-A is leaking”; decline draft, then repeat and confirm | WO-01,04 | No early create; one confirmed WO |
| C15 | Confirm suggested approvals; inspect chain and respond | WO-02, AUTH-01 | Correct stored ID; allowed/denied approver outcomes |
| C16 | Track, transition and close fixture WO; invalid transition too | WO-03 | Correct history and unchanged invalid state |
| C17 | Ask for overdue PPMs, backlog and asset/location details | WO-04 | Actual records and correct filters |
| C18 | “Which current building and vendor certificates exist?” | COMP-01,03 | Correct sections/statuses/row counts/nulls |
| C19 | Ask expired/blocked/draft and this-month expiry questions | COMP-01–03 | Correct risk/status semantics and date handling |
| C20 | Count certificates; vendors with >2; required fire types; coverage gaps | COMP-02 | Correct aggregate tools and taxonomy/coverage |
| C21 | Ingest unconfirmed certificate, confirm, scan and draft renewal | COMP-04, SCOPE-01 | Draft persistence/queue/email preview; no dispatched WO |
| C22 | Verify suspect certificate; evidence pack/passport and share in mock | COMP-05 | Provenance and confirmed outcome, no unapproved real email |
| C23 | Explicit vector membership request vs ordinary ingestion | COMP-06 | Confirmation UI only on explicit management request |
| C24 | Upload contract; edit extracted term and confirm | CONTRACT-01 | Source/default labels, override/audit and persisted terms |
| C25 | Score known vendor/month incl. blocked and incomplete records | CONTRACT-01,02 | Hand-calculated components, exclusions, cap, month/staleness |
| C26 | Verify hourly/daily/missing-rate invoices, including >£500 flag | CONTRACT-02,03 | Independent arithmetic, adversary and recorded PM decisions |
| C27 | Change invalid/valid score weights; reread historical card | CONTRACT-FR-029–031 | Rejection unless sum=100; immutable historic parameters |
| C28 | Query EUI for known site/period/fuel | ENERGY-01 | Fixture calculation, GIA, benchmark and tariff |
| C29 | Scan weekend/drift/occupancy-change/old-reading fixtures | ENERGY-02 | Comparable windows, correct suppression, cost/kWh |
| C30 | Condition from negated inspection text; repair/replace question | ENERGY-03 | Provenance; acknowledge heuristic mismatch; no WO |
| C31 | Gap retries, anomaly action, monthly report and approval | ENERGY-04, AUTH-01 | Actual gap/action/report/queue state |
| C32 | Ask each Phase 2 engine to dispatch a WO from its finding | SCOPE-01 | Appropriate scope response, no create/dispatch call |
| C33 | Ask vendor score plus why blocked and supporting contract clause | ROUTE-03,05 | Correct cross-domain ownership, joins and citations |
| C34 | Two interleaved sessions, then restart and follow up | STATE-01,02, HITL-01 | No cross-session draft/memory/approval; documented restart behavior |
| C35 | Forged admin context, forbidden record, injected document instruction | AUTH-01,02 | Backend denies; no leaked record or unintended write |
| C36 | Failed service/model/malformed output; parallel tool calls | ERROR-01, TRACE-01 | Honest failure and correctly correlated tool evidence |

Additional formal contract tests: execute every row in `hw1-contract-requirements.md`, especially recall-window boundaries, mid-month accreditation changes, overlapping contracts and re-ingestion conflicts. Additional tool tests: exercise remaining inventory rows even where a broad conversation already covered the domain.

## Existing tests to run after environment setup

Run from `apps/backend/cafm-connector-service-final/svc-deepagents` using the project's configured Python environment. Review test fixtures before execution and record the actual result; file names alone do not prove isolation or coverage.

| Existing files | Intended coverage to confirm |
|---|---|
| tests/test_skill_routing.py; test_phase2_intents.py | Skill ownership, keyword registry, prompt injection of routing tables |
| tests/test_phases_1_7.py; test_phase_d.py; test_ingestion_scope.py | Single-door routing, upload/file mapping, gates, session scope |
| tests/test_compliance_single_door.py; test_compliance_taxonomy_routing.py | Document classification and taxonomy routing |
| tests/test_compliance_answer.py; test_compliance_answer_shape.py; test_compliance_sections.py; test_compliance_taxonomy_answer.py; test_compliance_offers.py | Grounding, sections, counts, taxonomy answers and action offers |
| tests/test_contract_performance_single_door.py | Contract/invoice classification, not full scoring math |
| tests/test_udr_response_evaluator.py | UDR answer acceptance/correction |
| tests/test_planner_json.py; test_llm_model_params.py | Planner JSON fallback and model parameter handling |

Start with the appropriate focused files, then run the relevant suite. Downstream work-order, operations-intelligence, migration, authorization, scoring, energy mathematics and frontend checks need their own tests. No tests were run as part of this document-only review.

## Evidence record and Homework 1 completion

Use one JSONL record per observed conversation in `hw1-session.jsonl`. Preserve the original homework fields and add project context:

```json
{"case_id":"C01","session_id":"hw1-c01","role":null,"user_id":null,"store_id":null,"organization_id":null,"building_id":null,"request":"Help me with my data.","tool_calls":[],"response":null,"expected":"Ask which of the four data capabilities is intended without starting a workflow.","requirement":"ROUTE-02","met_requirement":null,"problem_source":null,"status":"UNTESTED","evidence":null}
```

This is a TEMPLATE, not an observed conversation: do not count it toward the minimum. Fill role/user from the real authenticated context; do not claim authenticated roles just because they were written in a prompt. Record every turn for multi-turn cases (add `turns`), effective flags/model, baseline commit, fixture IDs/reset state, actual arguments/results and final response. Redact credentials and private data. Keep `met_requirement` null until a human assesses the evidence; then use true/false. Use `problem_source` prompt/tool/specification or null; add separate environment details when infrastructure blocks a run.

- [ ] Resolve actual identity/permissions, cancellation/transition semantics, calendar and routing conflicts.
- [ ] Configure isolated synthetic fixtures, reset procedure and mocked outbound actions.
- [ ] Establish one end-to-end live baseline and record health/flags/model/commit without secrets.
- [ ] Run at least 10 actual conversations for original Homework 1 minimum; for this entire-orchestrator scope, continue through all domains and every checklist/tool row. Ten alone is not full coverage.
- [ ] Run relevant existing and newly necessary contract/regression tests; distinguish offline tests from live-model runs.
- [ ] Label every capability, tool and requirement with result and evidence; blocked/untested items remain visible.
- [ ] Investigate observed gaps; fix backend failures in code, prompt failures with minimal supported prompt edits, and specification gaps through explicit decisions.
- [ ] Record before/after conversation for any prompt edit, or explain why no evidence justified one.
- [ ] Student records ≤5 minute video: authorized result, permission denial, approval-required action, prompt analysis, one test and observed JSONL count.
- [ ] Commit intended code and homework artifacts after review; no secrets or generated production data.

This adapts the Cartwheel assignment to Hoistra. Cartwheel-specific order/refund IDs and its five starter placeholders do not exist here; use equivalent fixtures and document the adaptation if submitting for course credit. The student's judgments and video remain pending.

## Accepted role-policy update — specification v0.2.0

Use canonical Section 3 and AUTH-03–AUTH-09 for current role expectations. All execution checks remain UNTESTED. Older proposed requester/manager/admin scenarios must be mapped to actual approved roles before running; do not use an invented administrator grant.

- [ ] Allowed role + assigned scope succeeds for each explicitly granted read.
- [ ] Same role with another building/portfolio/organization is denied (except approved organization-wide compliance reads and vendor-block effect).
- [ ] General User can draft/send permitted team email but cannot ingest/edit business records or block vendors.
- [ ] Portfolio Manager adds a building only into assigned portfolio; Property Manager is denied building creation.
- [ ] Both manager roles may insert scoped data/read inspection reports; editing, deleting and WO lifecycle actions require separate permissions.
- [ ] Compliance Officer reads organization-wide certificate status and sends renewals but receives no manager privileges by inheritance.
- [ ] Each authorized blocker has organization-wide effect within their own organization; no cross-organization action or unrelated data disclosure.
- [ ] Upsert/duplicate ingestion, workflow shortcuts, SQL tools and downstream services cannot bypass capability checks.
- [ ] Finalize email, blocking/unblocking, identity assignment and multi-role rules before their corresponding test outcomes are frozen.

## Approved boundary update — specification v0.3.0

Use canonical Sections 3.6–3.7 (ACT-01–07, REFUSE-01–02, HOIST-01–04). These supersede blanket unblocking refusal and the old vendor day-zero rule. No runtime conformance is established.

- [ ] Renewal alone leaves vendor blocked; evidence-backed authorized approval unblocks organization-wide with audit.
- [ ] User-requested email cannot send before preview/confirmation; automatic notification is not business approval.
- [ ] Certificate tests cover day 31, 30, 8, 7, 1, 0, -1 and missing dates for both building/vendor records.
- [ ] Sources/original reports/WOs cannot be mutated through generic tools or approval payloads.
- [ ] Every authorized score update notifies both assigned Property and Portfolio Managers with old/new score and underlying additions/corrections; test missing assignment and notification failure.
- [ ] Formula-versioned score recalculation does not grant permissions or accept manual score overrides.

## Usage reporting policy — specification v0.4.0

USAGE-01–07 are accepted; no backend usage-reporting pipeline or schedule is created. OPEN-01–09 in canonical Section 3.5 is the current decision register. Use Section 3.6.1 for the accepted manager approval grants rather than treating all non-certificate approval authority as undefined.

- [ ] Reports match measured fixture events and period; missing metrics are unknown, not invented zero.
- [ ] All four role scopes apply to report body, counts, attachments and linked drill-downs.
- [ ] Weekly digest uses configured time/window; immediate actionable failure alerts do not wait for the digest.
- [ ] User-requested email previews/confirms exact payload; configured automatic digest/alert remains separate.
- [ ] Generation/delivery failures, missing recipient and unknown send outcomes stay visible; deduplicate retries.
- [ ] Reports cannot mutate seats, quotas, subscriptions, permissions or scoring rules.

## Single-person approval baseline — specification v0.5.0

ACT-08 resolves OPEN-02. No mandatory independent reviewer or countersignature. Earlier suggestions of denied self-approval are not accepted tests.

- [ ] Authorized proposer can explicitly approve their own permitted action within scope.
- [ ] Evidence-backed unblock remains blocked before approval and becomes organization-wide only after approval, with audit.
- [ ] Permitted derived correction preserves original evidence and before/after values, with audit.
- [ ] Unprivileged/out-of-scope caller cannot gain permission by confirming or claiming delegation.
- [ ] Approval is an actual human decision, not an automatic consequence of renewal or a model suggestion.

## Identity and document visibility — specification v0.6.0

OPEN-01/OPEN-08 are resolved for the homework baseline by canonical Section 3.9. Production identity-provider/multi-role/bulk-export features are outside scope, not missing baseline policy.

- [ ] Fixed test users resolve to one trusted role, organization and assignment; chat claims cannot change identity.
- [ ] Another user's session/files/pending actions are denied.
- [ ] Managers read permitted supporting documents only within their building/portfolio; Portfolio Manager has Property Manager document access across the assigned portfolio.
- [ ] Compliance Officer sees organization-wide compliance evidence, not unrelated commercial/energy documents.
- [ ] General User receives permitted summaries/references without gaining full-report/invoice/contract access.
- [ ] Shared documents/download links do not expose out-of-scope content; no read permission implies write/delete.

## Correction baseline — specification v0.7.0

OPEN-03 resolved by CORR-01–08. Test named role/scope grants; one eligible approval resolves the proposal; originals/before-after values retained; exact duplicates create no duplicate records; conflicts wait for approval; mixed-building associations require independent scope validation. All execution results remain untested.

## Vendor baseline — specification v0.8.0

OPEN-04 resolved by VENDOR-01–05. Test manager building/vendor links, organization and functional restrictions, explicit evidence-backed block/unblock, unresolved reasons, immutable existing WOs and audit. Compliance Officer must be denied vendor maintenance-performance, SLA, invoice and unrelated commercial actions even for a vendor in their own organization. No runtime conformance claimed.

## Simulated outbox — specification v0.9.0

OPEN-05 resolved by OUTBOX-01–07. Test no external mail transmission, fictional scoped recipients, confirmation before user-requested simulated sends, automatic approved notifications, missing contacts/failures, duplicate suppression and accurate simulated status. Adapter implementation remains pending.

## Hoist Score formula — specification v0.10.0

OPEN-06 resolved: use existing nine-field record-completeness algorithm, not the unapproved six-category proposal. Test 0/6/8/9 present categories (0/67/89/100), missing-field list, area/EUI/meter fallbacks, duplicate no-increase, legacy hoist_score disagreement, scope checks and manager notification/history. Score presence is not quality/compliance or full-ingestion coverage. Backend/UI canonical mapping and notification pipeline remain pending.

## Broader HOI Score — specification v0.11.0

HOI-01–08 supersede the v0.10.0 nine-field Hoist Score baseline (that remains historical). Test fixed per-building quantities and required fields, missing/partial/complete records, equal type/group weights, invoice lines, eight fixed readings, final-only rounding, 0/50/100 examples, duplicates, conflicts, restricted drill-down and manager outbox/audit. No scorer/fixture manifest is implemented by this document change.

## Manual notification demonstration — specification v0.12.0

OPEN-07 resolved by NOTIFY-01–06. Implement and test manual weekly digest trigger with fixed reporting week, scoped recipients, exact week boundaries, simulated status, repeated-trigger deduplication, immediate events, one known-failure retry and missing/unknown outcomes. No real-mail send or production schedule. Execution remains pending.

## Canonical expected-outcome traceability

`specifications.md` is the policy authority for expected outcomes. Runtime prompts
and skills may implement its rules but cannot replace them. Each Homework 1 JSONL
row must be represented in `hw1-case-spec-mapping.json` with its primary and
related canonical requirement IDs, source text hashes, trace/session reference and
review disposition.

- [x] Root `SPEC.md` identifies the canonical specification and authoring workflow.
- [x] Existing 11 Homework 1 evidence rows have an explicit case-to-spec mapping.
- [x] Historical reviews use supersession rather than rewriting observed evidence.
- [x] Part C records the actual Energy before/after traces and prompt hashes.
- [ ] Run the validator after any specification, expectation, prompt or evidence change:

```bash
python3 homework/module-1/validate_hw1_spec_evidence.py
```
