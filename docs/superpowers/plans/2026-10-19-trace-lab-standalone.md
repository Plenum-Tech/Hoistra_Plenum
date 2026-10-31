# Trace Lab standalone app — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace Lab opens at its own address `/lab/` with its own sign-in (the Hoistra email and password), its own shell and Home page, and no longer appears inside Hoistra.

**Architecture:** A second Vite page in the existing frontend package (`apps/frontend/lab/index.html` → `src/lab/main.jsx`). Same package means the same design tokens, the same `src/api/client.js` (a separate page has its own module state, so the lab's token and acting company never mix with Hoistra's tab), and the unchanged `src/tracelab/` screens. Both servers already serve `dist/lab/index.html` at `/lab/` (`try_files $uri $uri/`), so no gateway change. Backend unchanged.

**Tech stack:** React 18, Vite multi-page build, node:test, existing svc-operations-intelligence `/api/auth/*`.

**Spec:** `docs/superpowers/specs/2026-10-19-trace-lab-standalone-design.md`

## Global constraints
- Own session: refresh token under `localStorage`/`sessionStorage` key `tracelab.session.v1` (never `hoistra.session.v1`), Web Lock name `tracelab.refresh.<accountId>`, access token in memory only.
- Auth endpoints at base `/backend/ops-intelligence`: `POST /api/auth/login {email,password}`, `POST /api/auth/verify-email {email,code}`, `POST /api/auth/refresh {refresh_token}`, `POST /api/auth/logout {refresh_token}`, `GET /api/auth/me`.
- Access: `role === 'superadmin'` → platform team (company picker); `role === 'admin'` → own company; anything else → the "not allowed" screen.
- Theme: Hoistra tokens (`src/styles/base.css`), Inter, headings weight 500, accent `#FF5A1F`.
- Copy: "Sign in to Trace Lab", "Use your Hoistra email and password. There is no separate Trace Lab account.", "Trace Lab is for company admins and the platform team".
- No internal names in UI copy (existing `noInternalNames` scan); Hoistra keeps zero imports from `src/tracelab/` or `src/lab/` except the A3 cost line.

## Review focus
1. A reused refresh token (two lab tabs refreshing at once) must not trigger replay revocation → the Web Lock serialises refresh; test the lock name and that the stored token is replaced after each refresh.
2. A refresh that answers 401 must clear the session and show sign-in, not loop.
3. A company admin must never see the picker or another company; the acting company is set only for the platform team.
4. Switching company must empty the previous company's data (existing `resetForCompany`, exercised through TraceLabApp's scope key).
5. Signing out of the lab must not touch Hoistra's stored session.

---

### Task 1: Lab session and sign-in logic
**Files:** Create `src/lab/session.js`, `src/lab/authFlow.js`, test `src/lab/__tests__/labAuth.test.mjs`.
**Interfaces (produces):**
- `session.js`: `KEY = 'tracelab.session.v1'`; `loadSession(storage) -> {refreshToken, user} | null`; `saveSession(storage, s)`; `clearSession(storage)`; `access(user) -> 'platform' | 'company' | 'denied'`; `lockName(userId) -> 'tracelab.refresh.' + userId`.
- `authFlow.js`: `createLabAuth({ api, storage, locks, onChange }) -> { signIn(email, password), verify(email, code), boot(), refresh(), signOut(), token(), user() }`; it calls `configureAuth({ getToken, refresh, onTerminal })` from `src/api/client.js`. `signIn` returns `{ ok: true }`, or `{ needsCode: true }` on 403 `email_not_verified`, or `{ error }`.
- [ ] Tests: storage key and shape; `access()` for superadmin/admin/user/missing; `boot()` with a stored token refreshes once under the lock, saves the rotated refresh token and loads `/me`; a 401 on refresh clears storage and reports signed out; `signOut()` posts logout and clears only `tracelab.session.v1`.
- [ ] Implement, run `node --test src/lab/__tests__/labAuth.test.mjs`.

### Task 2: TraceLabApp without Hoistra's frame
**Files:** Modify `src/tracelab/TraceLabApp.jsx` (export `Shell` gains `chrome = true`; default export `TraceLabApp({ vals, chrome = true })`), test in `src/tracelab/__tests__/render.test.mjs`.
- [ ] Test: `Shell` with `chrome={false}` renders no "Administration" crumb, no `<h2>Trace Lab</h2>` and no tablist; default still renders all three.
- [ ] Implement; run the module tests.

### Task 3: The lab page
**Files:** Create `apps/frontend/lab/index.html`, `src/lab/main.jsx`, `src/lab/LabApp.jsx` (screens: booting, SignIn, VerifyCode, Denied, Picker, Workspace), `src/lab/Home.jsx`, `src/lab/logic/home.js`, `src/lab/lab.css`; tests `src/lab/__tests__/labScreens.test.mjs`, `src/lab/__tests__/home.test.mjs`.
**Interfaces:** `logic/home.js`: `homeView({ golden, runs, progress, modes, judges, batches }) -> { next: {text, action}, stages: [{name, state, big, line, tab}] , recent: [] }` with "—" for anything unknown. Workspace nav groups: check (golden, runs), explain (conversations, models), group (progress, labels, map), fix and watch (judges, watch); clicking calls `act.setTab(key)` from `src/tracelab/actions.js` and renders `<TraceLabApp vals={{ tenant: companyName }} chrome={false} />`. Picker lists `GET /backend/ops-intelligence/api/superadmin/companies` (`companies[].organization_id`, name) plus "Platform view"; choosing calls `setActingOrg(id | null)` and remembers it under `tracelab.company.v1`.
- [ ] Tests: SignIn renders the copy and labelled inputs; Denied copy; Picker lists Platform view + companies and the selected one is `aria-checked`; `homeView` next-step rules (no golden set → upload; open batch with missing verdicts → start reviewing; unfiled notes → file them; else draw a batch) and "—" on missing data.
- [ ] Implement; run the lab tests; `npm run dev` page `/lab/` loads.

### Task 4: Build, and leave Hoistra
**Files:** Modify `vite.config.js` (`build.rollupOptions.input = { main: 'index.html', lab: 'lab/index.html' }`), `src/App.jsx` and `src/logic/integrations.js` (remove attachments A1, A2), `src/tracelab/views/EvalSpendBody.jsx` (link "Open Trace Lab" → `/lab/`), `src/tracelab/__tests__/goldenRule.test.mjs` (Hoistra must not import the module except A3), `docker-compose.tracelab.yml` comment.
- [ ] Test: guard test fails first while App.jsx still mounts Trace Lab; passes after removal.
- [ ] `npm test` (expect only the old time-zone failure), `npm run build` (dist has `index.html` and `lab/index.html`, bundle check passes).
