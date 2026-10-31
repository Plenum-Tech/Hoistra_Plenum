---
name: compliance-vocabulary
agent: compliance
title: Certificate terminology and evidence boundaries
description: Defines certificate expiry, vendor risk, approved block state, draft evidence and verification terminology. Loaded only through an explicitly enabled reviewed compliance instruction path.
---

# Compliance vocabulary

Target policy: specification v0.37.0.

Use these meanings with the core/shared policies and Compliance skill.
This document grants no permissions or tools. Its presence does not
mean it is loaded or that Compliance execution is enabled.

## 1. Certificate subject

Building certificate: evidence associated with a building or its
applicable assets, systems or inspections.

Vendor accreditation: evidence held by a vendor, such as a licence,
qualification, accreditation or insurance document.

Use the adapter's verified subject and entity relationships.
A display name or document title does not establish ownership,
building assignment or access permission.

## 2. Expiry classification

Use the same date interpretation for building and vendor certificates:

| Days to expiry | Expiry label |
|---|---|
| 8–30 inclusive | Action-required |
| 1–7 inclusive | Critical |
| 0 | Expires today |
| Negative | Expired/lapsed |
| Missing | Unknown, unless an applicable documented review date provides the anchor |

Days to expiry must come from a validated backend calculation with
its assessment date and applicable expiry/review-date anchor.

Day zero is not expired/lapsed and must not be hidden solely under
a generic critical label.

Above 30 days, preserve documented advance-reminder bands separately.
Do not describe the entire advance-reminder period as the inclusive
30-day action window.

"In date" describes the date evidence only. It does not prove
authenticity, satisfaction of every requirement or building compliance.

When a user asks whether something is "compliant", distinguish
certificate date status from verified requirements and broader
compliance. Clarify where that distinction changes the answer.

## 3. Risk and approved vendor block state

Expiry classification: what the certificate dates establish.

Vendor risk: a returned risk assessment or warning with its basis.
Risk terminology must not silently change certificate dates or state.

Blocked vendor: the backend confirms an approved organization-wide
restriction on eligibility for new requests.

A lapsed accreditation, risk badge or recommendation does not by itself
prove that a vendor block was approved or applied.

Unblocked vendor: the backend confirms a successful, evidence-backed,
explicitly approved unblock after all outstanding reasons were reviewed.

A renewed certificate does not mean the vendor is unblocked.
Keep pending approval and unresolved blocking reasons visible.

An absent block-state value means unknown, not clear.
No accreditation on file means missing evidence, not proof that the
vendor is clear, blocked or eligible.

Approved block/unblock does not modify existing external work orders.

## 4. Draft, accepted and superseded evidence

Draft: extracted or proposed information awaiting the applicable
acceptance/confirmation process. It is separate from expiry status.

Accepted: the backend confirms the applicable acceptance process.
Acceptance alone does not prove that every extracted fact is correct.

Proposed correction: a suggested change to a derived record.
Show its evidence, current value, proposed value and reason.
It is not an applied change.

Superseded: a previous representation remains retained while an
approved replacement becomes current. The original is not deleted.

Use only filter fields declared by the enabled adapter.
Do not invent a status or filter argument from these vocabulary words.

## 5. A named company — holds versus issued

Holds: vendor-scope evidence belonging to that company.

Issued: building-scope evidence the company issued or signed.

These relationships answer different questions. Include the relevant
relationship or both when the request requires it, within authorized
scope. Clarify ambiguity when necessary.

A company may have issued building certificates without held
accreditations being recorded. Do not report "no records" when
authorized evidence exists under the other relationship.

Held accreditation and returned blocking reasons inform vendor
eligibility; neither relationship permits guessing its approved state.

## 6. Document verification and authenticity

Use returned verification fields and their documented meanings.
Do not assume every document has been assessed.

| Returned evidence | How to describe it |
|---|---|
| Pass/genuine verdict | Passed the reported automated check; identify its scope |
| Review/suspect verdict | Suspect or requires review; not a confirmed forgery |
| Fail/edited verdict | Failed the reported check or editing was detected; provide the returned reasons |
| No assessment result | Not assessed or assessment unavailable, as the backend establishes |

Do not convert an automated signal into a broader verified conclusion
that the evidence does not support.

Report the returned score, scale, reasons, source and assessment time
where available. A risk score is not a probability unless its
documented definition says so.

Document authenticity, certificate expiry and vendor block state
are independent dimensions. Keep their filters and counts separate.

## 7. Coverage and HOI

Pack coverage: returned pack types represented on file against the
specified pack/version denominator.

Applicable compliance coverage: only claim this where the backend
establishes the building-specific applicable requirements.

HOI Score: the separate approved ten-group record-completeness measure.
Certificate coverage is not HOI Score, safety or overall compliance.

Never use fixed Homework document quantities as legal obligations.
Apply manager-only HOI visibility even in a compliance conversation.
