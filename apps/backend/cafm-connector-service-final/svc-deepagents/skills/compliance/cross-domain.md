---
name: compliance-cross-domain
agent: compliance
title: Combining compliance with other authorized evidence
description: Defines permission, entity, period and evidence boundaries for cross-domain requests. Does not enable specialist delegation, joins, additional tools or business actions.
---

# Cross-domain compliance requests

Target policy: specification v0.37.0.

Follow the core/shared policies and reviewed Compliance instructions
when explicitly loaded.

A relationship between records does not grant access to either record.
Every participating domain must independently satisfy authorization.

The current homework profile permits one guarded read-tool call.
Multi-domain orchestration, specialist fan-out and cross-domain
calculation remain unavailable unless separately enabled and verified.

## 1. Check each part of the request

Identify the requested compliance facts and each additional domain:
external work orders, assets, documents, contracts, vendor performance,
invoices, energy, HOI or usage.

Check the authenticated role, organization, building/entity scope and
information type for every part before retrieval.

Managers' comprehensive reads remain within assigned buildings.
General Users retain only their specifically permitted summaries.

Compliance Officers have organization-wide certificate-management
access, not general work-order, invoice, performance, energy or
unrelated asset access.

Certificate-related inspection evidence may be disclosed to a
Compliance Officer only within the approved evidence boundaries.

Do not use a specialist or indirect query to obtain information that
the caller could not access directly.

## 2. Preserve narrow organization-wide exceptions

Approved vendor block/unblock may affect new-request eligibility
organization-wide. It does not expose unrelated building information.

Portfolio Manager vendor-scoring configuration authority is also
organization-wide, but does not grant access to other portfolios'
restricted source records or HOI results.

Neither exception crosses organization boundaries.

## 3. Use verified entity relationships

Use backend-validated building, vendor, asset, work-order and document
relationships. A matching name or similarly formatted identifier
does not establish a join.

The backend adapter owns schema/key validation and source mapping.
Do not guess UUID/string conversions, infer a building association
or broaden an unresolved record into the portfolio.

A shared vendor or document requires independently authorized
associations. Do not expose another building's evidence through
a shared entity.

Only an enabled verified backend workflow may combine sources or
calculate cross-domain aggregates. Do not perform a mental join and
present it as a verified result.

## 4. Common request patterns

| Request | Evidence required | Boundary |
|---|---|---|
| Can this vendor receive a new request? | Relevant accreditation evidence and verified approved block state | Do not infer eligibility solely from a current certificate; do not assign or dispatch work |
| Which buildings have lapsed fire certificates and urgent open work orders? | Matching certificate and external work-order evidence joined by validated building/entity relationships | Managers within scope only; exact backend conditions/counts; no Officer access to general WO records |
| Does this vendor score reflect accreditation lapse? | Dated accreditation/block evidence and verified scoring contributions/configuration | No blanket current-block cap or retrospective cap of unaffected contributions |
| What does the certificate document say? | Authorized original document or verified excerpt with provenance | Full-content authorization; excerpt access does not grant original download |
| Does missing certificate data affect HOI? | Accepted certificate evidence and canonical ten-group calculation | Only assigned Property/Portfolio Managers may receive HOI results |

These examples describe required behavior, not enabled tool contracts.
Do not invent tool names, fields or extra calls to satisfy them.

## 5. Dates, populations and scoring

Preserve each source's reporting period, assessment date, freshness,
scope, filters and completeness.

Do not compare a current vendor state with historical work and assume
the same state applied throughout the reporting period.

For vendor scoring, use the scoring engine's evidenced date-specific
cap result and parameter version. The starting cap is a configurable
default, not a universal rule for every historical score.

Keep invoice verification diagnostic separate from the numerical
vendor performance score.

Keep HOI record completeness separate from certificate validity,
vendor performance, risk and overall compliance.

Missing evidence or a failed join is not zero and does not prove
the absence of work, certificates or risk.

## 6. Documents and mixed evidence

Apply full-content scope checks before returning documents, chunks,
attachments or links.

A verified excerpt may contain only permitted information with
source/version and page/section references.

Do not use model-generated redaction as the authorization boundary.
Withhold content when safe separation or scope cannot be verified.

A Compliance Officer's certificate evidence access does not authorize
the unrelated commercial or maintenance sections of a mixed report.

## 7. Actions and communications

A finding or cross-domain recommendation is not approval to change data,
block/unblock a vendor, renew a certificate or send an email.

Use separately enabled action-specific approval and simulated-outbox
workflows. Confirmation does not expand permissions.

No cross-domain flow creates, assigns, approves, dispatches, transitions
or closes operational work orders.

A structured email request remains a communication to the separate
system, not evidence that an external work order exists or progressed.

## 8. Partial completion and actual execution

Preserve every requested part. Identify which parts were completed,
forbidden, unavailable or failed.

Do not answer one part as though the combined question was completed.
Do not infer an unqueried domain's result from another domain.

If the current profile cannot execute a combined query, explain that
limitation. Do not silently exceed the one-call limit.

Keep intent, selected route/instructions and actual executed tools
separate. A mentioned specialist or join is not an executed operation.

Report only matching backend results and verified calculation evidence.
If no tool executed, the executed-tool list is empty.
