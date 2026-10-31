# Isolated Homework backend runbook

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


Verified checkpoint: all seven readiness checks passed on2026-10-26; full isolated
backend active18008, candidate18009, 503 tests and65 subtests passed. Exact deployment
and rollback image IDs are recorded in the authoritative receipt below.

The authoritative completion receipt is `milestones/evidence/07-readiness.json`.
Until its seven checks pass, this document is an operational runbook, not a readiness
claim. The gate freshly executes gates01–06, maps canonical requirements, runs the
complete service suite and candidate role/domain matrix, then prepares private exact
rollback state, promotes the verified image and tests active status/provider behavior.
It automatically restores the saved isolated image if post-promotion checks fail.

Use `/root/Hoistra_Plenum`. Private sessions/configuration stay in ignored `runtime/`;
never print tokens, keys, .env or private rollback files. The app uses OpenRouter;
Codex authentication is independent. Services use non-owner RLS app roles. Setup
scripts alone append fictional fixtures to the isolated database. No real mail or
external appointment/work-order operation exists.

## Verify and activate

```bash
python3 homework/module-1/milestones/gates/07-readiness.py
```

This rebuilds only candidate18009, preserves previous receipts privately, reprovisions
missing fictional fixtures without overwriting sources, executes every prior gate and
all service tests, then promotes only `hoistra-hw1` orchestrator18008 through:

```bash
docker compose --env-file homework/module-1/runtime/.env -p hoistra-hw1 -f homework/module-1/docker-compose.hw1.yml -f homework/module-1/docker-compose.hw1-active.yml up -d --no-build --no-deps --pull never orchestrator
```

Use that direct promotion command only with the image tag pinned by a successful
readiness run; the gate performs all checks and rollback preparation first. The
original dependency/base image remains available; candidate is retained on18009.
Unrelated containers/projects are not recreated. Do not delete the database/volume.

## First question and status

```bash
python3 homework/module-1/hw1-cli.py status
python3 homework/module-1/hw1-cli.py --user hw1-property ask 'Calculate Energy EUI for building_id hw1-building-a1, meter_id hw1-energy-m2-condition, period_start 2026-10-01, period_end 2026-10-31.'
python3 homework/module-1/hw1-cli.py --user hw1-general tools communications
python3 homework/module-1/hw1-cli.py --user hw1-compliance ask 'Show Building certificates for hw1-building-a1 expiring in 0 through 30 days inclusive.'
```

The active default is18008. Add `--url http://127.0.0.1:18009` before the command for
candidate. Continue with `ask 'QUESTION' --conversation CONVERSATION_ID` under the
same authenticated user. A changed assignment scope requires a new conversation.

## Manual reports and owned traces

```bash
python3 homework/module-1/hw1-cli.py --user hw1-property report weekly 2026-10-26 --building hw1-building-a1
python3 homework/module-1/hw1-cli.py --user hw1-portfolio report monthly 2026-10-01
python3 homework/module-1/hw1-cli.py --user hw1-property traces --conversation CONVERSATION_ID
```

Periods are Asia/Dubai half-open windows; generation/as-of time is explicit. Configured
scoped digests need no per-message approval. Observed metrics do not imply whole-platform
adoption. Unknown pricing/charges/quotas/coverage trends remain unavailable. Reports retain
immutable generation-time delivery snapshots; current recovery state is in scoped outbox.

## Requested email and human approval

Create a private JSON file with typed `building_id`, `entity_id`, `purpose`, `contact_ids`,
`subject`, `body`, `source_ids` (optional supported work-request `vendor_id`). Use fictional
configured contacts and permitted content only. Then:

```bash
umask 077
python3 homework/module-1/hw1-cli.py --user hw1-property email-preview /PRIVATE/REQUEST.json > /PRIVATE/PREVIEW.json
python3 homework/module-1/hw1-cli.py --user hw1-property email-confirm /PRIVATE/PREVIEW.json
```

