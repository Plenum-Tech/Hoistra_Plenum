---
name: work-orders
description: The work_orders table as it actually is — which of its duplicated columns carry data, the fifteen status spellings, the priority scheme, and which cost column means what.
---

# Work orders — what the columns actually hold

`plenum_cafm.work_orders` has 2,775 rows and around ninety columns, many of them alternative
names for the same thing from different imports. Reading the wrong one is the main way to be
confidently wrong here, so this is which one to read.

---

## 1. Status has fifteen spellings, and they collide

```
Completed 2174   pending_approval 175   Open 72   Closed 71   blocked 69   open 55
Cancelled 40     InProgress 38          In Progress 36        Raised 21
preparing 10     completed 5            closed 4              active 4     prepared 1
```

**`Open` and `open` are different values.** So "how many open work orders" answered from
`status = 'Open'` returns **72**, when the rows a person means — `Open`, `open`, `active`,
`Raised` — come to about **152**. The same trap sits in `Completed`/`completed`,
`Closed`/`closed` and `InProgress`/`In Progress`.

Always match case-insensitively and group the synonyms:

| what a person means | values |
|---|---|
| open / outstanding | `Open`, `open`, `active`, `Raised` |
| in progress | `InProgress`, `In Progress`, `preparing`, `prepared` |
| waiting on someone | `pending_approval`, `blocked` |
| done | `Completed`, `completed`, `Closed`, `closed` |
| dropped | `Cancelled` |

`blocked` and `pending_approval` are **not** open in the "somebody should be working on it"
sense and not done either — 244 rows sit there. Say which of the two you counted; a backlog
figure that silently folds them in reads as work nobody has started rather than work nobody
can start.

## 2. Which duplicated column to read

| you want | read | coverage | do not read |
|---|---|---|---|
| the reference | `wo_code` | **100%** | `wo_id`, `cmms_work_order_id`, `external_wo_id` — all **0%** |
| the vendor | `vendor_id` | **100%** | `assigned_vendor` 37%, `vendor` 42% |
| the asset | `asset_id` 79%, `asset` 81% | — | `asset_code` only **28%** |
| what it cost | `actual_cost` 48%, else `estimated_cost` 65% | — | see §4 |
| when it was raised | `created_at` / `reported_at` 62% | — | **`raised_at` is 0% — empty on every row** |
| when it finished | `completed_at` 63% | — | `closed_at` 0.1% |

Two of those matter most. **`raised_at` is populated on nothing**, so "how long has this been
open" computed from it is a null, not a zero — use `created_at` or `reported_at` and say which.
And **`asset_code` is on only 28% of rows** while `asset_id` is on 79%, so joining by code
silently drops two thirds of the work.

## 3. Priority is two schemes in one column

```
P3 777 · P2 510 · P4 482 · P1 385 · Routine 51 · (null) 400
```

`P1`–`P4` plus a stray `Routine`, and **400 rows with no priority at all**. A "how many P1s"
answer should say how many rows carry no priority rather than counting them as low — a missing
priority is unknown, not routine.

## 4. Cost: five columns, two currencies

```
estimated_cost 65%   actual_cost 48%   parts_cost 62%
cost_vendor_aed 18%  cost_parts_aed 32%
```

The `_aed` columns are explicitly dirhams. The unsuffixed ones carry whatever the building's
market bills in, which across this estate is four different currencies. **Never total a cost
column across buildings without grouping by market**, and never add an `_aed` column to an
unsuffixed one — that is adding dirhams to pounds.

Prefer `actual_cost` where it exists and `estimated_cost` otherwise, and say which you used: an
estimate reported as a cost is a different claim about the same job.

## 5. Type and trigger are mostly empty

```
wo_type:           (null) 2095   Reactive 504   PPM 176
maintenance_type:  (null) 2212   Reactive 504   Planned 54   Compliance 1   Emergency Repair 1
```

75% and 80% null respectively. So "how many compliance work orders" is **1**, and that is a
statement about record-keeping rather than about compliance activity. Say so — the honest answer
is that the field is not populated, not that compliance work is not happening.

## 6. Never

- Never filter status with `= 'Open'`. Match case-insensitively across the synonym set.
- Never count `blocked` or `pending_approval` inside an "open" figure without saying so.
- Never join work orders to assets by `asset_code` alone — it is on 28% of rows.
- Never compute an age from `raised_at`; it is empty on every row.
- Never add an `_aed` cost column to an unsuffixed one, or total costs across markets.
- Never report an estimate as an actual cost.
- Never read a null `wo_type` or `maintenance_type` as "none of that kind" — it is unrecorded.
