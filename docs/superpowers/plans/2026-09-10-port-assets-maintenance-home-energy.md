# Port Assets, Maintenance, Home (Platform Value), Energy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task, adapted per the Global Constraints below (no worktree, no commits). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring four already-finished areas from `/Users/hussain/Downloads/hoistra-frontend 2` (a complete, working "handoff" build of the Hoistra design prototype — see its `docs/NEW-WORK.md`) into `apps/frontend` of this repo, on the currently-checked-out `workorder-and-asset` branch: a real Assets page, a real Maintenance page (+ Inspection Reports query page), a "Platform Value" card on Home, and two label/copy corrections on the Energy page — without altering the behavior of any currently-working page or breaking any of the 226 existing tests.

**Architecture:** This is a **port**, not new design work — every screen, logic file, and data file needed already exists, fully written and working, in `hoistra-frontend 2`. `apps/frontend` shares the same base architecture (one `HoistraLogic` controller, one `renderVals()` view-model function, module screens dispatched from `Module.jsx`) but has evolved further than the port source: it has real backend wiring (`buildingsLive.js`, `complianceLive.js`, `vendorsLive.js`, `graphLive.js`, `auth.js`, `sessions.js`, `spacesLive.js`, `chat.js`, `reports.js`) that the port source never had. New pages are added by (a) copying new logic/data/screen files verbatim from the port source, and (b) making small, additive edits to `apps/frontend`'s shared plumbing files so they pick up the new pieces — verified line-by-line against `apps/frontend`'s actual current content, not assumed from the port source.

**Tech Stack:** React 18 + Vite, inline styles, one global controller object (`src/logic/HoistraLogic.js`). **Unlike the port source, this repo has a real test suite**: `npm test` runs Node's built-in test runner over `test/**/*.test.mjs`. Confirmed baseline: **226/226 passing, 0 failures.** Every task in this plan ends by re-running `npm test` and confirming it is still 226/226 (or more, if a task's own new coverage is added — none is planned here) with 0 failures.

**Spec:** User-supplied walkthrough (Assets, Maintenance, Home "Platform value", Energy "Cost above benchmark"/"Anomaly cost") cross-checked against `/Users/hussain/Downloads/hoistra-frontend 2/docs/NEW-WORK.md`. Source-of-truth files for every new/copied file in this plan live in `/Users/hussain/Downloads/hoistra-frontend 2`. Every plumbing edit below (exact current text, exact line anchors) was verified directly against the real files in `apps/frontend` on the `workorder-and-asset` branch immediately before this plan was written.

## Global Constraints

- **No git worktree.** Work happens directly in the current checkout of `apps/frontend` at `/Users/hussain/Desktop/hoist/Hoistra_Plenum`, on the branch already checked out (`workorder-and-asset`). This branch is open live in the human partner's Cursor editor — never run anything that reverts, stashes, or discards working-tree state.
- **No commits, ever, by any subagent or by the controller.** The human partner commits manually. Every implementer dispatch must explicitly say: make the file edits, run the verification commands, report — never run `git add`, `git commit`, `git stash`, `git checkout --`, `git reset`, or any other state-changing git command. Task review happens against the accumulated uncommitted working-tree diff (`git diff`, scoped by file path per task), not commit ranges.
- **Never touch `apps/frontend/src/logic/spacesLive.js`.** `apps/frontend/test/spacesLive.test.mjs:49` hard-asserts `BUILTIN_SPACES.map(b => b.name)` equals exactly `['Compliance','Energy','Vendor performance','Vendor operations']`. This file is unrelated to the `MODULES` object this plan edits (different file, different object) — the landmine is only touching `spacesLive.js` itself, which no task in this plan needs to do. If any implementer's instinct is to "keep it consistent" by editing `spacesLive.js`'s `ops` entry name — that is explicitly wrong; do not do it.
- **Also do not touch:** `src/logic/buildingsLive.js`, `complianceLive.js`, `vendorsLive.js`, `graphLive.js`, `auth.js`, `session.js`, `sessions.js`, `chat.js`, `reports.js`, `buildingsCrud.js`, `buildingsGraph.js`, or any screen other than `Home.jsx`, `Module.jsx`, `App.jsx`, and the new `Assets.jsx`/`Maintenance.jsx`/`InspectionReports.jsx`. None of this plan's work requires them.
- **No pre-existing untracked files get touched, read into a commit, or deleted.** `.impeccable/`, `apps/frontend/.impeccable/`, `docs/AUTH_SCHEMA_DRIFT_2026-09-09.md`, `docs/superpowers/plans/2026-09-09-frontend-auth.md`, `docs/superpowers/specs/2026-09-09-frontend-auth-design.md`, and `hussain.html` all pre-exist this plan, untracked, in the working tree. Leave them exactly as they are.
- **No backend/API work.** Confirmed across all four areas: zero `fetch`/network calls anywhere in the ported source. Everything is static seed data or client-only computation.
- **Two items from the walkthrough are NOT buildable by porting, and are out of scope:**
  1. **"Users section" for energy cost/anomaly visibility** — does not exist anywhere in `hoistra-frontend 2` either. Needs a product decision before any work can start.
  2. **Currency-aware cost figures** — even in the finished port-source build, `money()` in `energy.js` is hardcoded to `£`. This plan reproduces the two label/copy changes only, not real multi-currency conversion.
- **`hover.css` numbering must never be renumbered.** Confirmed byte-identical to the original (pre-renumbering) Downloads copy. This plan adds exactly one new class (`hv26`) and reuses two existing classes (`hv18`, `hv19`) under their current numbers — never renumbers anything.
- **`Module.jsx` is edited additively, not restructured** — two new conditional JSX lines, nothing removed or rewritten.
- **Sidebar labels in `renderVals.js`'s `navSections` array are literal hardcoded strings, not derived from `MODULES.x.name`.** Changing `MODULES.ops.name` (Task 1) does not change what the sidebar shows — the `navSections` array (Task 3) needs its own, separate edit to the "Maintenance" label.
- **The "ops" sidebar badge count (`spm.byKey.ops...`) is a live backend-derived figure and must not be touched** — only its label text changes. Assets' new badge count uses a simple local value instead (`this.asVals(s).asThreatN`) — it is a new, self-contained, locally-seeded feature, not backend-live like Buildings/Compliance/Vendors/Energy, so it does not plug into `spm`.
- Every file path below is relative to `/Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend` unless marked "(source, hoistra-frontend 2)" — those are absolute paths into `/Users/hussain/Downloads/hoistra-frontend 2`.

