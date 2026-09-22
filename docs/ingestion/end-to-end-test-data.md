# End-to-end test data: Assets, Energy, Maintenance

What to write, into which table, under which column name, to make those three pages render
from the database rather than from an empty state.

Every column below was read from `information_schema` on `hoistra_test`, not from an ORM
model. Where the two disagree the database wins, and they do disagree: `assets` carries 49
columns of which the ORM maps about half, and `asset_reading_bands` has no ORM model at all.

Conventions used in the column tables:

| Mark | Meaning |
|---|---|
| **PK** | primary key |
| **REQ** | `NOT NULL` with no default, so an insert without it fails |
| **GEN** | generated column, so an insert *with* it fails |
| **FK** | a declared foreign key, enforced by the database |
| *(link)* | a plain `uuid` column that points at another table with **no constraint behind it** |

That last distinction is the one that costs a day. A wrong `uuid` in a *(link)* column inserts
happily, joins to nothing, and the row disappears from the page with no error anywhere.

---

## 1. The building spine

`plenum_cafm.buildings` is the anchor for all three pages, and `buildings.name` is the only
place the display name lives. Nothing else stores it. Every name you see on any of the three
pages was resolved by joining back to this one row.

```
                          buildings.building_id  ← the anchor
                          buildings.name         ← the only display name
                                  ▲
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
   ASSETS                     ENERGY                   MAINTENANCE
        │                         │                         │
  assets.building_id       energy_meters.building_id   work_orders.building_id
        ▲                         ▲                         ▲
        │                         │                         │
  asset_readings            meter_readings            maintenance_plans
   .asset_id  (FK)           .meter_id  (FK)            .asset_id  (FK)
        │                         │                         │
   no building column       no building column         no building column
```

### How each table reaches a building

| Table | Route to `building_id` | Kind |
|---|---|---|
| `buildings` | itself | anchor |
| `floors` | `floors.building_id` | **FK** |
| `building_sections` | `building_sections.building_id` | *(link)*, REQ |
| `assets` | `assets.building_id` | *(link)* |
| `asset_readings` | `asset_id` → `assets.building_id` | FK then *(link)* |
| `asset_reading_bands` | none. Bands are global or per asset | not building-keyed |
| `asset_failure_assessments` | `building_id` written alongside `asset_id` | *(link)* |
| `energy_meters` | `energy_meters.building_id` | *(link)* |
| `meter_readings` | `meter_id` → `energy_meters.building_id` | FK then *(link)* |
| `eui_snapshots` | `eui_snapshots.building_id` | *(link)*, REQ |
| `energy_anomalies` | `energy_anomalies.building_id` | *(link)* |
| `bms_trends` | `bms_trends.building_id` | *(link)*, REQ |
| `work_orders` | `work_orders.building_id` | *(link)* |
| `maintenance_plans` | `asset_id` → `assets.building_id` | FK then *(link)* |
| `work_order_tasks` | `work_order_id` → `work_orders.building_id` | FK then *(link)* |
| `vendors`, `vendor_contracts`, `sla_policies`, `spare_parts` | none | portfolio-wide |

### Three consequences worth planning around

**Readings never name a building.** Neither `meter_readings` nor `asset_readings` has a
building column. A reading reaches a building only through its meter or its asset. Ingest a
meter CSV without first placing the meter on a building and the readings land, count towards
nothing, and every figure on the Energy page ignores them.

**Work orders do not inherit a building from their asset.** `work_orders.building_id` is the
column the access scope filters on directly. A work order with an `asset_id` but no
`building_id` is invisible to a building-scoped user even though its asset is not.

**Maintenance plans have no building column at all.** A plan reaches a building only through
`maintenance_plans.asset_id`, which is `NOT NULL`, so the asset must exist first and must
itself carry a `building_id`.

---

## 2. Ingestion order

Dependencies run in one direction. This order never needs a second pass.

