# Connector service authentication, certificate backfill, and the portfolio asset API

Answers the four points raised after the MEES tile and Assets page review. Read the
"What breaks" section first: point 1 changes every call you currently make to
cafm-connector-service.

---

## 1. cafm-connector-service now requires a token

**What was true.** `get_current_user` returned `TokenPayload(sub="anonymous")` and the real
JWT check sat commented out inside a string literal. 135 CRUD routes under `/api/v1/plenum`,
14 connector-management routes holding the credentials for customer source systems, and 8
Table Editor routes that can add or drop columns were all reachable by anyone who could
reach the port, against the production database. Confirmed and fixed.

**What is true now.** Identity is resolved the same way svc-work-order-management and
svc-deepagents resolve it: by asking operations-intelligence `GET /api/auth/me` with your
token. The connector service does not decode the token and does not hold the signing secret.

Send the bearer you already use for the other two services:

```
Authorization: Bearer <access token>
```

| Situation | Status | `detail.reason` |
|---|---|---|
| No `Authorization` header | 401 | `missing_token` |
| Expired or unknown token | 401 | `invalid_token` |
| Valid token, Table Editor, not an admin | 403 | `admin_required` |
| Writing a record into another company | 403 | `wrong_organization` |
| Writing against a building you are not allocated to | 403 | `building_not_allocated` |
| operations-intelligence unreachable | 503 | `identity_unavailable` |

The error body matches the other services, so one handler covers all three:

```json
{"detail": {"ok": false, "error": "Send an Authorization: Bearer <token> header.",
            "reason": "missing_token", "code": "missing_token",
            "message": "Send an Authorization: Bearer <token> header."}}
```

### Tenancy no longer comes from the query string

Every one of those routes took `organization_id` as an optional query parameter and filtered
on it if present. That is the whole tenancy story it had. The company is now taken from your
token and applied by the session, so:

- `?organization_id=<someone else's>` no longer returns their rows. It returns yours.
- Omitting `organization_id` no longer means "every company". It means yours.
- Pagination totals are filtered too, so `X-Total-Count` style figures match the rows.
- `POST`/`PUT` that omit `organization_id` are stamped with yours instead of writing a null.
- A **superadmin** is not narrowed, by design.

Building allocation applies to `assets`, `work_orders` and `locations` — the three tables
that carry a populated `building_id`. Other tables are company-scoped only; use the
building-scoped services for building-restricted reads.

### Table Editor

`/table-editor/**` now needs a token **and** an admin role. It reads and writes arbitrary
rows in a table named at request time and can add or drop columns, so no per-row company
filter can be expressed over it — the gate is the role. The `?confirm=true` requirement on
DDL is unchanged.

CORS on that sub-app was `allow_origins=["*"]` with `allow_credentials=True`. It now names
origins explicitly via `TABLE_EDITOR_CORS_ORIGINS` (comma-separated, default
`http://localhost:3001,http://127.0.0.1:3001`) with credentials off, since the token travels
as a header rather than a cookie.

### What breaks

Any current call to cafm-connector-service without a bearer token starts returning 401. That
is the point, but it is a breaking change to the Table Editor UI and to anything in the shell
still pointing at port 8000 or 8005. Set `OPERATIONS_INTELLIGENCE_BASE_URL` if the service
does not run in the same container as operations-intelligence (default
`http://127.0.0.1:8009`).

---

## 2. Certificates can be re-linked to their building

**What was true.** `attach_to_graph` guesses a certificate's building once, at ingest. If the
guess missed, nothing ever asked again, and that certificate was invisible to every
building-scoped view while sitting in the register looking correct. There was a backfill for
the separate `site_id` link and none for `building_id`. Confirmed.

### `POST /api/compliance/coverage/backfill-building-links`

| Parameter | Default | Meaning |
|---|---|---|
| `dry_run` | `true` | Report what would be linked; write nothing |
| `limit` | `1000` | Certificates to examine |
| `organization_id` | your own | Superadmin only |

It asks the certificate's own fields first (name, reference, site — the same resolver the
ingest uses), then the building its source document sits on, and records which of the two
decided it. Four outcomes, kept separate because they need different actions:

| Outcome | Meaning | Action |
|---|---|---|
| `linked` | Resolved, with `basis` of `certificate` or `document` | none |
| `ambiguous` | Matches more than one building | a person chooses |
| `no_evidence` | Names nothing; `missing` lists the empty fields | someone says which building |
| `not_applicable` | Covers a contractor, not a building | none — a null is correct here |

