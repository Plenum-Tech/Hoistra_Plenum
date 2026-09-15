# The condition engine: Threat, Watch, In control

The Assets page bands every asset from two signals that already existed, and nothing in the
backend computed the band, held the thresholds it was decided by, or recorded when the scan
last ran. All three now exist, on **svc-operations-intelligence**.

Building-scoped like everything else: no `building_id` gives you every building you may see,
`?building_id=` narrows and is 403 if you are not allocated to it, and a caller allocated to
nothing gets a predicate matching no row.

---

## Why the page does not band assets itself

`apps/frontend/src/logic/assetsCondition.js` reads the band from this endpoint. It used to
decide Threat / Watch / In control in the browser instead, and the two answers were not the
same: on `hoistra_test` they disagreed about **ten of fifty-four assets**, measured below.
Three things were behind that, and the first two are reasons it should stay this way:

- **The thresholds are per organisation and editable.** `section_over_reference_pct` and
  `anomaly_persistent_weeks` live in `asset_condition_rules`, and `PUT
  /api/energy/condition/rules` changes them. A browser-side copy cannot see that row, so moving
  a stepper would change what the server says and leave the page banding on the shipped
  defaults — silently, and only for the organisations that have set their own.
- **The chips are already query parameters.** `?band=threat|watch|in_control` and
  `?min_deviation_pct=10` / `=30` back the four chips directly, filtered against the full set
  rather than against whatever the page happened to load.
- **The page banded on the building's deviation, the server bands on the section's.** All ten
  disagreements came from this one. `_signals()` in `condition_engine.py` reads
  `asset_intelligence.sections()`, the same function that fills the section headers, so an
  asset row and its header cannot disagree. `evalA` read `bldByEui[asset.building_id]`
  instead — a different number about a different thing.

### What the disagreement was: 44 of 54 assets, 81.5%

Not a hypothetical. Both rules were run over `hoistra_test` on 15 September 2026 — the backend
through `condition_engine.assess()`, the page through its own exported `conditionOf()` fed the
rows `GET /api/energy/buildings` and the anomalies table return — at the shipped thresholds
(10%, 3 weeks). Re-run after the swap, the page reproduces the server on all 54.

| Page says | Server says | Assets | |
|---|---|---|---|
| In control | In control | 40 | agree |
| Watch | Watch | 4 | agree |
| In control | **Watch** | 8 | disagree |
| Watch | **Threat** | 2 | disagree |

**All ten disagreements ran the same way: the page banded the asset lower than the server, and
never higher.** Two Threats were showing as Watch and eight Watches as In control.

One cause accounts for all ten. `assetsCondition.evalA` reads `bldByEui[asset.building_id]`
and passes the **building's** deviation to `conditionOf`; the server reads the **section's**.
On `hoistra_test` not one building is over its reference — every asset row carries a negative
building deviation, as low as −51.3% — while ten of them sit in a section that is over by 18%
to 46%. Averaged across a whole building, an over-consuming plant room disappears into the
floors around it. That is the difference the section split exists to catch, and banding on the
building figure discards it.

The `health_score` branch of `conditionOf` contributed nothing to this count, because
`assets.health_score` is null on all 54 rows. It is a real difference between the two rules —
the server has no such signal — but a dormant one on this data, so it is not part of the 10.
It was **not** dropped in the swap: `bandFromServer()` lays it on top of the server's band
under the constraint it has always had in this page, that it may raise a band and never lower
one. An asset scored 20 is a Threat whatever the energy says.

### What the page now calls

| Page needs | Now | Status |
|---|---|---|
| Band, reasons, section deviation per asset | `GET /api/energy/condition/assets` on load | wired |
| Which section an asset is in, in bulk | `section_id` on the same row | wired — this closed the gap the page carried, since `AssetResponse` still does not return the column |
| "Banded on one signal" on an unmetered section | `section_measured` on the same row | wired, per asset, in the row's explanation |
| Band chips, Above 10% / Above 30% | `?band=` / `?min_deviation_pct=` on the same call | **not wired** — the chips still filter the loaded array, which is correct but re-filters rather than re-asks |
| KPI cards, building and section rollups | `GET /api/energy/condition/summary` | **not wired** — the client method exists (`energyApi.conditionSummary`), the page still sums rows itself |

`energyApi.assetValueAtRisk()` is **not** replaced by this — replacement value at risk is its
own figure and stays where it is. Nor is `assetIntelligence()` for the opened-asset drawer.
This is only about which surface decides the band.

**The two steppers are the open question.** `section_over_reference_pct` and
`anomaly_persistent_weeks` are per organisation and only `PUT /api/energy/condition/rules`
moves them, so a stepper that writes changes the thresholds for everyone in the company, not
just for the person dragging it. The page therefore reads the server's thresholds back
(`asCondRules`) and quotes those in each row's explanation, and the steppers were left as they
were rather than silently turned into a shared write. Wiring them to the PUT is a product
decision, not a mechanical one.

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

## What a building, a section and an asset row now carry

The page prints far more beside each row than a band, so the rollups carry it — a header is
one response rather than four joined in the page.

**Building row:** `eui_kwh_per_m2`, `reference_eui_kwh_m2`, `deviation_pct`,
`benchmark_standard`, `over_reference`, `sections_over_reference` of `sections`,
`work_orders_open`, `inspections_recommended`, plus the three band counts. Ranked by the
building's own deviation.

**Section row:** its intensity against its own reference, `over_reference`, the band counts,
and `route` — **`"sub-meter"`** where a meter measured it, **`"building-level · inferred"`**
where the figure is apportioned. An asset in no section is gathered into a stated
`is_whole_building` row rather than dropped, because the page shows those assets and the
honest label is that the reading is inferred, not that the asset has no home.

**Asset row:** adds `installation_date` and `last_ppm_date` — the most recent visit actually
completed, from either a PPM visit or a completed planned work order. Null when none is on
record; never silently the install date.

---

## `GET /api/energy/assets/{asset_id}/notes`

The work orders and inspection notes under an asset.

```jsonc
{"ok": true, "asset_id": "…", "asset_name": "CHILLER-101",
 "work_orders": [{"wo_code": "WO-4421", "date": "2026-06-14", "vendor": "Apex Mechanical",
                  "grade": "4", "status": "Completed",
                  "notes": ["Compressor 2 contactor replaced · Refrigerant charge 8% low"],
                  "recommendation": {"text": "Leak test within 3 months; condenser clean",
                                     "state": "open", "became_work_order": null},
                  "report_on_file": true}],
 "reports_without_an_order": [],
 "recommendations_open": 1,
 "warranty": {"asset": {"expires": "2027-03-01", "in_warranty": true},
              "parts": [{"part_name": "Compressor 2 contactor", "expires": "2027-03-01",
                         "in_warranty": true, "claimable": true}],
              "claimable": true}}
```

A recommendation is **open until an order is raised off it** — that is the row worth reading,
and `recommendations_open` counts them.

**Warranty is two separate claims.** The asset may be in warranty, and a part fitted to it may
be under its own term long after the asset's has run out — a contactor fitted last month on a
chiller installed in 2009. Neither is inferred from the other. `claimable` is a prompt to check
a claim, not an assertion that one exists.

---

## The two buttons

**Raise work order** and **Request inspection** are both `POST /api/work-orders/` on
svc-work-order-management, differing only in `request_type`. No separate endpoint exists
because none is needed. Note that `asset` is required as a **name**, not an id — pass
`building_id` alongside it so the order lands in the right building.

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