```
 1  organizations                 (exists already)
 2  locations                     country_code decides which regulation pack applies
 3  buildings                     name, building_code, gross_area_sqft, primary_use
 4  floors                        building_id
 5  building_sections             building_id, optional floor_id
 6  asset_categories · vendors    no building link
 7  assets                        building_id + section_id + category_id + vendor_id
 8  asset_reading_bands           global rows, one per reading_type
 9  asset_readings                asset_id
10  energy_meters                 building_id, optional asset_id and section_id
11  meter_readings                meter_id
12  eui_snapshots                 written by the benchmark pass, not ingested
13  energy_anomalies              written by the scan, not ingested
14  spare_parts · sla_policies · vendor_contracts
15  maintenance_plans             asset_id
16  work_orders                   building_id + asset_id
17  work_order_tasks · work_order_parts · maintenance_history
```

Steps 12 and 13 are outputs, not inputs. Do not write them by hand. Ingest readings, then
press **Run energy scan**, and the benchmark pass and the anomaly sweep produce both.

---

## 3. Assets page

Reads `plenum_cafm.assets` grouped under `building_sections`, with an instrumented-asset panel
driven by `asset_readings` graded against `asset_reading_bands`.

### 3.1 `buildings`

| Column | Type | Notes |
|---|---|---|
| `building_id` | uuid | **PK**, defaults to `gen_random_uuid()` |
| `organization_id` | uuid | *(link)*. The company the building belongs to |
| `location_id` | uuid | **FK** → `locations.id`. How the country is resolved |
| `name` | text | The display name. Nothing else stores it |
| `building_code` | varchar(50) | `B-101`. What people type |
| `primary_use` | enum | `Commercial`, `Residential`. Picks the benchmark |
| `floors` | integer | |
| `gross_area_sqft` | numeric(16,2) | Divided by 10.7639 for the m² the EUI uses |
| `eui_kwh_m2` | numeric(14,2) | Derived. Leave null on ingest |
| `hoist_score` | integer | Derived from what has been ingested. Leave null |
| `site_id` | text | |
| `raw_metadata` | jsonb | Defaults `{}` |

### 3.2 `floors`

| Column | Type | Notes |
|---|---|---|
| `floor_id` | uuid | **PK** |
| `building_id` | uuid | **FK** → `buildings.building_id` |
| `level` | integer | `-1` basement, `0` ground |
| `name` | text | `Basement`, `Ground`, `Level 3` |
| `gross_area_sqft` | numeric(16,2) | |

### 3.3 `building_sections`

| Column | Type | Notes |
|---|---|---|
| `section_id` | uuid | **PK** |
| `organization_id` | uuid | *(link)* |
| `building_id` | uuid | **REQ**, *(link)* |
| `floor_id` | uuid | *(link)*. Null where the section spans floors |
| `name` | text | **REQ**. `Tenant floors`, `Central plant · basement` |
| `section_type` | text | Defaults `general`. `office`, `plant`, `car_park`, `server_room`, `common`, `residential` |
| `gross_area_m2` | numeric(12,2) | A share of the building's own area |
| `reference_eui_kwh_m2` | numeric(10,2) | The section's own reference, not the building's |
| `reference_source` | text | `CIBSE TM46 general office` |

A section carries its own reference on purpose. A server room read against an office
benchmark looks like a catastrophe and a car park looks like a triumph.

### 3.4 `asset_categories`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** → `organizations.id` |
| `name` | varchar(150) | **REQ** |
| `description` | text | |
| `parent_id` | uuid | **FK** → self |

### 3.5 `vendors`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `vendor_name` | varchar(255) | **REQ**. Shown on the asset card |
| `vendor_code` | varchar(100) | |
| `city`, `country`, `phone`, `website` | varchar | |
| `status` | varchar(50) | Defaults `active` |

### 3.6 `assets`

