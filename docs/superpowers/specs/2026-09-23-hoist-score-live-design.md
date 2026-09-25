# Hoist Score — read from what was actually ingested

**Date:** 2026-09-23
**Status:** Implemented 2026-09-23 on branch `test`, uncommitted; verification against `hoistra_test` pending (see plan).
**Scope:** `svc-operations-intelligence` (one new read, two count fixes) and `apps/frontend`
(`logic/homeLive.js`, `api/energy.js`, tests). No schema change. No database writes by the code
in this change; the verification ingests are Hussain's to run against `hoistra_test`.

## Goal

The Home page's Hoist Score reads what has been ingested for each hoisted building — the asset
register from the CMMS migration, contracts / meters / certificates from the file ingests — and
moves as each file lands. The Home tile and the per-building score on the Buildings page share
one definition, so a building that reads "contracts missing" on one page cannot read "covered"
on the other.

## What the widget does today, and why it reads 7 / 100

`shapeLiveHome()` in `apps/frontend/src/logic/homeLive.js` draws four bars, each a ratio
computed in the browser from a different `svc-operations-intelligence` read. The score is the
mean of the bars that answered. Assets is hard-coded as unsourced.

| Bar | Formula today | Read | What the screenshot shows and why |
|---|---|---|---|
| Certificates | pack types on record ÷ (on record + not on record), portfolio-wide, UK pack, building + vendor scopes | `GET /api/compliance/saved-space/summary` | **14%** — 8 certificates on record against 49 pack types nobody has filed. This measures statutory-catalogue completeness, not whether the hoisted buildings have evidence; with two buildings and a 57-type pack it can never read high. |
| Meter consent | active meters linked to a building ÷ active meters | `GET /api/energy/meters` (`energy_meters`) | **—** — zero `energy_meters` rows (no half-hourly CSV ingested yet) makes the ratio 0/0, so the bar is "unsourced" instead of "0 of 2 buildings have a meter". |
| Contracts | vendors with a `contract_sla_parameters` row ÷ vendors in the compliance register | `GET /api/contract-performance/contracts` + the console's register | **0%** — vendors exist in the register but no returned parameter set carries a matching `vendor_id`, or the list came back empty for this caller. Either way the denominator is vendors, not buildings. |
| Assets | none | none | **—** — `set("assets", null, "connector service not connected")`. Yet the migration writes `plenum_cafm.assets` with a resolved `building_id`, and `GET /api/assets` on `svc-work-order-management` already serves them to the Assets page. |

Four unrelated denominators (pack types, meters, vendors, nothing) averaged into one number.

## What the backend already has

`hoist_score_for()` in `src/engines/energy/buildings.py` scores **one building** on five equal
domains, each present or absent from the graph counts in `building_rollup.child_counts()`:

| Domain | Counted from | Written by which ingest | Blind spot |
|---|---|---|---|
| assets | `plenum_cafm.assets.building_id` | CMMS migration (`write_node.py`, `_NEEDS_RESOLUTION` resolves the building) | none, if the resolved id is a `buildings.building_id` (see Preflight) |
| compliance | `compliance_certificates.building_id`, else via its document | certificate ingest | none |
| contracts | `plenum_cafm.contracts.building_id` | **nothing in this service writes that table.** Contract ingest writes `contract_sla_parameters` and reaches a building through `documents.building_id` (`parameters.py:~897`). | every building reads "contracts missing" forever |
| energy | `plenum_cafm.meters` (UDR graph table), or an EUI on record | CSV readings ingest creates **`energy_meters`**, not `meters` | covered only once an EUI is computed |
| maintenance | `plenum_cafm.work_orders.building_id` | CMMS migration | none |

The result rides on every row of `GET /api/energy/buildings` as `hoist_score`,
`hoist_score_covered`, `hoist_score_missing` when `plenum_cafm.buildings` has rows
(`root: "buildings"`). The Buildings page reads `hoist_score` from it today.

## Decisions to confirm (my defaults in bold)

1. **Coverage unit is the hoisted building.** A bar is "buildings with at least one record in
   that domain ÷ hoisted buildings". Not catalogue completeness, not vendors, not meters.
2. **Home keeps its four bars** (Contracts, Assets, Meter consent, Certificates). The read
   returns maintenance too; the tile does not draw it. Adding a fifth "Maintenance — work
   orders" bar is one line if you want exact parity with the per-building score.
3. **Denominator is the buildings the caller may see** — the rows `GET /api/energy/buildings`
   returns for them. A one-building user sees their own coverage, an admin the company's.
4. **Certificates is presence, not pack percentage.** A building with one building-scope
   certificate is covered. Per-building pack coverage (`/coverage/buildings`) exists and could
   replace this later; it is a richer measure and a separate decision.
5. **Score = mean of the drawn bars; bands unchanged** (60 supervised, 85 delegated).

## Design

### Backend — `svc-operations-intelligence`

**Count fixes in `building_rollup.child_counts()`** (no change to `hoist_score_for`):

- `contracts`: count `contract_sla_parameters` per building through
  `documents.building_id` (document key resolved the way `_list_from_graph` already does),
  merged with the existing `plenum_cafm.contracts` count when that table has rows.
- `meters`: count `energy_meters.building_id` merged with the existing `plenum_cafm.meters`
  count. Same relation name, so the energy domain lights up on a CSV ingest.

