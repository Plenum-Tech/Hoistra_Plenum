---
name: contract-performance-vocabulary
agent: contract_performance
title: Words the user says, and what they actually mean here
description: ALWAYS loaded. Every line is a distinction that produces a wrong answer when collapsed.
---
## Vocabulary (critical — do not confuse these)

- **Score** vs **scorecard.** A *score* is one work order (`vendor_wo_scores`, grain: one WO).
  A *scorecard* is a vendor × month roll-up (`vendor_monthly_scorecards`). "What did they score
  on that job" and "what is their score" are different tables. Never answer one from the other.
- **A capped 60 is not an earned 60.** `capped_by_block` / `block_capped` means the components
  were overridden because the vendor is blocked. Reporting 60 without saying it was capped tells
  the reader the vendor performed adequately. Say which it is, every time.
- **Flagged is a claim; agreed is money.** A line with `match_status = flagged` is a suspicion.
  Only `adversary_agreed = true` makes `delta_gbp` a figure worth quoting to a vendor. An
  unreviewed or rejected flag is "queried", never "overcharged".
- **Three different denominators, never interchangeable:**
  - `sla_completion` — jobs closed inside the contracted completion hours.
  - `ppm_compliance_pct` — planned visits inside tolerance (`ppm_visits.within_tolerance`).
  - `matched_flagged_ratio` — invoice lines matched vs flagged.
  A vendor can be 100% on PPM and terrible on SLA. Name which one you are quoting.
- **Cost variance** (`cost_actual` vs `cost_estimated`) is a job costing more than expected.
  **Invoice discrepancy** is being billed against the wrong contracted rate. A job can overrun
  honestly and be billed correctly; a job can come in on budget and be billed at the wrong rate.
- **Criticality L1/L2/L3** is the asset. **Priority P1–P4** is the work order. They are different
  axes with different owners. An asset with no *approved* criticality scores as **L2** — it is not
  "uncategorised", it is L2 for every calculation.
- **`defaults_used`** names parameters the extractor could NOT find and filled from system
  defaults. A figure in that list is not a contract term. Never quote it as one.
- **Confirmed vs draft contract.** `confirmed_by` / `confirmed_at` empty means a human has not
  accepted the extraction. It is still a real row and still scores — say it is unconfirmed.
- **Blocked** is a compliance state, not a performance one. This agent reads `capped_by_block`;
  it does not decide or explain the block. `compliance` owns why.

## A named company

The user says "Apex", "Apex Mechanical", "Apex Mechanical Services Ltd". These are the same
company, and none of them is a `vendor_id`. Pass any of them as `vendor_name` — matching ignores
case, punctuation and Ltd/Limited, and tolerates a wrong letter. `vendor_id` takes a UUID only.

One name can still mean two companies. "Apex" matches both Apex Mechanical and Apex Lifts, and
the tool says so with `vendor_match: "ambiguous"`. Ask which is meant; a figure attributed to the
wrong company is worse than no answer.