```json
{"ok": true, "dry_run": true, "examined": 21,
 "counts": {"linked": 0, "ambiguous": 0, "no_evidence": 3, "not_applicable": 18},
 "no_evidence": [{"certificate_id": "…", "certificate_type_code": "EICR",
                  "cert_scope": "Building", "reason": "no_match",
                  "missing": ["building_name", "building_reference", "site",
                              "document_on_a_building"]}]}
```

**What the dry run says about today's data.** On the database behind the deployed app, all 11
unlinked certificates are vendor accreditations, where a null building is the correct answer;
nothing is actually broken. On the other database, 18 are vendor accreditations and 3 are
building certificates (two EICR, one fire alarm) that name no building, no reference and no
site, and whose source documents are on no building either. So re-running name matching
recovers zero certificates — the names were never there to match. The backfill exists so
they link the moment evidence appears, and it names exactly what is missing rather than
guessing a building, which would put a legal obligation on a building on no evidence.

---

## 3. An account with no allocation

`role` and `platform_role` are different columns and only one of them controls access.

- **`role`** is a free-text job title — "HVAC Specialist", "Facilities Director". It is null
  on plenty of accounts including admins and superadmins, and it grants nothing. A blank one
  is not the cause of empty lists.
- **`platform_role`** is the access role: `user`, `admin`, `superadmin`.

Empty lists across all three services come from `platform_role = 'user'` **and** zero rows in
`user_buildings`. That combination is the intended rule, not a fault: an admin sees the whole
company, a user sees the buildings they are allocated to, and a user allocated to nothing sees
nothing.

This is widespread rather than specific to one account. On the database the local backends
read, 5 accounts have `platform_role = 'user'` with a null job title, and only 2 accounts in
the whole table have any `user_buildings` rows at all.

I have not changed any account, because the report does not say which one is yours and
granting an access role to the wrong person is not a guess worth making. Tell me the email
and which you want, or run it directly:

```sql
-- see the whole company (the usual fix for a developer or staff account)
UPDATE plenum_cafm.users SET platform_role = 'admin' WHERE email = '<your email>';

-- or allocate specific buildings and stay a plain user
INSERT INTO plenum_cafm.user_buildings (user_id, building_id)
SELECT u.id, b.building_id FROM plenum_cafm.users u, plenum_cafm.buildings b
 WHERE u.email = '<your email>' AND b.name IN ('<building>', '<building>')
ON CONFLICT DO NOTHING;
```

`GET /api/auth/me` already reports `platform_role` and `building_ids`, so the page can tell
"you are allocated to nothing" apart from "there is nothing here" without guessing.

---

## 4. Portfolio assets and category names

Both live on svc-work-order-management, which is properly scoped.

### `GET /api/assets`

No parameter is required — this is the portfolio list. Every filter listed below is applied;
`asset_type`, `location` and `active` remain accepted and ignored, as before, because the real
table uses UUID foreign keys and a status string.

| Parameter | Meaning |
|---|---|
| `q` | Substring of name, **code or serial number** |
| `building_id` | Narrow to one building. 403 if you are not allocated to it, rather than an empty list |
| `category_id` | Compared as text, so it works whether the key is a UUID or an integer |
| `criticality`, `status` | Exact match on the value as stored |
| `page`, `limit` | 1-based, `limit` up to 200 |

The pre-paging total comes back in the **`X-Total-Count`** header. The response stays a plain
array, so nothing you already parse changes shape.

New fields on each asset, all real columns that were simply never mapped in this service:
`asset_code`, `status`, `category_id`, **`category_name`**, `location_id`, `criticality`,
`health_score`, `installation_date`.

`category_name` is resolved in one extra query per page, so a UUID becomes a word without a
second service. `GET /api/assets/{id}` returns it too.

### `GET /api/asset-categories`

```json
[{"category_id": "7", "name": "Chillers", "description": null,
  "parent_category_id": null, "asset_count": 1}]
```

Company-wide, not building-scoped: the table has no building column. The column names differ
between databases — one spells it `name`/`parent_id` keyed by UUID, the other
`category_name`/`parent_category_id` keyed by integer — so the service reads the spelling from
`information_schema` once per process and compares keys as text. Both work without a flag.

Your change adding `category_id`, `criticality`, `health_score` and `installation_date` to
this service's Asset model was not on `Hoistra_Frontend`, so the same columns are mapped here
along with `location_id`, `asset_code` and `status`. Expect a small conflict if you push yours
separately.
