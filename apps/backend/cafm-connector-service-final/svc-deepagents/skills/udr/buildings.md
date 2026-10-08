---
name: udr-buildings
description: The Buildings page's data - the building register, floors, sections and spaces, what each building holds, and who may see it. Loaded after the UDR skill.
---

# Buildings

The Buildings page lists one row per building the caller may see: its code, name, use, floors,
floor area, country, EUI against its benchmark, and the counts of what it holds. Every one of
those comes from the tables below except EUI and benchmark, which the energy engine computes
(see §5).

Columns named here are what `hoistra_test` holds on 30 Sep 2026. `plenum_agent` has fewer:
its `buildings` has no `country_code`, `gross_internal_area_m2` or `site_name`, and it holds no
`floors` rows at all. **Run `table_card("buildings")` before you select a column from this
file.** A column that is not on the card is not in this deployment.

---

## 1. The tables

> `buildings.hoist_score` is a recorded override and is usually empty. The Hoist Score itself is
> derived (assets, compliance, contracts, energy, maintenance on file, 20 points each) and comes from
> the energy agent's `get_hoist_score`. Never report a building's score as "not recorded"
> because this column is empty.

| Table | Grain | Key | What to read |
|-------|-------|-----|--------------|
| `buildings` | one building | `building_id` (uuid; **there is no `id` column**) | `building_code` ("B-301"), `name`, `primary_use` (an enum; cast with `::text`), `floors` (int, the recorded count), `gross_area_sqft`, `gross_internal_area_m2` (**varchar**; cast with `NULLIF(…,'')::numeric`), `eui_kwh_m2` (often null), `hoist_score`, `country_code`, `organization_id`, `site_id` (text, links to `sites`) |
| `sites` | the CMMS site record behind a building | `site_id` (varchar) | `site_name`, `city`, `postcode`, `address`, `manager`, `manager_email`, `region`, `use_type`, `use_mix` (jsonb list of `{use, pct}`), `metering_granularity`, `benchmark_standard`, `benchmark_standing` |
| `floors` | one floor | `floor_id` | `building_id`, `level` (int; 0 is ground, below 0 is basement), `name`, `gross_area_sqft` |
| `building_sections` | a zone on a floor (plant room, tenant floor, common area) | `section_id` | `building_id`, `floor_id`, `floor_name`, `name`, `section_type`, `gross_area_m2`, `reference_eui_kwh_m2` |
| `spaces` | a room or tenancy | `space_id` | `floor_id`, `building_id`, `space_type`, `tenant_ref`; **usually empty**, so say "no spaces recorded" rather than zero rooms |
| `locations` | the CMMS location tree | `id` | `building_id`, `parent_location_id`, `level`; one row in hoistra_test |
| `user_buildings` | who may see a building | (`user_id`, `building_id`) | The allocation. The service already narrows every read to it. Never widen a query past what came back. |

Joins:
- `buildings.site_id = sites.site_id` (text to varchar). Every building in hoistra_test has its site.
- `sites.organization_id` is an **integer** in hoistra_test and does not join to
  `organizations.id`. Take the company from `buildings.organization_id`.

---

## 2. What a building holds

Every one of these tables carries `building_id`, so these counts need no chain through sites
or locations:

| Holding | Table and filter |
|---------|------------------|
| assets | `assets.building_id` |
| work orders | `work_orders.building_id`. Open means `lower(status) NOT IN ('completed','closed','cancelled')`. The stored spellings are mixed ("Completed", "In progress", "pending_approval"), so always compare with `lower()`. |
| certificates | `compliance_certificates.building_id`. The count by stored status is yours; whether a certificate is *really* current is compliance's call (§5). |
| meters | `energy_meters.building_id`; `is_sub_meter` separates main meters from sub-meters |
| contracts and invoices | `contracts.building_id`, `invoices.building_id` |
| documents | `documents.building_id` |
| floors and sections | `floors.building_id`, `building_sections.building_id` |

**Assets never join through `sites`.** `assets.site_id` is a uuid that is empty on every row in
hoistra_test, while `sites.site_id` is a varchar. A count joined that way returns zero for every
building. Key on `assets.building_id`.

---

## 3. Floor area

