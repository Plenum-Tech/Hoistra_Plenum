# Hoist a building — API contract (2026-09-08)

> **Superseded.** This was written as a request for the backend before the endpoint existed.
> `feature/compliance-core-skill-and-question-bank` has since shipped the real routes (`POST` /
> `PATCH` / `DELETE /api/energy/buildings/{id}`, plus `GET .../cost-drivers`), and the frontend
> (`apps/frontend/src/logic/buildingsCrud.js`, `buildingsGraph.js`) is wired to them — nothing
> here is aspirational any more. It differs from this draft in a few places worth knowing if
> you read on: the create/edit fields are `site_name`/`name`, `region`/`state` (either spelling
> accepted), not the `code`-as-primary-reference shape drafted below; `PATCH`'s allowed fields
> do not include `organization_id`; and country/region resolve to a **location** row, which is
> what actually carries the regulation pack. Kept for its reasoning, not as the current contract.

## Where this stands

The Buildings page (admin view) has a **Hoist a building** button. It opens the orchestrator
dock on a three-step flow: the building record, the prepared record, the documents hand-off.
The form (`apps/frontend/src/logic/hoistBuilding.js`) collects one `plenum_cafm.sites` row,
validates it against the live Building table and prepares the request body below.

**No write endpoint exists yet, and the UI does not call one.** Step 2 shows the body and
states that nothing was written. The stack points at the production database, so wiring the
call is a separate, deliberate decision. This document is the contract that decision needs.

svc-udr does expose a generic `POST /api/tables/{table}/records` that would insert into
`sites`. It is not the answer here: it takes any columns unvalidated, allocates no `site_id`
(the primary key is a VARCHAR the caller must supply), records no actor, and fills none of
the defaults the Building table relies on. The UI should never call it for this.

## Endpoint

```
POST /api/energy/buildings
```

- **Service:** svc-operations-intelligence, mounted at `/backend/ops-intelligence/`. It already
  owns `GET /api/energy/buildings` (the table's read), `sites_shape` (knows which of the two
  `sites` schemas the live database has) and `BENCHMARK_PACKS` (the defaults per market).
- **Router:** `src/api/routes/energy.py`, beside `list_buildings`. Engine code in
  `src/engines/energy/buildings.py`.
- **Success:** `201 Created`.
- **Content type:** `application/json` both ways.
- **Who may call it:** admins only. There is no auth on the stack yet; until there is, the
  route must at least log `created_by` and `source`, and must not treat them as authorisation.

## Request body

Every field the form sends, in the order it sends them. The frontend applies the same rules
before sending, so a body that reaches the service failing them is a bug, not a user error.

| Field | Type | Required | Validation | `plenum_cafm.sites` column(s) | Notes |
| --- | --- | --- | --- | --- | --- |
| `site_id` | string | no | 1–50 chars, `^[A-Za-z0-9._-]+$`, not an existing `site_id` / `site_code` / `building_code` | `site_id` (PK), `site_code`, `building_code` | Omitted when the admin left Building ID blank → the service allocates (see below). |
| `site_name` | string | **yes** | 1–200 chars, trimmed | `site_name`, `building_name` | Duplicate names are allowed (the register already repeats names); the UI warns, the service accepts. |
| `country_code` | string | **yes** | one of `UK` `US` `AE` `SG` | `country_code` | Selects the regulation pack. Extend the set when a new market gets a pack. |
| `country` | string | **yes** | display name for `country_code` | `country` | `United Kingdom` · `United States` · `UAE` · `Singapore`, as the seed rows spell them. |
| `state` | string | **yes** | 1–120 chars | `state`, `region` | Free text with the market's regions suggested. |
| `city` | string | no | ≤ 100 chars | `city` | |
| `postcode` | string | no | ≤ 40 chars | `postcode` | |
| `use_type` | string | **yes** | one of `Commercial` `Retail` `Residential` `Mall` `Hospital` `Hotel` `Mixed` | `use_type`, `site_type` | Drives the TM46 category and the benchmark. |
| `use_mix` | array of `{use: string, pct: number}` | **yes** | shares sum to 100 ± 0.5; ≥ 2 entries when `use_type` is `Mixed`, exactly `[{use_type, 100}]` otherwise; uses from the list above; sorted largest first | `use_mix` (JSONB) | The split bar on the table. |
| `floors` | integer | **yes** | 1–300 | `floors` (TEXT in the live schema — write the integer as text) | |
| `gfa_sqm` | integer | no | > 0 and ≤ 10 000 000 | `gfa_sqm` (TEXT in the live schema) | Always m². The form converts ft² before sending (÷ 10.7639, rounded). |
| `metering_granularity` | string | **yes** | one of `none` `building-level` `sub-metered` | `metering_granularity` | `none` when no meter is on record yet. |
| `metering_route` | string | no | ≤ 160 chars | `metering_route` | How the reading arrives (e.g. `HH data collector · LoA`). Omitted when blank. |
| `organization_id` | string | no | tenant id | `organization_id` | Sent only when `VITE_ORGANIZATION_ID` is set; otherwise the service's default org. |
| `created_by` | string | no | e-mail of the signed-in admin | audit only | Not a `sites` column today; see audit below. |
| `source` | string | **yes** | literal `hoistra-ui` | audit only | Distinguishes UI writes from connector imports and seeds. |