The 49 columns matter unevenly. These are the ones the page reads.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `building_id` | uuid | *(link)*. **The access boundary.** An asset without it 404s for every scoped caller |
| `section_id` | uuid | *(link)* → `building_sections.section_id`. Where it appears in the tree |
| `category_id` | uuid | **FK** → `asset_categories.id` |
| `location_id` | uuid | **FK** → `locations.id` |
| `asset_name` | varchar(255) | **REQ** |
| `asset_code` | varchar(150) | `HP-CH-01`. Routes accept this in place of the uuid |
| `status` | varchar(50) | Defaults `active` |
| `criticality` | varchar(50) | `high`, `medium`, `low`. Orders the tree |
| `criticality_level` | text | **GEN**. Never insert this |
| `health_score` | integer | 0-100. Orders the tree ascending |
| `condition_score` | integer | 1 as new to 5 end of life. **Weighs 35% of the failure model** |
| `condition_updated_at` | timestamptz | |
| `installation_date` | date | **Required for any value arithmetic** |
| `replacement_value` | numeric(14,2) | **Required for any value arithmetic** |
| `replacement_currency` | text | `GBP` |
| `design_life_years` | numeric(6,2) | **Required for any value arithmetic** |
| `wear_coefficient` | numeric(6,3) | How much faster it ages when driven hard |
| `vendor_id` | text | *(link)* → `vendors.id`, **stored as text** |
| `warranty_expiry` | date | The page shows this where run hours are not on record |
| `serial_number`, `manufacturer`, `model` | varchar | Optional detail |

Miss any one of `installation_date`, `replacement_value` or `design_life_years` and the engine
reports "not computable: needs replacement value, design life and an install date" instead of
a figure. That is correct behaviour and a blank demo.

`vendor_id` is `text`, not `uuid`, because `vendors.id` is `uuid` on one database and
`varchar` on the other. Write `str(vendor.id)`.

### 3.7 `asset_reading_bands`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK** |
| `organization_id` | uuid | |
| `reading_type` | text | **REQ**. Must match `asset_readings.reading_type` exactly |
| `asset_category` | text | See the warning below |
| `asset_id` | text | Null for a global band |
| `unit` | text | `°C`, `bar`, `mm/s`, `%`, `V`, `Hz` |
| `lo` | numeric(14,4) | Bounds are inclusive. A value equal to a limit grades **in band** |
| `hi` | numeric(14,4) | |
| `note` | text | `ISO 10816 zone boundary`. Shown under the value |

Most specific wins: per asset, then per category, then global.

**Write global rows.** The lookup binds its category parameter to `assets.criticality`, not to
a category name, so a category-scoped band only matches an asset whose criticality is
literally `chiller`. Global rows (`asset_id IS NULL AND asset_category IS NULL`) and per-asset
rows are the two that work.

The fifteen reading types with bands on record:

| reading_type | unit | lo | hi | note |
|---|---|---|---|---|
| `temperature` | °C | 5 | 95 | plant operating range |
| `temperature_supply` | °C | 5 | 20 | chilled water supply |
| `temperature_return` | °C | 8 | 25 | chilled water return |
| `coolant_temp` | °C | 70 | 95 | engine coolant on load |
| `pressure` | bar | 3 | 5.5 | lubrication circuit |
| `pressure_discharge` | bar | 12 | 22 | refrigerant discharge |
| `oil_pressure` | bar | 3 | 5.5 | bearing protection limit |
| `vibration` | mm/s | 0 | 7.1 | ISO 10816 zone boundary |
| `frequency` | Hz | 49.5 | 50.5 | supply tolerance |
| `voltage` | V | 216 | 253 | BS EN 50160 |
| `voltage_output` | V | 216 | 253 | BS EN 50160 |
| `battery_voltage` | V | 25.5 | 28.5 | float charge range |
| `load_percentage` | % | 30 | 100 | recommended load band |
| `fuel_level` | % | 60 | 100 | statutory minimum reserve |
| `exhaust_temp` | °C | 350 | 550 | turbo inlet limit |

