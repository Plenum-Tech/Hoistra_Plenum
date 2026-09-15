# Assets and Maintenance: what the screens need, and what the backend now serves

Answers two questions about the attached page: whether the Assets APIs exist to build it, and
what Maintenance needs. Everything below is building-scoped — `building_id` is the key, on
every route, with no exceptions.

The page's own seed data is the contract I read against: `src/data/hoistra-assets.js`
(`HOISTRA_AS`) and `src/data/hoistra-maintenance.js` (`HOISTRA_MX`).

---

## How scoping works on all of these

Identical on every route here:

- **No `building_id`** — you get every building you may see. For an admin that is the whole
  company; for a user it is their allocation; for a user allocated to nothing it is nothing.
- **`?building_id=<uuid>`** — narrows to that building, and is **403** if you are not
  allocated to it. Refused rather than answered empty, so "you may not see this" is never
  confused with "there is nothing here".
- A caller allocated to nothing gets a predicate that matches no row, never the absence of a
  predicate. That distinction is what stops an empty allocation reading as unrestricted.

Rows that carry no building of their own reach one through what they are about — an
inspection through its asset, a meter through its building — and a row that cannot be placed
is left out rather than shown to everybody.

---

## Assets — mostly there, four gaps

### What exists now

| Need on the page | Endpoint | Status |
|---|---|---|
| Asset register, whole portfolio | `GET /api/assets` | **Yes**, no building_id required |
| Filter by building, category, criticality, status | `GET /api/assets` | **Yes** |
| Search by name, code or serial | `GET /api/assets?q=` | **Yes** |
| Total before paging | `X-Total-Count` header | **Yes** |
| Class or category as a word | `category_name` on each row, `GET /api/asset-categories` | **Yes** |
| Install year | `installation_date` | **Yes** |
| Criticality, health score | `criticality`, `health_score` | **Yes** |
| One asset | `GET /api/assets/{id}` | **Yes** |
| Work history for an asset | `GET /api/energy/assets/{asset_id}/work-history` | **Yes** |
| Sub-metered zones and their intensity against a reference | `GET /api/energy/detection/coverage` gives per-meter rows; `GET /api/energy/ratings/position` gives intensity against the country benchmark | **Partly** |

### The four gaps — three now closed

1. **`section` on an asset.** ~~Nothing carries that.~~ **Closed.** `building_sections` now
   exists and `assets.section_id` points at it. See
   [asset-intelligence-api.md](asset-intelligence-api.md).
2. **Vendor and vendor email on an asset.** ~~`assets` has no vendor column.~~ **Closed.**
   `assets.vendor_id` now exists and resolves to a vendor name on the asset itself, rather
   than only through work orders.
3. **Next PPM date per asset.** Still derived from planned work orders, not stored, and no
   endpoint returns it per asset. This is the one gap left.
4. **Linked anomaly per asset.** **Closed.** `GET /api/energy/assets/{asset_id}/intelligence`
   returns the open findings attributed to the asset, what they are costing, and how long the
   worst has run.

---

## Maintenance — four new endpoints

None of this existed. All four are on **svc-work-order-management**, which owns work orders
and was already building-scoped.

Three things the screen asks for are **derived, not stored**: there is no decisions table, no
report-health table and no PPM-compliance table. They are read from `work_orders`,
`approvals_queue_items` and `inspections`.

### `GET /api/maintenance/decisions`

What the property manager owes, from both places a decision arrives from.

```json
{"ok": true, "count": 134,
 "by_state": {"Blocked": 0, "Deviation": 0, "Awaiting approval": 134, "To raise": 0},
 "decisions": [
   {"work_order": "WO-202606181318211249", "state": "Awaiting approval",
    "source": "Maintenance", "trigger": "Approval outstanding",
    "detail": "Request for painting services in the lobby area.",
    "asset": "painting", "building": "Garden Square",
    "building_id": "95649fde-…", "vendor": null, "estimated_cost": null,
    "priority": "medium", "due": null}]}
```

| State | Means |
|---|---|
| `Blocked` | The order is held — status blocked, on hold, suspended |
| `Awaiting approval` | Approval outstanding |
| `Deviation` | Live and past its due date |
| `To raise` | **No work order exists.** Another module says one should. `work_order` is null and `queue_item` names the approvals row |

`source` is the module that raised it: Energy, Compliance, Vendors, Assets or Maintenance.
Vendors resolve to a name whether the row stores a name or a UUID.

### `GET /api/maintenance/inspections`

Reports with their finding, risk level, and whether a corrective action is still open.

```json
{"ok": true, "count": 3, "recommendations_open": 0,
 "by_risk": {"High": 1, "Medium": 2},
 "inspections": [{"id": "…", "asset_code": "AHU-3", "asset_name": "Air handling unit 3",
                  "building": "Bishopsgate Tower", "building_id": "…",
                  "inspector": "…", "inspection_date": "2026-06-14",
                  "finding_type": "…", "observations": "…", "risk_level": "Medium",
                  "recommendation_open": false, "section": null, "source_file": "…"}]}
```

Reports carry no building column, so each reaches one through its asset. A report whose asset
cannot be placed is not returned.

### `GET /api/maintenance/ppm`

Planned maintenance health per vendor, derived from the orders themselves. A planned order
that is finished is **done**; one past its due date and unfinished is **missed**; one finished
after its due date is **late**.

```json
{"ok": true,
 "summary": {"contracts": 3, "planned": 54, "done": 54, "missed": 0, "late": 0,
             "completion_pct": 100.0},
 "contracts": [{"vendor": "Apex Mechanical Services Ltd", "planned": 28, "done": 28,
                "missed": 0, "late": 0, "buildings": 1, "completion_pct": 100.0,
                "next_due": null}]}
```

### `GET /api/maintenance/summary`

All three counted in one call, for the screen's first paint.

### Also: `/api/ppm/*` now needs a token

The PPM scheduler creates work orders from an external system and had **no caller at all**.
`GET /api/ppm/due` and `POST /api/ppm/run` now require a bearer like everything else.

---

## What the data says today

The endpoints work; the data behind them is thin, and that is worth knowing before wiring a
screen to them.

| | Database behind the deployed app | The one your local backends read |
|---|---|---|
| Decisions | **0** — every work order is open or closed, none blocked or awaiting approval | **134**, all awaiting approval |
| Inspections | **0** rows | **3** rows |
| PPM | **0** — no order records a planned type | **54** across 3 vendors, all complete |

So on the deployed database the Maintenance screen will render empty, correctly. That is the
same situation the Energy tiles were in before they were seeded, and the fix is the same
shape: seed the states and types the module reads, or point it at the other database.

Two smaller things the numbers expose: no work order on the deployed database records a
`maintenance_type` or `wo_type` at all, so nothing can be classified as planned; and
`sla_due_at` is unset everywhere, so nothing can be late or in deviation.
