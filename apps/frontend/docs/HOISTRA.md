# Hoistra — platform component reference

The single reference for everything built in `Hoistra.dc.html` and its data files. It covers the product model, every screen and shared component, the data layer, the rules the design holds itself to, and what is still open. `README.md` documents the earlier Hoistway explorations (Options A, B, Edila) that this platform grew out of; this file documents the platform itself.

---

## 1. What Hoistra is

An AI-native property management platform for the person accountable for a portfolio they do not operate day to day. It reads the portfolio — documents, meter feeds, work orders, contracts, certificates — into one graph (the **Hoist Graph**), finds the exposure, prices it, and brings the decision to the user with the evidence attached. Existing systems stay; Hoistra ingests from them and writes back.

**Three principles every screen obeys**

1. **Every number sources its evidence.** A service credit names the work order, asset, building, metric and weight behind it. A score decomposes to the clause it was measured against. No orphaned figures.
2. **Nothing is asserted without disclosure.** A platform default is marked as a default. A proposal is never shown as a duty. A reading that arrived by inference is labelled inferred. A projection carries its months of data and a confidence.
3. **Query first, then read.** Every non-admin report opens with the ask bar above the analysis. The saved reports are conveniences over the query interface, not a separate product.

**The daily loop** (from the customer journey): Detect → Price → Decide → Dispatch → Verify. Verified outcomes re-enter the graph and sharpen the next detection.

---

## 2. Files

| File | Role |
| --- | --- |
| `Hoistra.dc.html` | The whole platform — template + logic class, one file |
| `hoistway-data.js` | Shared demo dataset: portfolio, certificates, vendors, work orders, anomalies, buildings, activity, answer set |
| `hoistra-compliance.js` | Compliance console data: countries → states → buildings, certificates, vendors and coverage |
| `hoistra-vendors.js` | Contract performance: weights, terms parsed from contracts, breaches, invoices, coverage |
| `hoistra-integrations.js` | Integrations: connected sources, catalogue by category, endpoints, field mapping |
| `hoistra-energy.js` | Energy: 13 detection rules, route-based arming, investigation scripts per anomaly type and for buildings |
| `hoistway.css` / `hoistway-electric.css` / `hoistway-nocturne.css` | Colour tokens (paper ground, orange accent, status set, marker yellow, dark theme) |
| `support.js` | Design Component runtime (do not edit) |

---

## 3. Visual system

Held as tokens in `hoistway.css`; pages never hard-code a colour.

- **Ground:** warm paper `#FEFDFB`, white panels, `#E7E4DB` rules. Dark theme is a true dark for the plant room.
- **Ink:** three levels only — primary, secondary, tertiary.
- **Accent:** hi-vis orange `#FF5A1F`, the only fill. One job: the live, the actionable, the primary button. Kept under ~3% of the surface.
- **Marker yellow** `#FFD400`: the document layer — unstructured files bound by similarity, platform defaults, tables that exist only because of a connector. Never carries type.
- **Status set** (separate from brand): critical, at-risk amber, healthy, dormant. Every one clears 4.5:1 on page and tint. Red means one thing.
- **Type:** body font from the design system, `ui-monospace` for every figure, identifier and table name. Tabular numerals on all numbers.
- **Surfaces:** cards on `--color-surface` with `--shadow-sm`, 10–11px radius, 3px status rail on the left of tiles. Tabs are a 2px underline. Pills are 11px on a tint.
- **Density:** 12–13px body in tables, 10–10.5px uppercase tracked labels for section headers, 24–28px for card values and page titles.

---

## 4. Shell and shared components

### 4.1 Sign-in gate
Email field + Continue. Session opens in **user view**.

### 4.2 Top bar
Logo → home · reporting currency (GBP · USD · AED · SGD) · **Pending** pill (decision queue count, pulsing) · tenant · orchestrator icon · **account avatar "A"**.

**Account menu (Aasim):** header line states the current mode ("User view · Planum Technologies"). Items: **Pricing**, **Support**, and a mode toggle that reads **Admin view** in user mode and **User view** in admin mode. **Sign out** as a separated last row. The click-away layer sits below the header's stacking context so menu rows stay clickable.

### 4.3 Navigator (left)
Collapsed rail (icons) or open panel (248px). Open panel shows: **New query** · **Reports** group · **Spaces** · **Sessions**.

