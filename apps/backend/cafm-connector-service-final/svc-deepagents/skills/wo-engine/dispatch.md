---
name: dispatch
description: Assigning a work order — vendor selection, the accreditation gate that blocks dispatch, SLA response targets and breaches, approvals, and what the activity-log entry for one order should say.
---

# Dispatch — who it goes to, and what stops it

A work order becomes work when a vendor is assigned and the dispatch is not blocked. Three
things decide that, and each has its own record.

---

## 1. The accreditation gate

A vendor whose accreditation for the asset type is not current **cannot be dispatched to it**.
This is the reason a work order sits at `blocked` rather than `Open`, and it is the single most
useful thing to say about such an order — "blocked" alone tells nobody what to do, while
"blocked because the assigned vendor's accreditation for this asset type has lapsed" names the
fix and the person who can make it.

The accreditation itself lives in compliance, not here: `compliance_certificates` with
`cert_scope = 'Vendor'`, and `vendors.block_state` carrying `Blocked` with `block_reason` and
`blocked_accreditation_type`. Cross to the compliance agent for the certificate; this domain
holds the consequence.

**Changing the assigned vendor re-runs the check.** Say so when proposing a reassignment — it is
not a field edit, it is a re-evaluation that can unblock or re-block the order.

69 work orders are at `blocked` today.

## 2. SLA response

```
sla_response_target_mins   what the contract allows
sla_response_actual_mins   what actually happened
sla_response_mins          the figure the import carried
sla_breached               boolean
```

`sla_policies` holds four policies. A breach is a contractual fact with money attached — it is
what a service credit is claimed against — so never state one from a computed difference when
`sla_breached` is recorded; and where `sla_breached` is null, say the breach is **not recorded**
rather than that there was none.

There is **no deadline column** on a work order. "Deadline" in a rendered entry is either the SLA
target counted from the response clock, or the state that blocks it. Do not invent a date.

## 3. Approvals

`wo_approval_requests` (36) against `wo_approval_rules` (13), and 175 orders sit at
`pending_approval`. An order waiting on approval is not waiting on a contractor, and the two
should never be reported as the same backlog — one needs a decision, the other needs a visit.

## 4. Vendor performance

`vendor_wo_scores` holds 2,801 scored orders and `vendor_monthly_scorecards` the monthly roll-up.
Use these for "is this the right contractor", and read `contract-performance` for the scoring
rules themselves. A vendor missing its windows repeatedly is an argument for reassignment that a
single late order is not.

## 5. What an activity-log entry for one order should say

Lead with the sentence, then the fields — and every field states where it came from:

> **WO-4512 — Boiler-22, Town Hall.** P2, held. Blocked because the assigned vendor's
> accreditation for this asset type is not current, so allocation is locked. Estimated £640 from
> the contracted rates; no actual cost recorded yet.

| field | source | if absent |
|---|---|---|
| work order | `wo_code` (100%) | — |
| asset | `asset_id` → assets; `asset_code` only on 28% | say the asset link is missing |
| trigger type | `maintenance_type` / `wo_type` | **80% null** — say unrecorded, not "none" |
| priority | `priority` | 400 rows null — unknown, not routine |
| estimated cost | `estimated_cost`, with its currency | say which column and market |
| assigned vendor | `vendor_id` (100%) | — |
| deadline | SLA target, or the blocking state | **never invent a date** |

**Do not narrate a planner or a quality gate that did not run.** A rendered chain of thought
saying "operative availability checked against ResourceSkill" is false here: `resource_skills`
holds **0 rows** and `work_order_tasks` holds 0, so no availability was checked and no task list
was generated. Describing work the system did not do is the same fault as inventing a figure,
and harder to spot because it reads like process.

## 6. Never

- Never report `blocked` without the reason that blocks it.
- Never state an SLA breach that `sla_breached` does not record.
- Never invent a deadline date — there is no deadline column.
- Never fold `pending_approval` into a contractor backlog.
- Never claim an availability or skills check: `resource_skills` is empty.
- Never propose a vendor change without saying it re-runs the accreditation check.
