# Hoistra skill — Buildings and the Hoist Graph

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Buildings

Two entries, one page, driven by `role`.

### 6.1 Buildings (Admin)
- **Hoist Graph** — organic cluster: five building hubs, shared nodes for vendors and regulation packs, direct vs indirect relationships by line style, marker-yellow badge where unstructured files are bound to a table by similarity. Click to expand a node; per-building hierarchy tree beneath (building → floors → assets → documents, with vectorised files shown as marker chips).
- **Hoist a building** — orchestrator declare flow.
- **Update the graph** — NLP instruction to re-bind or correct.
- **Documents** — structured (tables and rows) and unstructured (vectorised and bound), per building, with reconciled counts.
- **Export canonical table** — with snapshot history. Columns: Building ID · Name · Country · State · Use / floor-area split · Floors · Floor area · EUI · Benchmark · EUI vs benchmark · Benchmark standard · Hoist Score.
  - Under **EUI**: the data route and granularity — *HH data collector · LoA*, *SMETS2 · SEC intermediary*, *Green Button CMD · aggregator*, *Retailer feed · contracted*, *Own sub-meters + BMS* — with *building-level · inferred* in grey where attribution is not measured.
  - Under **Benchmark standard**: legal standing, coloured by tone — UK "guidance · EPC E law, EPC B proposed 2031", US "enacted, city-scoped · NYC LL97", SG "submission mandatory · rating voluntary", AE "no operational standard · portfolio benchmark".

### 6.2 Buildings (User)
Same graph and data, read-only, showing only documents that user ingested. Carries the ask bar. No admin controls.


---

## Data model — the Hoist Graph

One table per node, keyed on a natural key, joined by foreign keys. Core tables: `building`, `floor`, `space`, `asset`, `equipment`, `meter_reading`, `work_order`, `document`, `certificate`, `contract`, `invoice`, `vendor`. Tables created by connectors: `service_charge_line`, `lease`, `purchase_order`, `cost_centre`, `gl_posting`, `ppm_schedule`, `occupancy`, `news_item`, `regulation_signal`, `tenant_billing`. Unstructured files are vectorised and bound to a column by similarity (marker yellow).

**Provenance carried on records**
- Meter reading: route (data collector under LoA · SEC intermediary · Green Button CMD · retailer feed · own sub-meters/BMS) and granularity (sub-metered · building-level, inferred)
- Regulation pack: legal standing (enacted · guidance · proposed · no operational standard)
- Contract term: source (clause and page) or platform default
- Rating: basis (certificate · filing · consumption) and months of data → Actual / Projected / Insufficient

**Market profiles** (`EN_PROFILE`, `ENC`, `PACKS` in `Hoistra.dc.html`): per country — benchmark standard and reference, unit (kWh/m², kBtu/ft², RTh), data route and refresh, per-meter/per-section availability, identifier (MPAN/MPRN · utility account + service point · SP account + meter · DEWA premise), known limits, electricity and gas price and billing unit, currency.

