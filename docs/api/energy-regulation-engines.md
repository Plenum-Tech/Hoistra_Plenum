# Energy regulation engines (B5–B10)

The energy page shows, per market, three "ratings and duties" tiles and a card of thirteen
detection rules. Until this release the tiles were constants in the frontend and ten of the
thirteen rules had no detector. This documents what now computes them, what each needs as
input, and the endpoints the frontend reads and writes.

Every endpoint is behind the access boundary described in `access-control-api.md`: a bearer
token is required, the company is the caller's, a `building_id` must be one the caller is
allocated to (403 `building_not_allocated` otherwise), lists are narrowed to the caller's
buildings, and every write that adds data needs the per-user ingest right (403 `cannot_ingest`).

## The one endpoint the tiles come from

`GET /api/energy/ratings/position?country_code=UK|US|SG|AE[&building_id=]`

Returns `{ok, country_code, buildings, tiles: [...], detail: {...}}` where each tile has the
shape the frontend's `EN_RATINGS` already uses:

```json
{"l": "MEES — enforceable now", "v": "0", "s": "below EPC E · 6 with an EPC on file",
 "tone": "ok|warn|risk|dormant", "basis": "certificate|filing|consumption|scheme", "months": 12}
```

`basis` follows the frontend's rule: a certificate or filing is actual the day it is on file;
a consumption figure is actual at 12 months, projected from 3, and not shown below 3 (the tile
then reads `v: "—"` with `tone: "dormant"`). Nothing is invented: a market with no record
says so.

