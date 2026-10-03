---
name: udr-assets
description: The Assets page's data - the asset register, its building, category, criticality, condition grade and inspections, open work orders, vendor, and life and value. Loaded after the UDR skill.
---

# Assets

The Assets page lists one row per asset the caller may see, with:
- its building and zone, category, criticality and vendor;
- its condition grade, with the inspection that set it, and the notes from its service reports;
- its open work orders.

The register is yours. The band (Threat / Watch / In control, set by energy against its
section) is energy's (see §5).

Columns named here are what `hoistra_test` holds on 30 Sep 2026. `plenum_agent`'s `assets`
differs:
- It has none of `building_code`, `vendor_name`, `maintained_by`, `site_name` or `section_name`.
- It does have `document_ids`, `location`, `category` (text) and `asset_type`.

**Run `table_card("assets")` before you select a column from this file.**

---

## 1. The tables

| Table | Grain | Key | What to read |
|-------|-------|-----|--------------|
| `assets` | one asset | `id` (uuid) | `asset_code` ("B-301-CHILLER-101"), `asset_name`, `building_id`, `building_code`, `section_id` / `section_name`, `category_id`, `criticality`, `criticality_level`, `status`, `is_online`, `condition_score`, `condition_updated_at`, `condition_provenance`, `health_score`, `vendor_id` (text), `vendor_name`, `maintained_by`, `manufacturer` / `make` / `model` / `model_number`, `serial_number`, `installation_date`, `warranty_expiry`, `design_life_years`, `replacement_value`, `replacement_currency`, `parent_asset_id` |
| `asset_categories` | category tree | `id` | `name` (for example "HVAC · Chillers"), `parent_id` |
| `inspections` | one graded service report | `id` | `asset_id`, `asset_code`, `inspection_date`, `inspector`, `section`, `finding_type` ("Condition grade 3"), `risk_level` (Low / Medium / High), `observations` (findings separated by `; `), `recommendation`, `corrective_action`, `work_order_id`, `converted_work_order_id` |
| `work_orders` | one job | `id` | `asset_id` (uuid), `asset_code`, `wo_code`, `title`, `status`, `priority`, `wo_type`, `vendor_name`, `raised_at`, `sla_due_at`, `completed_at` |
| `asset_criticality` | the criticality approval register | `id` | `asset_id`, `criticality`, `proposed_criticality`, `approved`, `rationale`. This is a proposal register, not the asset's criticality. |
| `asset_readings` | one reading | `id` | `asset_id`, `reading_type`, `value`, `unit`, `recorded_at` |
| `energy_meters` | a meter on the asset | `id` | `asset_id`, `meter_type`, `is_sub_meter`, `section_id` |
| `vendors` | the maintainer | `id` | Joins on `assets.vendor_id = vendors.id::text` |

**Empty in hoistra_test:**
- `asset_warranties`, `asset_documents`, `maintenance_history`, `asset_offline_log`,
  `work_order_assets` and `asset_types` have no rows. For a question they would answer, say
  "not recorded" and use what does exist:
  - warranty: `assets.warranty_expiry`
  - work history: `work_orders` for the asset
  - downtime: `is_online`
- `health_score` is null on every asset. Say "not scored". A null is not a pass.

---

## 2. Reading the fields right

**Criticality**
- `criticality` is stored lowercase (`high` / `medium` / `low`). `criticality_level` is the same
  thing as L1 / L2 / L3.
- Show it as "High (L1)".
- `asset_criticality.proposed_criticality` is a pending change, not the current value.

**Condition grade**
- This is a 1–5 grade from the asset's service reports. In this data **a higher grade is worse**:
  - Grade 1 and 2 inspections are Low risk.
  - Grade 3 is Medium.
  - Grade 4 is High.
- Never call a grade "good" or "poor" from the number alone. Quote it with the risk level and
  the date of the inspection that set it:
  "Grade 4 (High risk), inspected 11 Jul 2026".
- `condition_score` on the asset should equal the grade of its latest graded inspection. When
  they differ, give both, and say which is newer.
- `finding_type = 'Condition'` with no number is an ungraded report. It has findings but no
  grade.

**Service report notes**
- `observations` splits on `; `. List the findings, not the raw string.

**Open work orders**
- Open means `lower(status) NOT IN ('completed','closed','cancelled')`.
- The live spellings include "In progress", "Scheduled", "Blocked", "Held", "Draft" and
  "pending_approval". Show each one as it is stored.
- Match an order to an asset on `asset_id`, or on `asset_code` when `asset_id` is null.

**Life and value**
- Age is `installation_date` to today.
- The asset is past design life when `installation_date + design_life_years` is before today.
- Always show `replacement_value` with its `replacement_currency`.

**Every asset is shown with its building:** "CHILLER-101 (Bishopsgate Tower)".

---

## 3. Recipes