### 3.8 `asset_readings`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ**. No default, so supply it |
| `organization_id` | uuid | **REQ**, **FK** |
| `asset_id` | uuid | **REQ**, **FK** → `assets.id` |
| `reading_type` | varchar(100) | **REQ**. Free text, no constraint. Must match a band |
| `value` | numeric(18,4) | **REQ** |
| `unit` | varchar(50) | Falls back to the band's unit when null |
| `recorded_at` | timestamp **without** time zone | Defaults `now()`. Write naive UTC |
| `submitted_by` | uuid | **FK** → `users.id` |

The panel takes the **newest row per `reading_type`** and grades that one. The sparkline wants
the last 24, so write history rather than a single row per metric. An asset appears in the
panel only because its readings came back non-empty; there is no instrumented flag.

Which metrics belong to which plant:

| Class | reading_type list |
|---|---|
| chiller, CRAC | temperature, temperature_supply, temperature_return, pressure, pressure_discharge, oil_pressure, vibration, load_percentage |
| boiler | temperature, temperature_supply, temperature_return, pressure, load_percentage |
| AHU | temperature, temperature_supply, temperature_return, vibration, load_percentage |
| pump | temperature, pressure, oil_pressure, vibration |
| generator | coolant_temp, oil_pressure, fuel_level, exhaust_temp, battery_voltage, frequency, voltage_output, load_percentage |
| FCU | temperature, temperature_supply, temperature_return |
| lift, fire panel, door controller, lighting | none. No building management system produces a feed for these |

### 3.9 The failure model, so the numbers agree with each other

`rule:condition+anomaly+age+band/v1`. Not a fitted model, so it reports no accuracy figure.

| Driver | Input columns | Contribution |
|---|---|---|
| condition grade | `assets.condition_score` | `max(0, (grade − 2) / 3) × 0.35` |
| open anomalies | `energy_anomalies` on the asset | `min(n / 2, 1) × min(weeks / 4, 1) × 0.25` |
| design life used | `installation_date`, `design_life_years` | `min(max(pct − 60, 0) / 40, 1) × 0.25` |
| readings out of band | `asset_readings` vs bands | `(out / graded) × 0.15` |

Only graded readings count in the denominator. A reading with no band is excluded from both
sides rather than counted as healthy. A driver with no signal is omitted from the card, which
is why a card can show three drivers instead of four.

Keep these consistent when you generate data. An asset at grade 1 with six of eight readings
out of band is arithmetically valid and invites the one question the page cannot answer.

---

## 4. Energy page

### 4.1 `locations`

The country lives here, and the country picks the rule book.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `name` | varchar(255) | **REQ** |
| `type` | varchar(100) | **REQ** |
| `country_code` | char(2) | `GB`, `US`, `AE`, `SG`. **Picks the benchmark pack** |
| `building_id` | uuid | *(link)* |
| `city`, `address`, `postcode` | varchar | |
| `pack_id` | uuid | **FK** → `regulation_packs.pack_id` |

A building with no resolvable country gets no benchmark, so its EUI has nothing to be judged
against and it is reported as unattributed rather than as compliant.

### 4.2 `energy_meters`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK** |
| `organization_id` | uuid | *(link)* |
| `building_id` | uuid | *(link)*. **The only thing that places a reading** |
| `asset_id` | uuid | *(link)*. Sub-meter on a specific asset |
| `section_id` | uuid | *(link)*. Sub-meter on a section |
| `meter_type` | varchar(20) | **REQ**. `electricity`, `gas` |
| `mpan` | varchar(40) | Electricity supply number |
| `mprn` | varchar(40) | Gas supply number |
| `tariff_gbp_per_kwh` | numeric(12,6) | Defaults `0.28`. Prices every excess figure |
| `carbon_kg_per_kwh` | numeric(12,6) | Defaults `0.207` |
| `is_sub_meter` | boolean | Defaults false |
| `active` | boolean | Defaults true. The scan only sweeps active meters |
| `asset_type_benchmark_kwh` | numeric(14,4) | |

