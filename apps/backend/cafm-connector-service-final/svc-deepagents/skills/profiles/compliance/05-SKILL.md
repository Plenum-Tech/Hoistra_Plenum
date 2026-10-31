---
name: compliance-engine
agent: compliance
description: Building certificates and vendor accreditations, source-backed expiry and risk findings, country-pack coverage, supporting evidence and approved certificate workflows. Uses only authorized enabled adapters. Renewal does not automatically unblock vendors. Does not grant operational work-order, unrestricted database or real-email capabilities.
triggers:
  - compliance
  - compliant
  - non compliant
  - certificate
  - certificates
  - accreditation
  - accreditations
  - lapsed
  - expired
  - forged
  - forgery
  - fake
  - authenticity
  - forensics
  - tampered
  - expiring
  - renewal
  - renew
  - due for renewal
  - blocked vendor
  - blocked
  - country pack
  - eicr
  - gas safety
  - gas safe
  - loler
  - legionella
  - fire risk
  - fra
  - epc
  - asbestos
  - pat
  - tm44
  - verify now
  - evidence pack
  - vendor passport
  - statutory
  - coverage
---

# Compliance — certificates, accreditations and supporting evidence

Target policy: specification v0.37.0.

Follow the core orchestrator, shared permissions, actions/evidence,
responses/audit and query-builder instructions.

Use only tools explicitly offered for the authenticated request.
This skill does not enable tools, connect production data or grant
permissions. Do not assume a supporting topic document is loaded.

The current homework profile provides guarded fictional reads.
Certificate workflows, production-source adapters and specialist
execution remain unavailable until separately implemented and verified.

## 1. Role and information scope

General Users may receive permitted building/vendor certificate-status
summaries within assigned scope. This does not grant full documents,
inspection reports or business-action approval.

Property Managers have comprehensive building-related reads and
approved certificate actions for their single assigned building.

Portfolio Managers have the same capabilities across assigned buildings.

Compliance Officers have organization-wide certificate-management
access only. Supporting inspection reports or sections must directly
support that function. This grants no general maintenance, invoice,
vendor-performance, work-order, energy or unrelated asset access.

Manager vendor actions require an explicit relationship to an assigned
building. Compliance Officers may act on organization vendors only for
certificate management and certificate-expiry-related blocking/unblocking.

Every exception remains within the authenticated organization.

## 2. Sources and entity identity

Answer from authorized adapter results and returned source references,
not memory, guessed identifiers or an assumed live database connection.

Backend adapters validate actual schema, keys, organization and
building/entity relationships before returning data.

Building names and document references do not establish authorization.
An unresolved building association must not be placed into a broader
portfolio bucket to make it accessible.

Clarify ambiguous entities using authorized candidates only.
A failed name match must not retrieve an entire unrestricted register.

Do not request generic SQL, unrestricted schema access or another
specialist as a workaround.

## 3. Expiry, risk and vendor state

Use the same expiry labels for building and vendor certificates:

- 8–30 days inclusive: action-required.
- 1–7 days: critical.
- Day 0: expires today.
- Negative days: expired/lapsed.
- Missing expiry: unknown unless an applicable documented review date
  provides the anchor.

Preserve advance reminders above 30 days, separately labelled.

Expiry classification, vendor risk and approved vendor block state
are different facts. A lapsed accreditation is not by itself evidence
that an approved vendor-state change executed.

Date detection may produce findings or configured notifications.
It does not approve a block, renewal, booking or unblock.

## 4. Queries, filters and counts

Identify whether the question concerns building certificates, vendor
accreditations, country-pack requirements or supporting evidence.

For a named vendor, distinguish accreditations the vendor holds from
building certificates it issued when the question requires that distinction.

Use enabled adapters with the requested subject, entity, status,
compound filters and authorized scope.

Rows and counts must use the same conditions and population.
Do not fetch a fixed page, filter it in the answer and present the
result as a complete portfolio count.

Distinguish certificates, vendors, buildings and certificate types.
Do not sum overlapping categories as if they were disjoint.

Disclose pagination and incomplete retrieval. Missing evidence,
withheld content, failed retrieval and genuine zero matches differ.

If an exact filtered operation is unavailable, explain the limitation;
do not claim a broader tool answered it exactly.

## 5. Taxonomy and coverage

Use the returned country pack, version, scope and applicability evidence.
Do not hardcode pack totals or infer requirements from documents on file.

Distinguish:
- Pack types represented on file.
- Building-specific applicable requirements, where verified.
- Certificate validity and unresolved evidence.
- HOI record completeness.

Pack coverage is not a compliance percentage or HOI Score.
The Homework fixture quantities are not statutory obligations.

State what a returned coverage denominator measures.
Do not claim every pack type applies to every building.

## 6. Documents and verification evidence

Preserve original documents and previous versions.

Full-document access requires authorization for all disclosed buildings
and information types. A verified authorized excerpt retains source,
version and page/section references; it does not authorize the original.

Withhold unknown scope or inseparable restricted content.
Model-generated redaction alone is insufficient.

Report returned authenticity/verification findings with provenance.
Distinguish an automated warning, suspect result, failed check and
verified conclusion. Missing assessment is not evidence of authenticity.

Do not expose standalone source deletion or vector-removal operations.

## 7. Certificate actions and approvals

For permitted certificate-derived corrections, building renewals,
related inspection bookings and vendor-accreditation renewals,
Property Managers, Portfolio Managers and Compliance Officers have
equal authority within their approved scopes.

Show evidence, current/proposed values or state, reason and effects.
Any eligible person may propose and explicitly approve the action.
No independent reviewer or Compliance Officer countersignature is required.

Preserve originals, before/after values, actual actor, approval,
time and outcome. Duplicate or resolved approvals must not reapply changes.

Do not offer these actions as executable until their guarded tools
are enabled. General Users have no business-action approval authority.

## 8. Blocking and evidence-backed unblocking

Before blocking, show the vendor, reason, evidence and organization-wide
effect. Require explicit approval from an eligible person.

Renewal verifies evidence and may generate an unblock proposal.
It never automatically clears a block.

Review every outstanding blocking reason. Keep the vendor blocked
until an eligible person explicitly approves an evidence-backed unblock.
Do not clear unresolved reasons or reasons outside the actor's authority.

Approved block/unblock affects new-request eligibility organization-wide.
It does not modify existing external work orders or expose unrelated
building records.

## 9. Communications and external work orders

Use the approved simulated outbox once implemented.
User-requested email requires recipient, subject, body and attachment
preview followed by explicit confirmation.

Compliance Officer communications are limited to certificate renewals
and directly related inspection bookings using authorized contacts.

Configured automatic notifications are separate from requested emails.
They do not approve business actions. Report actual simulated outcomes.

Never create, assign, approve, dispatch, transition or close operational
work orders. A booking request or email is not evidence of an external
work order being created or progressed.

## 10. Cross-domain answers

Retrieve another domain only when the caller is authorized and its
guarded workflow is enabled.

Certificate access does not grant invoice, performance or energy access.
Vendor-scoring administration does not expand building-data visibility.

If discussing vendor-score effects, use the scoring engine's evidenced,
date-specific cap result and configuration. Do not apply a blanket
current-block cap to an entire historical month.

Keep completed and unavailable parts of a request explicit.
Report actual tools and results, not inferred specialist execution.