---

## Task 1: Shared plumbing — constants, controller mixins, routing condition, hover class

**Files:**
- Modify: `src/logic/constants.js`
- Modify: `src/logic/HoistraLogic.js`
- Modify: `src/logic/energy.js`
- Modify: `src/styles/hover.css`

**Interfaces:**
- Produces: `MODULES.assets`, `MODULES.ops` repurposed to Maintenance's config, `VALUE_LEDGER` (exported array), `assetsMethods`/`maintenanceMethods` mixed onto `HoistraLogic.prototype`, `isNotEnergy` now excludes `"assets"`/`"ops"`, hover class `.hv26`.

- [ ] **Step 0: Confirm the baseline**

  Run `npm test` from `apps/frontend` and confirm **226 passing, 0 failing**. Run `npm run dev`, confirm it starts with no console errors, stop it. Do not proceed if the baseline isn't clean — report and stop instead.

- [ ] **Step 1: Add the `assets` module entry to `MODULES` in `src/logic/constants.js`**

  `MODULES` has exactly four keys: `compliance` (line 535), `energy` (546), `vendors` (557), `ops` (568). Insert a new `assets` entry directly before the `ops: {` entry:

  ```js
  assets: {
    name: "Assets", kicker: "Feature E · asset-condition-engine", icon: "ph-cube", answer: "energy",
    blurb: "Condition inferred from energy before a fault shows. Each section's EUI is read against its reference to find where the load is; anomalies attributed to an asset say which one. Both signals together are a threat, one alone is a watch. Actions go to the vendor who holds the asset, as an inspection request or a work order.",
    scanLabel: "Run condition scan", exportLabel: "Export asset register",
    tableTitle: "Buildings · EUI against reference, sections and assets beneath",
    head: [],
    tableFoot: "",
    sideTitle: "", sideFoot: "",
    filters: ["All", "Threat", "Watch", "In control", "Above 10%", "Above 30%"],
    asks: ["Which assets should I inspect before winter?", "What is the work order backlog on threat assets?", "Which sections have gone over reference since last month?"]
  },
  ```

- [ ] **Step 2: Repurpose the `ops` entry from "Vendor operations" to "Maintenance"**

  This is `constants.js`'s `MODULES.ops` object (this is a *different* object from `spacesLive.js`'s `BUILTIN_SPACES` — do not confuse the two, and do not touch `spacesLive.js`, per Global Constraints). Replace the entire current `ops: { ... }` block with:

  ```js
  ops: {
    name: "Maintenance", kicker: "Feature D · work-order-engine", icon: "ph-wrench", answer: "queue",
    blurb: "Work orders are not raised by the FM operative; they arrive here from triggers — a vendor blocked, a certificate expiring, an asset flagged, an anomaly priced — and wait for your decision. Completed orders bring inspection reports, which are read together rather than one at a time. Planned maintenance is checked against plan.",
    scanLabel: "Re-read inspection reports", exportLabel: "Open PPM calendar",
    tableTitle: "Live work orders",
    head: ["Work order", "Asset", "Building", "Estimate", "Status", "Next step"],
    tableFoot: "Lifecycle: Draft → Approved → Assigned → In Progress → Completed → Verified → Closed. Invoices and completion photographs are ingested through the query bar; statutory jobs require a certificate, which lands in the Hoist Graph.",
    sideTitle: "Building health score", sideFoot: "Composite of PPM compliance, open reactive volume and compliance coverage per building.",
    filters: ["All", "Blocked", "To raise", "Awaiting approval", "Deviation", "Compliance", "Vendors", "Assets", "Energy"],
    asks: ["Which decisions are statutory?", "Which recommendations were never converted to orders?", "Which PPM contracts are behind plan?"]
  }
  ```

