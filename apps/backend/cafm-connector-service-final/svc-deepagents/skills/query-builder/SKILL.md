---
name: query-builder
agent: shared
shared: true
description: The shared question-to-tables-to-answer discipline every CAFM sub-agent inherits. Loaded ahead of each agent's own skill. Covers resolving names to keys, reading the live schema before naming a table, pulling from one or more tables, joining across domains, and the shape of the final summary.
triggers: []
---

# Query Builder — the loop every agent runs

A user asks a question in their own words. Your job is to turn it into **keys**, then into
**rows**, then into **an answer**. Five steps, in this order, every time.

```
RESOLVE  →  LOCATE  →  PULL  →  JOIN  →  SUMMARISE
 names       schema     rows    across      answer
 to keys     names              tables
```

Skipping RESOLVE is how you end up querying for the literal string the user typed.
Skipping LOCATE is how you get `undefined column` and report "no data" for data that exists.

---

## Step 1 — RESOLVE: turn the nouns into keys

The user says "the Daresbury chiller", "AIB Solutions", "Tower A". None of those is a key.
Resolve every named entity **before** you filter on it.

| The user named | Resolve with | You get back |
|----------------|--------------|--------------|
| An asset — any code, name, barcode, serial | `find_asset(identifier)` | the asset row incl. `id`, `asset_code`, `site_id`, `document_ids` |
| A site / building / location | `find_location(identifier)` | the site or location row incl. its key |
| A vendor / contractor / company | `query_table("vendors", {...})` or a `LIKE` select on `vendor_name` | `vendors.id`, `vendor_name`, `block_state` |
| A user / approver / engineer | `lookup_user(user_id)` | name, email, department, roles |
| A work order | `get_work_order(work_order_id)` / `list_work_orders(...)` | the WO row |

Rules:

- **Never assume which column holds the value.** `AST-AHU-601` may live in `id`, `asset_code`,
  `barcode` or `serial_number`. `find_asset` searches all of them. A hand-written
  `WHERE asset_code = 'AST-AHU-601'` finds nothing when the value sits in `id`.
- **Only report "not found" after the resolver returns zero.** Zero rows from your own
  hand-written filter is not evidence of absence.
- If a name resolves to **more than one** row, do not pick one. List the candidates and ask
  which they meant — unless the question is an aggregate, in which case use all of them and
  say so.

---

## Step 2 — LOCATE: read the live schema, never the model file

**Call `get_schema()` once per session before your first query.** It returns every table and
every column that exists *right now*. That is the only source of truth.

This matters more here than in a normal codebase, because **the live `plenum_cafm` schema does
not match the SQLAlchemy models.** Real differences you will hit:

| You would assume | Live schema actually has |
|------------------|--------------------------|
| `assets.id UUID` | `assets.id varchar(50)` |
| `assets.location_id UUID` | `assets.site_id varchar(50)` **and** `location_id integer` |
| `organization_id UUID` | `organization_id integer` |
| a `sites` table keyed on `id UUID` | `sites` keyed on `site_id varchar(50)` |
| `spare_parts.stock_on_hand` | `spare_parts.stock_quantity` |
| `assets` has no document link | `assets.document_ids jsonb` |

So:

- Pick table and column names **out of the `get_schema()` result**, never out of memory.
- On `undefined table` or `undefined column`, call `get_schema()` again and retry once with
  the corrected name. Do not retry a third time — say what failed.
- `udr_describe_table(table)` gives one table's columns, PK and FKs when you need detail.

---

## Step 3 — PULL: most specific tool first, SQL last

Reach for tools in this order. Going straight to raw SQL when a domain tool exists is how
answers drift from what the UI shows.

1. **A domain tool that answers exactly this question** — `count_compliance_certificates`,
   `get_dashboard_stats`, `list_vendor_scorecards`, `compute_site_eui`. These carry the
   engine's own business rules. Their number *is* the answer; do not recompute it.
2. **A structured read** — `query_table(table, filters)` (equality filters, max 100 rows),
   `udr_read_records(...)` for paging/sorting, `udr_search_records(...)` for text search.
3. **`udr_execute_select(sql, params)`** — for anything needing a JOIN, GROUP BY, aggregate,
   date arithmetic, or more than equality. Parameterise every value:

   ```sql
   SELECT a.asset_code, a.asset_name, COUNT(w.id) AS open_wos
   FROM plenum_cafm.assets a
   LEFT JOIN plenum_cafm.work_orders w
     ON w.asset_id = a.id AND w.status NOT IN ('completed', 'closed')
   WHERE a.site_id = :site_id
   GROUP BY a.asset_code, a.asset_name
   HAVING COUNT(w.id) > 0
   ORDER BY open_wos DESC
   ```
   with `params = {"site_id": "<resolved key>"}`.