| Country | Tile 1 | Tile 2 | Tile 3 | Source |
|---|---|---|---|---|
| UK | MEES — enforceable now (buildings below E) | MEES — proposed 2030 (below B, over 1,000 m²) | EPCs on file (current / expiring 12 m) | EPC band on `compliance_certificates` (B5) |
| US | LL97 limit (within / over, headroom %) | Energy Star score (estimate) | LL84 benchmarking (filed / due / overdue) | `energy_ratings` snapshots (B6, B7), `regulatory_filings` (B8) |
| SG | BCA energy submission (filed / due / overdue) | EUI vs BCA reference (192 kWh/m²/yr office) | Green Mark (level, or none) | `regulatory_filings` (B8), site EUI |
| AE | Operational rating (none — Estidama / Al Sa'fat are new-build) | EUI vs rolling benchmark | Chiller plant kW/RT (worst chiller vs design) | site EUI, `chiller_design_specs` + readings (B9) |

## B5 — EPC band and MEES

`compliance_certificates.energy_rating` (A–G) and `energy_score` are filled three ways, in
precedence order: the GOV.UK register (`current_energy_rating` in the verification evidence,
written when an EPC is verified — the register is the issued figure), the document at
extraction (heuristic patterns for "Asset rating C 63", "Energy rating B (42)", "This
building's energy rating is D"; Claude extraction returns `energy_rating` / `energy_score`),
or the API body (`POST /api/compliance/certificates` accepts `energy_rating`, `energy_score`).
Only EPC-type certificates (`EPC`, `DEC`) carry a band. Existing verified EPCs were backfilled
by the migration.

Every certificate row now returns `energy_rating`, `energy_score` and `mees` —
`compliant | at_risk_2030 | below_minimum | unknown` (E is the enforceable minimum, B the
proposed 2030 floor; an unknown band is `unknown`, never compliant).

`GET /api/compliance/mees[?building_id=]` → per building the latest EPC's band, score, expiry,
`mees`; counts `below_minimum_now`, `below_proposed_2030_over_1000m2`, `epcs_current`,
`epcs_expiring_within_12_months`; and the three UK tiles.

## B6 — ENERGY STAR score (estimate)

`POST /api/energy/ratings/compute {building_id, scheme: "energy_star", months: 12}`

Site kWh by fuel from every active meter on the building → source energy (EPA site-to-source
factors: electricity 2.80, gas 1.05, steam 1.20) → source EUI in kBtu/ft² → percentile
against the national median for the property type (Portfolio Manager Technical Reference
medians, e.g. office 116.4) on a lognormal fit. Median = 50; about a quarter below median =
75 (the certification threshold). The property type is mapped from the building's
`primary_use` / site `use_type`.

**This is an estimate**, not an official Portfolio Manager score — EPA's per-type regression
tables are not on the platform. The response says so (`estimate: true`, `method`) and the
tile appends "· estimate". Response includes `score`, `points_short`, `reduction_to_certify_pct`,
`site_eui_kbtu_ft2`, `source_eui_kbtu_ft2`, `median_source_eui_kbtu_ft2`, `months_of_data`,
`basis`. Snapshotted to `energy_ratings` (scheme `energy_star`).

## B7 — LL97 emissions cap

`POST /api/energy/ratings/compute {building_id, scheme: "ll97", months: 12, year?: 2025}`

Annual tCO₂e from kWh by fuel using the coefficients in 1 RCNY §103-14 (2024–29: electricity
0.000288962 tCO₂e/kWh, natural gas 0.00005311 tCO₂e/kBtu, steam 0.00004493; 2030–34
electricity 0.000145) against the cap = occupancy-group limit (tCO₂e/ft²) × gross floor area.
Occupancy group from `primary_use` (Commercial → B, Retail → M, Residential → R-2, Hotel → R-1,
Hospital → I-2, Education → E, Industrial → F, Logistics → S, Leisure → A); a `use_mix`
blends the limits by area share. Penalty $268/tCO₂e over. Response: `emissions_tco2e`,
`cap_tco2e`, `headroom_pct`, `over_tco2e`, `penalty_usd`, `status: within|over`,
`compliance_period`, `by_fuel`. Snapshotted (scheme `ll97`).

`GET /api/energy/ratings[?building_id=&scheme=]` → the latest snapshot per building and scheme.

Requirements for both: `buildings.gross_area_sqft` (or site `gfa_sqm`) and ≥ 3 months of
readings on meters linked to the building (`energy_meters.site_id = building_id`, or
`plenum_cafm.meters.building_id` matched by MPAN/MPRN). Errors: `gross_area_required`,
`insufficient_data` (with `months_of_data`).

## B8 — Filings: LL84, BCA benchmarking, Green Mark

`POST /api/compliance/filings` (201)
```json
{"building_id": "...", "scheme": "LL84|BCA_BENCHMARKING|GREEN_MARK", "period_year": 2025,
 "status": "filed|submitted|accepted|certified|due|overdue|lapsed", "filed_at": "2026-04-28",
 "reference": "PM-123456", "certification_level": "Gold", "valid_until": "2029-06-01",
 "submitted_by": "…", "evidence_document_id": "…"}
```
Idempotent on (building, scheme, period_year). `period_year` is the year reported on: LL84 for
2025 is due 1 May 2026; BCA for 2025 is due 30 Sept 2026; Green Mark is a certification
(levels Certified | Gold | GoldPlus | Platinum) valid three years from `filed_at` unless
`valid_until` is given.

`GET /api/compliance/filings[?building_id=&scheme=]` → filings on record + the scheme catalogue.
`GET /api/compliance/filings/position?country_code=US|SG[&building_id=]` → per building and
scheme: `filed | due | overdue` (with `period_year`, `due_date`, `days_overdue`, `next_due`) or
`certified | lapsed | none` (with `level`, `valid_until`); counts; the tiles.

## B9 — Chiller kW/RT (UAE)

1. `POST /api/energy/chillers/{asset_id}/design {design_kw_per_rt: 0.68, design_capacity_rt?, design_ambient_c?, building_id?}`
2. `POST /api/energy/chillers/{asset_id}/readings {readings: [{reading_at, kw_input, cooling_load_rt | cooling_load_kw, ambient_c?, chw_supply_c?, chw_return_c?}]}` (ingest right)
3. `GET /api/energy/chillers/{asset_id}/efficiency?window_days=14` → `actual_kw_per_rt`,
   `design_kw_per_rt`, `deviation_pct`, `breach`, `samples_used` — the position whether or not
   it breaches.
4. `POST /api/energy/chillers/scan {asset_id?}` → assesses one chiller or every chiller with a
   design figure; more than 15% above design at matched ambient (±2 °C of design ambient when
   given) and ≥ 40% load (when capacity given) raises a `chiller_efficiency` anomaly on the
   asset into the approvals queue, priced at the building's meter tariff. No work order.

Thermal kW is converted to RT at 3.517 kW/RT. The figure is load-weighted (Σ kW / Σ RT).

## B10 — the ten new detection rules

`POST /api/energy/anomalies/scan {meter_id}` runs all thirteen rules and returns
`rules_run` and `skipped: {rule: why}`. A rule whose input the meter does not have is skipped,
not run on nothing.

| Rule (card id) | anomaly_type | Rule as implemented | Input beyond readings |
|---|---|---|---|
| nonocc | `nonocc_spike` | unoccupied-hours mean > 30% of occupied-hours mean over 7 days, and ≥ 5 points above the meter's own baseline ratio when history exists | occupancy hours (meter `raw_metadata.occupancy_hours {start_hour, end_hour, weekdays_only}`, default 07–19 weekdays) |
| schedule | `schedule_mismatch` | plant start/stop (first/last interval above the midpoint of base and peak) > 45 min outside the calendar, median over working days | occupancy hours |
| baseload | `baseload_creep` | overnight floor (00–05 daily minimum) up ≥ 3% three complete weeks running while weekday 09–17 median moves < 5% | — |
| calendar | `weekend_spike` (existing) | weekend > 130% of the other weekends in 4 weeks | — |
| drift | `baseline_drift` (existing) | 7-day mean > 110% of prior 7 days, persisting 48 h | occupancy log suppresses |
| spike | `asset_spike` (existing) | sub-meter > 120% of its 4-week profile | sub-meter |
| weather | `weather_residual` | OLS kWh ~ HDD + CDD on ≥ 12 complete months; one-sided CUSUM (slack 0.5σ, limit 4σ, σ from MAD) on residuals | `POST /api/energy/weather/degree-days {building_id, months: [{month, hdd, cdd}]}` |
| peak | `peak_excursion` | interval kW above agreed capacity, else above prior-year max | meter `raw_metadata.capacity_kw` (or `agreed_capacity_kva`); else ≥ 1 year of readings |
| fight | `simultaneous_heating_cooling` | heating and cooling both > 10% in one zone for ≥ 30 min | `POST /api/energy/bms/trends {building_id, samples: [{recorded_at, zone, heating_pct, cooling_pct}]}`; priced only with meter `raw_metadata.zone_kw` |
| cop | `chiller_efficiency` | kW/RT > 115% of design at matched ambient, ≥ 40% load | B9 (runs via `/chillers/scan`) |
| regress | `post_works_regression` | a work order closed on the asset/building in 30 days whose week-after was ≤ 90% of the week-before, and the latest week is back ≥ 95% of it | `plenum_cafm.work_orders` (closed_at / completed_at) |
| dataq | `data_quality` | gaps ≥ 2 h, flatlines ≥ 12 identical intervals, estimated reads (`quality_flag`), negatives; ≥ 2% of expected intervals | — · **never priced**: `financial_gbp = 0` |
| tou | `tou_misalignment` | peak-band mean load > 125% of the rest of the working day | meter `raw_metadata.tariff_bands [{name, start_hour, end_hour, weekdays_only, rate}]`, `offpeak_rate`; default UK red band 16–19 |

Every detector returns the same dict as the three existing ones (`anomaly_type`, `metric_pct`,
`excess_kwh`, `annualised_excess_kwh`, `financial_gbp`, `tariff_gbp_per_kwh`, `detail`), so
anomalies list, act and monthly report unchanged. Thresholds are the card's; the
implementation lives in `svc-operations-intelligence/src/engines/energy/detectors.py`
(`RULE_IDS` maps card ids to anomaly types).

## Deep-agents tools

Energy engine: `compute_building_rating`, `get_ratings_position`, `record_chiller_design`,
`ingest_chiller_readings`, `scan_chiller_efficiency`, `ingest_degree_days`, `ingest_bms_trends`
(and `scan_energy_anomalies` now runs all 13 rules). Compliance engine: `get_mees_summary`,
`record_regulatory_filing`, `list_regulatory_filings`.

## Schema (migration `phase3_energy_regulation.sql`, idempotent, auto-applied at startup)

`compliance_certificates.energy_rating / energy_score`; tables `energy_ratings`,
`regulatory_filings` (unique on building, scheme, year), `chiller_design_specs`,
`chiller_performance_readings`, `weather_degree_days` (unique on building, month), `bms_trends`.