- [ ] **Step 3: Add `VALUE_LEDGER` to `src/logic/constants.js`**

  Insert after the `MODULES` object's closing `};` and before the final `export { ... MODULES };` line:

  ```js
  /* Platform value ledger, 2026 YTD. A line exists only where a cost was detected,
     an action was approved, and the cost afterwards is measured or contractually
     fixed. Estimated lines are marked; nothing is claimed for detection alone. */
  const VALUE_LEDGER = [
    { mod: "Energy", detected: "£312k", saved: "£286k", tone: "ok", items: [
      { what: "Car park lighting · Building 5 · non-occupancy spike", action: "Schedule reset, Feb", detected: "£4.1k/yr", saved: "£4.1k/yr", basis: "measured · 4 weeks of readings" },
      { what: "AHU-1 · Kingsway House · schedule overrun", action: "BMS schedule corrected, Mar", detected: "£31k/yr", saved: "£29k/yr", basis: "measured" },
      { what: "Server room · Meridian Quay · CRAC sequencing", action: "Sequencing changed, Apr", detected: "£44k/yr", saved: "£41k/yr", basis: "measured" },
      { what: "Bishopsgate chillers · re-benchmark on actual hours", action: "Pack corrected, May", detected: "£118k excess", saved: "£96k", basis: "benchmark fit — not waste, but not a gap either" },
      { what: "11 further anomalies closed Jan–Aug", action: "Work orders", detected: "£115k/yr", saved: "£116k/yr", basis: "measured" }
    ] },
    { mod: "Vendors", detected: "£188k", saved: "£164k", tone: "ok", items: [
      { what: "Service credits · L1 and L2 breaches", action: "Claimed against 6 contracts", detected: "£71k", saved: "£62k", basis: "credited on invoice" },
      { what: "Invoice lines outside rate schedule", action: "Held and re-issued", detected: "£54k", saved: "£54k", basis: "invoice corrected" },
      { what: "Duplicate call-outs consolidated", action: "Visits merged per building", detected: "£63k", saved: "£48k", basis: "invoiced vs prior run rate" }
    ] },
    { mod: "Maintenance", detected: "£212k", saved: "£148k", tone: "ok", items: [
      { what: "Warranty claims found in inspection reports", action: "Claimed", detected: "£18k", saved: "£16k", basis: "credited" },
      { what: "Reactive orders averted by predictive orders", action: "9 predictive orders raised", detected: "£94k", saved: "£67k", basis: "estimated · reactive cost avoided less predictive cost" },
      { what: "Replacements deferred by remediation", action: "3 remediations", detected: "£100k", saved: "£65k", basis: "estimated · capex deferred 24+ months, discounted" }
    ] },
    { mod: "Compliance", detected: "£140k", saved: "£122k", tone: "ok", items: [
      { what: "Lapsed statutory certificates renewed inside window", action: "31 bookings from the ladder", detected: "£96k exposure", saved: "£96k", basis: "estimated · penalty and void-insurance exposure at lapse" },
      { what: "Forged certificate rejected · AN Other House EICR", action: "Re-inspection ordered", detected: "£26k", saved: "£26k", basis: "estimated · exposure had it been accepted" },
      { what: "Blocked vendors kept off regulated work", action: "4 swaps", detected: "£18k", saved: "—", basis: "not counted — no measurable cost after" }
    ] },
    { mod: "Assets", detected: "£118k", saved: "£80k", tone: "ok", items: [
      { what: "Asset value preserved by early remediation", action: "CH-2, Boiler-14, AHU-1 remediated", detected: "£118k at risk", saved: "£80k", basis: "estimated · value-at-risk model, before vs after" }
    ] }
  ];
  ```

  Then add `VALUE_LEDGER` to the file's final `export { ... MODULES };` line (append `, VALUE_LEDGER` before the closing `};` — this export line does not include `SESSIONS`, that's fine, just add `VALUE_LEDGER` alongside what's already there).

