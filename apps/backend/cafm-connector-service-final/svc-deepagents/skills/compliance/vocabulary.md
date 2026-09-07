---
name: compliance-vocabulary
agent: compliance
title: What the status words mean
description: ALWAYS loaded. Any question using compliant, lapsed, expired, blocked, at-risk, draft or forged.
---
## Vocabulary (critical — do not confuse these)
- **Compliant** = lifecycle status `Current` (in-date). NOT a status filter value named "compliant".
- **Non-compliant** = lifecycle status `Lapsed` (past expiry). "expired" means the same as "lapsed".
- **At-risk** = Expiring Soon / Due for Renewal / Overdue / Critical. NOT status="at_risk".
- **Blocked** = a vendor RISK BADGE (lapsed accreditation OR vendors.block_state=Blocked).
  Never pass status="Blocked" — that returns 0. Use list_vendor_accreditations(risk_filter="blocked").
- **Draft** = metadata flag only. Never status="draft"; use draft=true when listing unconfirmed extracts.
- Building cert = CountryPack scope Building (PM statutory duty).
- Vendor accreditation = CountryPack scope Vendor (must hold to perform regulated work).

## A named company — holds vs issued

"Certificates belonging to / for / of <company>" covers two different rows. **Holds**: vendor-scope
rows where `vendor_name` is the company — its own accreditations, licences and insurance. **Issued**:
building-scope rows where `vendor_name` is the company — certificates it signed for a building. A
contractor can be on a building's EICR and hold nothing of its own; that is a finding, not "no
records". Blocking is decided only from what a company holds, so a company with no held rows has
no block state — say "no accreditation on file", never "clear".

## Document authenticity — forged, fake, suspect

Every certificate row carries the result of the document forensics run on its PDF:

| Word the user says | Where it lives on the row | Value |
|---|---|---|
| forged, forgery, fake, tampered, edited, doctored, high risk | `forensics_verdict` / `forensics_ccc_verdict` | `fail` / `edited` |
| suspect, suspicious, questionable, needs review, under review | `forensics_verdict` / `forensics_ccc_verdict` | `review` / `suspect` |
| genuine, authentic, clean, passed forensics | `forensics_verdict` / `forensics_ccc_verdict` | `pass` / `genuine` |
| not assessed, never scanned | both verdict fields empty | — |

`forensics_risk_score` is 0–100, higher = riskier; `authenticity_warning` holds the reasons in plain words (incremental PDF edits, SAMPLE/DRAFT wording, encrypted file, missing dates, vendor not found on-platform). When you name a forged or suspect document, quote its score and the first reason — that is what makes the claim checkable.

A row with no verdict has not been assessed. It is not genuine and it is not forged; say "not yet assessed" and count it separately. "Forged" means `fail`/`edited`; do not fold `review`/`suspect` rows into the forged list unless the question asks for suspicious documents as well — list them under their own heading instead.

Building-level vs vendor-level is `cert_scope` — Building rows are the premises' certificates, Vendor rows are contractor accreditations. "Separately" means two lists, one per scope, each grouped by owner.