The confirmation command fetches the server-retained exact preview and asks for literal
APPROVE or REJECT. It cannot substitute changed local text, another user's preview or a
stale recipient/source/target. General Users can confirm only permitted team emails;
Officers certificate-related messages only. The actual result is simulated, never delivered.
Known failure gets one retry, unknown no blind retry, missing address stays pending.

Other human decisions remain explicit:

```bash
python3 homework/module-1/hw1-cli.py --user hw1-property certificate-confirm PROPOSAL_ID
python3 homework/module-1/hw1-cli.py --user hw1-property source-confirm PROPOSAL_ID REVIEWED_PREVIEW_HASH 'Reason for accepting the reviewed immutable source'
```

Review the source preview before the latter command. Certificate decisions display the
server-retained preview. Model planning never invokes those human decision routes.

## Rollback

Private `runtime/readiness/activation-state.json` identifies the original image tag,
container/environment snapshot and exact private Compose override. These files are
mode0600 inside mode0700 folders. Do not paste them into chat/logs. The rollback image
is pinned and validated before activation. Restore only the isolated orchestrator:

```bash
python3 homework/module-1/rollback-hw1.py
```

Rollback changes the isolated image/environment, never deletes or rewinds source/history/
audit evidence. The database changes are additive; no unrelated service is affected.
After a manual rollback, the full-baseline active status is no longer claimed; rerun
readiness to verify/promote again. A failed post-promotion gate performs this rollback
itself and writes a failing receipt, rather than claiming readiness.

## Boundaries

Fictional backend/API acceptance only: structured inputs rather than production OCR,
no real mail/renewal/appointment/register attestation, no production identity or external
connector activation, no frontend, calendar scheduler, unrestricted SQL/schema, source
removal, operational WO lifecycle, role/billing/quota administration or manual HOI edit.
Only ten-group fixed HOI is labelled HOI Score; managers alone see it. Course reference
materials are mapped read-only; no video, external publication or Cartwheel modification
is invented. Detailed canonical status/limits are in the readiness requirement register
and UNATTENDED-REPORT.md; old receipts preserve their original checkpoint meanings.

## Retained-evidence resource bounds

The isolated Postgres service is capped at256MiB with shared_buffers32MB, work_mem2MB,
maintenance_work_mem32MB and max_connections40. Expiry draining commits one certificate
version/stage event per transaction, so lock retention stays bounded as evidence grows.
Outbox authorization/count/page predicates run in SQL on the same transaction snapshot;
all original messages/history remain retained. These corrections replaced the earlier
160MiB/cache/organization-wide-lock bottlenecks; no volume reset or evidence deletion
is a permitted workaround. Active and candidate app caps remain448MiB each.

## Bounded outbox continuation

POST `/api/homework/communications/tools/execute` with tool `communications_outbox`
and arguments `building_id`, `limit` (at most100). The initial page includes
`total_matching` and optional `notification_issues`. Continue with the returned
`next_cursor` as `after_id`, offset0, and `include_issues:false` after retaining the
first issues page. Cursor pages report `total_matching:null` with an explicit not-
recounted state. A null `next_cursor` ends traversal; every page retains the same
role/content/building predicates. Cursor and nonzero offset together are rejected.
Concurrent additions may change the population; clients must not claim a stable
complete snapshot without checking their initial count and observed unique IDs.
The verification gates enforce those checks against the isolated test population.

## Canonical expected-outcome validation

Expected outcomes are governed by `specifications.md`, not inferred solely from a
runtime prompt or skill. Before committing specification, prompt, expected-outcome
or Homework 1 evidence changes, run:

```bash
cd /root/Hoistra_Plenum
python3 homework/module-1/validate_hw1_spec_evidence.py
```

The command verifies the canonical specification hash/version, readiness requirement
index, valid requirement IDs, complete JSONL-to-case mapping, trace/session references
and historical supersession. It does not call a model or expose private runtime data.