Example, as the form prepares it for the seed's first building entered in ft²:

```json
{
  "site_name": "Bishopsgate Tower",
  "country_code": "UK",
  "country": "United Kingdom",
  "state": "Greater London",
  "city": "London",
  "postcode": "EC2N 4AY",
  "use_type": "Commercial",
  "use_mix": [{ "use": "Commercial", "pct": 92 }, { "use": "Retail", "pct": 8 }],
  "floors": 34,
  "gfa_sqm": 38276,
  "metering_granularity": "sub-metered",
  "metering_route": "HH data collector · LoA",
  "created_by": "admin@example.com",
  "source": "hoistra-ui"
}
```

### Required at a glance

`site_name` · `country_code` (+ `country`) · `state` · `use_type` (+ `use_mix`) · `floors` ·
`metering_granularity` · `source`. Everything else is optional and omitted when blank —
never sent as an empty string.

## What the service must do

1. **Validate** every rule in the table and reject the whole body on the first failure set
   (all field errors at once, see 400 below). Trim strings. Normalise `use_mix` shares to one
   decimal place.
2. **Read the live shape** with `sites_shape` and write only the columns the table has. Both
   schemas in the repo must work: the varchar-keyed one (`site_id VARCHAR(50)` PK, `floors` and
   `gfa_sqm` as TEXT — the deployed one) and the UUID-keyed one (`id UUID` PK, `site_name`,
   `gfa_sqm NUMERIC`). On the UUID shape `site_id` is stored in `site_code` and the row's key is
   the generated `id`.
3. **Allocate the id** when `site_id` is absent: the next `B-NNN` after the highest existing
   `B-\d+` key in the organisation, zero-padded to three digits, inside the insert transaction
   (`SELECT … FOR UPDATE` on the max, or a retry on unique violation). Two admins hoisting at
   the same moment must get two ids. When `site_id` is present and taken, answer 409 — never
   overwrite (the seed loader's `ON CONFLICT DO NOTHING` is not the behaviour here either).
4. **Fill the defaults** the Building table reads: `status = 'active'`; `benchmark_standard`,
   `benchmark_standing`, `benchmark_standing_note` from `BENCHMARK_PACKS[country_code]` (the
   fallback pack for an unknown market); `hoist_score = 0`; `created_at = now()`. Leave
   `eui_kwh_per_m2` and `benchmark_kwh_per_m2` NULL — they are measured or computed, never
   entered here.
5. **Write the same-name columns** as the seed does, so the row reads identically wherever the
   engine looks: `site_name` and `building_name` both get the name; `site_code` and
   `building_code` both get the id.
6. **Record the actor.** Log a structured line `energy.buildings.create` with `site_id`,
   `created_by`, `source`, `organization_id`. If the deployment has an activity or audit table,
   write the same there. The dock already tells the admin "every write is logged with actor
   and timestamp"; this is where that becomes true.
7. **Return the row as the table will show it** — run the new site through the same
   `shape_building_row` the GET uses, so the frontend can append it without a refetch and the
   figures (completeness, benchmark standing, metering) match what the next load shows.

## Responses

**201 Created**

```json
{
  "created": true,
  "site_id": "B-010",
  "building": { "...": "one item exactly as GET /api/energy/buildings returns it" }
}
```

**400 Bad Request** — validation. Field-keyed so the form can put each message under its input.

```json
{
  "error": "validation failed",
  "fields": {
    "floors": "Between 1 and 300.",
    "use_mix": "Shares total 90% — they must total 100%."
  }
}
```

**409 Conflict** — `site_id` already exists. `{ "error": "site_id already exists", "site_id": "B-010" }`

**422 Unprocessable Entity** — the body is not the shape above (FastAPI's default).

**503 Service Unavailable** — the database is unreachable or `sites_shape` reports the table
unusable. `{ "error": "sites table unavailable" }`

## Idempotency

Optional `Idempotency-Key` header. When present, a repeated POST with the same key inside
24 hours returns the original 201 body instead of creating a second site. A double-click on
"Write the record" must never make two buildings; without the header the frontend must
disable the button while the request is in flight.

## Follow-ups this unlocks (not part of this endpoint)

- `PATCH /api/energy/buildings/{site_id}` — correct a record from the same form.
- `GET /api/energy/buildings/{site_id}` — one row, for a detail drawer.
- The documents step: ingestion already accepts a building reference (`site_ref` on
  compliance certificates, `site_id` on meters). The new `site_id` is what step 3 hands to it.
- `POST /api/energy/buildings/profile` (exists) — the energy profile (GIA, TM46 type) once
  the building has one; not needed to hoist.

## Frontend wiring, when approved

One change, in `hoistPrepare` (`apps/frontend/src/logic/hoistBuilding.js`):

- add `createBuilding: (body) => apiFetch(B, '/api/energy/buildings', { method: 'POST', body, timeoutMs: 20000 })`
  to `src/api/energy.js`;
- in `hoistPrepare`, after validation, call it with the prepared body; on 201 call `bldLoad()`
  so the table shows the row, move to step 2 with the returned `site_id`, and flash
  "B-010 written to plenum_cafm.sites"; on 400 map `fields` onto the form's errors; on 409 put
  the message under Building ID.
- Until the endpoint responds, step 2 keeps saying nothing was written.
