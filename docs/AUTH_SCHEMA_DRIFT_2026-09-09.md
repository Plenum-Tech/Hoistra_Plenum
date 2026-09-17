# Auth vs. production `plenum_cafm` — schema drift report

**Date:** 2026-09-09 · **For:** Bala (svc-operations-intelligence / `plenum_cafm` owners) · **From:** Hussain's frontend session
**Method:** read-only `SELECT`s against production through the running `hoistra_plenum-svc-operations-intelligence-1` container (Azure `plenum-agentic-ai…/plenum_agent`), plus a read of `feature/compliance-core-skill-and-question-bank` at `79f86c6`. Nothing was written; no migration was run.

## Verdict

The new `/api/auth` service (commits `d621017`…`79f86c6`) follows the cafm-connector ORM, which keys `users` and `organizations` by **UUID**. Production has drifted from that ORM: both tables are **integer-keyed**, and `users.role` holds **job titles**, not platform roles. Against production the auth migrations cannot create their tables, registration cannot resolve an organisation, and every issued token would be rejected on the next request. Separately, no email transport is configured for the service, so no one-time code could be delivered even if the schema matched.

The frontend integration is complete and verified against a mock of the documented API; it treats ids as opaque strings and needs no change whichever option below is chosen.

## 1. Three sources, two shapes

| | cafm-connector ORM (`models/plenum_cafm.py:254-263`) | Production `plenum_cafm` (observed) | Auth engine assumes |
|---|---|---|---|
| `users.id` | `UUID(as_uuid=True)`, default `uuid4` | `integer`, `nextval('users_id_seq')` | UUID — `Principal.user_id: UUID` (`tokens.py:51`), `UUID(str(claims["sub"]))` (`tokens.py:166`) |
| `users.organization_id` | `UUID` | `integer`, nullable | UUID — `resolve_organization` returns `UUID` (`accounts.py:128-170`), insert binds `str(org)` (`accounts.py:321`) |
| `organizations.id` | `UUID` | `integer` — 2 rows, ids `1` and `5` | UUID — `UUID(candidate)` (`accounts.py:138`), `WHERE id = :i` bound as text (`accounts.py:144`) |
| `users.role` | (declared by ORM) | `varchar`, nullable — values: *Maintenance Planner, Plumbing Specialist, Electrical Engineer, Maintenance Supervisor, HVAC Specialist, Maintenance Tech, System Administrator, Requester, Facility Manager, Vendor Coordinator, Facilities Director, Mechanical Engineer, Quality Inspector, Operations Manager, Senior Technician*, 3 × NULL | `superadmin \| admin \| user` — anything else ranks 0 (`roles.py:66`) |
| `users` rows | — | **18 real staff rows**, all `status='active'`, unique emails, `password_hash` nullable | accounts created by `/register` |
| `auth_otp_codes`, `auth_sessions`, `auth_role_changes` | — | **absent** | created by `auth_identity_and_otp.sql` / `auth_user_roles.sql` |

The local compose Postgres (`plenum_cafm` db) shows the same drift: `organizations.id integer`, zero rows.

## 2. What each migration statement would do against production

`apply_sql_migrations` runs each statement in its own transaction and logs failures as `db.migration.stmt_skipped`, so a run would not abort — it would leave this:

| Statement | Outcome on production |
|---|---|
| `CREATE TABLE IF NOT EXISTS plenum_cafm.users (id UUID …)` | no-op — table exists with integer id |
| `ALTER TABLE users ADD CONSTRAINT fk_users_organization … REFERENCES organizations(id) ON DELETE CASCADE` | **succeeds** (integer→integer) — the live staff table would start cascade-deleting with organisations |
| `ADD COLUMN IF NOT EXISTS password_changed_at / failed_login_count / locked_until / email_verified_at` | succeed — four new columns on the staff table |
| `CREATE UNIQUE INDEX CONCURRENTLY uq_users_email_lower ON users (lower(email))` | succeeds (no case-variant duplicates today) |
| `CREATE TABLE auth_otp_codes (… user_id UUID REFERENCES users(id) …)` | **fails** — `uuid` → `integer` FK is not implementable; table never exists |
| `CREATE INDEX … ix_auth_otp_email_purpose / ix_auth_otp_expires` | fail — no table |
| `CREATE TABLE auth_sessions (… user_id UUID NOT NULL REFERENCES users(id) …)` | **fails** — same reason; no refresh sessions can be stored |
| `ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'` | no-op — `role` exists (varchar, nullable, no default) |
| `ADD CONSTRAINT ck_users_role CHECK (role IN ('superadmin','admin','user'))` | **fails** — 18 rows violate it (job titles, NULLs) |
| `CREATE INDEX ix_users_role` | succeeds |
| `CREATE TABLE auth_role_changes (… user_id UUID REFERENCES users(id) …)` | **fails** — same FK type mismatch |
| `COMMENT ON COLUMN users.status …` | succeeds |

Net effect: columns and a cascade FK added to a production staff table; none of the three auth tables created; role check absent.

## 3. What breaks at runtime (with the routes deployed)

