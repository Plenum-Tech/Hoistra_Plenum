# Generate a Hoistra building test-data workbook

A prompt for Claude Code. Paste it, then give the parameters in §1. The output is one
`.xlsx` per building that the Migration page ingests in a single run and that, once ingested
and scanned, fills every Hoistra page — Assets, Maintenance, Vendors, Energy, Compliance —
with figures that trace back to rows in the file.

The reference this was written from is `northbridge_B-101_harbour_point.xlsx` (Harbour Point,
B-101, 17 sheets) and its sibling `northbridge_B-102_ashgrove_court.xlsx`. The build tooling
behind them lives in `db/tools/`: `update_northbridge_workbook.py` (Buildings + Meter_Readings
sheets), `close_workbook_gaps.py` (Compliance_Certificates, technician logins, new vendors,
country conformance, work-order titles), `build_missing_certificates.py` and
`build_epc_certificates.py` (the synthetic PDFs). Read those before inventing a layout.

---

## 1. Parameters to collect first

```
company            e.g. Northbridge Estates          (organization already exists in the DB)
organization_id    uuid of that organization in plenum_cafm.organizations
buildings[]        code, name, city, postcode, region, primary_use, floors, gross_internal_area_m2
                   e.g. B-101, Harbour Point, London, E14 9GE, Greater London, Commercial, 12, 14200
                        B-102, Ashgrove Court, Manchester, M15 4FN, Greater Manchester, Residential, 9, 8600
country            UK   (the platform keys packs on 'UK' — never 'GB', 'GBR' or 'United Kingdom')
readings_end       yesterday 23:30Z (the last half-hour); readings_start = 365 days before
technician_logins  real plenum_cafm.users ids for this organization (technicians.user_id is a FK)
```

Everything else is derived. Do not ask for more.

## 2. Deliverables

```
<company>_<code>_<name_snake>.xlsx      one per building, 17 sheets, self-contained
<company>_cmms_export.xlsx              all buildings combined (same 17 sheets, rows unioned)
certificates/*.pdf                      one synthetic PDF per certificate row (see §6)
```

Rows that carry no building column (Vendors, Spare_Parts, Asset_Reading_Bands, vendor-scope
certificates, Vendor_Contracts) appear in **every** per-building workbook — a workbook must
ingest alone into an empty database and leave no dangling name.

## 3. Sheet contract (17 sheets, in this order)

Sheet names are destination table names in Title_Case with underscores; the schema mapper
matches them to `plenum_cafm.<table>` directly. Column names below are the destination column
names or a known alias — keep them exactly. Header on row 1, no banner rows, no merged cells,
no formulas, no blank rows.

| # | Sheet | Rows for one building | Purpose |
|---|-------|----------------------|---------|
| 1 | `Sites` | 1 | the site the building sits on |
| 2 | `Buildings` | 1 | the building itself — area is what EUI divides by |
| 3 | `Assets` | 11–12 | plant register |
| 4 | `Work_Orders` | 8 | reactive + planned work, mixed states |
| 5 | `Vendors` | 6 | one per trade the certificates need |
| 6 | `Technicians` | 2 | in-house engineers with logins |
| 7 | `PPM_Visits` | 60–70 | a year of planned visits per asset |
| 8 | `Inspections` | 4 | condition findings with a corrective action |
| 9 | `Spare_Parts` | 6 | stock with reorder levels |
| 10 | `Energy_Meters` | 2 | incoming electricity + gas |
| 11 | `Meter_Readings` | 35,040 | 2 meters × 17,520 half-hours = one year |
| 12 | `Vendor_Contracts` | 4 | one per contracted trade |
| 13 | `Maintenance_Plans` | 1 per asset | the PPM regime |
| 14 | `Building_Sections` | 5 | areas the EUI benchmark is judged against |
| 15 | `Asset_Reading_Bands` | 15 | normal ranges per reading type |
| 16 | `Asset_Readings` | 600–1,100 | sensor readings, some outside band |
| 17 | `Compliance_Certificates` | 16–17 | building, asset and vendor certificates |

### 3.1 `Sites`
`site_id, site_name, city, postcode, region, manager_email`
`site_id` = `S-<nnn>` matching the building number (`B-101` → `S-101`). `site_name` = building name.

### 3.2 `Buildings`
`building_code, name, site_ref, country_code, primary_use, floors, gross_area_sqft, gross_internal_area_m2, eui_kwh_m2, hoist_score`
- `site_ref` = the `Sites.site_id`. `country_code` = `UK`.
- `gross_area_sqft` is the column the platform stores; `gross_internal_area_m2` is what a UK FM
  reads. Give both, sqft = m² × 10.7639. Keep GIA **over 1,000 m²** or the MEES 2030 tile
  will not count the building.
