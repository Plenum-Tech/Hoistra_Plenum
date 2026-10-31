# Trace Lab as its own app — design

Date: 19 Oct 2026. Requested by Hussain's manager: Trace Lab must not live inside Hoistra. It gets its own place, people
sign in with the same Hoistra email and password, and it works on the same data.

Mockup (4 screens): https://claude.ai/artifact/3HaLnv4gN8RcVKYg34me2N

## Intent

Hoistra is the product customers use. Trace Lab is the tool the team uses to check the chat's answers. Mixing them puts
an internal tool in front of customers and couples two release cycles. Separating them keeps Hoistra clean and lets Trace
Lab change on its own schedule, without a second account system and without copying data.

## Goals

1. Trace Lab opens at its own address, with its own sign-in screen and its own top bar and menu. Nothing about it shows
   inside Hoistra.
2. Same credentials: the Hoistra email and password. No new accounts, no new password store.
3. Same data: the same svc-trace-lab service, the same Trace Lab database (`hoist_tracelab`), and the same read-only view of
   the main database. Nothing is copied or moved.
4. The platform team chooses which company to work in inside Trace Lab itself, instead of Hoistra's "view as company".
5. A short, obvious flow: sign in, (platform team: pick a company), land on a home page that says where the loop stands
   and the one next step.

## Non-goals

- No single sign-on between Hoistra and Trace Lab. Signing in to one does not sign you in to the other (reason below).
- No change to the Trace Lab backend, its database or its migrations.
- No change to how Hoistra's login works.
- No new roles. Same rule as today: company admins and the platform team only.

## Anti-goals (would make this a failure even if it works)

- Two apps sharing one refresh token. Hoistra rotates refresh tokens and treats a reused one as theft: it revokes every
  session (`api/auth.js:29`, `logic/auth.js:341`). If both apps used one token, normal use would sign people out of
  everything.
- Hoistra breaking or changing for customers. Removing Trace Lab from Hoistra must leave every other screen identical.
- A second copy of the Trace Lab screens that drifts from the first. The new app reuses `apps/frontend/src/tracelab/` as is.

## How it works

### Address

- Same server, own path: **`/lab/`**. Locally `http://localhost:3000/lab/` through the gateway, and `http://localhost:5175/lab/`
  for development (its own dev server). On Azure, `https://<hoistra host>/lab/`.
- Why a path and not a subdomain: the login service (svc-operations-intelligence) only allows Hoistra's own origins
  (`src/app.py:132-155`) and svc-trace-lab has no CORS at all. Same origin means no CORS changes to existing services.
  A subdomain (`lab.hoistra.com`) can come later by adding that origin to both services.

### Sign in

1. The lab's sign-in screen calls the same endpoints Hoistra uses, at `/backend/ops-intelligence/api/auth/*`:
   `POST /login {email,password}`; on 403 `email_not_verified`, the same email-code step (`/verify-email`); then
   `GET /me`.
2. The lab gets **its own** access token and refresh token from its own login. It keeps the access token in memory and the
   refresh token under its own storage key (`tracelab.session.v1`), never Hoistra's `hoistra.session.v1`. It refreshes with
   its own Web Lock name (`tracelab.refresh.<accountId>`). Tabs of the lab coordinate with each other the same way Hoistra's do.
3. Who may enter, from `/me`: a company admin (works in their own company) or a superadmin (platform team). Anyone else
   signs in successfully but sees "Trace Lab is for company admins and the platform team", with Open Hoistra and Sign out.
4. Sign out revokes only the lab's refresh token.
5. "Forgot password" links to Hoistra's reset page: Trace Lab has no password screens of its own.

### Choosing a company (platform team)

- After sign-in, a superadmin sees "Where do you want to work?": **Platform view** (accept proposed failure modes, judges,
  monitoring, spend) or one company. The list comes from the existing superadmin companies endpoint.
- The choice is remembered on that computer and shown in the top bar as a switcher, available on every page.
- Under the hood this is the same `organization_id` query parameter the Trace Lab API already reads (`tracelab/api.js:14-23`).
  The lab sets it from its own company switcher instead of Hoistra's `getActingOrg()`.
- A company admin skips this screen.

### The lab's shell

- Top bar: Trace Lab mark, company switcher (platform team), "Open Hoistra" link, the signed-in email, Sign out.
- Left menu grouped by the loop: **check** (Golden set, Runs), **explain** (Conversations, Review queue, Model calls),
  **group** (Failure modes, Labels, Map), **fix and watch** (Judges, Watch). The platform team also sees **Spend**.
- **Home** (new): five cards, one per loop stage, each with one number, one line of explanation and a link, plus a
  "Next step" bar with one button (for example "7 answers are waiting for your verdict: Start reviewing") and a short
  Recent list. Home reads only existing endpoints (runs, sampling batches, progress, failure modes, judges, watch).
- The existing tab views render inside this shell unchanged. Deep links keep working: `/lab/?tl=runs&run=...`.

### Leaving Hoistra

- Hoistra stops showing Trace Lab: the Administration menu item, the Trace Lab page, and the lazy mount.
- The "Evaluation $X, not included in the totals above" line on Platform cost stays (the senior's decision 5) and gains an
  "Open Trace Lab" link. **My call** — remove it instead if the manager wants zero Trace Lab in Hoistra.
- The approved attachments A1 (App.jsx) and A2 (integrations.js) are reverted; A3 stays as above; A4–A8 change to build and
  serve the lab app instead of the flag.

## Build shape (for the plan)

- A second entry in the existing frontend package, so it shares node_modules, design tokens and the tracelab module:
  `apps/frontend/lab.html` → `src/lab/main.jsx` (sign-in, company picker, shell, home), and `vite.lab.config.js`
  (`base: '/lab/'`, port 5175, the same `/backend` dev proxy). `npm run build:lab` writes `dist-lab/`.
- The tracelab module's few imports from the host app (`apiFetch`, `getActingOrg`, `accessToken`, `isStaleScope`, the
  format helpers in `logic/tracesPage.js`, `fmtUsd`, `scrubInternal`, `vals.tenant`) get a small adapter so the module asks
  "the host" for its token, company and company name. Hoistra no longer hosts it; the lab app does.
- Gateway: `location /lab/` serves `dist-lab` with `try_files ... /lab/index.html`. Locally a new `tracelab-web` service in
  `docker-compose.tracelab.yml`; on Azure the all-in-one image copies `dist-lab` and nginx serves it.
- Tests: the existing 705 module tests stay green; new tests for the sign-in flow (own storage key, own lock, refresh,
  role gate, verify-email step), the company switcher, Home's view model, and a guard that Hoistra no longer imports
  anything from `src/tracelab/` or `src/lab/`.

## Left to the builder

Exact copy on each screen, empty and loading states, the Home view model's field names, how the company list is cached,
and the order of tasks in the plan.

## Calls I made (check these)

1. Own path `/lab/` on the same server, not a subdomain (no CORS changes).
2. Separate sign-in, no shared session with Hoistra (refresh-token rotation makes sharing unsafe).
3. Keep the one-line Evaluation spend on Hoistra's Platform cost page, with a link to the lab.
4. A new Home page as the landing screen.
5. Visual look: Hoistra's own theme (Hussain's request): Hoistra's design tokens from `src/styles/tokens.css` — cream ground
   `#FEFDFB`, white cards, `#E7E4DB` dividers, `#1A1A18` text, the single orange accent `#FF5A1F` (`#FFEDE6` tint), green
   `#15703A` for ok, Inter with headings at weight 500. Only the name and the flask mark say "Trace Lab".
