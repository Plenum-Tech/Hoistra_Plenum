---
name: contract-performance-domain-knowledge
agent: contract_performance
title: How to rank, and what a number means to the person reading it
description: ALWAYS loaded. Used whenever the question says worst, best, risk, priority or urgent.
---
## Ranking — "worst vendor" is ambiguous until you choose

Four different orderings are defensible, and they disagree. Choose the one the question asks
for, and **say which you ranked on** in the answer:

1. **Consequence** — failures on L1 assets, and SLA completion misses on statutory or
   life-safety work. Default when the question says *risk*, *worst*, *urgent* or *priority*.
2. **Money** — Adversary-agreed `delta_gbp`, then `cost_variance_alerts.total_delta`. Use when
   the question says *cost*, *overcharge*, *recover* or *exposure*.
3. **Score** — `overall_score`, capped scores flagged as capped. Use when the question names the
   score, a threshold ("below 80"), or a ranking of performance as such.
4. **Trend** — `trend_delta`. Use when the question is about *getting worse*, *slipping* or
   *improving*. A vendor at 91 falling 8 points is a different conversation from one steady at 72.

Never mix two orderings in one list. A number of vendors below 80 is not the same set as the
vendors who cost the most, and presenting one as the other is the mistake that makes a
performance review indefensible.

## What each number means to a property manager

- **A low score** is a conversation, not a breach. It becomes a breach only against a term in
  `contract_sla_parameters`, and only when the term was confirmed.
- **A capped score** says nothing about the work. It says the vendor is blocked on compliance
  and should not have been instructed at all. That is the more urgent finding, and it belongs
  to `compliance` to explain.
- **A flagged invoice line** is money not yet lost — the invoice is usually unpaid. Timeliness
  matters more here than size: a £200 flag on an invoice due Friday outranks a £2,000 flag on
  one already settled.
- **A PPM visit outside tolerance** may carry warranty or statutory consequence far beyond its
  cost. Late planned maintenance on an L1 asset is not a scheduling nuisance.
- **A recall** (`recall`) means the vendor went back to the same job. It costs the client twice
  and signals the first fix was not a fix. Weigh it above a single slow response.

## First fix and recall together

`first_fix` and `recall` read the same underlying failure from two ends. A vendor with high
first-fix and high recall is not contradictory — it means the jobs they close on one visit are
easy and the hard ones come back. Say that rather than reporting the two figures side by side
and leaving the reader to reconcile them.

## Honesty about small samples

A monthly scorecard built on two work orders is not a performance measurement. When the count
behind a score is small, lead with the count. A vendor "scoring 45" on one missed job should
never be ranked against one scoring 78 on forty jobs without saying so.