- `eui_kwh_m2`, `hoist_score` blank — the engine computes them.

### 3.3 `Assets`
`asset_code, asset_name, manufacturer, model, site_ref, site_name, status, install_date, maintained_by, replacement_value, replacement_currency, design_life_years, wear_coefficient, condition_score, criticality, section_name, building_code`
- `asset_code` = `<building_code>-<TYPE>-<nn>`: AHU, CHILLER, BOILER, PUMP, LIFT, FIRE-PANEL, DB.
  Include at least one LIFT (LOLER), one CHILLER (PSSR) and one BOILER (CP17) so asset-scope
  certificates have something to attach to.
- `maintained_by` = a `Vendors.vendor_name`, exactly. `section_name` = a `Building_Sections.name`
  for this building, exactly (the writer resolves `section_id` by name).
- `status` Active; `install_date` ISO date 2014–2022; `replacement_value` GBP integer;
  `design_life_years` 15–25; `wear_coefficient` 0.8–1.2; `condition_score` 1–5;
  `criticality` low/medium/high.

### 3.4 `Work_Orders`
`wo_code, vendor_name, asset_code, asset_name, fault_description, priority, status, reported_at, attended_at, completed_at, first_fix, recall, labour_hours, parts_cost, cost_estimated, cost_actual, building_code, wo_type, sla_due_at, title`
- `wo_code` = `WO-<building_code>-<nn>`. `title` is **required** (copy `fault_description`).
- Mix: 2 Blocked, 2 pending_approval, 4 Completed; priorities P1/P2/P3/Planned/Routine.
- Timestamps `YYYY-MM-DDTHH:MM` within the last 60 days. At least **one Completed work order on
  a building or asset inside the last 37 days** — the post-works-regression anomaly needs it.
- `vendor_name` and `asset_code` must resolve to rows in this workbook.

### 3.5 `Vendors`
`vendor_code, vendor_name, trade, address, phone, country, accreditation`
- `vendor_code` is a stable 4-letter code (MERI, BRLT, KSTL, GRDF, TAML, CPSL). Six trades:
  Mechanical (Gas Safe), Electrical (NICEIC), Lifts (LEIA), Fire (BAFE SP203-1),
  Asbestos (HSE licence), Pest control (BPCA). `country` = `United Kingdom` is fine here — it
  is a free-text address field, not the platform key.

### 3.6 `Technicians`
`engineer_id, full_name, trade, site_ref, certification, user_full_name, base_location, user_id`
- `user_id` is a **real** `plenum_cafm.users.id` in the target database, looked up per
  organization — never invented. Two matching the technician by name is ideal; a login belonging
  to someone else on the team is allowed when the org has fewer users than technicians.

### 3.7 `PPM_Visits`
`ppm_ref, vendor_name, asset_code, task, frequency, scheduled_date, completed_date, tolerance_days, status, contract_name, building_code`
- `ppm_ref` = `PPM-<asset_code>-<nn>`. One row per scheduled visit over the past 12 months
  (Quarterly → 4, Monthly → 12, Six-monthly → 2). ~85 % Completed within tolerance, a few Late,
  a few Scheduled in the future. `contract_name` = a `Vendor_Contracts.contract_name`, exactly.

### 3.8 `Inspections`
`inspection_ref, asset_code, inspection_date, inspector, finding_type, risk_level, observations, recommendation, corrective_action`
`corrective_action` `true`/`false`; one High, two Medium, one Low.

### 3.9 `Spare_Parts`
`part_code, part_name, unit_price, stock_quantity, reorder_level, supplier`
Two rows with `stock_quantity < reorder_level`. `supplier` = a vendor name.

### 3.10 `Energy_Meters`
`meter_ref, building_code, site_ref, meter_type, mpan, mprn, is_sub_meter, tariff_gbp_per_kwh, carbon_kg_per_kwh, active, description`
- Two rows: `<CO>-<building_code>-E0` electricity (mpan = meter_ref, mprn blank) and
  `<CO>-<building_code>-G1` gas (mprn = meter_ref, mpan blank).
- **Tariff per fuel**: electricity 0.21, gas 0.062. The benchmark card prices the excess at the
  contracted tariff, so gas at the electricity rate overstates cost 4.5×.
- `carbon_kg_per_kwh` 0.207 electricity, 0.183 gas. `is_sub_meter` false, `active` true.

### 3.11 `Meter_Readings`
`meter_ref, building_code, meter_type, reading_at, consumption_kwh, period_minutes, source`
- Exactly **17,520 rows per meter**: every half-hour from `readings_start 00:00Z` to
  `readings_end 23:30Z`, `period_minutes` 30, `reading_at` as `YYYY-MM-DDTHH:MM:SSZ`.
  Coverage is measured in metered minutes; 500 missing half-hours annualise the EUI upward.
