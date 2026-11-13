# Access Roles and Page Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can give a user one of four access roles and tick the pages that user may open; every service, the chat and the frontend then withhold data owned by any unticked page.

**Architecture:** `svc-operations-intelligence` stores `access_role` / `allowed_pages` on the user and returns them from `GET /api/auth/me`, which every other service already calls to identify the caller. Each service holds one reviewed table mapping every route to its owner page(s) and one dependency that enforces it; a test fails for any route not in the table. The chat filters tools by the same page tags and relies on the backends (which receive the caller's token) as the second layer. The frontend feeds the user's pages into the existing instance-scoping helpers.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / pytest (backend); LangGraph `create_react_agent` (chat); React + plain JS mixins, `node --test` (frontend); PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-11-12-access-roles-and-page-access-design.md`

All backend paths below are under `apps/backend/cafm-connector-service-final/`. Frontend paths are under `apps/frontend/`.

## Global Constraints

- **No commits, pushes, stashes or resets.** Each task ends with a suite run, not a commit.
- **No database writes.** Tests use fakes or a scratch Postgres. The migration file is written and proven on scratch; Hussain runs it.
- **Shared working tree.** Other sessions edit this tree. Edit with exact string replacements; never rewrite a file from a stale read. No container rebuild without checking for other sessions' edits.
- **Do not touch** `svc-trace-lab`, `apps/frontend/src/lab`, `src/tracelab` (eval golden rule), the admin view, the superadmin console.
- **A caller with `pages = None` takes today's code path.** Normal users, admins, superadmins and `AUTH_ENFORCE_SCOPE=false` must see no change.
- Page keys, exactly: `buildings`, `compliance`, `energy`, `vendors`, `ops`, `assets`.
- Access role keys and labels, exactly: `portfolio_management` "Portfolio Management", `property_manager` "Property Manager", `general_user` "General User", `compliance_officer` "Compliance Officer".
- Non-page tags, exactly: `shared`, `ingest`, `admin`, `public`.
- Refusal body: `403 {"ok": false, "reason": "page_not_allowed", "page": "<key>", "error": "That is not part of the pages you have access to."}`.
- No internal names (service names, table names, routes) in any text a user can read.
- Test-first: write the failing test, watch it fail, then implement.
- Suites and baselines to keep green: ops-intel `python3 -m pytest -q -p no:warnings`; work-order `python3 -m pytest tests/unit -q` (never bare pytest); deepagents in the `hoistra-testenv-svc-deepagents-1` container at real repo depth; frontend `npm test` then `npm run build` on Node 24.

## Review Focus

1. **A user row with junk in the new columns** (unknown role key, unknown page key, empty array, pages set but role NULL). Expected: fails closed to the valid pages only; never unrestricted, never a 500. → Task 1.
2. **The columns do not exist yet.** Expected: sign-in works, everyone is a normal user, assigning a role answers 409 `access_roles_not_set_up`. → Task 2.
3. **A route added later with no entry in the page table.** Expected: the guard test fails and names the route. → Tasks 3, 6, 7.
4. **An admin unticks a page while the user is signed in.** Expected: the next call is refused, the client re-reads `/api/auth/me` and re-scopes with no error banner; earlier chat turns from that page are no longer returned. → Tasks 8, 10.
5. **A role user holding only one page loads Home.** Expected: no request is sent for an unheld page, no card is empty-with-error, the Pending count counts only held rows. → Tasks 5, 10, 11.

---

### Task 1: Page vocabulary and assignment rules (pure)

**Files:**
- Create: `svc-operations-intelligence/src/engines/auth/pages.py`
- Test: `svc-operations-intelligence/tests/unit/test_access_pages.py`

**Interfaces — Produces:**
- `PAGES: tuple[str, ...]`, `ACCESS_ROLES: dict[str, str]`, `SHARED`, `INGEST`, `ADMIN`, `PUBLIC`
- `clean_pages(raw: Any) -> frozenset[str]` — unknown keys dropped, `None`/junk → empty
- `resolve(platform_role: str | None, access_role: str | None, allowed_pages: Any) -> tuple[str | None, frozenset[str] | None]` — `(None, None)` unless platform role is `user` and `access_role` is non-empty; otherwise `(access_role, clean_pages(allowed_pages))`
- `class AccessRoleError(Exception)` with `.reason`, `.message`
- `validate_assignment(*, platform_role: str, access_role: str | None, pages: list[str] | None) -> tuple[str | None, list[str] | None]` — reasons: `unknown_access_role`, `pages_required`, `unknown_page`, `pages_without_role`, `role_needs_plain_user`; returns pages de-duplicated in `PAGES` order
- `narrowed(before: frozenset[str] | None, after: frozenset[str] | None) -> bool` — True when `after` is a set and (`before` is None or `before - after` is non-empty)
- `label(access_role: str | None) -> str | None`

- [ ] **Step 1: failing tests** — `test_resolve_is_unrestricted_for_admin_even_with_a_role_on_the_row`, `test_resolve_drops_unknown_pages`, `test_resolve_role_with_no_valid_page_holds_nothing` (`== ("general_user", frozenset())`), `test_resolve_unknown_role_key_is_still_restricted`, `test_validate_needs_at_least_one_page`, `test_validate_refuses_pages_without_a_role`, `test_validate_refuses_a_role_on_an_admin`, `test_validate_orders_and_dedupes`, `test_narrowed` (None→set True; set→smaller True; set→bigger False; set→None False).
- [ ] **Step 2:** run `python3 -m pytest tests/unit/test_access_pages.py -q` → fails on import.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → pass.

### Task 2: Columns, principal, `/me`, invite and patch

**Files:**
- Create: `svc-operations-intelligence/migrations/manual/users_access_role.sql`
- Modify: `src/engines/auth/pages.py` (add `columns_ready`), `src/engines/auth/tokens.py` (`Principal`, `principal_from_token`), `src/engines/auth/access.py` (`Scope`, `scope_for`, unscoped scope), `src/engines/auth/invitations.py` (`create`, `accept`), `src/api/routes/auth.py` (`me`, `list_roles`), `src/api/routes/admin.py` (`InviteUser`, `PatchUser`, `list_users`, `invite_user`, `patch_user`, `delete_user`)
- Test: `tests/unit/test_access_role_identity.py`, `tests/unit/test_access_role_admin.py`; scratch-Postgres check of the SQL

**Interfaces — Produces:**
- SQL: `ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS access_role varchar(32), ADD COLUMN IF NOT EXISTS allowed_pages text[], ADD COLUMN IF NOT EXISTS access_narrowed_at timestamptz;` and the first two on `plenum_cafm.invitations`. Comments only; no backfill, no constraints.
- `async pages.columns_ready(session) -> bool` — `information_schema.columns` lookup for `users.access_role`; a True is cached for the process, a False for 60 s; `pages.reset_cache()`.
- `Principal.access_role: str | None = None`, `.pages: frozenset[str] | None = None`, `.access_narrowed_at: datetime | None = None`
- `Scope` gets the same three fields plus `allows_page(page: str) -> bool` and `allows_any(*pages: str) -> bool` (both True when `pages is None`)
- `access.assert_page(scope, *any_of)` and `access.assert_pages_all(scope, *all_of)` raising the Global Constraints refusal
- `/api/auth/me` `user` gains `access_role`, `access_role_label`, `pages` (`null` or list in `PAGES` order), `access_narrowed_at` (ISO or null)
- `/api/auth/roles` gains `access_roles: [{key, label}]`, `pages: [key]`
- `InviteUser.access_role: str | None = None`, `.pages: list[str] | None = None`; `PatchUser` the same, applied only when the field is in `model_fields_set`
- `/api/admin/users` rows gain `access_role`, `access_role_label`, `pages`
- Errors: `400 {reason: <AccessRoleError.reason>}`; `409 {reason: "access_roles_not_set_up"}` when a role is assigned and `columns_ready` is False

Behaviour the tests pin: the sign-in `SELECT` names the new columns only when `columns_ready`; `PATCH` sets `access_narrowed_at = now()` exactly when `pages.narrowed(before, after)`; `PATCH {access_role: null}` clears both columns and leaves `access_narrowed_at`; promoting to admin (existing role-change route) clears both; soft delete clears all three; the invite and patch audit payloads carry `access_role` and `pages`.

- [ ] **Step 1: failing tests** — identity: `test_me_reports_pages_null_for_a_normal_user`, `test_me_reports_role_and_pages_in_order`, `test_scope_allows_every_page_when_unrestricted`, `test_scope_refuses_an_unheld_page`, `test_signin_query_omits_the_columns_when_they_do_not_exist` (fake session that raises if `access_role` appears in SQL), `test_unscoped_scope_is_unrestricted`. Admin: `test_invite_with_role_needs_a_page` (400 `pages_required`), `test_invite_with_role_writes_users_and_invitation`, `test_invite_with_role_before_migration_is_409`, `test_patch_removing_a_page_stamps_narrowed_at`, `test_patch_adding_a_page_does_not_stamp`, `test_patch_absent_fields_change_nothing`, `test_patch_clearing_role_clears_pages`, `test_role_refused_for_admin_target`, `test_list_users_carries_role_and_pages`.
- [ ] **Step 2:** run both files → fail.
- [ ] **Step 3:** implement. Follow the existing fake-session style in `tests/unit/test_access_control.py` and `test_b4_admin.py`.
- [ ] **Step 4:** run both files, then the whole suite → baseline + new, 0 failed.
- [ ] **Step 5:** scratch Postgres: create minimal `plenum_cafm.users` / `invitations`, apply the SQL twice (idempotent), insert a row with and without the columns set, read back. No shared database.

### Task 3: ops-intelligence route table and gate

**Files:**
- Create: `svc-operations-intelligence/src/engines/auth/page_map.py`
- Modify: `src/engines/auth/access.py` (`bind` returns a third dependency), `src/api/routes/auth.py:517` (`scope, ingest_scope, page_gate = access.bind(...)`), `src/app.py:202-216` (`include_router(..., dependencies=[Depends(page_gate)])` on the twelve domain routers)
- Test: `tests/unit/test_page_gate.py`

**Interfaces — Produces:**
- `page_map.PAGE_MAP: dict[tuple[str, str], tuple[str, ...]]` keyed `(METHOD, route.path)`, value = tags; one entry for **every** route the app serves, by the spec §7 rule and §7.1 table. Actions that write into a second register list it under `page_map.NEEDS_ALL: dict[tuple[str, str], tuple[str, ...]]` (spec §7.4).
- `page_map.tags_for(method: str, path: str) -> tuple[str, ...] | None`
- `page_gate(request: Request, s: Scope = Depends(scope)) -> None` — reads `request.scope["route"].path`; no-op when `s.pages is None`; an unmapped route is refused for a restricted caller (fail closed); otherwise needs any page tag held, and every `NEEDS_ALL` page.

- [ ] **Step 1: failing tests** — `test_every_route_has_tags` (walk `app.routes`, skip docs/openapi/health; failure message lists the missing `(method, path)`), `test_every_page_tagged_route_carries_the_gate` (walk `route.dependant` recursively for `page_gate`), `test_unrestricted_caller_passes_every_gate` (parametrised over the table with the `Exploding` session from `test_domain_routes_scoped.py`: status is never 403 `page_not_allowed`), `test_compliance_officer_is_refused_energy_and_vendors` (pages `{compliance, buildings}`: `GET /api/energy/anomalies`, `/api/energy/meters`, `/api/contract-performance/contracts` → 403 `page_not_allowed`; `GET /api/compliance/certificates`, `/api/energy/buildings` are not), `test_buildings_register_lives_under_energy_prefix_but_needs_buildings`, `test_vendor_buildings_allowed_with_either_page`, `test_action_needing_two_pages_refused_with_one`, `test_unmapped_route_refused_for_restricted_caller`.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** dump `(method, path, endpoint name)` for every route with a throwaway script, classify, write `PAGE_MAP`; implement the gate.
- [ ] **Step 4:** run file, then whole suite.

### Task 4: ops-intelligence mixed responses

**Files (modify):** `src/api/routes/approvals.py` + `src/shared/approvals.py`; `src/api/routes/value.py` + `src/engines/value_ledger.py`; `src/api/routes/crons.py` + `src/engines/crons/jobs.py`; `src/api/routes/reports.py` + `src/engines/reports/cards.py`; `src/api/routes/document_search.py`; `src/api/routes/energy.py` (hoist-score, building graph nodes, asset certificate/anomaly parts); `src/api/routes/work_order_blockers.py`
**Create:** `src/engines/auth/page_filter.py`
**Test:** `tests/unit/test_page_filter.py`, plus one route-level test per endpoint in `tests/unit/test_page_mixed_responses.py`

**Interfaces — Produces (`page_filter.py`, all pure, all identity when `pages is None`):**
- `approval_page(source_feature: str | None, related_entity_type: str | None) -> str` — `related_entity_type` first (`compliance_certificate`→compliance; `vendor`, `contract_sla_parameters`, `invoice`→vendors; `energy_anomaly`, `meter_reading_gap`, `energy_recommendation`→energy; `asset_criticality`→assets; `site`→buildings; `document`→by its source feature), then `source_feature` (A compliance, B vendors, C energy, P → `admin`)
- `approvals(rows, pages)`, `value_summary(payload, pages)` (modules key→page with `maintenance`→`ops`; totals recomputed; a P&L head kept only when all its modules are held), `cron_jobs(rows, pages)` (catalogue module→page; `question` job dropped for a restricted caller), `document_page(doc_type, links) -> str` (spec §7.3), `documents(rows, pages)`
- Reports: a restricted caller lists only cards where `owner == scope.user_id`, runs with `created_at > scope.access_narrowed_at`; `cards.run` passes the owner's pages to the chat (Task 8 consumes header `X-Hoistra-Pages`? **No** — it forwards the owner's token if it holds one; otherwise the builder adds the owner lookup. Decide at the code; the test asserts a restricted owner's run never calls the chat unrestricted.)

- [ ] **Step 1: failing tests** — one per helper with held/unheld rows; route tests: `test_pending_queue_shows_only_held_rows`, `test_deciding_an_unheld_approval_is_refused`, `test_value_total_is_recomputed`, `test_question_cron_hidden_from_role_user`, `test_hoist_score_needs_every_component_page`, `test_building_graph_drops_unheld_node_kinds`, `test_report_runs_before_narrowing_are_hidden`, `test_normal_user_payloads_are_unchanged` (same input, `pages=None` → equal output).
- [ ] **Step 2–4:** fail → implement → file and whole suite pass.

### Task 5: Other services read the pages

**Files (modify):** `svc-udr/src/services/principal.py`, `svc-work-order-management/src/services/principal.py`, `cafm-connector-service/src/cafm_connector/api/security.py`, `svc-deepagents/src/services/principal.py`, `svc-ai-schema-mapper/src/services/principal.py`
**Test:** each service's existing principal test file gains the cases below.

**Interfaces — Produces (same in all five):** `Principal.pages: frozenset[str] | None`, parsed in `_from_me` / `from_me` from `user["pages"]` (`None` or absent → `None`; list → frozenset of known keys); `Principal.allows_page(page) -> bool`. `svc-deepagents` also keeps `access_narrowed_at: datetime | None` and `access_role: str | None`.

- [ ] **Step 1: failing tests** per service — `test_pages_absent_means_unrestricted` (an older identity service that does not send the field), `test_pages_list_is_kept`, `test_unknown_page_keys_are_dropped`, `test_empty_list_holds_nothing`.
- [ ] **Step 2–4:** fail → implement → each suite passes.

### Task 6: work-order, connector, udr, schema-mapper gates

**Files:**
- Create: `svc-work-order-management/src/services/page_access.py`, `cafm-connector-service/src/cafm_connector/api/page_access.py`, `svc-udr/src/services/page_access.py`
- Modify: `svc-work-order-management/src/app.py:233-253`, `cafm-connector-service/src/cafm_connector/api/app.py:124-130`, `svc-udr/src/app.py:175-183` (attach the gate at `include_router`); `svc-work-order-management/src/api/routes/maintenance.py` (decisions), `.../dashboard.py` (counts)
- Test: `tests/unit/test_page_gate.py` in each service

**Interfaces — Produces (each `page_access.py`):** `PAGE_MAP`, `NEEDS_ALL`, `tags_for`, `page_gate(request, principal = Depends(current_principal))`, with the same semantics and refusal body as Task 3.

Decisions the table follows: work-order `work_orders`, `maintenance`, `ppm`, `journeys`, `email`, `chat`, `approvals` → `ops`; `/api/assets`, `/api/asset-categories`, `/api/locations` → `assets`; `/api/maintenance/ppm/contracts` → `ops`; `/api/dashboard/stats` → `ops` or `assets`, each count only with its page; `GET /api/maintenance/decisions` drops rows whose source is Compliance unless `compliance` is held. Connector `/api/v1/plenum`: assets, locations, asset-categories, asset-documents, asset-readings, spare-parts, inventory-transactions → `assets`; vendors, vendor-contacts, vendor-contracts, sla-policies → `vendors`; work orders, maintenance-plans, technicians, skills → `ops`; a `GET` list that returns only id and name for a pick-list is also allowed to the page that names it (spec §7.4); org-users, rbac, table-editor → `admin`; connectors/imports/schema → `ingest`. udr: catalog, agent, tables → `admin`; spaces → `shared`; runs → `ingest`. Schema-mapper needs no table (migration is `ingest`; its other groups are already superadmin/admin by path).

- [ ] **Step 1: failing tests** per service — `test_every_route_has_tags`, `test_unrestricted_caller_passes_every_gate`, `test_restricted_caller_refused_unheld_page`, and for work-order `test_compliance_decisions_dropped_without_compliance`, `test_dashboard_counts_follow_pages`.
- [ ] **Step 2–4:** fail → implement → suites pass.

### Task 7: Chat — pages from the server, tools by page

**Files:**
- Create: `svc-deepagents/src/agents/page_access.py`
- Modify: `src/api/routes/workflow.py` (the four entry points at ~:371, :419, :501, :1104), `src/agents/spaces.py` (allowance = space ∩ pages), `src/agents/orchestrator.py` (`self._agent` → per-page-set agent; `_space_engine_tools`; compliance preflight), `src/agents/meta_tools.py` (sub-agent tool lists, `run_phase2_engine_verbose`), `src/agents/agent_router.py`, `src/agents/planner.py`, `src/agents/udr_agent.py` (`query_table`), `src/agents/document_search_tool.py`
- Test: `svc-deepagents/tests/test_page_access.py`, `tests/test_page_access_tools.py`

**Interfaces — Produces:**
- `page_access.active_pages: ContextVar[frozenset[str] | None]`; `set_for(principal) -> None`; `current() -> frozenset[str] | None`
- `ENGINE_PAGE: dict[str, str]` = `{"wo_engine": "ops", "contract_performance": "vendors", "compliance": "compliance", "energy_intelligence": "energy"}`
- `allows_engine(engine: str | None) -> bool`; `refusal(engine: str | None) -> str | None` — "That question is about <domain>, which is outside the pages you have access to."
- `TOOL_PAGES: dict[str, tuple[str, ...]]` — every tool name in `ALL_TOOLS`, the four engine lists, `MAINTENANCE_READ_TOOLS` and the sub-agent registry, tagged with page keys or `shared` / `ingest`
- `tool_allowed(name: str) -> bool` (unknown name → False for a restricted caller); `filter_tools(tools: list) -> list` (identity when unrestricted)
- `TABLE_PAGE: dict[str, str]` and `table_allowed(table: str) -> bool` (unlisted → False when restricted)
- `spaces.allows(space, engine)` additionally requires `page_access.allows_engine(engine)`

- [ ] **Step 1: failing tests** — `test_every_tool_is_tagged` (import the lists; message names the untagged tool), `test_unrestricted_filter_is_identity` (same list object contents and order), `test_compliance_officer_gets_no_energy_or_vendor_tool`, `test_engine_outside_pages_is_out_of_module_with_plain_refusal` (no service or table name in the sentence), `test_browser_space_cannot_widen_pages` (space `full`, pages `{compliance}` → energy engine refused), `test_pages_set_at_every_entry_point` (each of the four handlers plus resume sets `active_pages`), `test_query_table_refuses_unlisted_table_when_restricted`, `test_forced_route_in_context_still_gated`, `test_main_agent_is_built_per_page_set_and_cached`.
- [ ] **Step 2–4:** fail → implement → file passes, then the full suite in the testenv container.

### Task 8: Chat — history, memories, documents, runs on behalf

**Files (modify):** `svc-deepagents/src/services/chat_threads.py` (`list`, `read`, `conversation_context`), `src/api/routes/threads.py`, `src/api/routes/traces.py`, `src/services/chat_memories.py` + `src/api/routes/memories.py`, `src/api/routes/documents.py`, `src/agents/document_search_tool.py`, `src/agents/doc_rag_agent.py`; ops-intel `src/engines/reports/cards.py` (owner's identity on the run)
**Test:** `svc-deepagents/tests/test_page_access_history.py`

**Interfaces — Consumes:** `Principal.pages`, `.access_narrowed_at` (Task 5); `page_filter.document_page` rule (Task 4) mirrored as `page_access.document_allowed(doc_type, links) -> bool`.

- [ ] **Step 1: failing tests** — `test_turns_before_narrowing_are_not_listed_or_read`, `test_turns_before_narrowing_do_not_enter_context`, `test_normal_user_history_is_unchanged`, `test_role_user_recalls_only_own_memories_after_narrowing`, `test_memories_list_follows_the_same_rule`, `test_document_search_drops_unheld_types`, `test_document_link_refused_for_unheld_type`.
- [ ] **Step 2–4:** fail → implement → suite passes.

### Task 9: Close the routes with no sign-in (spec §11)

Built last so it can be dropped or reverted alone.

**Files (modify):** `svc-deepagents/src/api/routes/workflow.py` (`/activity*`, `/workspace/{sid}`, `/status/{sid}`, `/resume/{sid}`), `src/services/activity_log.py`, `src/api/routes/ingest_batch.py`; `svc-work-order-management/src/app.py` (approvals and chat routers get `Depends(current_principal)`), `src/api/routes/chat.py`; `svc-ai-schema-mapper/src/services/run_access.py` (`/doc-rag/row-index`, `/doc-rag/rows` → admin)
**Test:** `svc-deepagents/tests/test_open_routes_closed.py`, `svc-work-order-management/tests/unit/test_open_routes_closed.py`, `svc-ai-schema-mapper/tests/test_doc_rag_admin_only.py`

- [ ] **Step 1:** trace every caller of each route (frontend `api/deepAgents.js`, ops-intel, workers) and record which send a token. A caller that sends none gets the token forwarded or is listed for Hussain; it is not silently broken.
- [ ] **Step 2: failing tests** — `test_activity_needs_a_token`, `test_activity_returns_only_the_callers_sessions`, `test_workspace_and_status_need_the_owner`, `test_resume_sets_pages`, `test_work_order_approvals_need_a_token`, `test_chat_history_needs_the_owner`, `test_doc_rag_rows_are_admin_only`.
- [ ] **Step 3–4:** implement → suites pass.

### Task 10: Frontend — identity and scope

**Files:**
- Create: `apps/frontend/src/logic/accessRoles.js`
- Modify: `src/logic/instances.js` (`usesInstances`, `scopedInstance`, `instanceReports`, module/page maps for `buildings`), `src/logic/auth.js` (`authLoadScope` ~:477, `authEnter` ~:415, the User-view switch ~:1036-1048), `src/logic/core.js` (`loadLiveData` :103-154), `src/api/client.js` (`page_not_allowed` handling), `src/logic/HoistraLogic.js` (view guard ~:337, :381), account menu / top-bar chip in `renderVals.js`
- Test: `apps/frontend/test/accessRoles.test.mjs`, `test/roleUserSeesOnlyTickedPages.test.mjs`; update `test/instances.test.mjs`, `test/auth.test.mjs`

**Interfaces — Produces (`accessRoles.js`):**
- `ACCESS_ROLES = [{ key, label }]` (four, in the order of Global Constraints), `PAGE_OPTIONS = [{ key, label, hint }]` — labels Buildings, Compliance, Energy, Vendors (hint "Vendor performance"), Maintenance (hint "Vendor operations"), Assets
- `isRoleUser(account) -> boolean` (`role === 'user'` and `access_role` set)
- `roleReports(account) -> string[] | null` — pages plus `'graph'` when `compliance` is held; null when not a role user
- `roleLabel(account) -> string | null`
- `holdsPage(s, page) -> boolean` — true for anyone who is not a role user

Behaviour: `usesInstances` false for a role user; `instanceReports(s)` returns `roleReports(s.account)` for a role user in every view; Home keeps the full-platform layout; each loader in `loadLiveData` runs only when its page is held (`ccLoad`→compliance, `vpLoad`/`vmLoad`→vendors, `vbLoad`→vendors or compliance, `bldLoad`→buildings, `energyLoad`/`enPositionLoad`→energy, `asLiveLoad`/`asCondLoad`→assets, `mxLiveLoad`→ops); a `page_not_allowed` response resolves as "not available", triggers one `authLoadScope`, and raises no banner; a view whose page is not held falls back to `home`.

- [ ] **Step 1: failing tests** — `roleReports` cases; `usesInstances` false; sign-in lands on `home` not `instances`; a role user with `['compliance','buildings']`: navigator has those two only, `loadLiveData` calls no energy/vendors/ops/assets client (spy on the api modules), opening view `vp` lands on `home`, the account menu has no "Switch instance", the chip reads "Compliance Officer"; a normal user and an admin produce the same navigator and loader calls as before (snapshot against current behaviour).
- [ ] **Step 2–4:** fail → implement → `npm test` passes.

### Task 11: Frontend — gate what an instance never had to

**Files (modify):** `src/logic/instances.js` (`MODULE_REPORT` gains `Buildings: 'buildings'`), `src/logic/renderVals.js`, `src/logic/homeLive.js`, `src/logic/queueLive.js`, `src/logic/chatBuilding.js`, `src/logic/assetsActions.js`, `src/logic/assetsCondition.js`, `src/logic/assetsVendor.js`, `src/logic/vendorHoist.js`, `src/logic/complianceLive.js` (chat context), `src/logic/crons.js`, `src/logic/reports.js`
**Test:** `apps/frontend/test/roleUserCrossPageActions.test.mjs`, `test/roleUserHome.test.mjs`

Behaviour: Buildings unticked hides the Buildings navigator entry, the Buildings view and building-register detail; building names remain in pickers (from the sign-in record). Home shows a card or figure only when its page is held; Hoist Score only when the server returned it. Every control that writes into another register (spec §7.4) is hidden unless all its pages are held: Raise work order and Request inspection on Assets need `ops`; Create vendor from a certificate needs `vendors`; opening a certificate from an asset or vendor needs `compliance`. Chat context text names only held pages. Uses `holdsPage` from Task 10.

- [ ] **Step 1: failing tests** — Assets-only user: no Raise work order control, no certificate row, no anomaly signal; Compliance+Buildings user: Home has no energy, vendor, maintenance or asset card and the Pending count equals compliance rows; Energy-only user: no Buildings navigator entry, building picker still lists names; `test/noInternalNames.test.mjs` still passes.
- [ ] **Step 2–4:** fail → implement → `npm test`, `npm run build`.

### Task 12: Frontend — Users & access

**Files (modify):** `src/screens/Users.jsx` (invite form :56-78, row :102-140), `src/logic/users.js` (`usersVals`), `src/logic/usersLive.js` (`shapeLiveUser`, `usLiveInvite`, new `usLiveSetAccess`), `src/api/admin.js` (bodies already pass through)
**Test:** `apps/frontend/test/usersAccessRoles.test.mjs`; update `test/usersLive.test.mjs`

Behaviour: the invite form gets a Role control — "No role — normal user" plus the four — under the name and email. Choosing a role reveals "Pages this user can open" with six chips in the existing building-chip style, none on. "Send invitation" is inert with the line "Tick at least one page for this role." until one is on. With no role the body sends neither field. The table shows the role label under the user's name; the expanded row has the same Role control and chips and saves through `PATCH` with `{access_role, pages}`; clearing the role sends `{access_role: null}`. A 409 `access_roles_not_set_up` shows the server's sentence. Admin rows show no Role control.

- [ ] **Step 1: failing tests** — `test_invite_body_has_no_access_fields_without_a_role`, `test_invite_with_role_and_no_page_does_not_send`, `test_invite_with_role_sends_role_and_pages_in_order`, `test_picking_a_role_ticks_nothing`, `test_row_shows_role_label`, `test_changing_pages_patches_both_fields`, `test_clearing_role_sends_null`, `test_admin_row_has_no_role_control`.
- [ ] **Step 2–4:** fail → implement → `npm test`, `npm run build`.

### Task 13: End-to-end proof and review

- [ ] **Step 1:** all four suites, full, against their baselines.
- [ ] **Step 2:** scratch Postgres + the ops-intel app in-process: apply the migration, invite a Compliance Officer with Compliance and Buildings, accept, then with that token: `/api/auth/me` shows the role and two pages; every `PAGE_MAP` route tagged only with another page answers `page_not_allowed`; the Pending queue and value summary carry compliance only.
- [ ] **Step 3:** Chrome walk against a stub backend (recipe in the `hoistra-ui-verification` and `testenv-stack-limits` notes): role user — no picker, two navigator entries, no banners, chip shows the role; normal user and admin — unchanged. Screenshots kept in the scratchpad.
- [ ] **Step 4:** fresh reviewer over the whole diff (`superpowers:requesting-code-review`), fix what it finds, re-run suites.
- [ ] **Step 5:** report to Hussain: what changed, the migration to run, the Azure warning, anything not closed.
