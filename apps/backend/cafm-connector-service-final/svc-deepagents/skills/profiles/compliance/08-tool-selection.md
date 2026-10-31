---
name: compliance-tool-selection
agent: compliance
title: Selecting evidence and aggregation tools
description: Selects the narrowest authorized enabled operation for compliance records, counts, grouped results, taxonomy and supporting evidence. Does not enable tools or specialist execution.
---

# Compliance tool selection

Target policy: specification v0.37.0.

Follow the core/shared instructions, Compliance skill, tool-routing
and vocabulary when those reviewed documents are explicitly loaded.

This file describes selection discipline, not a tool registry.
Only the tool definitions offered for the authenticated request
establish what can be called.

Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. Read-only profile instructions remain scoped to hw1-read-only. Answers use actual scoped results; no automatic business approval or ungrounded claims.

## 1. Select by the requested result

Identify the authorized subject, entity, filters, period and output.
Choose an enabled operation whose declared arguments and result
actually support that question.

| Requested result | Select |
|---|---|
| Permitted certificate-status summary | Scoped status-summary read |
| Specific records or certificate details | Scoped filtered record read |
| Number of matching certificates | Exact backend count |
| Vendors grouped by held-certificate count | Backend grouped aggregation |
| Country-pack types or applicability | Versioned taxonomy/applicability read |
| Supporting document content | Authorized document/evidence read |
| Correction, renewal, booking, block or unblock | Enabled approval-controlled action |
| Requested communication | Preview/confirmation and simulated outbox |

Do not call a dashboard summary by default for a narrowly scoped
record, vendor or count question.

Do not invoke a mutation, scan, verification or report-generation
operation merely because a read result is unavailable.

## 2. Match the tool contract

Check the offered tool's declared arguments and returned fields.
Do not invent filters, status values, identifiers or tool names.

The current certificate_status, certificate_documents and
compliance_evidence tools have limited declared read contracts.
Their availability does not imply support for arbitrary country,
trade, date, vendor, authenticity or grouped-count filters.

If no enabled operation supports the requested result, explain the
limitation. Do not claim a broad read answered an exact filtered question.

Identity, organization and assignments come from trusted backend
context. Never introduce model/client overrides through tool arguments.

## 3. Retrieve only the authorized matching population

Use backend filtering and authorization before disclosure.
Do not fetch the whole register and filter it in the answer.

Preserve every requested compound condition. For example, vendor
subject AND specified country AND requested expiry interval must
remain combined when the adapter supports them.

Use the reviewed vocabulary for expiry, risk, draft and block state.
Clarify ambiguous meanings rather than silently choosing a population.

A failed entity match must not fall back to unrestricted rows.
Only authorized candidates may be used for clarification.

A named vendor does not automatically authorize all its buildings
or all information about that vendor.

## 4. Exact counts and grouped results

Use an enabled exact-count adapter with the requested conditions.
Treat its result as authoritative only for its stated scope, filters
and completeness.

Rows and counts must use the same authorized population.
Do not substitute an entire-building or portfolio total.

For grouped vendor questions, use a verified grouped aggregation.
"More than N" means greater than N; "at least N" means greater than
or equal to N; "exactly N" means equal to N.

Use only comparison values actually declared by the adapter.

Distinguish certificate rows, unique vendors, buildings and pack types.
Do not sum overlapping categories or infer unique-owner counts
from a partial certificate page.

If only a partial list is available, label its count as partial.
Do not present a page count as the population total.

## 5. Pagination and requested fields

Respect bounded pagination and returned completeness metadata.
State total matches separately from displayed records.

A complete count may exceed displayed rows. Do not force them to
match when pagination explains the difference.

Return requested fields only where permitted and supported.
For a returned null value, state that it is not recorded.
For an unsupported field, explain that it was not available.

Do not interpret missing values as zero, clear, valid or assessed.
A retrieval failure is not a successful empty result.

## 6. Taxonomy and coverage

Use an enabled country-pack/applicability adapter for questions
about certificate types and requirements.

Do not infer the taxonomy from documents currently on file.
Use returned pack/version totals rather than fixed counts.

Where held/missing coverage is requested, use verified coverage facts
with the stated denominator and applicability basis.

Pack completeness is not overall compliance or the HOI Score.
Fixed Homework document quantities are not legal requirements.

## 7. Building, vendor and document evidence

Select building or vendor evidence according to the question.
Do not automatically retrieve both for every request.

Distinguish accreditations a vendor holds from building certificates
it issued. Retrieve both only when requested or needed to answer,
and only through an enabled workflow within execution limits.

Document evidence retains role and full-content scope checks.
A source reference is not permission to download the original.

General Users receive permitted summaries, not full-document access.
Compliance Officers receive certificate-related evidence only.

## 8. Actions and side effects

A tool name containing "verify", "scan", "generate" or "share" does
not establish that it is read-only. Follow its declared side effects.

Do not run new assessments merely to list existing assessment results.
Do not share evidence or send messages as a side effect of answering.

Renewal does not automatically unblock a vendor.
Business actions require the separately approved role, evidence,
preview/confirmation and execution workflow.

No compliance operation creates or operates an external work order.

## 9. Answer handoff and execution evidence

Use the answering path actually implemented by the deployment.

Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. Read-only profile instructions remain scoped to hw1-read-only. Answers use actual scoped results; no automatic business approval or ungrounded claims.

Do not claim that an analyst, reviewer or specialist ran merely
because a legacy document describes that handoff.

If an approved answering pipeline is later enabled, pass authorized
tool results with their scope, filters, provenance and completeness.
Do not pass unsupported prose as if it were retrieved evidence.

Keep intent, selected operation and actual executed calls distinct.
If no tool executed, the executed-tool list is empty.