- `source` = `dcc` (a metered read). Never `simulator` for the base year.
- Shape (§5) targets an annual EUI of roughly 170–220 kWh/m² for an office, 110–140 for
  residential communal areas, against the section benchmarks in `Building_Sections`.

### 3.12 `Vendor_Contracts`
`contract_name, vendor_code, vendor_name, service_scope, visits_per_year, contract_start, contract_end, contract_value, country_code, status, sla_terms`
- One per contracted trade: `Mechanical PPM – UK`, `Electrical – UK`, `Lifts – UK`,
  `Fire and security – UK`. `visits_per_year` = the PPM_Visits count for that vendor's assets.
  `country_code` = `UK`. `sla_terms` text: `P1 4h response / 24h fix; P2 8h / 72h; P3 5 days`.

### 3.13 `Maintenance_Plans`
`sm_code, asset_code, building_code, description, maintenance_type, frequency_type, frequency_value, next_due_date, status, vendor_name`
One per asset: `sm_code` = `PPM-<asset_code>`, `maintenance_type` preventive, `frequency_type`
months, `frequency_value` 1/3/6, `next_due_date` within the next 90 days, `status` active.

### 3.14 `Building_Sections`
`building_code, name, section_type, floor_name, gross_area_m2, reference_eui_kwh_m2, reference_source`
Five: `Central plant – basement` (plant, 180), `Tenant floors` (office, 180), `Car park`
(car_park, 45), `Server room` (server_room, 380), `Common areas` (common, 205). Areas sum to
the building GIA. `reference_source` = `CIBSE TM46 <type>`.

### 3.15 `Asset_Reading_Bands`
`reading_type, unit, lo, hi, note`
Fifteen rows: temperature 5–95 °C, temperature_supply 5–20, temperature_return 8–25,
coolant_temp 70–95, pressure 3–5.5 bar, pressure_discharge 12–22, oil_pressure 3–5.5,
vibration 0–7.1 mm/s, frequency 49.5–50.5 Hz, voltage 216–253 V, voltage_output 216–253,
battery_voltage 25.5–28.5, load_percentage 30–100 %, fuel_level 60–100 %, exhaust_temp 350–550 °C.

### 3.16 `Asset_Readings`
`asset_code, reading_type, value, unit, recorded_at`
Per asset, 2–4 reading types, hourly for the last 3–4 days. ~5 % of values just outside the
band on the assets that have an Inspection finding — the Assets page shows those as alerts.

### 3.17 `Compliance_Certificates`
`certificate_number, certificate_type_code, cert_scope, building_code, asset_code, vendor_code, vendor_name, issuer, inspector_name, inspector_accreditation_number, issue_date, expiry_date, next_due_date, inspection_frequency_months, result, status, country_code, defects_found, remedial_actions, remedial_status, energy_rating, energy_score`

Building scope (9): `EICR` (60 m, Satisfactory), `EPC` (120 m), `FIRE_ALARM_SERVICE` ×2
(6 m, one with an observation and `remedial_status` open), `CP17` (12 m, Pass), `TM44` (60 m,
Inspected), `ASBESTOS_SURVEY` (12 m, No ACMs identified). Asset scope (2): `LOLER` on a lift
(6 m), `PSSR` on a chiller (24 m). Vendor scope (7, one per vendor accreditation): `NICEIC`,
`GAS_SAFE`, `LEIA`, `BAFE_SP203_1`, `BPCA`, `HSE_ASBESTOS_LICENCE`, `EMPLOYERS_LIABILITY`.

Rules the compliance engine actually enforces:
- **The EPC's `certificate_number` is its RRN**, 20 digits as `0660-5580-7384-4815-3898`. It is
  the key GOV.UK is queried by and the number printed on the certificate; a made-up
  `EPC-B-101-…` number verifies as nothing and a second EPC row appears when the PDF is ingested.
- **EPC rows fill `energy_rating` (A–G), `energy_score` (0–150) and `result` = the band.** The
  MEES tiles read `energy_rating`; everything else leaves both blank.
- `expiry_date` **and** `next_due_date` both present (equal is fine — they are two destination
  columns and are no longer merged). `status` = `valid`. `country_code` = `UK`.
- `vendor_code` must be in `Vendors`; `asset_code` in `Assets`; `building_code` in `Buildings`.
- Dates must be in the past for issue and consistent with `inspection_frequency_months`. Put
  one certificate expiring inside 12 months (drives the "expiring" tile) and none already
  expired unless you want the risk tile red.

## 4. Cross-sheet integrity — run these before saving

