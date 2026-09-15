# The condition engine: Threat, Watch, In control

The Assets page bands every asset from two signals that already existed, and nothing in the
backend computed the band, held the thresholds it was decided by, or recorded when the scan
last ran. All three now exist, on **svc-operations-intelligence**.

Building-scoped like everything else: no `building_id` gives you every building you may see,
`?building_id=` narrows and is 403 if you are not allocated to it, and a caller allocated to
nothing gets a predicate matching no row.

---

## The rule

| Band | When |
|---|---|
| **Threat** | The section is over reference **and** an anomaly is attributed to this asset |
| **Watch** | The section is over reference but **nothing is attributed to this asset** — it shares the load; **or** an anomaly attributed to it has persisted past the threshold while its section is within reference |
| **In control** | Neither |

A Threat does **not** require the anomaly to be persistent. That is deliberate and matches the
page: an asset with a 1.3-week anomaly in an over-reference section is a Threat, because two
independent signals agreeing is the point, not the age of either one.

Counted separately inside In control: assets that **do** carry an anomaly which has not
persisted far enough to count. "Found nothing" and "found something too small to act on" are
different answers and the second is the one worth looking at.

The two thresholds are **data, not constants** — they are the steppers on the page. Every
verdict and every run records the thresholds it was reached under, because a band decided at
10% means nothing once somebody moves the stepper to 30%.

---

## `GET /api/energy/condition/summary`

The four KPI cards, the building rollup, the section rollup, and the last-run stamp.

```jsonc
{
  "ok": true,
  "rules": {"section_over_reference_pct": 10.0, "anomaly_persistent_weeks": 3.0,
            "is_default": true, "updated_at": null},
  "method": "rule:section-over-reference+anomaly-attributed/v1",
  "summary": {
    "assets": 19, "threat": 4, "watch": 7, "in_control": 8,
    "watch_shares_section": 6,               // "6 shared section"
    "watch_persistent_anomaly": 1,           // "1 persistent anomaly"
    "in_control_anomaly_under_threshold": 3, // "3 with an anomaly under threshold"
    "section_not_measured": 5                // banded on ONE signal — see below
  },
  "buildings": [{"building_id": "…", "building": "Bishopsgate Tower",
                 "threat": 2, "watch": 3, "in_control": 2, "assets": 7,
                 "worst_deviation_pct": 19.0}],
  "sections": [{"section_id": "…", "section": "Central plant · basement",
                "eui_kwh_per_m2": 262.0, "reference_eui_kwh_m2": 180.0,
                "deviation_pct": 45.0, "over_reference": true,
                "threat": 5, "watch": 3, "in_control": 0, "assets": 8}],
  "last_run": {"run_id": "…", "started_at": "2026-09-15T02:14:07+00:00",
               "assets_scanned": 19, "threat": 4, "watch": 7, "in_control": 8,
               "scope": "portfolio"}
}
```

Buildings come back ranked by worst section deviation — the order the page lists them in.

**`section_not_measured` matters.** An asset whose section has no sub-meter was banded on one
signal, not two. It is not "checked and clean"; one of the two things that would have been
checked could not be read. On the deployed database this is 35 of 54 assets today, so please
surface it rather than letting those read as a clean bill of health.

---

## `GET /api/energy/condition/assets`

The banded assets, each with the sentence that explains its band.

```jsonc
{"ok": true, "count": 4, "total": 19,
 "assets": [{
   "asset_id": "…", "asset_name": "CHILLER-101", "asset_code": "AS-1007",
   "building_id": "…", "building": "Bishopsgate Tower",
   "section_id": "…", "section": "Central plant · basement",
   "vendor": "Apex Mechanical", "criticality": "L3",
   "band": "threat",
   "reasons": ["section_over_reference", "anomaly_attributed"],
   "explanation": "Section over reference and an anomaly attributed to this asset. Both signals agree; a work order is justified without waiting for a fault.",
   "section_deviation_pct": 45.0, "section_over_reference": true,
   "section_eui": 262.0, "section_reference": 180.0, "section_measured": true,
   "anomalies_open": 1, "anomaly_weeks": 1.3, "anomaly_persistent": false,
   "anomaly_annual_cost": 28000.0, "currency": "GBP"}]}
```

`reasons` is machine-readable; `explanation` is the prose. Render whichever suits, but the
reasons are what a filter should key on — the wording may change, the reasons will not.

Query: `?band=threat|watch|in_control` backs the band chips; `?min_deviation_pct=10` and `=30`
back the **Above 10%** and **Above 30%** chips.

The section deviation on an asset row is **the same number** as the section header's, because
both come from the same function rather than from two queries that compute it separately.

---

## `GET` / `PUT /api/energy/condition/rules`

The two steppers. `is_default: true` means nobody has set them and the shipped values are in
force.

```bash
PUT /api/energy/condition/rules
{"section_over_reference_pct": 15, "anomaly_persistent_weeks": 4}
```

Applies to every read from the next one onward — the reads assess live, so there is no scan to
re-run before the page changes. Set per organisation; a caller with no organisation gets a 400
saying so.

---

## `POST /api/energy/condition/scan` and `GET /api/energy/condition/last-run`

What the **Run condition scan** button calls, and what **LAST RUN** reads.

The reads do **not** depend on a scan having happened — they assess from current signals every
time, so the page is never showing a stale band. What a scan adds is a timestamp somebody can
point at and one verdict row per asset, kept per run rather than overwritten, so this week's
band can be compared with last week's.

`last_run` is **null** before the first scan. That means no scan has been recorded, not that
there is nothing to scan — don't render it as "0 assets".

---

## What the data says today

| | Deployed database | `plenum_agent` |
|---|---|---|
| Assets banded | 54 | 68 |
| Threat | 0 | 1 |
| Watch | 0 | 5 (all shared-section) |
| In control | 54 | 62 |
| …with an anomaly under threshold | 10 | 6 |
| **Section not metered** | **35 of 54** | **52 of 68** |

Two honest notes. Most assets sit in sections with no sub-meter, so they are banded on the
anomaly signal alone — that is what `section_not_measured` counts, and attaching more
sub-meters to sections is what would change it. And no anomaly on either database has been
open longer than about a week, so `watch_persistent_anomaly` is 0 everywhere; that is a
property of the data, not of the rule.
