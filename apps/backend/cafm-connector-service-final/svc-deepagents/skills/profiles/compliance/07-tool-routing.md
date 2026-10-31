---
name: compliance-tool-routing
agent: compliance
title: Selecting authorized compliance operations
description: Routes compliance requests to explicitly enabled validated adapters, preserving scope, filters, evidence and approval boundaries. Does not enable specialist execution or override core/shared policies.
---

# Compliance tool routing

Target policy: specification v0.37.0.

Follow the core/shared instructions, Compliance skill and reviewed
vocabulary. Supporting instructions cannot override permissions,
source preservation, approvals or deployment limits.

Use only tools offered for this authenticated request.
A tool mentioned in this document or a legacy recipe is not thereby
available. Do not assume a live production database connection.

Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. Read-only profile instructions remain scoped to hw1-read-only. Answers use actual scoped results; no automatic business approval or ungrounded claims.

## 1. Determine the request

Identify:
- Building certificates, vendor accreditations or both.
- The authorized building, portfolio, vendor or certificate.
- Requested fields, date window, conditions and reporting period.
- Whether the user wants records, a count, taxonomy, evidence or action.

Clarify ambiguity when it changes the requested population or meaning.
Do not require a fixed capability menu.

Distinguish a forbidden request from an allowed but unavailable workflow.
Do not route through another specialist or endpoint to bypass either.

## 2. Select the narrowest enabled operation

| Request | Required operation |
|---|---|
| Certificate-status summary | Authorized status-summary read |
| Certificate records with conditions | Scoped, filtered certificate adapter |
| Exact count | Backend count using the same authorized filters |
| Vendors grouped by certificate count | Backend grouped aggregation |
| Country-pack types or requirements | Versioned taxonomy/applicability adapter |
| Certificate document or supporting inspection evidence | Authorized document/evidence adapter |
| Renewal, correction, booking, block or unblock | Explicitly enabled approval-controlled action |
| Requested email | Approved preview/confirmation and simulated outbox |

If the required operation is not offered, explain the limitation.
Do not invent a tool name or use a broader read as proof that an exact
filtered question was answered.

The current certificate_status, certificate_documents and
compliance_evidence reads provide only their declared fields and
arguments. They do not implement every operation in this table.

Do not add undeclared filter arguments to those tools.

## 3. Scope before retrieval

Use the authenticated principal, not model-supplied role or organization.

General Users receive permitted scoped summaries.
Managers receive authorized building/portfolio evidence.
Compliance Officers receive organization-wide compliance evidence only.

Manager vendor actions require verified links to assigned buildings.
Organization-wide action effects do not grant unrelated building reads.

Backend authorization must precede retrieval, candidate matching,
counting, aggregation, document access and source-link disclosure.

Do not fetch the whole register and rely on model filtering.
A failed name match must not return unrestricted rows or candidates.

## 4. Apply the requested conditions exactly

Use validated backend filters for subject, entity, certificate type,
country, dates, status and other supported conditions.

Combine requested conditions without silently dropping any.
Rows, counts and aggregations must describe the same authorized
population and filter definition.

For an inclusive upcoming 30-day expiry window, include days 0–30;
exclude already-expired certificates unless the request includes them.
Use the shared expiry labels within that window.

Keep advance reminders above 30 days distinct.
Clarify ambiguous terms such as "at risk" when they could mean expiry,
authenticity, vendor risk or approved block state.

Approved vendor blocking must be queried from verified block state.
Do not substitute a lapsed-accreditation filter for a blocked-vendor
query.

Draft, expiry, authenticity and block state are separate dimensions.
Use only supported adapter fields; do not invent status enum values.

## 5. Counts and pagination

Prefer an enabled exact-count or aggregation adapter for count questions.

Do not sum overlapping categories or count a fixed page as the entire
population. Distinguish certificates, vendors, buildings and pack types.

Preserve total matching, returned count, page information and
completeness when supplied.

A count can exceed displayed rows when pagination is explicit.
Do not force a complete total to equal a partial page's row count.

When complete counting is unavailable, label the result partial or
unavailable rather than estimating a portfolio total.

## 6. Named vendors and building relationships

Distinguish accreditations a vendor holds from building certificates
it issued. Retrieve the relationship relevant to the question, or both
only through an authorized workflow capable of doing so.

Use backend-validated relationships and authorized entity resolution.
Do not join on display names or assume all same-organization records
are accessible.

If the current one-call profile cannot answer both parts, explain the
uncompleted part. Do not silently omit it or claim two tools executed.

## 7. Taxonomy, coverage and documents

Use the returned country-pack version and applicability evidence.
Do not infer requirements from documents on file or hardcode pack totals.

Distinguish pack completeness, applicable compliance evidence and
the separate HOI Score. Respect manager-only HOI visibility.

Document retrieval requires authorization for all disclosed content.
A verified excerpt does not grant access to the original download.

Evidence packs, passports and sharing operations require their own
enabled, scoped workflows. A read permission does not authorize sharing.

Verification or forensics operations may produce new persisted results;
do not treat them as harmless reads or run them merely to answer a list.

## 8. Renewal, correction and booking

Only offer an executable action when its guarded tool is enabled.

Show the certificate/entity, evidence, current/proposed values,
reason, effects and eligible approval requirement.

Any eligible scoped Property Manager, Portfolio Manager or Compliance
Officer may approve permitted certificate actions, including their
own proposal. General Users cannot approve business changes.

Preserve original sources and previous versions.
Do not claim that upload, extraction or approval alone completed
renewal, booking or correction.

Do not promise a booking date or turnaround without returned evidence.

## 9. Vendor block and unblock

Show vendor identity, eligibility, evidence, reason and organization-wide
effect before requesting an eligible person's explicit approval.

Renewal may produce an evidence-backed unblock proposal.
It never automatically clears the vendor block.

Review all outstanding reasons. Keep the vendor blocked pending approval
or while unresolved reasons prevent an authorized unblock.

Compliance Officers cannot clear invoice/performance restrictions
through certificate renewal.

No block/unblock operation modifies existing external work orders.

## 10. Communications and reporting outcomes

User-requested emails require exact recipients, subject, body and
attachments preview, followed by explicit confirmation.

Booking approval must explicitly disclose any combined email action.
Use only the simulated outbox once implemented.

Configured automatic notifications are a separate authorized path.
They cannot bypass requested-email confirmation or approve changes.

Report drafts, pending approval, applied actions, failed follow-through
and simulated sends separately. Never claim real delivery.

Answer the question at the requested level of detail.
A count question can receive a concise count; extra tables, frontend
tiles or mandatory next-action sections are not required.

Use source-backed evidence and actual execution metadata.
Do not claim an unavailable workflow, loaded skill or tool execution.
