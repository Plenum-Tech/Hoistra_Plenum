---
name: compliance-renewals
agent: compliance
title: Certificate renewal and evidence-backed follow-through
description: Guides authorized certificate renewal, related inspection booking, replacement evidence and vendor reinstatement proposals. Requires enabled guarded workflows; renewal never automatically unblocks a vendor.
---

# Certificate renewals and related actions

Target policy: specification v0.37.0.

Follow the core/shared policies, Compliance skill, vocabulary,
tool-routing and tool-selection when explicitly loaded.

Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. Read-only profile instructions remain scoped to hw1-read-only. Answers use actual scoped results; no automatic business approval or ungrounded claims.

## 1. Establish the evidence and urgency

Use authorized source-backed certificate identity, holder, building
or vendor relationship, expiry/review-date anchor and assessment date.

Use the approved expiry labels:
- 8–30 days inclusive: action-required.
- 1–7 days: critical.
- Day 0: expires today.
- Negative days: expired/lapsed.
- Missing date: unknown unless an applicable documented review date
  provides the anchor.

Keep advance reminders above 30 days separate.
Do not invent an expiry date, renewal requirement, contact, booking
slot or turnaround time.

Distinguish date status, verification findings, vendor risk and
approved vendor block state.

Answer at the requested level of detail. Suggest next steps when
useful; do not force a detailed action plan onto a simple count question.

## 2. Check action eligibility

Property Managers may approve permitted certificate actions for their
assigned building.

Portfolio Managers have the same authority across assigned buildings.

Compliance Officers may approve certificate-management actions
throughout their organization, including directly related inspection
bookings and certificate-expiry-related vendor actions.

Managers require an explicit vendor relationship to assigned buildings.
Compliance Officers cannot use certificate renewal to resolve invoice,
performance or other unrelated vendor restrictions.

All three roles have equal authority within those boundaries.
An eligible proposer may explicitly approve their own proposal.
No independent reviewer or countersignature is required.

General Users cannot approve renewal, booking, correction or vendor
state changes. Their separately permitted team emails do not confer
business-action authority.

## 3. Prepare a renewal or inspection-booking proposal

Only offer an executable workflow when its guarded tool is enabled.

Show:
- The certificate, building/vendor and relevant source evidence.
- Current status and reason for the proposed action.
- Proposed renewal or booking details supported by available evidence.
- Any affected derived values or state.
- Any communication included in the approval.
- The action's scope and consequences.

Obtain the eligible person's explicit approval of that proposal.
If the target, details or effects change, obtain confirmation for
the changed proposal.

A proposed booking is not a confirmed appointment.
A renewal request is not a renewed certificate.
Do not promise a five-business-day booking or another service level
without supporting returned evidence.

Homework approval and review occur through authenticated backend/API
workflows; Saved Space or other frontend completion is not required.

## 4. Requested renewal and booking communications

Use authorized fictional contacts and the simulated outbox once enabled.

Preview exact recipients, subject, body and attachments before a
user-requested message is confirmed.

For Compliance Officers, messages must concern certificate renewal
or directly related inspection booking using permitted information.

Combined booking-and-email approval must explicitly disclose both
actions. Generic approval must not hide a message send.

Do not transmit real email, hand off to a mail client or claim delivery.
Report drafted, awaiting confirmation, pending, failed or simulated
sent according to the actual result.

A simulated request email does not prove booking, renewal or external
work-order creation.

## 5. Receive and review replacement evidence

Preserve original documents and earlier versions.
New uploads or extraction outputs are not automatically accepted facts.

Use the approved ingestion, duplicate, conflict and correction rules.
Do not overwrite accepted records through a renewal/upsert shortcut.

Show discrepancies and retain accepted values while a correction
or replacement decision is pending.

An eligible reviewer confirms which derived representation becomes
current. Previous versions remain retained and superseded.

Recalculate date status from verified accepted evidence.
Do not claim the renewed certificate is current merely because
a file was uploaded.

Report verification failures or unavailable assessment explicitly.
An automated pass is not proof of facts beyond the check's scope.

## 6. Vendor block and evidence-backed unblock

Certificate expiry or a scan may generate a finding, notification
or proposed vendor action. It does not itself approve a state change.

Before blocking, show vendor identity, eligibility, reason, evidence
and organization-wide effect. Require eligible explicit approval.

Renewed accreditation may generate an unblock proposal.
It never automatically clears a vendor block.

Check every outstanding blocking reason, including reasons unrelated
to the renewed certificate.

Keep the vendor blocked until an eligible person explicitly approves
an evidence-backed unblock and the backend confirms execution.

Do not clear unresolved reasons or reasons outside the actor's
functional authority.

Approved block/unblock affects new-request eligibility throughout
the organization. It does not modify existing external work orders
or expand access to other buildings.

## 7. Remediation findings

Explain source-backed defects, failed checks and unresolved evidence.
A finding may support a permitted correction, renewal/booking proposal
or communication request.

Do not mark remedial work completed merely because a request was sent,
a replacement document was uploaded or a certificate date changed.

Do not imply general maintenance-management authority.
Any supported derived certificate/remediation-record correction
requires the specific role, evidence and approval process.

No renewal or remediation flow creates, assigns, approves, dispatches,
transitions or closes operational work orders.

## 8. Follow-through, notifications and audit

Approval, execution, recalculation and notifications are separate
outcomes. Do not claim all succeeded from one successful response.

After authorized data changes, follow the approved derived-result
and HOI recalculation rules once implemented.

Notify both assigned managers of HOI recalculation, even when unchanged.
Preserve source references, before/after values, actor/approval,
timestamp, formula version and notification outcomes.

Configured automatic renewal alerts are distinct from requested emails
and business-action approval.

Prevent duplicate application of resolved approvals and duplicate
notifications for the same event/version and recipient.

For a known failed simulated send, allow one retry.
For unknown outcomes, do not retry blindly.
Missing contacts remain visibly pending; never invent a recipient.

Preserve failure and audit information. Do not describe a pending
proposal, failed action or unavailable workflow as completed.