### 4.3 `meter_readings`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK** |
| `organization_id` | uuid | *(link)* |
| `meter_id` | uuid | **REQ**, **FK** → `energy_meters.id`. The only route to a building |
| `asset_id` | uuid | *(link)* |
| `reading_at` | timestamptz | **REQ** |
| `period_minutes` | integer | Defaults 30 |
| `consumption_kwh` | numeric(14,6) | **REQ** |
| `source` | varchar(40) | Defaults `dcc` |
| `quality_flag` | varchar(40) | |

**There is no building column here.** When you upload a meter CSV, choose the building in the
composer first. The platform looks the MPAN or MPRN up in its own register, and for a building
hoisted minutes ago there is nothing to find, so the building you picked is what places the
meter.

A CSV for ingest needs three columns: an identifier the meter can be found by, a timestamp,
and a consumption figure. Half-hourly for a year is 17,520 rows per meter.

### 4.4 `eui_snapshots` — derived, do not ingest

| Column | Type | Notes |
|---|---|---|
| `building_id` | uuid | **REQ**, *(link)* |
| `period_start`, `period_end` | date | **REQ** |
| `meter_type` | varchar(20) | **REQ** |
| `total_kwh` | numeric(16,4) | **REQ** |
| `gia_m2` | numeric(14,2) | **REQ**. From `buildings.gross_area_sqft` |
| `eui_kwh_per_m2` | numeric(14,6) | **REQ** |
| `benchmark_kwh_per_m2` | numeric(14,6) | From the country pack |
| `deviation_pct`, `excess_kwh`, `financial_gbp`, `tariff_used` | numeric | |

Written by the benchmark pass. The page's EUI, benchmark, deviation and benchmark-standard
fields all come from here, which is why readings alone leave them blank until a scan runs.

### 4.5 `energy_anomalies` — derived, do not ingest

| Column | Type | Notes |
|---|---|---|
| `building_id`, `meter_id`, `asset_id` | uuid | *(link)* |
| `anomaly_type` | varchar(60) | **REQ**. `weekend_spike`, `baseline_drift`, `peak_excursion`, `schedule_mismatch`, `nonocc_spike` and eight more |
| `detected_at` | timestamptz | Defaults `now()` |
| `window_start`, `window_end` | timestamptz | The window examined, not the moment of the run |
| `metric_pct` | numeric(10,4) | How far over the rule's threshold |
| `excess_kwh`, `annualised_excess_kwh`, `financial_gbp` | numeric | |
| `status` | varchar(40) | Defaults `open`. `resolved`, `closed`, `dismissed` are settled |
| `detail_json` | jsonb | |

Each rule reads a 35-day window. A year of readings scanned once reports on its final month
alone, so use the year sweep rather than a single scan on first ingest.

---

## 5. Maintenance page

Served by the work-order service, not by operations-intelligence.

### 5.1 `maintenance_plans` — the PPM plan

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `asset_id` | uuid | **REQ**, **FK** → `assets.id`. **The only route to a building** |
| `sm_code` | varchar(150) | `PPM-CH-Q` |
| `description` | text | |
| `maintenance_type` | varchar(100) | **REQ**. `preventive`, `corrective`, `statutory` |
| `maintenance_type_id` | uuid | **FK** → `maintenance_types.id` |
| `frequency_type` | varchar(50) | **REQ**. `days`, `weeks`, `months` |
| `frequency_value` | integer | **REQ**. `3` with `months` is quarterly |
| `next_due_date` | date | What makes a plan overdue or due soon |
| `priority_id` | uuid | **FK** → `priorities.id` |
| `task_group_id` | uuid | **FK** → `task_groups.id` |
| `status` | varchar(50) | Defaults `active` |

