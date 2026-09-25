---
name: contract-performance-scoring
agent: contract_performance
title: The rules that produce the numbers
description: ALWAYS loaded. How a score is built, and every way it can mislead.
---
## How a score is built

Five components, weighted by `vendor_score_weight_config` — never assume the weights, read them
with `get_score_weights()` when the question turns on them:

`sla_response_pct` · `sla_completion_pct` · `first_fix_pct` · `recall_pct` · `accreditation_pct`

Then two overrides that are not components and behave differently:

- **Blocked caps the score at `blocked_score_cap` (60 by default)**, whatever the components
  earned. `capped_by_block` (per WO) / `block_capped` (per month) says it happened. A capped
  score is a ceiling, not a measurement: never compare a capped 60 against an earned 72 as
  though they sit on the same scale, and never average them into a portfolio figure without
  saying how many were capped.
- **L1 assets weigh an SLA miss 3× an L3 miss.** An asset with no *approved* criticality is
  treated as L2. So a vendor can miss more jobs than a rival and still score higher, if the
  rival missed the critical ones. When a score surprises the reader, this is usually why —
  say it rather than leaving them to doubt the number.

## Invoice verification

- **The rate a line is checked against** comes from the contract: an explicit `labour_hour_rate`
  wins; otherwise `labour_day_rate ÷ 8`; if neither is known there is **no rate check on that
  line** — not a zero threshold, which would flag everything. A contract priced per hour has no
  day rate to divide, and falling back to the system default is what once turned 23 correct
  lines into 23 false flags.
- **The Adversary re-computes every flag over `invoice_flag_adversary_gbp` (£500 by default)
  independently.** It must land on the same hourly figure as the matcher, or it returns
  `delta_arithmetic_mismatch` and a genuine overcharge silently never reaches the PM queue.
  A `delta_arithmetic_mismatch` is a finding about *the platform*, not about the vendor —
  report it as a check that could not be completed, never as a cleared line.
- `pm_decision` is the human's verdict on a line. A line with no decision is outstanding, not
  accepted.

## PPM

`ppm_compliance_pct` counts `ppm_visits.within_tolerance`. Tolerance is **±7 days** on the
scheduled date unless the contract sets its own. A visit outside tolerance is late even if it
happened; a visit that never happened is absent from `completed_date` — those are different
failures and a PM chases them differently.

## Cost variance

`cost_variance_alerts` fires at `cost_variance_alert_pct` over a minimum job count. It compares
`cost_actual` to `cost_estimated` — an estimate the vendor gave, not a contracted price. So a
variance alert is a conversation about estimating discipline; it is **not** evidence of
overcharging. Only invoice verification against contracted rates is that.

## Staleness — attach it to every score

`fm_report_staleness.data_as_of` would say how current the ingested reports are, but it has no
read route — it is written by `POST /fm-staleness` and never returned to you. The staleness you
CAN see is `score_month`: which month the scorecard is for.

A score is an assertion about a month, and the newest row is still the newest row long after the
scoring stopped. On 16 Sep 2026 Gough and Kelly's most recent scorecard was `2024-01-01` — twenty
months back, while three other vendors were current to `2026-08-01`. It was quoted as how that
vendor is performing.

`list_vendor_scorecards` now puts `FRESHNESS_RULE` on any vendor's newest row that is three or
more months behind. When it is there, say the month, do not write the figure in the present
tense, and say no scorecard has been produced since. When it is absent the row is recent, but the
month still belongs in the sentence.

## `defaults_used`

Lists every parameter the extractor could not find in the document and filled from system
defaults. Always surface it: a scorecard built on defaults is a weaker claim than one built on
signed terms, and the reader needs to know which they are holding.