- [ ] **Step 4: Wire the two new mixins into `src/logic/HoistraLogic.js`**

  Current imports (lines 2-21) end with:
  ```js
  import { buildingsCrudMethods } from './buildingsCrud.js';
  import { buildingsGraphMethods } from './buildingsGraph.js';
  import { AUTH_DEFAULTS, authMethods, canAdmin } from './auth.js';
  ```
  Add two new imports anywhere in this list (e.g. right after the `energyMethods` import near the top):
  ```js
  import { assetsMethods } from './assets.js';
  import { maintenanceMethods } from './maintenance.js';
  ```
  Current final line of the file:
  ```js
  Object.assign(HoistraLogic.prototype, coreMethods, complianceMethods, vendorsMethods, energyMethods, integrationsMethods, complianceLiveMethods, homeLiveMethods, vendorsLiveMethods, buildingsLiveMethods, buildingsCrudMethods, buildingsGraphMethods, graphLiveMethods, chatMethods, sessionsMethods, spacesMethods, reportsMethods, authMethods, renderValsMethods);
  ```
  Insert `assetsMethods, maintenanceMethods` anywhere before `renderValsMethods` (order relative to the other mixins doesn't matter — `renderValsMethods` must stay last since it spreads the rest). E.g.:
  ```js
  Object.assign(HoistraLogic.prototype, coreMethods, complianceMethods, vendorsMethods, energyMethods, assetsMethods, maintenanceMethods, integrationsMethods, complianceLiveMethods, homeLiveMethods, vendorsLiveMethods, buildingsLiveMethods, buildingsCrudMethods, buildingsGraphMethods, graphLiveMethods, chatMethods, sessionsMethods, spacesMethods, reportsMethods, authMethods, renderValsMethods);
  ```
  (`src/logic/assets.js` and `src/logic/maintenance.js` don't exist yet — created in Task 2. Do Task 2 immediately after this step, before running anything that imports `HoistraLogic.js`.)

  **Also add the default-state slice `assetsMethods`/`maintenanceMethods` need.** `asVals(s)`/`iotVals(s)`/`scanVals(s)` (assets.js) and `mxVals(s)` (maintenance.js) read several `state` keys that must exist with a default value from the start — confirmed exactly against the source repo's `HoistraLogic.js:26`. In this repo's `state` object, find the line starting `pq: "", eScope: [], enMatrixOpen: false, ...` and insert this slice immediately after `pq: ""` and before `eScope: []`:
  ```js
  asPct: 10, asWeeks: 3, asOpenB: [], asOpenS: [], iotTick: 0, iotOpen: "AS-1042", inspQ: "", inspDraft: "", asScanB: [], asScanSec: [], asScanAllSec: true, asScanStage: 0, asScanDone: 0, asScanReport: null, asLastRun: "02:14 today",
  ```
  So the line reads `pq: "", asPct: 10, asWeeks: 3, asOpenB: [], asOpenS: [], iotTick: 0, iotOpen: "AS-1042", inspQ: "", inspDraft: "", asScanB: [], asScanSec: [], asScanAllSec: true, asScanStage: 0, asScanDone: 0, asScanReport: null, asLastRun: "02:14 today", eScope: [], enMatrixOpen: false, ...` (rest unchanged). Without this, `asVals(s)` throws on `s.asOpenB.indexOf(...)` the first time `renderVals()` runs after Task 3 wires it in — which is every render, since `navSections` calls `this.asVals(s)` unconditionally.

- [ ] **Step 5: One-line routing fix in `src/logic/energy.js`**

  Current, `energy.js:296`:
  ```js
  isNotEnergy: !(s.view === "module" && s.module === "energy")
  ```
  Change to:
  ```js
  isNotEnergy: !(s.view === "module" && (s.module === "energy" || s.module === "assets" || s.module === "ops"))
  ```

- [ ] **Step 6: Relabel the two Energy cost cards in `src/logic/energy.js`**

  Current, `energy.js:69-73` (inside `enScopeCards`):
  ```js
  { l: "Excess cost", v: money(excess), s: sc.single ? "per year at " + E.tariffLabel : "per year, local tariffs converted to GBP", tone: "risk" },
  { l: "Anomalies in scope", v: String(anoms.length), s: money(anomSum) + " annualised impact", tone: anoms.length > 4 ? "warn" : "ok" }
  ```
  Change to:
  ```js
  { l: "Cost above benchmark / year", v: money(excess), s: "(EUI − reference) × area × tariff", tone: "risk" },
  { l: "Anomaly cost / year", v: money(anomSum), s: anoms.length + (anoms.length === 1 ? " anomaly" : " anomalies") + " · deviation from own baseline × tariff", tone: anoms.length > 4 ? "warn" : "ok" }
  ```

- [ ] **Step 7: Add one new hover class to `src/styles/hover.css`**

  Confirmed: this file has `.hv1` through `.hv25`, byte-identical to the pre-renumbering original. Append at the end:
  ```css
  .hv26:hover { background:var(--marker-tint) !important; color:var(--color-text) !important; }
  ```

- [ ] **Step 8: Verify**

  Don't run `npm run dev`/`npm test` yet — `assets.js`/`maintenance.js` imports added in Step 4 don't exist until Task 2, and the build/tests will fail on that missing import. Re-read the four edited files and confirm all edits landed as specified. Report DONE with a note that full verification happens at the end of Task 2.

---

## Task 2: Data and logic files for Assets and Maintenance

**These four files must land together** — `assets.js` (source repo) has a *hard* ES import of `HOISTRA_MX` from `hoistra-maintenance.js` (`assets.js:5`, used unconditionally inside its `hist()` helper) — Assets cannot even build without Maintenance's data file present.

**Files:**
- Create: `src/data/hoistra-assets.js` (copy verbatim from `/Users/hussain/Downloads/hoistra-frontend 2/src/data/hoistra-assets.js`)
- Create: `src/data/hoistra-maintenance.js` (copy verbatim from `/Users/hussain/Downloads/hoistra-frontend 2/src/data/hoistra-maintenance.js`)
- Create: `src/logic/assets.js` (copy verbatim from `/Users/hussain/Downloads/hoistra-frontend 2/src/logic/assets.js`)
- Create: `src/logic/maintenance.js` (copy verbatim from `/Users/hussain/Downloads/hoistra-frontend 2/src/logic/maintenance.js`)

**Interfaces:**
- Consumes from `src/logic/constants.js`: `PACKS`, `CC_OF`, `ENC`, `t` — all confirmed present, unchanged, in `apps/frontend`'s `constants.js`.
- Consumes from `src/logic/core.js`: `this.D()` (line 48), `this.flash()` (69), `this.orch()` (80), `this.orchWith()` (109), `this.runAction()` (117), `this.woDetail()` (180), `this.rows()` (206), `this.bars()` (260) — all confirmed present at these line numbers in `apps/frontend`'s `core.js`, with `rows()`/`bars()` both confirmed to `return [];` for an unrecognized module key (safe fallback for `"assets"`).
- Consumes from `src/logic/energy.js`: `this.investigate()` (line 136), `this.anomalyDetail()` (395) — confirmed present, unchanged.
- Consumes from `src/data/hoistway-data.js`: `HOISTWAY.workorders` (line 168), `HOISTWAY.anomalies` (179) — confirmed present.
- Produces (for Task 3): `assetsMethods` exporting `asVals(s)`, `iotVals(s)`, `scanVals(s)`, `runScan(chosen)`; `maintenanceMethods` exporting `mxVals(s)`, `openInsp(...)`.

- [ ] **Step 1: Copy the two data files verbatim**

  ```bash
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/data/hoistra-assets.js" "src/data/hoistra-assets.js"
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/data/hoistra-maintenance.js" "src/data/hoistra-maintenance.js"
  ```
  (paths relative to `apps/frontend`). Pure seed-data ES modules — no edits needed.

- [ ] **Step 2: Copy the two logic files verbatim**

  ```bash
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/logic/assets.js" "src/logic/assets.js"
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/logic/maintenance.js" "src/logic/maintenance.js"
  ```
  No edits needed — every controller helper and constant they call already exists, unchanged, in `apps/frontend` (confirmed above).

- [ ] **Step 3: Verify**

  Run `npm run dev` and confirm no import-resolution or syntax errors, then stop it. Run `npm test` and confirm still **226/226 passing, 0 failing** — nothing in this task should change any existing test's outcome since nothing added is wired into any rendered screen yet. If either check fails, do not proceed to Task 3 — report the failure.

---

## Task 3: `renderVals.js` — wire Assets, Maintenance, and Home's Platform Value into the view model

**Files:**
- Modify: `src/logic/renderVals.js` (2184 lines)

**Interfaces:**
- Consumes: `assetsMethods.asVals/iotVals/scanVals` and `maintenanceMethods.mxVals` (Task 2), `VALUE_LEDGER` (Task 1).
- Produces: `vals.isAssets`, `vals.isMaint`, `vals.isInsp`, all of `asVals()`'s/`iotVals()`'s/`mxVals()`'s returned keys merged onto `vals` when relevant, `vals.pvTotal`/`vals.pvRows`/`vals.pvOpen`/`vals.fValue`/`vals.pvLedger` (Home's Platform Value), corrected `navSections` entries, corrected `modMetrics`/`mod.scan` branches.

- [ ] **Step 1: Add `isAssets`/`isMaint`/`isInsp` view flags**

  Add anywhere inside the main view-model object literal (this codebase's convention is one big object literal, not a spread-based build — e.g. near the existing `isReport`/`isSessions`/`isSpace` flags around line 1803):
  ```js
  isAssets: s.signedIn && s.view === "module" && s.module === "assets",
  isMaint: s.signedIn && s.view === "module" && s.module === "ops",
  isInsp: s.signedIn && s.view === "insp",
  ```

- [ ] **Step 2: Merge Assets/Maintenance view-model data onto `vals`, module-scoped**

  Find the `if (mod) { ... }` block (around line 2168, right after the closing `};` of the main object literal and before `return this.cvt(vals);` — search for the literal text below). It currently reads:
  ```js
  if (mod) {
    const en = modKey === "energy" ? this.enVals(s) : null;
    if (en) {
      vals.modAsks = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
      vals.abChips = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
    }
    vals.mod = {
      ...mod,
      sideTitle: en ? en.enSideTitle : mod.sideTitle,
      sideFoot: en ? en.enSideFoot : mod.sideFoot,
      scan: () => this.orch(mod.scanLabel, mod.name),
      export: () => this.orch(mod.exportLabel, mod.name)
    };
  }
  ```
  Change to:
  ```js
  if (mod) {
    const en = modKey === "energy" ? this.enVals(s) : null;
    if (en) {
      vals.modAsks = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
      vals.abChips = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
    }
    if (modKey === "assets") Object.assign(vals, this.asVals(s), this.iotVals(s));
    if (modKey === "ops") Object.assign(vals, this.mxVals(s));
    vals.mod = {
      ...mod,
      sideTitle: en ? en.enSideTitle : mod.sideTitle,
      sideFoot: en ? en.enSideFoot : mod.sideFoot,
      scan: () => modKey === "assets"
        ? this.orchWith(mod.scanLabel, mod.name, "scan", { asScanStage: 0, asScanDone: 0, asScanReport: null, asScanB: [], asScanSec: [], asScanAllSec: true })
        : this.orch(mod.scanLabel, mod.name),
      export: () => this.orch(mod.exportLabel, mod.name)
    };
  }
  ```

- [ ] **Step 3: Add the `modKey === "assets"`/`"ops"` branches to `modMetrics`**

  Around line 2140. Current:
  ```js
  modMetrics: !mod ? [] : modKey === "energy"
    ? this.enVals(s).enScopeCards
    : D.answers[mod.answer].metrics.map((m) => ({ ...m, color: t(m.tone).color })),
  ```
  Change to:
  ```js
  modMetrics: !mod ? [] : modKey === "energy"
    ? this.enVals(s).enScopeCards
    : modKey === "assets" ? this.asVals(s).asCards
    : modKey === "ops" ? this.mxVals(s).mxCards
    : D.answers[mod.answer].metrics.map((m) => ({ ...m, color: t(m.tone).color })),
  ```

- [ ] **Step 4: Rewrite the `navSections` array — Assets and Maintenance entries only**

  Current, verbatim (around line 1773-1800 — find by searching for `navSections: [`):
  ```js
  navSections: [
    { label: "Buildings", icon: "ph-buildings", key: "buildings", count: this.bldIsLive() ? this.bldData().length : "" },
    { label: "Buildings", icon: "ph-buildings", key: "buildings_user", count: "" },
    { label: "Compliance", icon: "ph-shield-check", key: "compliance", count: spm.byKey.compliance.count === null ? "" : spm.byKey.compliance.badge.split(" ")[0] },
    { label: "Vendors", icon: "ph-chart-line-up", key: "vendors", count: spm.byKey.vendors.count === null ? "" : spm.byKey.vendors.badge.split(" ")[0] },
    { label: "Energy", icon: "ph-lightning", key: "energy", count: spm.byKey.energy.count === null ? "" : spm.byKey.energy.badge.split(" ")[0] },
    { label: "Assets (Pending)", icon: "ph-cube", key: "assets" },
    { label: "Work orders (Pending)", icon: "ph-wrench", key: "ops", count: spm.byKey.ops.count === null ? "" : spm.byKey.ops.badge.split(" ")[0] }
  ].filter((n) => s.role === "admin" ? n.key === "buildings" : n.key !== "buildings").map((n) => {
    const active = (s.view === "module" && s.module === n.key)
      || (s.view === "buildings" && n.key === "buildings" && s.role === "admin")
      || (s.view === "buildings" && n.key === "buildings_user" && s.role !== "admin")
      || (s.view === "cc" && n.key === "compliance")
      || (s.view === "vp" && n.key === "vendors");
    return {
      label: n.label, icon: n.icon, count: n.count || "", show: n.count ? "flex" : "none",
      color: active ? "var(--color-accent)" : "var(--color-neutral-300)",
      chip: active ? "var(--color-accent-900)" : "transparent",
      click: () => {
        if (n.key === "buildings") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "admin", navOpen: true, detail: null }); }
        if (n.key === "buildings_user") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "user", navOpen: true, detail: null }); }
        if (n.key === "compliance") { window.scrollTo(0, 0); return this.setState({ view: "cc", navOpen: true, detail: null }); }
        if (n.key === "vendors") { window.scrollTo(0, 0); return this.setState({ view: "vp", navOpen: true, detail: null }); }
        // Asset registers hang off the buildings in the Hoist Graph; that page is where they are.
        if (n.key === "assets") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "user", navOpen: true, detail: null }); }
        return this.openModule(n.key);
      }
    };
  }),
  ```
  **Intentional behavior change — confirm this is wanted before/while doing this step.** Today, "Assets (Pending)" in the sidebar redirects to the Buildings page; after this change it opens the real Assets module like every other real module.

  Make exactly three edits, touching nothing else in this block:

  1. The `assets` array entry — from:
     ```js
     { label: "Assets (Pending)", icon: "ph-cube", key: "assets" },
     ```
     to:
     ```js
     { label: "Assets", icon: "ph-cube", key: "assets", count: this.asVals(s).asThreatN },
     ```

  2. The `ops` array entry — **label only**, leave the live `spm.byKey.ops...` count expression completely untouched — from:
     ```js
     { label: "Work orders (Pending)", icon: "ph-wrench", key: "ops", count: spm.byKey.ops.count === null ? "" : spm.byKey.ops.badge.split(" ")[0] }
     ```
     to:
     ```js
     { label: "Maintenance", icon: "ph-wrench", key: "ops", count: spm.byKey.ops.count === null ? "" : spm.byKey.ops.badge.split(" ")[0] }
     ```

  3. Inside the click handler, delete these two lines entirely (the comment and the `if` both):
     ```js
         // Asset registers hang off the buildings in the Hoist Graph; that page is where they are.
         if (n.key === "assets") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "user", navOpen: true, detail: null }); }
     ```
     so that an "assets" click falls through to the final `return this.openModule(n.key);` line, same as every other real module. The `ops` click handler already has no special case (it already falls through to `openModule` today) — do not add one.

- [ ] **Step 5: Add Home's "Platform Value" keys**

  Add near the existing `pnlSaved` key (line 359) — same object literal, anywhere nearby is fine:
  ```js
  pvTotal: "£800k",
  pvRows: VALUE_LEDGER.map((r) => ({ head: r.mod, detected: r.detected, saved: r.saved, color: t(r.tone).color })),
  pvOpen: () => this.orchWith("Platform value ledger · 2026", "All modules", "value", {}),
  fValue: s.flow === "value",
  pvLedger: VALUE_LEDGER.map((r) => ({ mod: r.mod, saved: r.saved, detected: r.detected, items: r.items.map((i) => Object.assign({}, i, { est: /estimated/.test(i.basis) ? "est." : "", estShow: /estimated/.test(i.basis) ? "inline" : "none" })) })),
  ```
  `t()` and `orchWith()` already exist unchanged — no new imports needed for those. `VALUE_LEDGER` does need a new import: add it to the existing `import { ... } from './constants.js'` line at the top of `renderVals.js`.

- [ ] **Step 6: Verify**

  Run `npm run dev`, confirm no console errors on load, click around the currently-working pages (Home, Buildings, Compliance, Vendors, Energy, Integrations, Sessions, Chat if reachable) and confirm nothing looks different yet (nothing new renders until Tasks 4-6 add the screens). Stop the dev server. Run `npm test` — confirm still **226/226 passing, 0 failing**. This is the checkpoint to catch any regression from this task's edits before building on top of them.

---

## Task 4: Assets screen

**Files:**
- Create: `src/screens/Assets.jsx` (copy from `/Users/hussain/Downloads/hoistra-frontend 2/src/screens/Assets.jsx`, with the class-name remap in Step 2)
- Modify: `src/screens/Module.jsx`
- Modify: `src/components/shell/OrchestratorDock.jsx`

- [ ] **Step 1: Copy the screen file verbatim**

  ```bash
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/screens/Assets.jsx" "src/screens/Assets.jsx"
  ```

- [ ] **Step 2: Remap two hover class names — confirmed against `apps/frontend`'s real `hover.css` (byte-identical to the pre-renumbering original, so this mapping is exact)**

  | In copied `Assets.jsx` | Occurrences | Means (as authored) | This repo's class with that meaning | Action |
  |---|---|---|---|---|
  | `className="hv17"` | 4 (group/section/asset-row/IoT-card toggle rows) | `background:var(--marker-tint)` | `.hv19` (`hover.css` line 20, exact rule already present) | Replace all 4 `hv17` → `hv19` |
  | `className="hv21"` | 4 (the −/+ threshold buttons) | `background:var(--marker-tint); color:var(--color-text)` | none existing — this is why Task 1 Step 7 added `.hv26` | Replace all 4 `hv21` → `hv26` |
  | `className="hv15"` | 2 | unchanged, confirmed present | — | leave as-is |
  | `className="hv4"` | 3 | unchanged, confirmed present | — | leave as-is |

  Do these two replacements only in the newly-created `src/screens/Assets.jsx` — do not touch `hover.css` again, and do not touch any other file's existing `hv17`/`hv21` usage.

- [ ] **Step 3: Wire Assets into `Module.jsx`**

  Add near the top, alongside the existing `import React from 'react';`:
  ```js
  import Assets from './Assets.jsx';
  ```
  Confirmed structure: the `{vals.isNotEnergy ? (...) : null}` block (starts line 282) is immediately followed by an `{vals.isEnergy ? (...) : null}` block (starts line 423). Insert this new line between them, after the `isNotEnergy` block's closing `) : null}` and before the next `{vals.isEnergy ? (`:
  ```jsx
  {vals.isAssets ? <Assets vals={vals} /> : null}
  ```

- [ ] **Step 4: Wire the condition-scan orchestrator flow into `OrchestratorDock.jsx`**

  In the source repo (`/Users/hussain/Downloads/hoistra-frontend 2/src/components/shell/OrchestratorDock.jsx`), the block `{vals.fScan ? ( ... ) : null}` runs from line 630 to its matching `) : null}` (a multi-stage flow: pick buildings → pick sections → running progress → report). Copy that entire tagged block verbatim into `apps/frontend`'s `src/components/shell/OrchestratorDock.jsx`, inserted as a new sibling immediately before the existing `{vals.fDone ? (` block (confirmed at line 598 in this repo's file — the last flow block in the file). Buttons in this block use `className="hv7"` and `className="hv11"` — both confirmed present in this repo's `hover.css`, no remap needed.

  **Two `renderVals.js` additions are required for this block to render at all — confirmed exactly against source, not optional:**
  1. The `fScan` boolean gate itself: at `renderVals.js:1601` (the line reading `fEmail: s.flow === "email", fDone: !!s.flowDone, fDoneText: s.flowDone,`), add a new line immediately after it: `fScan: s.flow === "scan",`. Without this, `{vals.fScan ? (...) : null}` is always falsy and the block never renders — the orchestrator drawer opens but stays empty. (Confirmed against source: `hoistra-frontend 2/src/logic/renderVals.js:1104` has `fScan: s.flow === "scan", ...this.scanVals(s),` on its own line, right after the `fEmail`/`fDone` line.)
  2. The `scanVals(s)` data merge: add `Object.assign(vals, this.scanVals(s));` next to the `if (modKey === "assets") Object.assign(vals, this.asVals(s), this.iotVals(s));` line that Task 3 already added — needed for the block's `vals.sc*`/`vals.asScan*` keys to be populated.

  These are the only two `renderVals.js` edits this task makes.

- [ ] **Step 5: Verify in the browser and against the test suite**

  Run `npm run dev`. Sign in, click "Assets" in the sidebar (previously "Assets (Pending)"). Confirm:
  - It opens as a real module page (not the old Buildings redirect) — the intentional behavior change from Task 3 Step 4.
  - Condition-rule ± buttons work and highlight on hover (exercises `hv26`).
  - Buildings → sections → assets nesting expands/collapses; toggle rows highlight on hover (exercises `hv19`).
  - The metric-card row shows Assets-specific cards (Task 3 Step 3).
  - Instrumented Assets section renders with sparkline/failure-model/RUL detail.
  - "Run condition scan" opens a working multi-stage flow through to a report (Step 4 above).

  Stop the dev server. Run `npm test` — confirm still **226/226 passing, 0 failing**.

---

## Task 5: Maintenance and Inspection Reports screens

**Files:**
- Create: `src/screens/Maintenance.jsx` (copy from source, with class remap)
- Create: `src/screens/InspectionReports.jsx` (copy from source, with class remap)
- Modify: `src/screens/Module.jsx`
- Modify: `src/App.jsx`

- [ ] **Step 1: Copy both screen files verbatim**

  ```bash
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/screens/Maintenance.jsx" "src/screens/Maintenance.jsx"
  cp "/Users/hussain/Downloads/hoistra-frontend 2/src/screens/InspectionReports.jsx" "src/screens/InspectionReports.jsx"
  ```

- [ ] **Step 2: Remap hover classes**

  | File | Class in copy | Occurrences | Means | This repo's class | Action |
  |---|---|---|---|---|---|
  | `Maintenance.jsx` | `hv17` | 2 (group-header toggle, decision-item row) | `background:var(--marker-tint)` | `.hv19` | Replace both → `hv19` |
  | `Maintenance.jsx` | `hv14`, `hv15`, `hv4` | 1, 1, 3 | unchanged, confirmed present | — | leave as-is |
  | `InspectionReports.jsx` | `hv19` | 1 (back-breadcrumb) | `color:var(--color-accent-300)` | `.hv18` | Replace → `hv18` |
  | `InspectionReports.jsx` | `hv4` | 1 | unchanged, confirmed present | — | leave as-is |

- [ ] **Step 3: Wire Maintenance into `Module.jsx`**

  Add alongside the `Assets` import added in Task 4:
  ```js
  import Maintenance from './Maintenance.jsx';
  ```
  Add alongside the `isAssets` block added in Task 4 Step 3:
  ```jsx
  {vals.isMaint ? <Maintenance vals={vals} /> : null}
  ```

- [ ] **Step 4: Wire Inspection Reports into `App.jsx`**

  This file has more screen imports/routes than the port source (`Chat`, `Sessions`, `Space`, `PasswordModal`, etc. are already present) — that's expected, leave them all untouched. Add:
  ```js
  import InspectionReports from './screens/InspectionReports.jsx';
  ```
  alongside the existing screen imports (e.g. near the `CustomReport`/`Sessions`/`Space` imports), and:
  ```jsx
  {vals.isInsp ? <InspectionReports vals={vals} /> : null}
  ```
  alongside the existing render lines (e.g. near the `{vals.isReport ? <CustomReport vals={vals} /> : null}` line).

- [ ] **Step 5: Verify in the browser and against the test suite**

  Run `npm run dev`. Click "Maintenance" in the sidebar (previously "Work orders (Pending)"). Confirm:
  - It shows the new Decisions/Inspection-intelligence/PPM-health layout, not the old generic "Live work orders" table — **intentional behavior change, confirm wanted**.
  - Group-header rows and decision rows highlight on hover (`hv19`).
  - Opening an inspection-intelligence finding lands on `InspectionReports` as its own page, breadcrumb highlighting correctly (`hv18`).
  - Every other sidebar item (Buildings, Compliance, Vendors, Energy, Integrations, Sessions, Chat) still opens exactly as before.

  Stop the dev server. Run `npm test` — confirm still **226/226 passing, 0 failing**.

---

## Task 6: Home page — Platform Value card

**Files:**
- Modify: `src/screens/Home.jsx`
- Modify: `src/components/shell/OrchestratorDock.jsx`

- [ ] **Step 1: Add the fourth stat card to `Home.jsx`**

  Confirmed: same 3-card grid as the port source (`Hoist Score`, `Portfolio P&L · YTD`, `Hoist Crons`) inside a `gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))"` container starting at line 9; `vals.pnlSaved` still read at line 52. Add this as a new sibling `<div>` right after the "Hoist Crons" card's closing tag, before the grid container's own closing tag:

  ```jsx
  <div className="hv19" onClick={vals.pvOpen} style={{ padding: "14px 22px 15px", borderLeft: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", minWidth: "0", cursor: "pointer" }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
      <span style={{ fontSize: "9.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
        {"Platform value · 2026"}
      </span>
      <i className="ph ph-arrow-up-right" style={{ fontSize: "11px", color: "var(--color-accent)" }}></i>
    </div>
    <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "9px" }}>
      <span style={{ fontSize: "22px", lineHeight: "1", fontVariantNumeric: "tabular-nums", color: "var(--st-ok)" }}>
        {vals.pvTotal}
      </span>
      <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
        {"saved after action"}
      </span>
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "10px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 46px 40px", gap: "7px", alignItems: "baseline" }}>
        <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
          {"Module"}
        </span>
        <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
          {"Detected"}
        </span>
        <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
          {"Saved"}
        </span>
      </div>
      {(vals.pvRows || []).map((r, $index) => (
        <React.Fragment key={$index}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 46px 40px", gap: "7px", alignItems: "baseline" }}>
            <span style={{ fontSize: "9.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {r.head}
            </span>
            <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: "var(--color-neutral-500)" }}>
              {r.detected}
            </span>
            <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: r.color }}>
              {r.saved}
            </span>
          </div>
        </React.Fragment>
      ))}
    </div>
  </div>
  ```

  `className="hv19"` here means `background:var(--marker-tint)` in this repo's `hover.css` — the correct hover effect for this card.

- [ ] **Step 2: Add the ledger drawer to `OrchestratorDock.jsx`**

  In the source repo, `{vals.fValue ? ( ... ) : null}` spans lines 560-629. Copy it verbatim into `apps/frontend`'s `src/components/shell/OrchestratorDock.jsx`, inserted as a new sibling immediately before the `{vals.fDone ? (` block. By the time this task runs, Task 4 will already have inserted its own `{vals.fScan ? (...) : null}` block immediately before `fDone` too — that's fine, the relative order of `fValue` and `fScan` against each other doesn't matter (they're mutually exclusive siblings gated by different flow states). Just make sure this new `fValue` block ends up somewhere before `{vals.fDone ? (` — immediately before `fDone` (i.e. immediately after Task 4's `fScan` block, if it's already there) is simplest:

  ```jsx
  {vals.fValue ? (
    <>
      <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
        <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
          {"Platform value · 2026 ledger"}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "8px" }}>
          <span style={{ fontSize: "22px", lineHeight: "1", color: "var(--st-ok)" }}>
            {vals.pvTotal}
          </span>
          <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
            {"saved to date"}
          </span>
        </div>
        <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", lineHeight: "1.5", marginTop: "6px" }}>
          {"A line counts only when a cost was detected, you approved an action, and the cost afterwards is measured or fixed by contract. Lines marked "}
          <span style={{ color: "var(--color-accent)" }}>
            {"est."}
          </span>
          {" rest on a model; detection alone earns nothing."}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "12px", maxHeight: "420px", overflowY: "auto" }}>
          {(vals.pvLedger || []).map((m, $index) => (
            <React.Fragment key={$index}>
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: "8px", paddingBottom: "5px", borderBottom: "1px solid var(--color-divider)" }}>
                  <span style={{ fontSize: "12px", flex: "1" }}>
                    {m.mod}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                    {m.detected}{" detected"}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: "var(--st-ok)" }}>
                    {m.saved}
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "6px" }}>
                  {(m.items || []).map((i, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ fontSize: "10.5px", lineHeight: "1.45" }}>
                        <div style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                          <span style={{ flex: "1", minWidth: "0" }}>
                            {i.what}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--st-ok)", whiteSpace: "nowrap" }}>
                            {i.saved}
                          </span>
                        </div>
                        <div style={{ color: "var(--color-neutral-500)" }}>
                          {i.action}{" · detected "}{i.detected}{" · "}{i.basis}{" "}
                          <span style={{ color: "var(--color-accent)", display: i.estShow }}>
                            {i.est}
                          </span>
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
        <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
          <div className="hv7" onClick={vals.fCancel} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
            {"Done"}
          </div>
        </div>
      </div>
    </>
  ) : null}
  ```
  `className="hv7"` and `vals.fCancel` both confirmed present/unchanged — no remap needed.

