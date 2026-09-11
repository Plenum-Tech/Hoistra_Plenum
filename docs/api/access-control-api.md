# Access control, companies and usage — API contract

Generated from the running FastAPI schema (`app.openapi()`), so it cannot drift from the code.

## The rules every endpoint follows

- **Every** `/api/energy`, `/api/compliance`, `/api/contract-performance` and `/api/approvals` route now requires `Authorization: Bearer <access token>`. No token → `401 {reason: "missing_token"}`.
- The **company is the caller's**. Do not send `organization_id` from the client on those routes — not in the query string, not in a JSON body, not in a form field. Omitted, it is the caller's company; naming another company is refused with `403 {reason: "wrong_organization"}` for a user or admin. A superadmin may name a company (`?organization_id=` or in the body) to act as it.
- **Only an admin creates buildings.** `POST /api/energy/buildings` from a plain user → `403 {reason: "admin_required"}`. The new building belongs to the caller's company.
- The **building is the access boundary**. A plain user sees only buildings allocated to them; a building named in a path they are not allocated to → `403 {reason: "building_not_allocated"}`. List endpoints (`/api/energy/buildings`, `/api/compliance/certificates`, `/api/contract-performance/contracts`, `/invoices`) are narrowed to their buildings server-side. Admins see their whole company.
- **Ingestion is per user.** `GET /api/auth/me` returns `can_ingest`; the deep-agents upload endpoint and the direct ingest routes (`/api/energy/readings/ingest`, `/readings/ingest/csv`, `/api/compliance/documents/ingest`, `/ingest-batch`, `/verification-dumps/ingest`, `/api/contract-performance/contracts/ingest`) refuse a user without it → `403 {reason: "cannot_ingest"}`, and a restricted user must send `building_id` (one of theirs) → else `400 {reason: "building_required"}` / `403 {reason: "building_not_allocated"}`.
- Roles are ranked: `user` < `admin` < `superadmin`. `GET /api/auth/roles` lists them.

## What `GET /api/auth/me` now returns (drive the shell from this)

```json
{ "ok": true, "user": {
    "id": "…", "email": "…", "full_name": "…", "organization_id": "…",
    "role": "user|admin|superadmin", "status": "active|invited|suspended",
    "can_ingest": true,
    "building_ids": ["…"],        // null = unrestricted (admin/superadmin); [] = allocated to nothing
    "all_buildings": false,
    "buildings": [{"id": "…", "name": "Riverside Court", "building_code": "B-006"}]
}, "session_id": "…" }
```

## Screen → endpoints

| Screen | Endpoints |
|---|---|
| Super Admin · Companies on the platform | `GET /api/superadmin/companies` (list + credits this month), `POST /api/superadmin/companies` (create; optional `admin_email` invites in one step), `GET /api/superadmin/companies/{id}` (usage card), `POST /api/superadmin/companies/{id}/invite-admin`, `GET /api/superadmin/credits` (bars) |
| Admin · Users & access | `GET /api/admin/users` (table + `summary` tiles), `POST /api/admin/users/invite`, `PATCH /api/admin/users/{id}` (buildings / can_ingest / status), `DELETE /api/admin/users/{id}` (suspend), `GET /api/admin/buildings` (allocation chips), `GET /api/admin/usage` |
| Admin · Ingestion audit trail | `GET /api/admin/ingestion-audit?outcome=accepted|reassigned|overridden|rejected|approved_on_confirmation` |
| Accept invitation (public page) | `POST /api/auth/invitations/accept {token, password, full_name?}` then normal `POST /api/auth/login` |

## Usage card fields (`GET /api/superadmin/companies/{id}`)

`buildings_created`, `hoist_graphs`, `last_activity`, `udr_data_bytes`, `compliance_certificates`, `certificate_countries[]`, `api_requests_30d`, `credits_this_month`, `credits_total`, `users{total,active,invited,can_ingest}`, `pending_invitations`, plus `counted_from{}` naming the source of every number. Credits: `query`=1, `ingest`=5, `api_request`=0.1 (`tariff` is returned too).

## Endpoints

### `GET /api/admin/buildings`

The company's buildings — the chips on the invite form.

Parameters: `organization_id` (query)

### `GET /api/admin/ingestion-audit`

Every flagged ingestion, clarification, override and approval for the company.

Parameters: `outcome` (query), `limit` (query), `offset` (query), `organization_id` (query)

### `GET /api/admin/usage`

Usage across the company's buildings and users.

Parameters: `organization_id` (query)

### `GET /api/admin/users`

Every account in the company with what the table shows: buildings, ingestion, usage, status.

Parameters: `organization_id` (query)

### `POST /api/admin/users/invite`

Invite by email; the allocation and ingestion right apply on activation.

Parameters: `organization_id` (query)

Body `InviteUser`: `full_name`*, `email`*, `building_ids`, `can_ingest`, `job_title`, `role`

### `PATCH /api/admin/users/{user_id}`

Change what a user may see and do. Takes effect on their next request — the
principal is read from the database every call, not from the token.

Parameters: `user_id`* (path), `organization_id` (query)

Body `PatchUser`: `building_ids`, `can_ingest`, `job_title`, `full_name`, `status`

### `DELETE /api/admin/users/{user_id}`

Suspend, never delete. The audit trail names this person and must keep doing so.

Parameters: `user_id`* (path), `organization_id` (query)

### `POST /api/auth/invitations/accept`

Public: follow an invitation link, set a password, activate the account.

Body `AcceptInvitation`: `token`*, `password`*, `full_name`

### `POST /api/auth/login`

Exchange an email and password for an access token and a refresh token.

Body `SignInRequest`: `email`*, `password`*

### `GET /api/auth/me`

The signed-in account. Used by a client to check a stored token still works.

### `POST /api/auth/refresh`

Trade a refresh token for a new pair. The old one stops working immediately.

Body `RefreshRequest`: `refresh_token`*

### `POST /api/superadmin/companies`

Create the company account. With admin_email, invite its administrator in one step.

Body `CreateCompany`: `name`*, `country_code`, `admin_email`, `admin_name`, `industry`, `timezone`

### `GET /api/superadmin/companies`

Every company on the platform, with its credits this month and lifecycle.

### `GET /api/superadmin/companies/{organization_id}`

One company's usage card: buildings, graphs, activity, UDR volume, certificates,
countries, API requests, credits, users.

Parameters: `organization_id`* (path)

### `POST /api/superadmin/companies/{organization_id}/invite-admin`

Invite Company Admin

Parameters: `organization_id`* (path)

Body `InviteAdmin`: `email`*, `full_name`

### `GET /api/superadmin/credits`

Credit consumption across companies this month — the bars on the console.


`*` = required. All errors are `{ok:false, error, reason}` under `detail`.
