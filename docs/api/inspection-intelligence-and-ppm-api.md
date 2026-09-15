# Inspection intelligence and PPM health by contract

The two panels on the Maintenance screen that had no backend: the four cards that read every
report together, and the PPM health table keyed on contract. Both on
**svc-work-order-management**, both building-scoped like everything else.

---

## What was added to the schema

Most of both panels turned out to be derivable from records that already existed — a
recommendation that never became an order is a join, an anomaly an earlier report named is a
date comparison, an asset graded poor is a column. Four things were not:

| Column | Why nothing could be derived without it |
|---|---|
| `ppm_visits.deferred`, `.deferred_to`, `.deferral_reason` | There was no deferral concept anywhere. A visit agreed to move is not a visit nobody attended |
| `vendor_contracts.country_code`, `.service_scope`, `.visits_per_year` | Contracts had a name and nothing else — no "· UK", no "Boilers, DHW, gas safety", no committed plan |
| `spare_parts.warranty_months`, `work_order_parts.fitted_at` / `.warranty_expiry` | Warranty lived on the **asset**. An asset installed in 2009 is out of warranty while a contactor fitted last month is not |
| `work_order_parts.invoiced_value`, `.currency` | Nothing recorded what a fitting cost, so nothing could be called claimable |

Also added where missing, because `inspections` is 14 columns on one database and 9 on the
other: `corrective_action`, `findings_jsonb`, `section`, `source_file`, `work_order_id`,
`converted_work_order_id`, `recommendation`.

---

## `GET /api/maintenance/inspection-intelligence`

The header and all four cards in one call.

```jsonc
{
  "ok": true,
  "corpus": {"reports": 12, "assets": 10, "since": "2026-03-10", "latest": "2026-08-22"},
  "cards": {
    "unconverted_recommendations": {
      "answerable": true, "count": 7, "now_flagged_by_energy": 5,
      "headline": "7 recommendations never converted to orders — 5 on assets now flagged by energy, so the inspector saw it first",
      "method": "a recommendation counts as converted if the report names the order, or if any work order was raised on that asset after the report date",
      "reports": [{"asset_name": "Boiler-7", "inspection_date": "2026-08-12",
                   "now_flagged_by_energy": true, "observations": "…"}]},

    "corroborated_anomalies": {
      "answerable": true, "count": 6, "open_anomalies": 8,
      "headline": "6 of 8 open anomalies corroborated by an earlier finding",
      "method": "same asset, report predates detection, and at least 2 subject words in common; the shared words are returned on every match",
      "anomalies": [{"asset_name": "CHILLER-101", "detected_at": "2026-08-02T…",
                     "corroborated": true,
                     "corroborating_report": {"inspection_date": "2026-06-14",
                                              "shared_terms": ["condenser", "refrigerant"],
                                              "days_before_detection": 49}}]},

    "warranted_findings": {
      "answerable": true, "count": 3, "claimable_value": 2100.0, "currency": "GBP",
      "headline": "3 findings on parts still under warranty — GBP2,100 of invoiced work claimable",
      "method": "warranty from the fitting, not the asset; a fitting with no term recorded is not counted as warranted"},

    "poorly_graded": {
      "answerable": true, "count": 3, "end_of_life": 0, "install_year_range": [2004, 2009],
      "headline": "3 assets graded poor (4 of 5) by inspectors — all 2004–2009 — none graded end of life"}
  },
  "unanswerable": []
}
```

**`answerable: false` is not zero.** A card this database cannot answer comes back with
`count: null` and a `reason` — because a zero reads as "we checked and there are none", which
is a different and wrong claim. Check `unanswerable` and render those cards as unavailable.

**Corroboration is subject and time, never a model.** A report corroborates an anomaly only if
it is about the same asset, **predates the detection**, and shares at least two subject words
with it — and `shared_terms` returns the words the match was made on, so a reader can disagree.
A report written *after* detection is the same observation, not independent support for it.

`GET /api/maintenance/inspection-intelligence/{card}` returns one card with its full list:
`unconverted-recommendations`, `corroborated-anomalies`, `warranted-findings`, `poorly-graded`.

---

## `GET /api/maintenance/ppm/contracts`

The PPM health table, keyed on the **contract** rather than the vendor — the contract is the
thing with a scope and a plan, and one vendor holding several means rolling them together hides
the one that is behind.