- **`POST /register`** — `resolve_organization`: the frontend sends no `organization_id` (`VITE_ORGANIZATION_ID` is empty), production has two organisations → `400 organization_id_required`. Setting `AUTH_DEFAULT_ORGANIZATION_ID=1` → `UUID('1')` raises → `400 "organization_id must be a UUID."`. Passing any UUID → `WHERE id = :i` compares text to integer → asyncpg type error → 500. Even past that, the `INSERT … VALUES (:o …)` binds a UUID string into `organization_id integer` → 500.
- **`POST /login`** for the 18 existing rows — `find_by_email` matches them, `verify_password(password, row["password_hash"])` runs against whatever the connector stored (or NULL). If it ever succeeds, `open_session` inserts into the absent `auth_sessions` → 500.
- **Any bearer call** — `principal_from_token` rebuilds `UUID(str(claims["sub"]))`; an integer id serialised into `sub` raises → `401 invalid`.
- **Roles** — `role_engine.rank('HVAC Specialist') == 0`: every existing user is "no role"; `superadmin_exists` is false, so the bootstrap path is live for whichever address is configured.
- **Registering with an existing staff address** returns the same 202 as any other and (with email enabled) sends that staff member a "someone tried to register with your address" warning.

## 4. Two more blockers independent of ids

**`users.role` means two things.** The CAFM side uses it as a free-text job title; the auth engine uses it as the platform role and wants a closed set. Even with UUID keys this collides. A separate column (`platform_role`, default `'user'`) or a link table keeps both meanings.

**No email transport.** The azure-safe overlay pins `EMAIL_DRY_RUN=true` for this service ("production contact rows are real people"), and the container has neither Graph (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `OUTLOOK_USER_MAIL`) nor SMTP (`SMTP_USER`, `SMTP_PASSWORD`) configured. `send_platform_email` then records and drops the message; the code is stored as a peppered HMAC and deliberately never logged. Hussain holds Graph credentials for `bala.r@plenum-tech.com`; they have not been written anywhere.

## 5. Two backend notes from the frontend review

1. **Rotation opens a new session id.** `rotate_session` (`tokens.py:319-334`) revokes the presented session row and `open_session` mints a new `sid`; `principal_from_token` refuses any access token whose `sid` is revoked (`session_revoked`). A second browser tab that adopts the rotated refresh token still holds an access token bound to the old `sid`, so its next bearer-checked call signs everyone out. Keeping `sid` stable across a rotation (rotate only `refresh_token_hash`) would make multi-tab sessions work; today only `/password/change` and `/logout {everywhere}` check the bearer, so the blast radius is small — until any other route does.
2. **Simultaneous boot is a replay.** Two tabs restored together both present the same stored refresh token; the second is treated as theft and revokes every session. That is correct against a stolen token; a few-second grace window for the immediately previous token (or a frontend cross-tab lease) would stop a browser-restore from signing the person out.

## 6. Options

**A. Migrate production to the ORM's shape.** `users.id`/`organizations.id` → UUID with every referencing FK rewritten; `role` normalised. Correct in the long run, large and risky: every CAFM table that references `users.id` or `organizations.id` moves with it, and the CSV/Fiix ingestion paths that produced integer ids need the same change.

**B. Adapt the auth engine to the real shape.** Ids as integers end-to-end (`Principal.user_id: int`, `sub` as int, `organization_id: int`, `resolve_organization` accepting integers); auth tables' FKs `INTEGER REFERENCES users(id)`; platform role in a new `platform_role` column with the closed-set check; existing staff rows become `status='invited'` (the migration's own "no usable password, set it via the reset flow" state); drop the `ON DELETE CASCADE` on `fk_users_organization` or make it a deliberate decision. Smallest blast radius; the frontend needs nothing.

**C. Separate identity table.** `plenum_cafm.auth_accounts (id UUID, user_id integer REFERENCES users(id), platform_role, password_hash, …)` owned by this service, with the OTP/session tables referencing it. Leaves the staff table untouched; the migration header's "two answers to who is this person" concern is real but bounded by the 1:1 link.

Recommendation from the frontend side: **B**, or **C** if touching `users` at all is unwelcome. Either way `platform_role` (or the account table) resolves the job-title collision, and email transport is needed before any of it can be exercised.

## 7. What is ready now

- **Frontend** (`apps/frontend`, uncommitted on `hussain`): gate with sign-in / create account / code / forgot / reset, bearer on every call, refresh-once interceptor, role-gated Admin view, change password, sign out everywhere. 197 tests; headless-Chrome pass against a mock of the documented API. Ids are opaque strings to it.
- **Staging recipe, if wanted before production is reconciled:** a second ops-intelligence container on `:8019` against a fresh local database (`plenum_auth_staging`), `AUTO_MIGRATE_ON_STARTUP=true`, a hand-made UUID `organizations` table with one row, Graph credentials in a `chmod 600` env file outside the repo, `EMAIL_DRY_RUN=false` (the fresh DB has no contacts), generated `AUTH_JWT_SECRET`/`AUTH_OTP_PEPPER`, and `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL=husain.kalabhai@plenum-tech.com`. A path-stripping proxy on `:3999` keeps the dev frontend at `127.0.0.1:5173` unchanged. Azure and the production container stay untouched.
- **Production prerequisites, once B or C lands:** rebuild the container, run the two (revised) migrations explicitly (`AUTO_MIGRATE_ON_STARTUP` is off), set `AUTH_JWT_SECRET`, `AUTH_OTP_PEPPER`, `AUTH_DEFAULT_ORGANIZATION_ID`, the bootstrap superadmin email, and an email transport with dry-run off for this service only.
