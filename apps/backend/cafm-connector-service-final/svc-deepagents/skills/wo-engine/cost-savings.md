# Cost savings — where the money is, and the work that captures it

A cost-saving question ("which work orders will save cost", "where can we save money at
Bishopsgate", "reduce our maintenance spend", "are vendors overcharging us") is answered from
**`get_cost_savings(building_name, period)`** — one read across the value ledger, open work
orders, repeat failures, invoice overcharges and reactive spend. Call it once; pass the building
the user named and the period they meant (`last_month` by default, `this_month`, `this_year`,
`last_90_days`). Follow the `answer_rules` it returns.

When the same question also asks for counts ("how many were raised last month and how many are
closed, and which save cost"), answer the counts from `answer_from_records` first, then the
cost-saving part from `get_cost_savings` — two calls, one answer.

## What each section means

| Section | What it is | How to use it |
|---|---|---|
| `ledger` | Detected vs saved per module for the year (energy, vendors, assets…) | "Detected" is found money; "saved" needs a recorded decision. Say "nothing recovered yet" when saved is 0. |
| `open_jobs` | Open work orders whose asset carries priced energy waste, predictive jobs, energy fixes | The "work orders that save cost" table — cost to act vs money at stake. |
| `repeat_failures` | Assets with 3+ reactive jobs in the period | Reactive cost against replacement value: a ratio near or above 1 is a replace-or-fix-the-cause case. |
| `overcharges` | Invoice lines over contract rates on the period's jobs | Money to challenge before the next payment run, by vendor. |
| `spend` | Reactive share of work-order cost | Above ~70 % reactive, planned and predictive work is the lever. |
| `warnings` | Figures to check before quoting | Always shown under "Check before quoting". |

## The answer

1. One line: what can be saved, what is recovered, for which building and period.
2. **Work orders that save money** — | WO number | What | Vendor | Cost to act | Money at stake |.
3. **Repeat failures** — the assets whose reactive cost outweighs a permanent fix.
4. **Overcharges to recover** — total, by vendor, top lines with WO numbers.
5. **Corrective action plan** — | # | Action | WO / vendor | Cost | Saving | When |, biggest saving per £ first.
6. **Check before quoting** — every warning.

## Never

- Add energy findings together — the rules overlap; each asset's largest finding is its figure.
- Call a detected figure saved, or a challenge in flight recovered.
- Invent a saving the read does not carry — an unpriced finding is described, not priced.
- Raise, approve or close a work order from a cost-saving question — recommend; the person acts.