```jsonc
{
  "ok": true,
  "source": "ppm_visits",
  "window": {"year_to_date": true, "from": "2026-01-01", "to": "2026-09-15"},
  "summary": {"contracts": 8, "done": 134, "plan": 148, "completion_pct": 90.5,
              "missed": 7, "late": 9, "deferred": 6, "reports": 127,
              "behind_plan": 1, "watch": 5, "to_plan": 2},
  "contracts": [{
    "contract": "Heating and gas", "contract_id": "…",
    "vendor": "Meridian Heating Ltd", "country_code": "UK",
    "service_scope": "Boilers, DHW, gas safety",
    "visits_to_plan": {"done": 12, "plan": 18, "plan_is_committed": true},
    "missed": 4, "late": 2, "deferred": 2,
    "reports": 10, "reports_to_done": {"filed": 10, "done": 12},
    "completion_pct": 66.7, "next_due": null, "buildings": 3,
    "state": "behind plan"}],
  "rule": "behind plan at 3+ missed visits or under 80% complete; watch at any missed, any late or under 100%; to plan otherwise. A deferred visit is not a missed one."
}
```

`?year_to_date=false` widens to all time. Contracts come back worst state first.

### The state rule

| State | When |
|---|---|
| **behind plan** | 3 or more missed visits, **or** under 80% complete |
| **watch** | any missed, any late, or under 100% |
| **to plan** | none of the above |

Missed visits outweigh lateness on purpose: a visit that happened late still happened, and a
visit that never happened is what the contract is judged on. **A deferred visit is not counted
as missed** — it was agreed to move, which is a different fact. This rule reproduces all eight
rows on the page exactly, and there is a test that keeps it doing so.

`plan_is_committed` says whether the denominator is what the contract commits to
(`visits_per_year`) or simply how many visits happened to be booked. 12 of 18 against an agreed
plan and 12 of 18 against whatever got booked are different claims, so the flag says which.

---

## `GET /api/maintenance/overview`

The four cards across the top of the Maintenance screen, in one call — each with the
sub-counts printed beneath it, because "2 blocked · 4 to raise · 2 deviating" is the part that
says what to do next.

```jsonc
{"ok": true,
 "cards": {
   "decisions_owed": {"value": 10, "blocked": 2, "to_raise": 4, "deviating": 2,
                      "awaiting_approval": 2,
                      "caption": "2 blocked · 4 to raise · 2 deviating"},
   "statutory": {"value": 3, "of_decisions": 10, "window_days": 30,
                 "caption": "certificate lapsed or inside 30 days",
                 "decisions": [ /* which ones, and the certificate forcing each */ ]},
   "recommendations_unconverted": {"value": 7, "answerable": true,
                                   "now_flagged_by_energy": 5, "reports": 12,
                                   "since": "2026-03-10",
                                   "caption": "from 12 inspection reports since 2026-03-10"},
   "ppm_to_plan": {"value": 91.0, "done": 134, "plan": 148, "missed": 7, "reports": 127,
                   "deferred": 6, "behind_plan": 1, "year_to_date": true,
                   "caption": "134 of 148 visits · 7 missed · 127 reports"}},
 "last_read": {"started_at": "2026-09-15T02:14:07+00:00", "reports_read": 12}}
```

**A decision is statutory two ways**, and each says which on `statutory_certificate.matched_on`:

1. The decision was raised **about** a certificate, and that certificate has lapsed or runs out
   inside 30 days. This is the exact test and the only one that catches **building-scope**
   certificates — an EICR or a fire risk assessment carries neither an asset nor a vendor, and
   those are most of them.
2. Otherwise, the asset or vendor the decision names carries such a certificate.

Every decision row now also carries `asset_id` and `vendor_id`, not only the display names, so
the join is a real one.

### `POST /api/maintenance/inspection-intelligence/read` and `GET .../last-read`

What **Re-read inspection reports** calls, and what **LAST RUN** reads. The panel computes live
on every read, so this is not what makes the numbers appear — it is the timestamp beside the
button and the record of what this reading found. `last_read` is **null** before the first run:
that means no read has been recorded, not zero reports.

---

## What the data says today

| | Deployed database | `plenum_agent` |
|---|---|---|
| Inspection reports | **0** | 3 (Apr 2024) |
| All four cards | answerable, all **0** | answerable, all **0** |
| PPM contracts (YTD) | **0** — `ppm_visits` is empty | **3** — 1 behind plan, 2 watch |
| PPM contracts (all time) | 0 | 5 |

The endpoints work; the records behind them are thin. On the deployed database `ppm_visits` and
`inspections` are both empty, so both panels correctly render as nothing-to-show rather than
as fixture data. Loading visits and reports is what turns them on — no further backend work is
needed for that.

One caveat on the contract names today: `ppm_visits.contract_id` does not link to
`vendor_contracts` on either database, so the contract name falls back to the vendor's. That is
deliberate fallback rather than a failure, and populating `contract_id` on visits is what gives
the table its real contract names, countries and scopes.
