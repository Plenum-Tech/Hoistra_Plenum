# Benchmark validation — every building against its market's rule, from its own readings

Backend only. Why this exists: the Energy page showed "no EUI reading on record for any
building in scope" and dormant country tiles over buildings whose meters read half-hourly.
The rules were all there (TM46, BCA, LL97 / Energy Star, the UAE rolling median); nothing
ran them unless someone POSTed `/eui/compute` by hand with a pre-built energy profile, and the
country tiles read a survey column on the site row instead of anything derived.

The service now runs the validation itself, daily, and exposes it.

## What a validation does, per building

1. **EUI** — from the readings on record over the trailing 12 months, from every active meter
   on the building (`energy_meters.site_id = building_id`, plus `meters` matched by
   MPAN/MPRN). Annualised from the months that actually have data; fewer than 3 months →
   `provisional: true`. Floor area comes from the energy profile, then `buildings.gross_area_sqft`,
   then `sites.gfa_sqm` (`gfa_source` says which). No readings → the figure recorded on the
   row is used and labelled `eui_source: "recorded"`; no figure at all → `null`, never invented.
2. **Fuel** — what was measured: `electricity`, `gas`, or `combined`. A TM46 benchmark is per
   fuel, so an electricity-only EUI reads against the electricity figure for its use class.
3. **Benchmark** — the market's rule:

   | Market | Rule (`benchmark_rule`) | Figure (`benchmark_basis`) |
   |---|---|---|
   | UK | `tm46` | CIBSE TM46 for the use class and fuel (`tm46_electricity_by_use` …); recorded figure if the use maps to no class |
   | SG | `bca` | the building's recorded BCA figure, else the BCA office median 192 (`bca_office_reference`) |
   | AE (and any market without an operational standard) | `portfolio_rolling` | median EUI of the *other* buildings in that market and scope (≥ 2 comparables) — else the recorded figure, else none with `reason: comparable_buildings` |
   | US | `ll97` | not a kWh/m² figure: LL97 and Energy Star are computed in their own units (`us_ratings`) once a building has 3+ months of consumption |

4. **Money** — `(EUI − benchmark) × area`, priced at the market's indicative electricity tariff
   in the market's currency (`GBP`, `AED`, `USD`, `SGD`); never below zero.
5. **Persist** — a derived EUI is written as an `eui_snapshots` row (one per building, fuel and
   window end; re-runs are idempotent). Every reader then sees the same number: the buildings
   table (`eui_source: eui_snapshot`), the ratings-and-duties tiles, and the market profiles.
   Recorded figures are not re-written — they were never derived.

## Endpoints (bearer required; building-scoped like every energy route)

### `GET /api/energy/benchmarks/validation?window_months=12&building_id=`
Read-only report for the caller's buildings (or one).

```json
{
  "ok": true, "validated_at": "…", "window_months": 12, "persisted": false, "snapshots_written": 0,
  "summary": {"buildings": 9, "validated": 9, "derived_from_readings": 1, "recorded_only": 8, "no_eui": 0},
  "markets": {
    "UK": {"buildings": 6, "validated": 6, "derived": 1, "recorded": 5, "provisional": 0,
           "rule": "tm46", "currency": "GBP", "excess_cost_per_year": 69868,
           "needs": {"meters": 5, "epc_certificate": 4},
           "tiles_ready": {"mees": true, "epcs_on_file": true, "eui_vs_tm46": true}},
    "AE": {"…": "…", "needs": {"meters": 1, "chiller_design_specs": 1},
           "tiles_ready": {"eui_vs_rolling": false, "chiller_kw_per_rt": false}},
    "US": {"…": "…", "tiles_ready": {"ll97": false, "energy_star": false, "ll84_filing": true}},
    "SG": {"…": "…", "tiles_ready": {"bca_submission": true, "eui_vs_bca": true, "green_mark": true}}
  },
  "buildings": [{
    "building_id": "…", "name": "Riverside Court", "country_code": "UK", "use": "office",
    "gfa_m2": 11241.0, "gfa_source": "energy_profile", "meters_active": 1,
    "eui_kwh_per_m2": 25.79, "eui_source": "derived", "fuel": "electricity", "months_of_data": 13,
    "provisional": false, "period_start": "2025-08-01", "period_end": "2026-09-14",
    "benchmark_kwh_per_m2": 95.0, "benchmark_basis": "tm46_electricity_by_use", "benchmark_rule": "tm46",
    "deviation_pct": -72.9, "excess_kwh": 0, "cost": 0, "currency": "GBP", "priced": true,
    "standing": "within", "validated": true, "needs": [], "us_ratings": null
  }]
}
```

`needs[]` names exactly what a building lacks, in words a data owner can act on:
`meters`, `meter_readings`, `floor_area`, `use_type`, `more_months_of_readings (n of 3 minimum)`,
`epc_certificate` (UK), `comparable_buildings` (rolling rule), `chiller_design_specs` (AE),
`twelve_months_consumption (LL97 / Energy Star need 3+ months)` (US).

`tiles_ready` says which of that market's ratings-and-duties tiles have the inputs to show a
figure rather than "—".

### `POST /api/energy/benchmarks/validate?window_months=12&building_id=`
Same report, and persists what it derives (EUI snapshots; LL97 / Energy Star positions for
US buildings with enough months). This is what the scheduler runs.

## The clock

The in-service scheduler runs `validate` over **every** building once per
`BENCHMARK_VALIDATION_EVERY_HOURS` (default 24) — first run within a minute of startup — and
logs `benchmark_validation.run buildings=… validated=… derived=… snapshots_written=…`. Off
switch: `BENCHMARK_VALIDATION_ENABLED=false`.

## What changed for readers

- `GET /api/energy/ratings/position` now reads the latest derived snapshot first and the
  site's survey column second (`coalesce`), so the AE "EUI vs rolling benchmark" and SG
  "EUI vs BCA reference" tiles reflect metered EUIs as soon as they exist.
- `GET /api/energy/buildings` already preferred snapshots; it now receives them without a
  manual `/eui/compute`.

## Why the screenshots showed "—"

On the database behind the dev frontend (`plenum_agent`), only one building has meters with
readings (electricity + gas since June 2026), none of the buildings in that user's scope has a
recorded EUI, and no EPC certificate is linked to a building — so the UK MEES tiles read
`0 / 0` and the headline EUI had nothing to derive from. This validation makes each of those
gaps a named `needs` item per building instead of a dash; the data still has to be filed.
