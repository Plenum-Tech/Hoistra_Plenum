# Building-scoped access, selected building, inactive & deleted users — API contract

Backend only. Nothing in the frontend changes until the frontend developer wires these; every
endpoint below is live on the same bearer token the app already sends on every base.

## What changed, in one paragraph

Every query a signed-in person makes — compliance, vendor contracts, scorecards, insights,
invoices, asset criticality, energy, assets, locations, work orders, the dashboard, and every
question the chat agent turns into SQL — is now narrowed **in SQL, before the limit**, to the
buildings that person may see. An admin sees the whole company. A user sees the buildings
allocated to them (`user_buildings`). Either of them may **select one building** to work in;
everything then answers for that building alone until the selection is cleared. A selection
can narrow what you see, never widen it. Accounts can now be **deactivated** (reversible) or
**deleted** (soft, irreversible: the person is scrubbed, the row and its history stay).

## `GET /api/auth/me` — drive the shell from this

```json
{ "ok": true, "user": {
    "id": "…", "email": "…", "full_name": "…", "organization_id": "…",
    "role": "user|admin|superadmin",
    "status": "active|invited|pending_verification|inactive|deleted",
    "can_ingest": true,
    "building_ids": ["…"],             // null = unrestricted (admin); [] = allocated to nothing
    "all_buildings": false,
    "selected_building_id": "…|null",  // the building they are working in now, or null
    "buildings": [{"id": "…", "name": "Riverside Court", "building_code": "B-006"}]
}, "session_id": "…" }
```

- `building_ids` is **already narrowed** by the selection: with a building selected it is a
  one-element list (for an admin, who otherwise has `null`, it becomes that one id). Read
  `selected_building_id` to know a selection is in force; read `buildings` for the chips.
- `inactive` and `deleted` accounts cannot sign in and their tokens are refused (`401`) the
  moment the state changes — no need to poll.

## Choose the building to work in

### `PATCH /api/auth/me/selected-building`

Body: `{"building_id": "<uuid>"}` to select; `{"building_id": null}` to clear (back to every
building the person holds).

| Result | When |
|---|---|
| `200 {ok, selected_building_id}` | selected or cleared |
| `403 {reason: "building_not_allocated"}` | a user chose a building they are not allocated |
| `403 {reason: "building_not_in_company"}` | an admin chose a building outside their company |

The selection is stored on the user (`users.selected_building_id`) so it survives a new
tab, a refresh and a new token. Re-read `/me` after changing it (or update local state
from the response).

## What is narrowed, and by what

| Where | Narrowed by |
|---|---|
| `GET /api/compliance/certificates`, `/certificates/count`, `/coverage/buildings`, `/coverage/vendors` | `compliance_certificates.building_id`; vendor accreditations (no building) stay visible; `/coverage/vendors` keeps only vendors with a footprint on the caller's buildings |
| `GET /api/contract-performance/contracts` | the contract's document placed on the caller's buildings **or** its vendor has a footprint there |
| `GET /api/contract-performance/scorecards`, `/insights`, `/saved-space/summary` | vendors with a footprint on the caller's buildings |
| `GET /api/contract-performance/invoices` | `invoices.building_id` |
| `GET /api/contract-performance/asset-criticality` | assets on the caller's buildings |
| `GET /api/energy/*` | unchanged — already building-scoped |
| Work-order service `GET /api/work-orders/`, `/filter/active`, `/filter/pending-approval`, `/api/assets`, `/api/locations`, `/api/dashboard/stats` | `building_id` on work orders / assets / locations |
| Chat (deep-agents) — every SQL the agents run (`query_table`, UDR reads, compliance report, open work orders) | the same rule per table: `building_id` where the table has one; vendor tables through the vendors on those buildings; assets, sites, meters through their own links |

"A vendor with a footprint on a building" = a vendor with a certificate filed for it, a
work order raised on it, or an invoice verified against it.

A user allocated to **nothing** gets empty lists everywhere (not errors), and `403` on any
building named in a path.

## Work-order service now requires the token

`/backend/work-order/api/...` routes that read or change a work order, asset or location now
take `Authorization: Bearer <token>` (the same token; it is verified against
operations-intelligence). Missing → `401 {reason: "missing_token"}`. A work order, asset or
location on a building the caller is not allocated to → `403 {code: "building_not_allocated"}`.

- `WorkOrderResponse`, `AssetResponse`, `LocationResponse` gain `building_id` (nullable).
- `POST /api/work-orders/` accepts optional `building_id`. If omitted and the caller can see
  exactly one building (allocated to one, or one selected) it is stamped automatically; a
  building the caller may not see → `403`.

## Inactive and deleted users (admin only, own company)

| Endpoint | Effect |
|---|---|
| `POST /api/admin/users/{id}/deactivate` | `status = inactive`; every live session revoked now; allocation, history and details kept |
| `POST /api/admin/users/{id}/reactivate` | `status = active`; sign-in works again with everything they had. Refused for a deleted user (`404 user_not_found_or_deleted`) |
| `DELETE /api/admin/users/{id}` | **soft delete**: `status = deleted`, email → `deleted+<id8>@invalid.local`, name → `Deleted user`, phone/title/password cleared, sessions revoked, building allocation dropped. Returns `{ok, user_id, status: "deleted", previous_email}`. Irreversible; calling it again returns `{already: true}` |
| `PATCH /api/admin/users/{id}` with `status` | accepts `active` or `inactive` (`suspended`/`disabled`/`deactivated` are folded into `inactive`). `deleted` → `400 {reason: "use_delete"}`; anything else → `422 {reason: "bad_status"}` |

Self-protection: an admin cannot deactivate or delete themselves (`400`). Non-admins get `403`.

`GET /api/admin/users` returns `status` with the new values; show `inactive` and `deleted`
distinctly (a deleted row keeps its id for the audit trail but identifies nobody).

## Errors you will see

| Status | `reason` / `code` | Meaning |
|---|---|---|
| 401 | `missing_token`, `invalid_token`, `no_account` | not signed in, token revoked, or account inactive/deleted |
| 403 | `building_not_allocated` | a building in the path / body is not one of the caller's |
| 403 | `building_not_in_company` | admin selected a building outside their company |
| 403 | `admin_required` | non-admin called an admin route |
| 400 | `use_delete` | tried to delete by setting status |
| 422 | `bad_status` | unknown status word |
