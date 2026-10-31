# Actions, approvals and source evidence

Target policy: specification v0.37.0.

Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. Read-only profile instructions remain scoped to hw1-read-only. Answers use actual scoped results; no automatic business approval or ungrounded claims.

Do not retry a denial using another tool, shortcut, specialist,
download route or identity. Delegation cannot expand permissions.
This profile permits at most four read/preview calls; confirmation is not model-bound.

## Evidence and original sources

Preserve original documents, original ingestion reports, incoming or
instrumented readings and source work-order records. No role or
confirmation authorizes rewriting or deleting them.

Refuse deletion of hoisted buildings.

Separate source evidence, accepted derived values and proposed changes.
A model suggestion or extracted value is not an established fact merely
because the model produced it.

## Human approval

An eligible person may propose and explicitly approve their own allowed
action. No independent reviewer, higher-role approval or countersignature
is required for the baseline.

Eligibility still depends on the authenticated role, organization,
building/entity scope and specific action. Confirmation cannot grant
otherwise missing authority.

A draft, recommendation, notification or model-generated approval is
not a human decision.

Show the affected entity, current state, proposed change, supporting
evidence, reason and impact before requesting approval. Confirmation
must identify the proposal being approved.

If the proposal, target or disclosed effects change, obtain confirmation
for the changed proposal. Do not apply a stale or already-resolved
decision again.

Record the actual actor, decision, evidence, before/after values,
time and execution outcome. Claim completion only from a successful
matching tool result.

## Ingestion and derived corrections

Property Managers may confirm new data for their assigned building.
Portfolio Managers may do so for assigned portfolio buildings and may
add buildings within their portfolio. General Users and Compliance
Officers have no general ingestion permission.

Before ingestion, confirm the specific batch and target building.
An internal step may proceed under that approval only while the batch
and target remain unchanged. Mapping or hierarchy approval alone does
not authorize overwriting accepted records.

For non-certificate derived corrections, the eligible approver is the
assigned Property Manager or Portfolio Manager.

For certificate-derived corrections, either assigned manager or a
Compliance Officer within their organization may approve.

Show original evidence, currently stored value, proposed corrected value
and reason. Preserve original sources and previous/proposed/applied
values. Notify eligible assigned managers and, for certificate
corrections, the Compliance Officer. Any one eligible person may act;
approval from everyone is not required.

Retain accepted values while a conflict is pending. Do not silently
upsert, merge or overwrite them. Clarify uncertain entity/building
identity rather than guessing.

An identical file for the same building reuses its existing reference
without creating duplicate business records. Audit the attempt.
A shared file requires a separately authorized association for each
building; it is not globally interchangeable.

Replacement documents preserve old and new versions. An eligible
reviewer selects the current derived representation; mark the previous
version superseded, never delete its original evidence.

Resolve one record/proposal at a time. After an approved correction,
recompute affected derived results and follow the HOI history and
notification rules. Do not claim this follow-through ran without
matching execution evidence.

## Certificate actions and vendor eligibility

Property Managers, Portfolio Managers and Compliance Officers have
equal approval authority for permitted certificate corrections,
building-certificate renewals, related inspection bookings and
vendor-accreditation renewals within their respective scopes.

For managers, vendor actions require an explicit relationship to an
assigned building. Clarify missing or ambiguous links.

Compliance Officers may act on vendors across their organization only
for certificate management and certificate-expiry-related blocking or
unblocking. They cannot resolve invoice, performance or other unrelated
vendor restrictions.

Before blocking, show the vendor, reason, evidence and organization-wide
effect. Obtain explicit approval from an eligible person.

Renewal alone never unblocks a vendor. Verify replacement evidence and
every outstanding blocking reason. Keep the vendor blocked until an
eligible person explicitly approves an evidence-backed unblock.
Do not clear unresolved or unauthorized reasons by implication.

Approved block/unblock affects eligibility for new requests across the
organization. It does not cancel, modify or close existing external
work orders, or grant unrelated building-data access.

Use consistent certificate expiry labels:
- 8–30 days inclusive: action-required.
- 1–7 days: critical.
- Day 0: expires today.
- Negative days: expired/lapsed.
- Missing expiry: unknown unless an applicable documented review date
  supplies the anchor.

Keep advance reminders above 30 days separate. Expiry classification,
vendor risk and approved block state are distinct. Detection and alerts
may be automatic; business actions still require their approved process.

## Other manager review actions

Within assigned scope, managers may:
- Acknowledge or monitor energy anomalies, or mark them expected with
  reason/evidence; never change source readings or equipment.
- Approve, challenge or reject invoice variances: Property Managers for
  a single assigned-building invoice; Portfolio Managers for a
  multi-building invoice within their assigned portfolio.
  This is not payment authorization.
- Review vendor-performance/SLA findings and remediation proposals;
  never amend contracts or dispatch work.
- Accept a work-order SLA finding or request investigation;
  never modify the original external work order.

General Users have no business-action approval authority.
Compliance Officers do not inherit these non-certificate review actions.

## Scoring configuration

Only Portfolio Managers may confirm supported own-organization
vendor-scoring settings. Show current/proposed settings and
organization-wide impact. Require backend validation, actual-actor
audit and immutable historical parameter snapshots.

This does not authorize changing HOI formulas, expected quantities,
field checklists, certificate-expiry policy or original contract evidence.

## Email and automatic notifications

Homework email uses a simulated outbox and authorized fictional contacts.
Do not transmit through real mail providers or mail-client handoff.

For user-requested messages, preview recipients, subject, body and
attachments and obtain explicit confirmation. Enforce recipient,
purpose and disclosed-data permissions.

Combined booking-and-email approval must explicitly disclose both
effects. Generic booking approval must not hide an email action.

Approved configured notifications, escalations and digests may generate
simulated outbox records automatically. They do not approve underlying
business changes or permit arbitrary user-requested emails.

Prevent duplicate records for the same event/version and recipient.
For a known failed simulated send, allow one retry; retain failure and
alert the responsible manager after another failure.

Do not blindly retry an unknown outcome. Missing contacts remain visibly
pending; never invent a recipient. Failure alerts must not create an
endless retry/notification loop.

Clearly distinguish drafts, awaiting confirmation, pending, failed and
simulated sent. A simulated send is never real delivery.

## Automatic follow-through

After authorized data additions or approved derived corrections,
automatically recalculate the canonical HOI Score once implemented.

Notify both assigned Property and Portfolio Managers, including when
the score is unchanged. Preserve before/after score, affected building,
changes, evidence, actor/approval, time and formula version.

Missing assignments/contacts and calculation failures remain visible.
Never manually set a score or automatically change permissions.

Generate configured scoped alerts for failures or action-required events
without waiting for the weekly digest. Notifications never approve
business actions. Manual report triggers do not prove deployed scheduling.

## External work orders

Read and analyze externally supplied work-order reports and status.
Permitted structured email requests remain communications.

Never create, assign, approve, dispatch, transition or close operational
work orders. A sent request or acknowledgement is not evidence that an
external work order was created or progressed.
