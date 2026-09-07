# Hoistra skill — Platform overview

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## What Hoistra is

An AI-native property management platform for the person accountable for a portfolio they do not operate day to day. It reads the portfolio — documents, meter feeds, work orders, contracts, certificates — into one graph (the **Hoist Graph**), finds the exposure, prices it, and brings the decision to the user with the evidence attached. Existing systems stay; Hoistra ingests from them and writes back.

**Three principles every screen obeys**

1. **Every number sources its evidence.** A service credit names the work order, asset, building, metric and weight behind it. A score decomposes to the clause it was measured against. No orphaned figures.
2. **Nothing is asserted without disclosure.** A platform default is marked as a default. A proposal is never shown as a duty. A reading that arrived by inference is labelled inferred. A projection carries its months of data and a confidence.
3. **Query first, then read.** Every non-admin report opens with the ask bar above the analysis. The saved reports are conveniences over the query interface, not a separate product.

**The daily loop** (from the customer journey): Detect → Price → Decide → Dispatch → Verify. Verified outcomes re-enter the graph and sharpen the next detection.


---

## Files

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

## Roles

`role` is session state: `"user"` (default) or `"admin"`. Toggled from the account menu; Buildings (Admin) and Integrations set admin on open. Admin gates: Hoist a building, Update the graph, Integrations page, admin navigator. Everything else is user. Built once, driven by state, so the two views cannot drift.


---

## Key state (logic class)

`view` (home · answer · module · buildings · cc · vp · report · integ) · `module` · `role` · `navOpen` · `orchOpen` / `orchTask` / `orchDone` · `flow` (declare · booking · pick · new · email · investigate) · `queueOpen` · `detail` · `sessions` · `reports` · `pq` (ask bar) · `eScope` · `enRatingCc` · `enOpenB` · `enMatrixOpen` · `enRulesOpen` · `inv` / `invStage` / `invSrcDone` · `intTab` / `intQ` / `intOpen` / `intModal` / `intExtra` · `acctOpen` · `dark`.


---

## Open items

- Energy, Assets and Work orders modules: full build-out beyond the generic layout (Energy is furthest along).
- Whether the tables connectors create (lease, gl_posting, occupancy, news_item) should also appear in the Hoist Graph and the canonical export.
- Compliance console pack cascade does not yet show legal standing per pack.
- The excess-cost bridge (anomalies · structural · benchmark fit) under the Energy scope card.
- Second accent colour: held at one orange plus marker yellow.
- Metering identifiers and routes are modelled per country in Energy; the shell copy elsewhere still says "half-hourly MPAN" in places.