```
every Assets.maintained_by            ∈ Vendors.vendor_name
every Assets.section_name             ∈ Building_Sections.name where building_code matches
every Work_Orders.asset_code          ∈ Assets.asset_code        and vendor_name ∈ Vendors
every PPM_Visits.contract_name        ∈ Vendor_Contracts.contract_name
every PPM_Visits.asset_code           ∈ Assets.asset_code
every Maintenance_Plans.asset_code    ∈ Assets.asset_code        and vendor_name ∈ Vendors
every Meter_Readings.meter_ref        ∈ Energy_Meters.meter_ref  and count per meter == 17,520
every Asset_Readings.reading_type     ∈ Asset_Reading_Bands.reading_type
every Compliance_Certificates.{building_code, asset_code, vendor_code}  resolve
every Technicians.user_id             exists in plenum_cafm.users for organization_id
every *.country_code                  == 'UK'
every Work_Orders.title               non-blank
EPC rows: energy_rating in A–G, energy_score int, result == energy_rating, RRN format
sum(Building_Sections.gross_area_m2)  ≈ Buildings.gross_internal_area_m2
```

Print the checklist result. A failed line is a failed build, not a warning.

## 5. Shaping the readings so the scan finds something

Half-hourly kWh per meter = base × time-of-day × weekday × season × noise:
- Electricity (office): night 40 % of day, 07:00–19:00 plateau, weekend 55 %, summer +15 %
  (cooling). Gas: winter-weighted heating (Nov–Mar 2.5× Jun–Aug), near zero overnight in summer.
- Noise ±6 %.

Inject, deliberately and only these, so the anomaly count after a scan is ~10 per building:
- `baseline_drift` — from 6 weeks before `readings_end`, ramp night-time electricity +15 %.
- `weekend_spike` — two weekends in the last 90 days at weekday load.
- `asset_spike` — one 3-day gas excursion +40 %.
- `post_works_regression` — after the `completed_at` of the most recent Completed work order,
  raise the building's electricity 8 % for 3 weeks.

Dedup is by 35-day window, so a pattern repeated every week counts once; do not smear the same
fault across the whole year.

## 6. Certificate PDFs

Never edit a genuine third-party certificate to insert the building name. Build synthetic PDFs
from the Compliance_Certificates rows (see `db/tools/build_missing_certificates.py::build`):
letterhead of the issuing vendor, type code and scope in the header, then a two-column table of
`Building name`, `Building reference`, `Certificate number`, dates, inspector, accreditation,
result. For the EPC print the RRN twice (`EPC reference number (RRN)` and `Certificate number`),
`Energy rating  Band C` and `Asset rating (A-G) and score  C (74)` — the extractor reads both.

## 7. Ingest and verify

```bash
# 1. empty the test database of everything a workbook can put back (hoistra_test only)
python db/tools/clear_asset_energy_maintenance.py --apply
```
2. Migration page → upload the per-building workbook → accept the mapping gate → accept the
   hierarchy gate → confirm the write. Watch the worker log for `stopped after 100 consecutive`
   or `no meter for this row` — either means a link rule in §4 was broken.
3. Energy page → **Run scan** (raises anomalies and recomputes EUI / cost above benchmark).
4. Chat → ingest each certificate PDF; the door forwards building, band and score.
5. Verify with SQL, not by eye:

```sql
SELECT b.building_code, count(*) FILTER (WHERE a.asset_code IS NOT NULL) assets,
       (SELECT count(*) FROM plenum_cafm.meter_readings r JOIN plenum_cafm.energy_meters m ON m.id=r.meter_id WHERE m.building_id=b.building_id) readings,
       (SELECT count(*) FROM plenum_cafm.energy_anomalies x WHERE x.building_id=b.building_id AND x.status='open') anomalies,
       (SELECT string_agg(certificate_type_code||':'||coalesce(energy_rating,'-'), ' ') FROM plenum_cafm.compliance_certificates c WHERE c.building_id=b.building_id) certs
  FROM plenum_cafm.buildings b LEFT JOIN plenum_cafm.assets a ON a.building_id=b.building_id
 GROUP BY b.building_id, b.building_code ORDER BY 1;
```
Expected per building: 11–12 assets, 35,040 readings, ~10 anomalies, an `EPC:C`-style entry.
Compliance → Energy ratings should then read `MEES enforceable now 0 · 1 with an EPC on file`,
`MEES proposed 2030 1` (a C is below B and the building is over 1,000 m²), `EPCs on file 1 / 1`.

## 8. Naming and file hygiene

- File names are lowercase snake: `northbridge_B-101_harbour_point.xlsx`.
- If the target file is open in Excel, write beside it as `<name>-updated-<yyyymmdd-hhmm>.xlsx`
  and say so; never fail silently.
- No real customer data, no real people, no real certificate numbers except the RRN format.
  The repository is public.
