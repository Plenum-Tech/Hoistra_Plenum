# Asset intelligence: sections, value at risk, vendor, bands and failure risk

Four blocks on the Assets page were reading fixture data because nothing behind them existed.
They now have tables, and three endpoints on **svc-operations-intelligence** serve them. This
is what they return and, where a figure is a judgement rather than a measurement, what it was
made from.

Everything here is building-scoped the same way as the rest of the platform: no `building_id`
gives you every building you may see, `?building_id=<uuid>` narrows and is **403** if you are
not allocated to it, and a caller allocated to nothing gets a predicate matching no row rather
than no predicate at all.

---

## What was added to the schema

`migrations/asset_intelligence_tables.sql` — idempotent, applied to both databases.

| Object | What it holds |
|---|---|
| `plenum_cafm.building_sections` | A sub-metered zone with **its own** intensity reference |
| `assets.section_id`, `energy_meters.section_id` | Which section an asset sits in, which one a meter measures |
| `assets.replacement_value`, `.replacement_currency` | What the asset costs new today |
| `assets.design_life_years` | Published service life for that kind of plant (CIBSE Guide M) |
| `assets.wear_coefficient` | How much faster it ages when run above reference |
| `assets.vendor_id` | The vendor who holds the asset, on the asset |
| `plenum_cafm.asset_reading_bands` | Low and high limits per reading type |
| `plenum_cafm.asset_failure_assessments` | A probability, the rule that produced it, and its drivers |

Every key pointing at an asset or a vendor is **TEXT**, not uuid: `assets.id` and `vendors.id`
are uuid on one database and varchar on the other, so comparisons are `::text` on both sides.

---

## `GET /api/energy/sections`

Sections with their measured intensity against **their own** reference — which is the whole
reason a section exists as a record. A server room read against an office benchmark looks like
a catastrophe and a car park looks like a triumph.

```jsonc
{
  "sections": [
    {"section_id": "…", "building_id": "…", "building": "Marlowe House",
     "name": "Central plant · basement", "section_type": "plant",
     "area_m2": 401.34, "meters": 1,
     "eui_kwh_per_m2": 57.4,            // null when no sub-meter or no area — never 0
     "reference_eui_kwh_m2": 180.0,
     "reference_source": "CIBSE TM46 general + plant uplift",
     "deviation_pct": -68.1,
     "measured": true}
  ],
  "summary": {"sections": 45, "measured": 18, "over_reference": 0,
              "worst_deviation_pct": null}
}
```

`measured: false` with a null intensity means no sub-meter is attached or the area is unknown.
Render that as "not metered", not as zero consumption — the two are very different claims.

Query: `?building_id=<uuid>` optional.

---

## `GET /api/energy/assets/value-at-risk`

The headline figure, plus the assets that make it up and a count of the ones that cannot be
computed.

```jsonc
{
  "ok": true,
  "value_at_risk": 271103.45,
  "currency": "GBP",
  "assets_counted": 54,
  "assets_not_computable": 0,
  "assets_contributing": 10,
  "note": "counts only assets carrying a replacement value, a design life and an install date; the rest are reported as not computable rather than treated as worthless",
  "assets": [{"asset_id": "…", "asset_name": "Booster pump 6", "building_id": "…",
              "value_at_risk": 5678.48, "deviation_pct": 231.55,
              "design_life_used_pct": 32.9}]
}
```

**Show `assets_not_computable` on the page.** An asset missing one of the three inputs is
excluded and counted, never silently added as a zero — a total that quietly includes zeros for
unpriced assets is a smaller number that looks like a real one.

### How the figure is computed

Straight line: an asset worth `replacement_value` new is worth nothing after
`design_life_years`. Running `deviation_pct` above its reference ages it faster by
`wear_coefficient`, so the worn line reaches nothing sooner. **The gap between the two lines
today is the loss attributable to the deviation.**

```
effective_age  = age × (1 + wear_coefficient × deviation_pct / 100)
straight_line  = replacement_value × (1 − age / design_life)
adjusted       = replacement_value × (1 − effective_age / design_life)
value_at_risk  = straight_line − adjusted          # floored at 0, never a credit
```

Running *below* reference yields 0, not a negative. Every input comes back beside the answer,
and `basis` states the arithmetic in words, so the number can be argued with.