- [ ] **Step 3: Verify in the browser and against the test suite**

  Run `npm run dev`, go to Home. Confirm the 4th "Platform value · 2026" card renders with `£800k`, a 5-row module breakdown, highlights on hover. Click it — the drawer opens with per-item detail and "est." tags on modelled lines. Click "Done" to close. Confirm the other 3 Home cards are pixel-identical to before this task. Stop the dev server. Run `npm test` — confirm still **226/226 passing, 0 failing**.

---

## Task 7: Full regression pass

**Files:** none modified — verification-only.

- [ ] **Step 1: `npm test` — the primary gate**

  Run `npm test` from `apps/frontend`. Confirm **226/226 passing, 0 failing** (or more tests than 226 only if any were added elsewhere outside this plan's scope — none should be). Any failure here is a stop-and-report condition, not something to route around.

- [ ] **Step 2: Walk every existing page in the browser**

  `npm run dev`. Click through: Home → Buildings → Compliance → Vendors → Energy → Integrations → Sessions → Chat (if reachable without live backend) → back to Home. Confirm each looks and behaves exactly as before this plan. On Energy specifically, confirm the metric cards now read "Cost above benchmark / year" and "Anomaly cost / year" with everything else unchanged.

- [ ] **Step 3: Walk the four ported/changed areas end-to-end**

  - **Assets**: adjust both condition-rule thresholds, confirm classification counts update live; expand building → section → asset; open each action (Raise work order / Request inspection / Investigate) and confirm a populated orchestrator drawer opens; run a condition scan to completion; open an Instrumented Asset's full detail.
  - **Maintenance**: filter/group the decisions list; open the inspection-intelligence panel and a finding (confirm it lands on `InspectionReports`); confirm the PPM-health table renders per-contract rows.
  - **Home**: confirm the Platform Value card and its drawer.
  - **Energy**: confirm the two relabeled cards and that everything else is untouched.

- [ ] **Step 4: Confirm the two flagged behavior changes are acceptable**

  Re-confirm explicitly with whoever reviews this: (a) sidebar "Assets" now opens a real module instead of redirecting to Buildings, (b) sidebar "Maintenance" (formerly "Work orders") now shows the new decisions/PPM view instead of the old generic table.

- [ ] **Step 5: Note the two explicitly out-of-scope items**

  Confirm "Users section" visibility and true currency-aware cost figures (Global Constraints) are consciously deferred, not silently dropped.

- [ ] **Step 6: Final status for the human partner to commit**

  Report the full list of new and modified files (no commits made, per Global Constraints):
  - New: `src/data/hoistra-assets.js`, `src/data/hoistra-maintenance.js`, `src/logic/assets.js`, `src/logic/maintenance.js`, `src/screens/Assets.jsx`, `src/screens/Maintenance.jsx`, `src/screens/InspectionReports.jsx`
  - Modified: `src/logic/constants.js`, `src/logic/HoistraLogic.js`, `src/logic/energy.js`, `src/styles/hover.css`, `src/logic/renderVals.js`, `src/screens/Module.jsx`, `src/screens/Home.jsx`, `src/App.jsx`, `src/components/shell/OrchestratorDock.jsx`