There is no `building_id` on this table. A plan with an asset that has no `building_id` is
invisible to every building-scoped user.

### 5.2 `scheduled_maintenance_assets` — a plan covering several assets

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `maintenance_plan_id` | uuid | **REQ**, **FK** |
| `asset_id` | uuid | **REQ**, **FK** |

### 5.3 `work_orders`

78 columns. These are the ones that matter for ingest.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `building_id` | uuid | *(link)*. **The access boundary. Set it explicitly** |
| `asset_id` | uuid | **FK** → `assets.id` |
| `location_id` | uuid | **FK** → `locations.id` |
| `wo_code` | varchar(150) | Auto-generated `WO-000123` if omitted |
| `title` | varchar(255) | **REQ** |
| `description`, `problem`, `solution` | text | |
| `priority` | varchar(50) | Defaults `medium`. `highest`, `high`, `medium`, `low` |
| `status` | varchar(50) | Defaults `open`. `open`, `in_progress`, `closed` |
| `maintenance_type` | varchar(100) | |
| `maintenance_plan_id` | uuid | **FK**. Links the work order back to its PPM plan |
| `assigned_technician` | uuid | **FK** → `technicians.id` |
| `assigned_vendor` / `vendor_id` | uuid | **FK** → `vendors.id` / *(link)* |
| `sla_id` | uuid | **FK** → `sla_policies.id` |
| `sla_due_at` | timestamp | |
| `estimated_hours`, `actual_hours`, `labour_hours` | numeric(10,2) | |
| `estimated_cost`, `actual_cost`, `parts_cost` | numeric(14,2) | |
| `reported_at`, `responded_at`, `attended_at`, `completed_at`, `closed_at` | timestamp | Drive response and resolution against SLA |
| `first_fix`, `recall`, `return_visit` | boolean | Vendor performance signals |
| `conflict_flag` | boolean | **REQ**. Write `false` |
| `contract_id` | uuid | *(link)* → `vendor_contracts.id` |

`conflict_flag` is `NOT NULL` with no default. It is the most common insert failure on this
table.

### 5.4 `work_order_tasks`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `work_order_id` | uuid | **REQ**, **FK** |
| `title` | varchar(255) | **REQ** |
| `description` | text | |
| `sort_order` | integer | Defaults 0 |
| `assigned_to` | uuid | **FK** → `technicians.id` |
| `estimated_hours`, `actual_hours` | numeric(10,2) | |
| `status` | varchar(50) | Defaults `pending` |
| `completed_at` | timestamp | |

### 5.5 `work_order_parts`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `work_order_id` | uuid | **REQ**, **FK** |
| `part_id` | uuid | **REQ**, **FK** → `spare_parts.id` |
| `asset_id` | uuid | **FK** |
| `quantity_used` | integer | **REQ** |
| `unit_cost` | numeric(18,2) | |
| `fitted_at`, `warranty_expiry` | date | |
| `invoiced_value` | numeric(14,2) | |

### 5.6 `spare_parts`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `part_name` | varchar(255) | **REQ** |
| `part_code` | varchar(150) | |
| `unit_price` | numeric(18,2) | |
| `stock_quantity` | integer | Defaults 0 |
| `reorder_level` | integer | Defaults 0. Below this is a reorder signal |
| `supplier_id` | uuid | **FK** → `vendors.id` |
| `unit_of_measure` | varchar(50) | |

### 5.7 `technicians`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `user_id` | uuid | **REQ**, **FK** → `users.id`. A technician **is** a user |
| `base_location` | varchar(255) | |
| `availability_status` | varchar(50) | |
| `performance_score` | numeric(10,2) | |

### 5.8 `sla_policies` and `vendor_contracts`

| `sla_policies` | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `name` | varchar(150) | **REQ** |
| `priority` | varchar(50) | **REQ**. Matched against `work_orders.priority` |
| `response_time_minutes` | integer | **REQ** |
| `resolution_time_minutes` | integer | **REQ** |

