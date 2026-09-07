# Hoistra skill — Energy

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Energy

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

## Assets and Work orders
Present as modules with the generic layout (metric cards, filterable table, side bars, asks). Assets currently routes to the Risky Buildings report. Full build-out is open.