Three figures, in two units, from three places:
- `buildings.gross_area_sqft`: square feet.
- `buildings.gross_internal_area_m2`: square metres, stored as text.
- The sum of `floors.gross_area_sqft`.

Rules:
- Say which one you quote, and its unit.
- 1 m² = 10.7639 sq ft. Never print square feet under an m² label.
- The floor sum and the building figure can disagree. When both exist and differ by more than
  5%, give both.
- `buildings.floors` is the recorded number of floors. `COUNT(floors)` is how many floors have
  rows. When they differ, say so: Bishopsgate records 22 and has 22 rows; Ashgrove Court
  records 9 and has 9 rows but no assets.

---

## 4. Recipes

### "Tell me about Bishopsgate Tower": the profile
1. Resolve the building. `find_location("Bishopsgate")`, or match `buildings.name ILIKE` /
   `building_code =`. Resolve it to a `building_id`.
2. Pull the profile and every holding in one statement:
```sql
SELECT b.building_code, b.name, b.primary_use::text AS primary_use, b.floors AS floors_recorded,
       b.gross_area_sqft, NULLIF(b.gross_internal_area_m2, '')::numeric AS gia_m2, b.country_code,
       s.city, s.postcode, s.manager,
       (SELECT COUNT(*) FROM plenum_cafm.floors f WHERE f.building_id = b.building_id)            AS floor_rows,
       (SELECT COUNT(*) FROM plenum_cafm.building_sections x WHERE x.building_id = b.building_id) AS sections,
       (SELECT COUNT(*) FROM plenum_cafm.assets a WHERE a.building_id = b.building_id)            AS assets,
       (SELECT COUNT(*) FROM plenum_cafm.work_orders w WHERE w.building_id = b.building_id
          AND lower(w.status) NOT IN ('completed','closed','cancelled'))                          AS open_work_orders,
       (SELECT COUNT(*) FROM plenum_cafm.compliance_certificates c WHERE c.building_id = b.building_id) AS certificates,
       (SELECT COUNT(*) FROM plenum_cafm.energy_meters m WHERE m.building_id = b.building_id)     AS meters
FROM plenum_cafm.buildings b
LEFT JOIN plenum_cafm.sites s ON s.site_id = b.site_id
WHERE b.building_id = $1
```
3. Answer in this order: one sentence of what and where, then a two-column table of every field.
   Leave EUI to energy, and say so if it was asked.

### "Compare our buildings": the portfolio
Use the same subqueries with no `WHERE`, ordered by `building_code`. The headline is how many
buildings there are and how many hold no assets. In hoistra_test that is 5 buildings, 3 with no
assets. A building with no assets is empty in the register; do not call it idle.

### "What floors and zones does B-301 have?"
```sql
SELECT f.level, f.name AS floor, f.gross_area_sqft,
       COUNT(x.section_id) AS sections,
       STRING_AGG(x.name, ', ' ORDER BY x.name) AS zones
FROM plenum_cafm.floors f
LEFT JOIN plenum_cafm.building_sections x ON x.floor_id = f.floor_id
WHERE f.building_id = $1
GROUP BY f.level, f.name, f.gross_area_sqft
ORDER BY f.level
```
Sections can carry a `floor_id` that is null (roof plant, a riser). List those after the floors,
as "not on a floor".

---

## 5. Hand off, do not compute

| The question is really | Hand to | Why yours would be wrong |
|------------------------|---------|--------------------------|
| EUI, benchmark, "is it efficient", consumption, cost | `energy_intelligence` | EUI is metered over a period, on main meters only (sub-meters are excluded), against the country's pack. `buildings.eui_kwh_m2` is a stored figure, usually null. |
| Is the building compliant, and what is lapsed or missing | `compliance` | Status needs expiry against today and the country pack. A stored status can be stale. |
| How the building's vendors are performing | `contract_performance` | |
| Raise, approve or chase a work order | `wo_engine` | |

---

## 6. Never

- Never join assets to buildings through `sites`. Use `building_id`.
- Never quote an area without its unit and its source.
- Never report a building's EUI from `buildings.eui_kwh_m2` as its performance.
- Never show a building from outside the caller's allocation, and never show a raw `building_id`
  unless it was asked for. Use the code and name.
- Never state a count that SQL did not return.