| `vendor_contracts` | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, **REQ** |
| `organization_id` | uuid | **REQ**, **FK** |
| `vendor_id` | uuid | **REQ**, **FK** |
| `contract_name` | varchar(255) | **REQ** |
| `contract_start`, `contract_end` | date | |
| `contract_value` | numeric(18,2) | |
| `sla_terms` | text | **`text` on this database, not jsonb** |
| `country_code` | text | |
| `service_scope` | text | |
| `visits_per_year` | integer | |

A contract has no building column. It reaches a building only through the document it was
filed against, which is why a portfolio contract is uploaded once per building.

---

## 6. Minimum row set for one end-to-end pass

Per building, this is the least that makes all three pages render something real.

| Table | Rows | Why that many |
|---|---|---|
| `buildings` | 1 | |
| `locations` | 1 | Carries `country_code`, which picks the benchmark |
| `floors` | 3+ | Basement, ground, one occupied level |
| `building_sections` | 4-5 | One per use, each with its own reference |
| `asset_categories` | 1 per class | |
| `vendors` | 3+ | Mechanical, lifts, fire |
| `assets` | 10+ | A spread of condition grades. All green proves nothing |
| `asset_reading_bands` | 15 | Global rows, one per reading type |
| `asset_readings` | 24 per metric per instrumented asset | The sparkline reads the last 24 |
| `energy_meters` | 2 | One electricity, one gas |
| `meter_readings` | 17,520 per meter | Half-hourly for a year. The score counts only months holding readings |
| `spare_parts` | 5+ | |
| `sla_policies` | 1 per priority | |
| `maintenance_plans` | 3+ | Mixed frequencies, one already overdue |
| `work_orders` | 15+ | Mixed status and priority, some closed with real durations |
| `work_order_tasks` | 2-4 per open order | |

Then press **Run energy scan**. That writes `eui_snapshots` and `energy_anomalies`, which is
what turns the EUI, benchmark, deviation and anomaly figures from blank into numbers.

---

## 7. The failures that cost the most time

**A reading that belongs to nothing.** `meter_readings` and `asset_readings` have no building
column. Ingest before the meter or asset is placed on a building and the rows are stored,
counted by nothing, and invisible on every page.

**A plain uuid that points nowhere.** Most of the building links are plain `uuid` columns with
no constraint. A typo inserts cleanly and the row vanishes from the page. Verify with a join,
not with a row count.

**A generated column in an insert.** `assets.criticality_level` is generated. Including it
fails with `cannot insert a non-DEFAULT value`.

**A missing `conflict_flag`.** `NOT NULL`, no default, on `work_orders`.

**Two placeholders of different types.** `assets.criticality` is `varchar` and
`criticality_level` is `text`. Reusing one parameter for both gives
`inconsistent types deduced for parameter`.

**Readings with no band.** `reading_type` is free text with no constraint. A metric with no
matching band grades as unknown and drops out of the failure denominator, quietly shrinking
the card rather than erroring.

**A year ingested, a month reported.** Every anomaly rule reads a 35-day window. Sweep the
history rather than scanning once.

---

## 8. Scripts that already do this

| Script | Writes |
|---|---|
| `db/tools/seed_northbridge_assets.py` | floors, sections, categories, vendors, assets, bands |
| `db/tools/refresh_asset_readings.py` | `asset_readings`, on the half hour, for ever |
| `db/tools/seed_asset_intelligence.py` | prices a register that already exists |
| `db/tools/seed_new_company.py` | a whole company, including buildings |

Both of the first two refuse to run against `plenum_agent` and default to a dry run.

```bash
python db/tools/seed_northbridge_assets.py --db hoistra_test --apply
python db/tools/refresh_asset_readings.py --db hoistra_test --backfill 2 --apply
python db/tools/refresh_asset_readings.py --db hoistra_test --loop --apply
```