Reports group is scoped by mode:
- **User view:** Buildings (User), Compliance, Vendors, Energy, Assets, Work orders, then the saved custom reports with their cadence badge.
- **Admin view:** Buildings (Admin), Integrations (Admin) · 15 min — only these.

Active state follows the actual page and role. Order: Vendors sits above Energy.

**Spaces:** the four built-in spaces — Compliance, Energy, Vendor performance, Vendor operations — carry live badges (lapsed certificates, open anomalies, vendors below 80, approvals pending; `—` until the engine answers) and open a space page: figures, the sessions filed there, an ask bar. **+** adds a saved space (svc-udr `saved_spaces`); saved spaces can be renamed and deleted from their page.
**Sessions:** every conversation with the orchestrator and every orchestrator task, newest first with a real elapsed time; a chat session reopens the conversation page on its transcript and continues the same thread, a task reopens the dock on its chain. **All sessions** opens the Sessions page (search, by day, delete, file in a space).

**+ New report:** build a saved report from a session — source (a recent session's question), refresh cadence (30 min · 1 hr · 6 hr · 12 hr · 24 hr · daily 02:00 · chosen days with a 7-day picker and time), name. The first refresh runs on creation; the badge is the cadence, or Pending / Running / Failed.

### 4.4 Ask bar (query first)
Sits directly under the breadcrumb on every non-admin report: Compliance, Vendors, Energy / Assets / Work orders modules, custom reports, Buildings (user view). Sparkle icon, page-scoped placeholder, **Ask** button, three suggested questions for that page. Enter or Ask runs through the query interface and lands on the answer view. Admin pages (Buildings admin, Integrations) do not carry it.

### 4.5 Orchestrator dock
Fixed left panel (280px; 420px during an investigation) opened by any action that makes the platform *do* something. Shows the task as intent, the **Orchestrator → Planner → Worker → Quality** chain playing in, then an armed flow:
- **booking**, **pick** (contractor swap), **new** (new vendor), **email** (draft with To / Subject / Body, Approve & send)
- **investigate** — the conversational investigation (see §9.6)

Every instruction is stored as a session. A free-text instruction box sits at the bottom.

### 4.6 Decision queue
Right drawer from the Pending pill: items needing approval, each with actions. Compliance route 1 lands here (risk card → pending drawer → action → orchestrator).

### 4.7 Detail drawer
Right drawer for any record (certificate, anomaly, vendor, work order): title, status meta, body, chain of thought, editable fields (accent = changeable, lock = fixed), one refinement suggestion, action buttons.

### 4.8 Toast
Bottom-centre confirmation stating what would be written.

---

## 5. Home and query

### 5.1 Home
The query input is the page. Beneath it: Portfolio P&L YTD (saved to date), Hoist Score, **Hoist Crons** live log (Compliance, Energy, Vendor, Orchestrator, Assets agents with timestamps and one-click actions), the four Spaces, pinned questions, recent sessions.

### 5.2 Answer view
Scope line · title · takeaway paragraph · caveat · four metric cards · ranked table (rows open the detail drawer) · chain of thought toggle · suggested follow-ups · actions. Answer set lives in `hoistway-data.js` (compliance, energy, vendors, ops).

Energy answer metrics: Portfolio EUI 194 vs 180 · Excess cost £312k · Anomalies 6 · **MEES — enforceable now: 0 below EPC E** · **MEES — proposed 2031: 2 below EPC B**. The caveat states which is law and which is a proposal (June 2026 interim response moved B from 2030 to 2031; the 2027 C milestone was dropped and is not scored).

---

## 6. Buildings

Two entries, one page, driven by `role`.

### 6.1 Buildings (Admin)
- **Hoist Graph** — organic cluster: five building hubs, shared nodes for vendors and regulation packs, direct vs indirect relationships by line style, marker-yellow badge where unstructured files are bound to a table by similarity. Click to expand a node; per-building hierarchy tree beneath (building → floors → assets → documents, with vectorised files shown as marker chips).
- **Hoist a building** / **Edit** — one modal, open to either role (`logic/buildingsCrud.js`): create sends the whole form as `POST /api/energy/buildings`; opening Edit on a row prefills every field (the primary-use enum verbatim, not the resolved TM46 category) and the submit sends only what changed as `PATCH /api/energy/buildings/{id}`, with `expected_updated_at` so a stale edit 409s instead of overwriting. **Remove** — `DELETE` without `confirm` reports what the building holds (detached, never deleted) before the dialog lets you confirm.
- **What it is costing** — the per-row graph drawer also reads `GET /api/energy/buildings/{id}/cost-drivers` (`logic/buildingsGraph.js`), ranked on the gap over contract; spend no work order names an asset for is shown separately, outside the ranking.
- **Update the graph** — NLP instruction to re-bind or correct.
- **Documents** — structured (tables and rows) and unstructured (vectorised and bound), per building, with reconciled counts.
- **Export canonical table** — with snapshot history. Columns: Building ID · Name · Country · State · Use / floor-area split · Floors · Floor area · EUI · Benchmark · EUI vs benchmark · Benchmark standard · Hoist Score.
  - Under **EUI**: the data route and granularity — *HH data collector · LoA*, *SMETS2 · SEC intermediary*, *Green Button CMD · aggregator*, *Retailer feed · contracted*, *Own sub-meters + BMS* — with *building-level · inferred* in grey where attribution is not measured.
  - Under **Benchmark standard**: legal standing, coloured by tone — UK "guidance · EPC E law, EPC B proposed 2031", US "enacted, city-scoped · NYC LL97", SG "submission mandatory · rating voluntary", AE "no operational standard · portfolio benchmark".

### 6.2 Buildings (User)
Same graph and data, read-only, showing only documents that user ingested. Carries the ask bar. No admin controls.

---

## 7. Compliance console

Scope cascade **country → state → building**, then:
- **Needs you** risk cards (lapsed, authenticity failed, inside 30 days, inside 90 days, building certificates, vendor certificates, all certificates, portfolio coverage) — each readable in **building view** or **vendor view** and routing to a different action.
- **Expiry runway** — every certificate in scope as a marker on a time axis with the 30/90-day bands.
- **Building and vendor pivots** with a focus record: building vault, vendor impact, not on record.
- **Route 2:** scope to a building → click a certificate → action → orchestrator.

Data in `hoistra-compliance.js`.

---

## 8. Vendors — Contract performance

- **Insights and actions** (six tiles): vendors blocked, pending critical, L1 breaches, invoice lines held, terms on default, plus a routed tile each — every tile opens its data.
- **Stats** (four cards): vendors by accreditation status, packages with single-point-of-failure flags, contracts by source coverage, commercial orders.
- **Vendor directory** with score, trend, coverage bar, and an accreditation cap line where one applies.
- **Scorecard** for the selected vendor. Every metric shows *contract requirement (clause) · measured · sample size*. Score is **capped by accreditation status** (Meridian 42/100, ceiling 60). Tabs:
  - **Evidence** — every breach with its weight multiplier (L1 = 3×) and the service credit it earns, summed as recoverable this period.
  - **Coverage** — which of the contract's terms were parsed (Apex 9 of 12, Meridian 4 of 18); platform defaults in marker yellow.
  - **Invoices** — lines checked against the rate schedule, mismatches flagged.
  - **Terms** — each term with source (clause and page, or *default*).

Data in `hoistra-vendors.js`.

---

## 9. Energy

The module with the most country-dependent behaviour. Everything below recomputes from the **scope**.

### 9.1 Scope row
All countries · 🇬🇧 United Kingdom · 🇺🇸 United States · 🇦🇪 UAE · 🇸🇬 Singapore, each with its building count; multi-select.

### 9.2 Fidelity banner
- **Single market · full fidelity** — names the benchmark and its legal standing, the data route, granularity and tariff.
- **Mixed portfolio · normalised metrics only** — states how many routes and benchmark bases are in scope; only metrics that survive the difference are shown.

### 9.3 Scope cards
Buildings in scope · EUI, area-weighted (delta against the market's standard, or "each building's own pack" when mixed) · Excess cost (local tariff, or converted to GBP when mixed) · Anomalies in scope with annualised impact.

**Excess cost vs anomaly impact are different numbers by design.** Excess = (EUI − reference) × area × tariff, a gap against an external standard, mostly structural. Anomaly impact = deviation from the building's own baseline, the actionable slice. They overlap and neither contains the other.

### 9.4 Ratings and duties
Always present. Single country: header shows the country. Multi: a dropdown of the countries in scope; cards swap on pick.
- **UK:** MEES enforceable now (0 below EPC E) · MEES proposed 2031 (2 below EPC B) · EPCs on file 6/6
- **US:** LL97 2024–29 limit (within) · Energy Star score 71 (certification at 75) · LL84 benchmarking filed
- **Singapore:** BCA energy submission filed · EUI vs BCA reference −8% · Green Mark none, voluntary
- **UAE:** Operational rating none (Estidama and Al Sa'fat rate new build only) · EUI vs rolling benchmark +8% · Chiller plant kW/RT

**Data-sufficiency badge on every card.** Certificates and filings are *Actual · from certificate/filing* on arrival. Consumption-based figures: *Actual · 12 of 12 months* at a full year; *Projected · n of 12 months · x% confidence* from 3 months (45% at 3, +5 per month); below 3 months the value is withheld and the card says so. A thin confidence bar sits under the badge.

### 9.5 Scope of this analysis
Two panels: **Available at this scope** and **Limits at this scope** (single market), or **Available across every market in scope** and **Not available across a mixed portfolio**. Beneath, the collapsible **Market profile** matrix — one column per market, rows grouped under *Benchmark* (standard, reference EUI, unit), *Data source* (route, refresh, per-meter/per-section, identifier, known limits) and *Commercial* (electricity, gas, other utilities, currency). Collapsed by default.

### 9.6 Buildings list (replaces the two tables)
Filters: All · Over benchmark · With anomalies · New · Above £20k. Under each market header (flag, name, standard, subtotal), one row per building sorted by excess + anomaly impact:
- line 1: caret, name, data route and granularity, **Investigate building**
- line 2: EUI bar against its own reference (marker at the reference), delta, excess cost, anomaly count

Open a building to see its anomalies beneath: asset, type, annualised, days active, status, **Investigate**. Bishopsgate Tower opens by default. Both Investigate buttons are always visible; rows wrap rather than clip.

**Investigate** opens the orchestrator as a conversation:
1. **You** — the question, posed for you ("Why is AHU-3 at Bishopsgate Tower showing a non-occupancy spike worth £38,400 a year, and what should I do about it?")
2. **Orchestrator** — the plan, then the tables walked live with row counts or a red *not found*: meter_reading, bms_trend, bms_audit_log, work_order, document, contract, ppm_schedule, occupancy, weather, utility_bill, emissions
3. **Worker** — findings as evidence cards with source table and confidence; missing records flagged
4. **Orchestrator** — cause and cost. If no finding clears 85% or a required record is missing: escalation — **Ask the FM lead why** (email draft with evidence attached; sending posts back into the thread) and **Plan an inspection** come first.
5. **Orchestrator** — actions as replies; each approval adds an exchange describing what was queued. Approve all · Not now.

Six scripts in `hoistra-energy.js`: building EUI vs pack (gap split into anomalies vs structural; capex case; re-benchmark on actual hours; schedule audit) and one per anomaly type — non-occupancy spike, single-asset spike, baseline drift, cooling load drift, schedule overrun.

### 9.7 Detection rules (collapsible)
13 rules, each with test, data needed, and **coverage** per building in scope (a rule that cannot arm on a building's route says so):
- Core: non-occupancy spike (>30% of occupied average) · single-asset spike · baseline drift week on week
- Added: schedule mismatch · baseload creep · calendar rule · weather-normalised residual (CUSUM) · peak demand excursion · simultaneous heating and cooling · chiller efficiency (kW/RT) · post-works regression · data-quality anomaly · time-of-use misalignment

### 9.8 Side copy and asks
Side title, footnote and the "Ask about this module" chips follow the selected market (UAE asks about chiller drift against cooling degree days; US about the LL97 cap and Energy Star 75; Singapore about the BCA submission and the retail data clause; mixed about which markets drive the cost).

---

## 10. Assets and Work orders
Present as modules with the generic layout (metric cards, filterable table, side bars, asks). Assets currently opens the Buildings page, where the asset registers hang off each site. Full build-out is open.

---

## 11. Custom reports
Saved from a session with a refresh cadence. Page: kicker *Custom report · dynamic*, title, source query, last refresh and cadence, summary, ranked table. First run shows a *scheduled* state until the orchestrator's next cycle. Carries the ask bar.

---

## 12. Integrations (Admin)
A saved report over the connection tables; admin only.

- **Header tiles:** Connected · Tables fed · Need attention · Available.
- **Connected** — table: connection, status (Healthy / Degraded / Auth expired / Rate limited / In review), last sync, rows 30d, tables fed, connected by. Expand a row for: mode and auth; **tables it enriches** (orange) vs **tables that exist only because of it** (marker yellow); what those tables unlock; a note; actions (Re-authorise / Resync / Field mapping / Open in the graph / Disconnect). Six seeded: Yardi Voyager, SAP S/4HANA, IBM Maximo (key expired, 0 rows — staleness shown, not hidden), Planon Universe, Reuters Connect, custom data lake.
- **Available sources** — search + category chips; 58 connectors in nine categories, each card naming the graph tables it would write: Finance and property accounting (Yardi, MRI, RealPage, AppFolio, Entrata, Sage Intacct, Coupa, AvidXchange) · ERP — Oracle · ERP — Microsoft · ERP — SAP · CMMS and CAFM · IWMS · Asset, plant and BMS · News and market intelligence · Custom and direct. **Connect** opens a modal (name, instance URL, target tables); the mapping is held in the decision queue until approved.
- **Custom API** — base URL, maskable bearer token, rate limits, five endpoints, field-mapping table (source field → table.column, natural key marked).

Data in `hoistra-integrations.js`.

---

## 13. Data model — the Hoist Graph

One table per node, keyed on a natural key, joined by foreign keys. Core tables: `building`, `floor`, `space`, `asset`, `equipment`, `meter_reading`, `work_order`, `document`, `certificate`, `contract`, `invoice`, `vendor`. Tables created by connectors: `service_charge_line`, `lease`, `purchase_order`, `cost_centre`, `gl_posting`, `ppm_schedule`, `occupancy`, `news_item`, `regulation_signal`, `tenant_billing`. Unstructured files are vectorised and bound to a column by similarity (marker yellow).

**Provenance carried on records**
- Meter reading: route (data collector under LoA · SEC intermediary · Green Button CMD · retailer feed · own sub-meters/BMS) and granularity (sub-metered · building-level, inferred)
- Regulation pack: legal standing (enacted · guidance · proposed · no operational standard)
- Contract term: source (clause and page) or platform default
- Rating: basis (certificate · filing · consumption) and months of data → Actual / Projected / Insufficient

**Market profiles** (`EN_PROFILE`, `ENC`, `PACKS` in `Hoistra.dc.html`): per country — benchmark standard and reference, unit (kWh/m², kBtu/ft², RTh), data route and refresh, per-meter/per-section availability, identifier (MPAN/MPRN · utility account + service point · SP account + meter · DEWA premise), known limits, electricity and gas price and billing unit, currency.

---

## 14. Roles

`role` is session state: `"user"` (default) or `"admin"`. Toggled from the account menu; Buildings (Admin) and Integrations set admin on open. Admin gates: Update the graph, Integrations page, admin navigator (hoisting, editing and removing a building are open to either role). Everything else is user. Built once, driven by state, so the two views cannot drift.

---

## 15. Key state (logic class)

`view` (home · answer · module · buildings · cc · vp · report · integ) · `module` · `role` · `navOpen` · `orchOpen` / `orchTask` / `orchDone` · `flow` (declare · booking · pick · new · email · investigate) · `queueOpen` · `detail` · `sessions` · `reports` · `pq` (ask bar) · `eScope` · `enRatingCc` · `enOpenB` · `enMatrixOpen` · `enRulesOpen` · `inv` / `invStage` / `invSrcDone` · `intTab` / `intQ` / `intOpen` / `intModal` / `intExtra` · `acctOpen` · `dark`.

---

## 16. Open items

- Energy, Assets and Work orders modules: full build-out beyond the generic layout (Energy is furthest along).
- Whether the tables connectors create (lease, gl_posting, occupancy, news_item) should also appear in the Hoist Graph and the canonical export.
- Compliance console pack cascade does not yet show legal standing per pack.
- The excess-cost bridge (anomalies · structural · benchmark fit) under the Energy scope card.
- Second accent colour: held at one orange plus marker yellow.
- Metering identifiers and routes are modelled per country in Energy; the shell copy elsewhere still says "half-hourly MPAN" in places.