**New pure function** `portfolio_hoist_score(rows)` in `src/engines/energy/hoist_score.py`,
over the rows `list_buildings()` returns:

```
{
  "ok": true,
  "root": "buildings" | "sites",
  "buildings": 2,
  "domains": [
    { "key": "assets",      "covered": 2, "of": 2, "pct": 100, "missing_buildings": [] },
    { "key": "compliance",  "covered": 1, "of": 2, "pct": 50,
      "missing_buildings": [{ "building_id": "…", "name": "Ashgrove Court", "building_code": "B-102" }] },
    { "key": "contracts",   … }, { "key": "energy", … }, { "key": "maintenance", … }
  ],
  "score": 70,          // mean of per-building hoist_score, informational
  "rows": [ { "building_id", "name", "building_code", "hoist_score", "covered": [...], "missing": [...] } ]
}
```

With `root: "sites"` (no `plenum_cafm.buildings` rows) there are no graph counts; every domain
returns `covered: null` and a `note` saying the building graph has no rows yet, rather than a
fabricated zero.

**New route** `GET /api/energy/hoist-score` in `src/api/routes/energy.py`: same `scope`
dependency and the same restricted-caller filter as `GET /buildings` (factor that filter into a
helper both routes call, so the two cannot drift).

**Tests** — `tests/unit/test_c_hoist_score.py` gains a `TestPortfolio` class: two buildings,
one covered on assets only → assets 50%, others 0; a `sites` root → nulls with the note; the
missing-buildings list names the right building. The count SQL for contracts-via-documents
gets a unit test in the style `test_c_building_graph.py` already uses for `child_counts`.

### Frontend — `apps/frontend`

- `api/energy.js`: `hoistScore: () => apiFetch(B, '/api/energy/hoist-score', { query: withOrg() })`.
- `logic/homeLive.js`:
  - `homeLoad()` adds the `coverage` read and drops the `compliance`, `contracts` and `meters`
    reads — they fed only the bars. `approvals` and `anomalies` stay (Hoist Crons); the
    register stays (hero line). Three fewer requests per Home load.
  - `shapeLiveHome()` maps domains to the existing `BARS` by key
    (`contracts→contracts`, `assets→assets`, `energy→meters`, `compliance→certificates`),
    `pct = covered/of`, note `"1 of 2 hoisted buildings with a contract on record"`.
  - The gap line names the building: `"Contracts lowest at 50% — Ashgrove Court has none on record"`.
  - Read failed → all four bars null, note `"coverage read did not answer"`; the wholesale seed
    fallback when nothing answered is unchanged.
- `logic/renderVals.js`: one condition changes. The three tile fields were gated on a non-null
  score value, so "not counted" and "no buildings hoisted" — both deliberate nulls — fell
  through to the seed 78%. They now render when the coverage read *answered*
  (`score.answered`), as "—" with the backend's reason; a read that did not answer still falls
  back to the labelled seed like every other tile.
- `test/homeLive.test.mjs`: fixtures for the new read; the certificates / meters / contracts /
  assets bar tests are rewritten to the new definition; bands, crons, hero and pending tests
  are untouched.
- `README.md` Home section: the three bullets naming the old reads are replaced with one.

### Buildings page

No code change. Its per-building `hoist_score` gains the contracts and energy domains as a
side-effect of the count fixes.

## Verification — on `hoistra_test` with the Harbour Point set

Every step after 0 is a write to `hoistra_test` and is Hussain's to run, in the order
`~/Desktop/Harbour-Point-Upload-Set/UPLOAD-ORDER.md` gives, signed in as
`ops.director@northbridge-estates.example`.

0. **Preflight, read-only, signed in:** `GET /api/energy/buildings` → confirm `root: "buildings"`
   and note the two building ids. `GET /api/assets` (work-order service) → confirm the migrated
   assets' `building_id` values are those ids. If they are `sites` uuids instead, the Assets bar
   will read 0 and the fix belongs in the migration's building resolution, not in this change.
   *This is the one thing I could not check: direct database reads are off-limits, and the
   attempt was blocked.*
1. Baseline after deploy: four bars drawn against N = 2, each with its "n of 2" note.
2. `4-migration/northbridge_cmms_export.xlsx` from the chat → Assets bar rises (and Maintenance
   in the per-building score, via `work_orders`).
3. `2-contracts` with Harbour Point chosen → Contracts reads "1 of 2".
4. `~/Downloads/northbridge_test_files/energy/*.csv` for both buildings → Meter consent "2 of 2".
5. `3-compliance-certificates` → Certificates "1 of 2".
6. Expected tile: (50 + 100 + 100 + 50) / 4 = **75 → "Supervised autonomy"**, gap line naming
   Ashgrove Court.
7. `npm test` in `apps/frontend` (Node 24). `pytest` for `svc-operations-intelligence` inside
   the testenv container (baseline 537 passed / 3 pre-existing failures).

## Out of scope

- The hero line's "2 buildings hoisted" still counts buildings named on certificates, not
  `plenum_cafm.buildings`. Separate change.
- The P&L tile stays on seed figures (no ledger exists).
- Diagnosing today's Contracts 0% (`vendor_id` on the three SLA rows) — superseded by the new
  definition.
- Per-building pack-coverage as the certificates measure (decision 4).