### "What's in the asset register?": the headline
```sql
SELECT b.building_code, b.name AS building, c.name AS category,
       COUNT(*) AS assets,
       COUNT(*) FILTER (WHERE a.criticality = 'high') AS high_criticality,
       COUNT(*) FILTER (WHERE a.condition_score >= 3) AS grade_3_or_worse
FROM plenum_cafm.assets a
JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
LEFT JOIN plenum_cafm.asset_categories c ON c.id = a.category_id
GROUP BY b.building_code, b.name, c.name
ORDER BY b.building_code, assets DESC
```
The total, and the high-criticality count, come from a separate `COUNT(*)`, not from adding up
the rows.

### "Complete information on CHILLER-101"
1. Run `find_asset("CHILLER-101")` to get the row.
2. Get the latest graded inspection and the one before it:
```sql
SELECT inspection_date, inspector, finding_type, risk_level, observations, recommendation, work_order_id
FROM plenum_cafm.inspections
WHERE asset_id = $1
ORDER BY inspection_date DESC
LIMIT 2
```
3. Get its open work orders:
```sql
SELECT wo_code, title, status, priority, vendor_name, raised_at, sla_due_at
FROM plenum_cafm.work_orders
WHERE (asset_id = $1 OR (asset_id IS NULL AND asset_code = $2))
  AND lower(status) NOT IN ('completed','closed','cancelled')
ORDER BY raised_at DESC
```
4. Answer under these headings:
   - `## Asset` (every field returned)
   - `## Condition` (the grade, its risk and its date, with the trend from the previous report)
   - `## Service report` (the findings as a list, and the recommendation)
   - `## Open work orders`
   - `## Maintainer` (vendor and trade)

   Add `## Energy` only when energy returned something (§5).

### "Which critical assets are in poor condition?"
"Poor" means the latest graded inspection is grade 3 or worse, or its risk level is High:
```sql
SELECT DISTINCT ON (a.id)
       a.asset_code, a.asset_name, b.name AS building, a.criticality, a.criticality_level,
       i.finding_type, i.risk_level, i.inspection_date
FROM plenum_cafm.assets a
JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
JOIN plenum_cafm.inspections i ON i.asset_id = a.id AND i.finding_type ILIKE 'Condition grade%'
WHERE a.criticality = 'high'
ORDER BY a.id, i.inspection_date DESC
```
Then filter that result on the grade or risk level. Filtering inside the query would keep an
older bad report, when the asset has been re-graded since.

### "Which assets have open work orders?"
Group `work_orders` (open) by `asset_code`. Join to `assets` for the name and building. List
the order codes for each asset. An order whose asset code is not in the register is shown with
"not in the asset register".

### "What's past its design life?"
```sql
SELECT a.asset_code, a.asset_name, b.name AS building, a.installation_date, a.design_life_years,
       (a.installation_date + make_interval(years => a.design_life_years::int)) AS end_of_life,
       a.replacement_value, a.replacement_currency
FROM plenum_cafm.assets a
JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
WHERE a.installation_date IS NOT NULL AND a.design_life_years IS NOT NULL
  AND a.installation_date + make_interval(years => a.design_life_years::int) < CURRENT_DATE
ORDER BY end_of_life
```
Assets with no installation date are counted and named as "age unknown". They are never
treated as within their life.

---

## 4. The page's words

The page uses these words, and the answer should too:
- **Condition**: "Grade N · risk".
- **Band**:
  - Threat: health below 40
  - Watch: health 40 to 69
  - In control: health 70 or more
  - Not scored: no health score

  In hoistra_test every asset is "Not scored", because `health_score` is empty.
- **Open WO**: the order code, its status and its vendor.

---

## 5. Hand off, do not compute

| The question is really | Hand to |
|------------------------|---------|
| Threat / Watch band, energy against its section, anomalies, "why is the chiller costing so much", value at risk | `energy_intelligence` (`list_asset_conditions`, `cross_ref_condition_consumption`) |
| The asset's certificates (LOLER, gas safety, F-gas) and whether they are current | `compliance` |
| How its vendor is performing, and the SLA on its orders | `contract_performance` |
| Raise, approve, reassign or close a work order on it | `wo_engine` |
| What its O&M manual or service report PDF says | `doc_rag` |

The energy engine's condition rules treat a **low** condition score as poor, which is the
opposite of the inspection grades in this data. When you give an answer from both, give the
inspection grade with its risk level. Name the disagreement; do not reconcile it yourself.

---

## 6. Never

- Never call a grade good or bad without its inspection's risk level and date.
- Never read a null `health_score` or a missing inspection as "in good condition".
- Never take criticality from `asset_criticality.proposed_criticality`.
- Never say there is no warranty, document or downtime for an asset when the table is empty.
  Say it is not recorded.
- Never show an asset without its building, or a raw uuid unless it was asked for.
- Never state a count that SQL did not return.