---

## `GET /api/energy/assets/{asset_id}/intelligence`

One asset, everything the drawer shows. **404** if the asset is not in your buildings — the
same response as an asset that does not exist, so the API never confirms that somebody else's
asset is there.

```jsonc
{
  "ok": true,
  "asset": {"id": "…", "asset_name": "Booster pump 6", "asset_code": "…",
            "building_id": "…", "building": "Marlowe House",
            "section_id": "…", "section": "Common areas",
            "section_reference_eui": 205.0,
            "vendor_id": "…", "vendor": "Halden Building Services", "vendor_code": "…",
            "status": "…", "criticality": null,
            "health_score": null, "condition_score": null,
            "installation_date": "2021-10-24", "warranty_expiry": null},

  "anomaly": {"open": 5, "weeks": 0.1, "worst_pct": 231.55,
              "annual_cost": 306600.07, "currency": "GBP"},

  "value": {"replacement_value": 9327.0, "design_life_years": 15.0, "age_years": 4.93,
            "wear_coefficient": 0.8, "deviation_pct": 231.55,
            "straight_line_value": 6261.53, "adjusted_value": 583.04,
            "value_at_risk": 5678.48, "design_life_used_pct": 32.9,
            "remaining_life_months": 11.3,
            "basis": "straight line over 15 years from a replacement value of 9327; effective age 14.06 years after a wear factor of 2.85"},

  "readings": [{"reading_type": "vibration", "value": 7.67, "unit": "mm/s",
                "recorded_at": "2026-09-15T07:07:42",
                "band_lo": 0.0, "band_hi": 7.1,
                "state": "out_of_band",            // in_band | out_of_band | unknown
                "note": "ISO 10816 zone boundary"}],

  "failure_assessment": {
    "probability": 0.0437,
    "method": "rule:condition+anomaly+age+band/v1",
    "is_fitted_model": false,
    "accuracy": null, "precision": null, "recall": null,
    "why_no_metrics": "no model has been fitted against labelled failures, so accuracy, precision and recall would be invented rather than measured",
    "remaining_life_months": 11.3,
    "design_life_used_pct": 32.9,
    "drivers": [
      {"signal": "open energy anomalies", "value": 5, "weeks_persistent": 0.1,
       "contribution": 0.0063,
       "note": "a finding that persists counts for more than a new one"},
      {"signal": "design life used", "value": 32.9, "contribution": 0.0,
       "note": "starts contributing past 60 per cent of design life"},
      {"signal": "readings outside band", "value": "2 of 8", "contribution": 0.0375}
    ]
  }
}
```

### Two things to hold the line on in the UI

**`state: "unknown"` is not "in band".** A reading with no band defined is ungraded. Show it
as ungraded — nothing is graded against a limit that was never set.

**The failure assessment is a named rule, not a trained model.** It carries
`is_fitted_model: false` and nulls for accuracy, precision and recall, because no model has
been fitted against labelled failures and quoting those numbers without one is inventing
evidence. Please render the `method` string and the drivers rather than a bare percentage with
a "model" label — the drivers are what make the number usable.

The four signals and their maximum contributions:

| Signal | Weight | Starts contributing |
|---|---|---|
| Condition grade (1 as new, 5 end of life) | 0.35 | grade 3 |
| Open energy anomalies, weighted by persistence | 0.25 | any open finding |
| Design life used | 0.25 | past 60 per cent |
| Readings outside band | 0.15 | any out-of-band reading |

They sum to 1.0 and the total is capped there.

---

## Where the current figures come from

The tables are populated by `db/tools/seed_asset_intelligence.py` (dry run by default,
`--apply` to write). What is **derived** from what was already on record: section area is a
share of the building's own gross area; the vendor is picked from vendors who already did work
on that asset's building, so the link agrees with the work-order history.

What is **chosen**, stable by hash, and stated per class in that file: replacement value bands,
design lives (CIBSE Guide M), wear coefficients, and install dates spread between a fifth and
nine tenths of each asset's design life. The reading bands are real published limits — ISO
10816 for vibration, BS EN 50160 for supply voltage, CIBSE TM46 for the section references.

This is demo data with a marker on it, not a survey. When real asset registers arrive, the
columns are already there to receive them and nothing in the engine changes.
