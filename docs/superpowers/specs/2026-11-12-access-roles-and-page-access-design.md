# Access roles and page access — design

12 Nov 2026. Branch `eval-2`. Status: awaiting Hussain's approval. Nothing is built yet.

## 1. Intent

Today a company admin invites a user and allocates buildings. The building is the only boundary: a plain user sees every page, for their buildings only.

Hussain wants a second boundary. When inviting or editing a user, the admin can leave them a **normal user** (today's behaviour) or give them a **role**. A user with a role opens only the pages the admin ticked, and data from every other page is withheld from them everywhere: the screens, the chat, and the API itself.

The example Hussain gave: a Compliance officer with Compliance and Buildings ticked sees those two pages and nothing else, and the chat will not answer from energy or vendors data.

## 2. What Hussain decided

1. Four roles: **Portfolio Management**, **Property Manager**, **General User**, **Compliance Officer**.
2. A user is either a normal user or has one role.
3. A role is a label. It ticks nothing by itself: **no page is pre-ticked**, the admin ticks every page by hand.
4. The six pages that can be ticked are Buildings, Compliance, Energy, Vendor performance, Vendor operations and Assets.
5. A user with a role **never sees the instance picker**.
6. The restriction is real. It holds in the chat ("restricted from orchestrator also") and "overall", which means the server refuses the data, not only the menu.
7. Confirmed from my list ("perfect, go ahead"):
   - a role needs at least one page ticked before the invitation sends;
   - a role user lands on Home, which shows only what the ticked pages feed;
   - ticking Compliance includes the Compliance Graph;
   - chat, Support, the user's own sessions and reports stay for everyone, limited to the ticked pages' data;
   - a ticked page never shows an unticked page's data, following the 2 Nov module rules;
   - the "Can ingest data" toggle stays separate and unchanged;
   - role and pages can be edited later from the user's row, with no new invitation;
   - normal users, admins and superadmins are unchanged;
   - two new database columns are needed, I write and prove the migration, Hussain runs it.

## 3. Goals, non-goals, anti-goals

**Goals**

- An admin can assign a role and tick pages at invite time and later.
- A role user's screens, chat answers and API responses contain only data owned by a ticked page.
- A user with no role behaves exactly as today, to the byte. This is the regression bar.
- A new route or chat tool added later cannot silently escape the restriction: a test fails until it is tagged.

**Non-goals**

- Default pages per role, custom role names, or roles that mean different permissions.
- Read-only versus read-write per page. A ticked page is the page as it is today.
- Different pages per building for one user.
- Any change to the admin view, the superadmin console, or the instance picker for normal users.
- Restricting what a user with "Can ingest data" may upload. Ingestion keeps its own toggle.
- Trace Lab and the eval module. They are a separate module and are not touched.

**Anti-goals** (it would be a failure even if it "works")

- Hiding the menu while the API still answers. A role user who types a URL or calls an endpoint directly must get nothing.
- Trusting the browser to say which pages a user holds.
- A role user seeing red error banners for pages they do not hold. Unticked pages are absent, not broken.
- Any change in behaviour for a user without a role.

## 4. Vocabulary

"Role" already means `user | admin | superadmin` in the code (`users.platform_role`). To keep the two apart:

| Term | Meaning |
|---|---|
| platform role | `user`, `admin`, `superadmin`. Unchanged. |
| **access role** | The new label. One of `portfolio_management`, `property_manager`, `general_user`, `compliance_officer`, or none. Shown on screen as "Role". |
| **pages** | The ticked page keys. A subset of `buildings`, `compliance`, `energy`, `vendors`, `ops`, `assets`. These are the report ids `logic/instances.js` and `svc-deepagents/src/agents/spaces.py` already use. |
| normal user | platform role `user` with no access role. Pages are unrestricted. |
| role user | platform role `user` with an access role. Restricted to their pages. |

The ticks are labelled with the page names a user sees in the navigator, with the space name as a hint where they differ (my call): Buildings, Compliance, Energy, Vendors (`vendors`, the Vendor performance space), Maintenance (`ops`, the Vendor operations space), Assets. The Compliance Graph (`graph`) has no tick of its own; it comes with `compliance`.

## 5. The data model

Two nullable columns on `plenum_cafm.users` and the same two on `plenum_cafm.invitations` (the invite flow writes both tables and the accept step copies from the invitation):

- `access_role varchar(32)` — NULL for a normal user.
- `allowed_pages text[]` — NULL for a normal user; one to six page keys for a role user.

One more column on `plenum_cafm.users`:

- `access_narrowed_at timestamptz` — set when an existing account's access gets narrower (see §9.4). NULL otherwise.

Rules, enforced in the server, not by the client:

- An access role requires at least one page, and every page must be one of the six keys.
- Pages without an access role are refused.
- An access role can only be given to platform role `user`. Giving one to an admin is refused; making a role user an admin clears the access role and pages.
- Clearing the access role clears the pages. The user is a normal user again and gets the instance picker back.
- Reading fails closed: an unknown page key is dropped; an access role with no valid page left means the user holds no page.
- Deleting a user (the existing soft delete) clears these columns with the rest of the personal details.

**Migration.** One new idempotent file in `svc-operations-intelligence/migrations/` (`ADD COLUMN IF NOT EXISTS`, comments, no backfill, no constraint that could fail on existing rows). I validate it on a scratch Postgres. **Hussain runs it. I never write to a database.** On Azure the service applies migrations on start, so it lands with the deploy.

**Running before the migration (my call).** The sign-in query runs on every request. If it named a column that does not exist yet, every request would fail and nobody could sign in. So the service checks once whether the columns exist (the same pattern as the existing key-shape check in `engines/auth/keys.py`). If they do not: everybody is a normal user, and assigning a role answers with a plain message saying access roles are not set up on this database yet. A missing check result is re-tested at most once a minute so a migration applied later is picked up without a restart.

## 6. Identity: one source, read on every request

`svc-operations-intelligence` owns identity. `principal_from_token` already reads the user's row on every request, and every other service (svc-udr, svc-work-order-management, cafm-connector-service, svc-deepagents, svc-ai-schema-mapper) resolves the caller by calling `GET /api/auth/me` with the caller's token and caching the answer for 60 seconds.

So the pages travel the same way the buildings do:

- `tokens.Principal` and `access.Scope` gain `access_role`, `pages` (`None` = unrestricted, a frozenset otherwise), `access_narrowed_at`, and a single question, `allows_page(page)`.
- `GET /api/auth/me` returns `access_role`, its label, and `pages` (`null` = every page).
- Each other service's `Principal` keeps `pages` from that answer. `svc-deepagents` also keeps `access_narrowed_at`.
- Admins and superadmins always have `pages = None`, whatever the row says.
- A superadmin acting as a company, and `AUTH_ENFORCE_SCOPE=false`, are unrestricted, as today.

**Timing.** A change takes effect on the next request in ops-intelligence and within 60 seconds in the other services (their cache). That is the same delay a building change has today. Stated, not fixed.

## 7. The rule that decides who owns a piece of data

URL prefixes do not match pages. `/api/energy/*` serves the buildings register, the building graph, asset condition, energy anomalies and the Home score. So enforcement cannot be "the energy router needs the Energy page".

The rule:

> Every route, and every chat tool, is tagged with the page or pages whose **own register** it serves. A caller needs at least one of those pages. Being *displayed* on another page does not add a tag.

Where one response mixes registers, the server removes the parts whose owner page the caller does not hold, rather than refusing the whole response.

Tags are one of: the six page keys; `shared` (identity, Support, the caller's own sessions, building names); `ingest` (governed by "Can ingest data"); `admin` (already admin-only). A route with no tag fails a test (§10).

### 7.1 Owner pages (my calls, for Hussain to read)

| Data | Owner |
|---|---|
| Buildings register and detail, building create/edit/delete, building graph (`/graph/shape`, `/graph/tables`, `/buildings/{id}/graph`), sections, the building's documents tree | `buildings` |
| Building names and codes used as labels and in pickers | `shared`, from the sign-in record (`/api/auth/me` already returns them) |
| Certificates, accreditations, coverage, country pack, scans, evidence pack, statutory lists, MEES, the Compliance Graph | `compliance` |
| Vendor scorecards, contracts, SLAs, invoices, weights, work-order scores, workbook extras, the vendor master list and contacts | `vendors` |
| Which vendor serves which building (`vendor-buildings`) | `vendors` or `compliance` |
| Vendor names on a certificate or a work order | label; travels with the row that names it |
| Work orders, maintenance overview and decisions, inspections, PPM, journeys, blockers | `ops` |
| Assets register, locations, condition, value at risk, asset intelligence, investigate, criticality, spare parts, an asset's own work history | `assets` |
| Energy anomalies, meters, market profiles, ratings position, tariffs, degree days | `energy` |

Cross-register parts that are stripped, following Hussain's 2 Nov decisions:

- A vendor's Blocked status and score stay wherever the vendor shows. Certificate and accreditation detail needs `compliance`.
- Maintenance decisions raised by Compliance need `compliance` as well as `ops`. Today the browser strips them (`maintenanceLive.js`); the server will.
- Buildings without `compliance` carry no certificate counts or certificate nodes.
- Asset screens without `energy` carry no energy anomaly signals; without `compliance`, no certificates for the asset.

### 7.2 Mixed lists: filter rows, do not refuse

| Endpoint | How a row's owner is known | Behaviour for a role user |
|---|---|---|
| `/api/approvals` (Pending queue) | `source_feature` (A compliance, B vendors, C energy, P platform) and `related_entity_type` | Only rows whose owner page is held. Deciding or emailing a row needs its page. |
| `/api/value/summary`, `/savings` | `modules[].key` (energy, vendors, maintenance→`ops`, compliance, assets) | Only held modules; totals recomputed from them; a P&L head that mixes modules shows only when all of its modules are held. |
| `/api/crons` | job key → catalogue module | Only jobs of held pages. The free-form question job belongs to no page: it is company-owned, created by an admin and answered unrestricted, so it and its stored answers are hidden from role users. |
| `/api/reports` cards and their run history | the card's owner, the run's time | A role user sees only cards they own, and only runs made after `access_narrowed_at` (§9.4). A card run for a role user is answered under that user's pages (§9.5). |
| `/api/documents`, document search, document links | `doc_type` and entity links | See §7.3. |
| Hoist Score (`/api/energy/hoist-score`) | composite | Shown only when the caller holds every page it is computed from. The builder reads the components from the engine. |
| WOM `/api/dashboard/stats` | ops and assets counts | Each count only with its page. |

### 7.3 Documents (my call)

A document is visible when the page that owns its type is held: certificate → `compliance`; contract, invoice → `vendors`; energy bill → `energy`; a document linked to an asset → `assets`. Anything else lives in a building's documents tree and needs `buildings`. Chat document search and "open this file" links follow the same test. This closes the gap noted on 2 Nov (document search reaching another module's documents) for role users.

### 7.4 Actions that cross pages (my call)

A button on a ticked page can read or write another page's register: "Raise work order" on Assets creates a work order (`ops`); "Create vendor" on a certificate writes the vendor master (`vendors`).

- An action needs **every** page whose register it creates or changes a record in. If one is missing the control is not shown, and the route refuses. A role user never creates a record in a register they cannot see.
- Picking from another register by name is not reading it. A lookup that returns only ids and names (the vendor list when raising a work order, the asset list on a work order) is allowed wherever a ticked page's own record names that thing.
- Because a refused call is never shown as an error (§12), every such control must be hidden in the client as well; a visible button that silently does nothing is a defect.

The plan lists every cross-page action with the pages it needs, in the same table as the routes, for Hussain to read before code.

## 8. Enforcement in each service

One helper per service, the same shape everywhere: a FastAPI dependency `require_page(*any_of)` that answers `403 {ok: false, reason: "page_not_allowed", page: "<key>", error: "<plain sentence>"}`. It is a no-op when `pages is None`, so normal users, admins and superadmins take the code path they take today.

| Service | What gets tagged |
|---|---|
| svc-operations-intelligence | Every route in `compliance.py`, `energy.py`, `contract_performance.py`, `approvals.py`, `value.py`, `reports.py`, `crons.py`, `document_search.py`, `maintenance_watch.py`, `replacement_candidates.py`, `work_order_blockers.py`, `ingestion.py`. Per route, because the files mix owners (§7). |
| svc-work-order-management | `work_orders`, `maintenance`, `ppm`, `journeys`, `email` → `ops`; `assets`, `locations`, `asset-categories` → `assets`; `dashboard` mixed; decisions stripped per §7.1. |
| cafm-connector-service | `/api/v1/plenum/*` by resource: assets, locations, readings, spare parts → `assets`; vendors, contacts, contracts, SLA policies → `vendors`; work orders, maintenance plans, technicians → `ops`. |
| svc-udr | The generic table routes are already admin-only, so a role user cannot reach them. `/api/spaces` and `/api/udr` are tagged; saved-space figures follow the pages. |
| svc-ai-schema-mapper | Migration is `ingest`. The `doc-rag` routers mounted inside it are covered in §11. |
| svc-deepagents | §9. |

The full route table is produced in the implementation plan as one reviewed list per service, before code. Ambiguous rows are settled by §7's rule.

## 9. The chat

Today the active space comes from the browser (`body.space`) and only four engines are gated. Everything else is open: the general tool loop, the `task()` sub-agents, the raw table tools, document search, Support sessions, `/resume`.

For a role user the chat must hold these invariants:

1. **The pages come from the server.** They are read from the caller's principal where the request arrives (the four entry points in `api/routes/workflow.py`, plus `/resume`) and held in a context variable beside `caller_principal`. The effective allowance is the intersection of the pages and whatever space the browser declared, so the browser can only narrow.
2. **The model is never handed a tool it may not use.** Every tool carries a page tag in one registry. Wherever a tool list is bound (the main loop's `ALL_TOOLS`, each engine's list, the sub-agent registry in `meta_tools.py`), tools outside the caller's pages are removed. The same check runs again at execution, because sub-agents build their own lists.
3. **Engines outside the pages are out of module.** The router and planner use the existing out-of-module path, so the user is told plainly that the question is outside the pages they have access to. No internal names in that sentence.
4. **Generic table tools fail closed.** `query_table`, `udr_execute_select`, `udr_read_records`, `udr_agent_query` and the hybrid tools can read any table. For a role user they are limited by a table → page map; a table not in the map is refused. The builder may instead withhold these tools from role users entirely if the map proves too large; either way an unlisted table is never readable.
5. **Nothing from an unticked page reaches the prompt.** That covers page context sent by the browser (including a `plenum_forced_route=` line, which must still pass the gate), documents (§7.3), saved memories, and earlier turns.

### 9.4 Earlier conversations

Turns are stored with their answers and re-injected into later turns. A brand-new role user has no history, but an existing user whose access is narrowed does, and it may hold answers from pages they no longer have.

Rule (my call): when an existing account's access gets narrower — a role is given to a user who was a normal user, or a page is removed — `access_narrowed_at` is set to now. A role user's thread list, thread read, trace turns and conversation context include only turns created after it. Nothing is deleted; if the user is made a normal user again, everything is visible again. Widening access does not move the timestamp.

### 9.5 Runs made on a user's behalf

Report cards call the chat from ops-intelligence with no space. A card owned by a role user must be answered under that user's pages. The builder traces which identity those runs carry and makes the owner's pages apply. The cron question job is admin-owned and stays unrestricted; role users do not see it (§7.2).

### 9.6 Saved memories (my call)

Chat memories are company-wide free text with no page. They are recalled into every turn and listed by `GET /api/memories`. For a role user, both the recall and the list are limited to memories that user saved themselves after `access_narrowed_at`. Company-wide corrections therefore do not reach a role user's chat. Tagging memories by page would need a new column and a classifier and is out of scope.

## 10. Completeness guard

The feature is only as good as its coverage, and coverage decays when someone adds a route. So each service gets a test that walks the application's route table and fails if any route is untagged, and svc-deepagents gets one that fails if any tool in `ALL_TOOLS`, the engine lists or the sub-agent registry has no page tag. A second test per service asserts that a caller with `pages = None` passes every gate.

## 11. Routes with no sign-in at all (decision needed)

Mapping the services found routes that answer without any token. They are older than this feature, but they defeat it, and they defeat the building boundary today:

- svc-deepagents: `GET /api/workflow/activity`, `/activity/{sid}`, `/activity/{sid}/turns` (full inputs and outputs of any session), `/workspace/{sid}`, `/status/{sid}`, `/resume/{sid}`, `/api/ingest/batches`.
- svc-work-order-management: `/api/approvals`, `/api/work-orders/approvals`, `/api/chat` including `/{sid}/history`.
- svc-ai-schema-mapper: the `doc-rag` routers mounted at `/doc-rag/*` are open to any signed-in user, including `GET /doc-rag/row-index/tables/{table}/rows`, which returns every row of any `plenum_cafm` table with no company filter.
- doc-rag-main, svc-ingestion, svc-query have no auth of their own; whether the gateway exposes them differs per deployment.

**Recommended:** close the ones a signed-in role user can reach through the gateway as part of this work — require sign-in and an owner check on the deepagents and work-order routes, and make the generic `doc-rag` row routes admin-only — after tracing every internal caller so no service-to-service call breaks. Report the rest to Bala. The alternative is to leave all of them and record them as known gaps, in which case "restricted overall" is true for the product's screens and chat but not against someone who knows these URLs, and the end-to-end check in §14 is limited to routes that already require sign-in.

Closing them changes behaviour only for callers with no token. The browser already sends the token on every call, so a signed-in normal user sees no difference; that keeps the regression bar in §3. It does mean editing work-order and doc-rag routes that other people own, which is why this is Hussain's decision and not mine.

## 12. The frontend

**Users & access (admin).** The invite form gains a Role choice: "No role — normal user" and the four roles. Picking a role reveals "Pages this user can open": the six pages, none ticked. "Send invitation" stays disabled, with the reason shown, until one is ticked. The users table shows the role beside the name; the expanded row carries the same role choice and ticks, saved with the existing `PATCH`. Existing layout, components and wording style are kept.

**A role user's session.** `logic/instances.js` already narrows the navigator, Home cards, the Pending queue, sessions, crons and page blurbs to an instance's `reports`. A role user is given a `reports` list built from their pages (plus `graph` when `compliance` is held), which those same helpers read. So:

- `usesInstances` is false for a role user: no picker after sign-in, no "Switch instance", straight to Home.
- `instanceReports` returns the role user's list; the top-bar chip shows the role name.
- **Home is the full-platform Home with everything fed by an unheld page removed** (my call). It is not one of the three instance Homes, which are hand-built per instance. No new cards, pinned questions or wording are composed for a role.
- Buildings can now be unticked, which no instance ever did. The Buildings page, its navigator entry, and building-register detail on other surfaces need the same gating the other modules have. Building names stay as labels.
- Loaders for unticked pages are not called. Today `loadLiveData` fetches every register for every user; for a role user that would be a burst of refusals.
- A `page_not_allowed` answer is never shown as an error. It makes the client re-read `/api/auth/me` and re-scope, which also covers an admin changing the ticks mid-session.
- Opening a view that is not held falls back to Home.
- Home shows only cards fed by held pages, with real data or nothing (the no-dummy-data rule).

Admin view, superadmin console and normal users' instance picker are untouched.

## 13. Constraints

- **No database writes by me.** Scratch Postgres for tests; Hussain runs the migration.
- **No commit, push, stash or reset** unless Hussain names it.
- **Shared working tree.** Other sessions edit `Hoistra_Plenum` at the same time, and `eval-2` currently has uncommitted Trace Lab edits that are not mine. No container rebuild without checking what else it would ship.
- **Shared test database, older code on Azure.** Azure's services read the same `hoistra_test` database with older code that ignores the new columns. A role assigned from a local build does **not** restrict that user on Azure until Azure is redeployed. Do not give a role to a real person before then.
- **Eval golden rule.** Trace Lab is not touched.
- **No internal names on screen**, in refusals or in chat answers.
- Test-first, per the project's practice. Suites to keep green: ops-intel, work-order unit, deepagents, frontend.

## 14. Verification

- Unit tests for the model rules, the gate, each mixed-list filter and the frontend scope helpers.
- The completeness guards of §10.
- An end-to-end run on a scratch Postgres: invite a Compliance Officer with Compliance and Buildings, accept, then confirm the two pages load, every route owned by another page answers `page_not_allowed`, the Pending queue and value card carry only compliance rows, and a chat question about energy or vendors is declined while a compliance question is answered.
- The same user walked in Chrome against a stub backend: no picker, two pages in the navigator, no error banners.
- A normal user and an admin walked through the same build to confirm nothing changed for them.

## 15. Left to the builder

- The full route table and tool registry (§8, §9), by §7's rule.
- Table → page map versus withholding the generic table tools (§9, point 4).
- The exact shape of the Role control and where the role sits in the users table, within the existing screen's style.
- Whether the server also serves role and page labels, or the client keeps them.
- Build order. Suggested: model and identity; ops-intelligence gate; the other services; chat; frontend. The role control is the last thing switched on, so a half-built restriction is never offered to an admin.