4. **`answer_with_graph_context(question)`** — when the question is about an entity **and
   everything around it** ("complete information on AHU-601", "everything for this vendor").
   It walks the PK/FK graph and returns the related rows plus document chunks in one call.
   Call it *first* for that shape of question, then fill gaps with targeted queries.

Never interpolate a user-provided string into a table or column name. Values are parameters;
identifiers come from `get_schema()`.

---

## Step 4 — JOIN: one query inside a domain, in-memory across domains

**Inside `plenum_cafm`** — join in SQL. One round trip, one consistent snapshot.

**Across engine boundaries** (compliance ↔ work orders ↔ energy ↔ documents) — pull each side
with its own domain tool, then join in your head on the key below. Do not try to SQL-join
across data that a domain tool owns, or your figures will disagree with the dashboard.

### The join key map

| From | Key | To |
|------|-----|-----|
| `assets.id` | `asset_id` | `work_orders`, `maintenance_plans`, `asset_readings`, `work_order_parts`, `asset_criticality`, `asset_condition_scores`, `energy_meters`, `energy_anomalies`, `compliance_certificates` |
| `assets.site_id` | site key | `sites.site_id`, `energy_meters.site_id`, `building_energy_profiles.site_id` |
| `assets.document_ids[]` | document id | `ingestion_documents.id` → `document_chunks.document_id` |
| `vendors.id` | `vendor_id` | `vendor_contracts`, `compliance_certificates`, `contract_sla_parameters`, `vendor_wo_scores`, `vendor_monthly_scorecards`, `invoice_verifications`, `invoice_lines`, `ppm_visits`, `resource_skills` |
| `work_orders.id` | `work_order_id` | `work_order_tasks`, `work_order_parts`, `work_order_history`, `vendor_wo_scores`, `ppm_visits`, `invoice_lines` |
| `energy_meters.id` | `meter_id` | `meter_readings`, `meter_reading_gaps`, `energy_anomalies` |
| `spare_parts.id` | `part_id` | `work_order_parts`, `inventory_transactions` |
| `ingestion_documents.id` | `document_id` | `document_chunks`, `compliance_certificates.document_id`, `contract_documents.document_id` |

### Two site-key traps — read before joining on a site

1. Phase 2 tables (`compliance_certificates`, `energy_meters`, `eui_snapshots`,
   `building_energy_profiles`) declare `site_id` as **UUID**, but the live `sites` table is
   keyed on **`site_id varchar(50)`**. A UUID-to-varchar join silently returns nothing.
2. Because of that, `compliance_certificates` carries **`site_ref varchar(120)`** for the
   varchar-keyed case, and `building_name` for certificates that name a building no site row
   matches. To group compliance by building, bucket on
   `site_id` → else `site_ref` → else normalised `building_name`. Never assume one column.

When a join comes back empty, check the key *type* before you report "no data".

---

## Step 5 — SUMMARISE: answer, then evidence

Every answer follows this shape:

1. **The answer, first line.** Not "I queried the assets table." Not a preamble.
   `"9 of 27 building certificates are lapsed."`
2. **Counts before rows.** `"17 open WOs: 4 urgent, 8 high, 5 medium."` then the detail.
3. **A markdown table** when there is more than one record with more than one field.
   Only the columns the question asked about — not every column you pulled.
4. **Every number traceable.** Each figure came from a tool result. If you filtered rows in
   your head, the count you state must equal the rows you listed.
5. **Sources when documents were used** — `- [file name](doc:<id>)` under `## Sources`.
   Never a bare UUID, never a `(#)` placeholder.
6. **What to do next**, when the answer implies an action (a lapsed certificate, a zero-stock
   part, an overdue PM). One or two lines, with the in-app link — `[Open Compliance](/compliance)`.

And the hard limits:

- **Never invent a value.** No asset code, WO number, date, reading, cost or count that did
  not come back from a tool. If the data is not there, say it is not there.
- **Never state an action was taken** before the tool that takes it has returned.
- **Never pad a filtered list.** If the user asked for lapsed certificates, a row whose status
  is Current must not appear — reporting zero matches is the correct answer when zero match.
- **Flag disagreements.** If two sources give different numbers, show both and say which you
  trust and why. Do not quietly pick one.
