# Frontend auth — sign-in, accounts and sessions against `/api/auth`

**Date:** 2026-09-09
**Scope:** `apps/frontend` only. The backend (`svc-operations-intelligence`, commits `d621017`…`79f86c6`) is taken as given; its contract is the captured API reference ("Hoistra Auth API") and `src/api/routes/auth.py`.

## Goal

Replace the prototype gate — one email field whose "Continue" flips `signedIn: true` — with the real account flows the platform now exposes: sign in, create an account and confirm it with a six-digit code, recover a forgotten password, change a password from inside a session, sign out of one or every session. Tokens are held and refreshed correctly, every backend call carries the bearer, and the real role decides who sees Admin view.

The existing UI stays as it is unless the feature needs otherwise. Concretely: the gate's marketing column and its animations are untouched; the top bar, navigator, screens and every other component keep their layout; the account menu gains rows but not a new shape.

## Decisions taken with Hussain

| Question | Decision |
|---|---|
| Real role vs. the Admin-view toggle | **Gate it.** `role=user` accounts get User view only — the toggle and the admin nav items are not offered. `admin` and `superadmin` keep the toggle exactly as today. |
| Scope of the first pass | Gate flows **plus** Change password and Sign out everywhere in the account menu. |
| The "Single sign-on" button | **Kept, inert.** Clicking it toasts "Single sign-on is not available yet — sign in with your email and password." No fake sign-in. |
| Architecture | **Controller mixin** (`logic/auth.js` + `api/auth.js` + bearer/refresh plumbing in `api/client.js`), following every existing pattern. Not a React context. |
| Browser verification | **Mock auth server only.** Nothing reaches the production-pointed backend or its database; no migrations are run; no accounts are created or signed in for real. |

## Backend contract, as relied upon

- Base: `BASES.opsIntelligence` (`/backend/ops-intelligence`) + `/api/auth/…`. Routes: `POST register · verify-email · resend-code · login · refresh · logout · password/forgot · password/reset · password/change`, `GET me · config · roles · users`, `POST users/{id}/role`.
- Success bodies: `SessionResponse { ok, user, tokens{access_token, refresh_token, token_type, expires_in}, message? }` from login / verify-email / refresh; `AcceptedResponse { ok, status, message, email, otp }` (202) from register / resend-code / password/forgot; `SimpleResponse { ok, message, sessions_ended }` from logout / password/reset / password/change.
- Every failure is `HTTPException` whose `detail` is `{ ok:false, error, reason, …extras }` — except 422, where `detail` is FastAPI's array of field errors.
- `user.role` ∈ `superadmin | admin | user`, ranked. `role_label` is display text.
- 401 `reason` values on protected calls: `missing_token, invalid, expired, wrong_type, session_revoked, password_changed, replayed, no_account, disabled`. Only `expired` is worth a refresh-and-retry.
- Refresh **rotates both tokens**; presenting an already-exchanged refresh token returns 401 `replayed` and revokes every session.
- `GET /config` (no token): `{ ok, self_registration, password:{min_length}, otp:{code_length, ttl_minutes, max_attempts, resend_cooldown_seconds, max_per_hour}, secrets_configured }`.
- Nothing outside `/api/auth` requires a token today.

## Architecture

### New files

**`src/api/auth.js`** — `authApi`, one thin function per endpoint, transport is `apiFetch`:

```
config()                         GET  /api/auth/config                  auth:false
register({email,password,full_name,phone})  POST /api/auth/register    auth:false, 202; adds organization_id: ORG_ID when set
verifyEmail(email, code)         POST /api/auth/verify-email            auth:false
resendCode(email)                POST /api/auth/resend-code             auth:false, 202
login(email, password)           POST /api/auth/login                   auth:false
refresh(refreshToken)            POST /api/auth/refresh                 auth:false
logout({refresh_token})          POST /api/auth/logout                  auth:false
logoutEverywhere()               POST /api/auth/logout {everywhere:true} bearer
forgot(email)                    POST /api/auth/password/forgot         auth:false, 202
reset(email, code, newPassword)  POST /api/auth/password/reset          auth:false
changePassword(current, next)    POST /api/auth/password/change         bearer
```

`auth:false` means: send no bearer and never run the 401 interceptor — a failing `/login` or `/refresh` must not itself trigger a refresh.

`GET /me` is deliberately not wrapped. The reference suggests it for "is my stored token still good?", but this client never stores the access token, so the reload path exchanges the refresh token instead and gets the fresh `user` from that response. Adding `me()` would be code nothing calls.

**`src/logic/auth.js`** — exports `AUTH_DEFAULTS` (state slice), `authMethods` (mixed into `HoistraLogic.prototype`), and the helpers the tests import (`normaliseCode`, `authReason`). `authMethods` includes `authVals(s)`, spread into `renderVals()` where the old `email/setEmail/signIn/gateKey/signOut` and the account-menu block were.

**`src/components/shell/GatePanel.jsx`** — the sticky sign-in column's contents, one component with the five modes. Extracted rather than inlined so `Gate.jsx` (already 315 lines of marketing JSX) only swaps its old three controls for `<GatePanel vals={vals} />`.

**`src/components/shell/PasswordModal.jsx`** — change-password dialog in `ConnectModal`'s idiom (scrim + centred card, `fadeUp`).

**`test/auth.test.mjs`** — see Testing.

### Edited files

| File | Change |
|---|---|
| `src/api/client.js` | `configureAuth({ getToken, refresh, onTerminal })` module hooks; bearer header; 401 interceptor; `ApiError.reason`; `ApiError.message` = `detail.error` when `detail` is an object (was the JSON of the whole object). New `opts.auth === false` opt-out. |
| `src/logic/session.js` | Persist `refreshToken` and the `account` summary; restore `signedIn` only when a refresh token is present. Header comment updated. |
| `src/logic/HoistraLogic.js` | Spread `AUTH_DEFAULTS` into `state`; mix in `authMethods`; after `loadSession()`, reset a restored `role:'admin'` when `account.role` does not allow it. |
| `src/logic/core.js` | `componentDidMount` calls `this.authBoot()` first; `componentWillUnmount` calls `this.authStop()`; the Escape handler also sets `pwOpen:false`. |
| `src/logic/renderVals.js` | Lines 301–305 (gate handlers) and the account-menu block (310–333) replaced by `...this.authVals(s)`. Nothing else moves. |
| `src/screens/Gate.jsx` | The right sticky panel (lines 284–311) renders by `vals.authMode`. The left column is not touched. |
| `src/components/shell/TopBar.jsx` | Avatar initial and `title` from `vals.acctInitial` / `vals.acctName`; menu header shows `vals.acctName`, a new small `vals.acctEmail` line, then the existing `acctRole · Planum Technologies` line. The rows loop and the Sign out row are unchanged. |
| `src/App.jsx` | `{vals.pwOpen ? <PasswordModal vals={vals} /> : null}` beside the other modals. |
| `docs/shell-and-shared-components.md` | §4.1 and §4.2 rewritten for the real gate and menu. |

### State slice (`AUTH_DEFAULTS`)

```
account: null,          // PublicUser from the server; a validated summary is persisted
accessToken: null,      // memory only — never written to storage
refreshToken: null,     // persisted; the "stay signed in" credential (14 days)
authBooting: false,     // the reload-time refresh is in flight
authMode: 'signin',     // signin | register | verify | forgot | reset
authBusy: false, authError: '', authNotice: '', authReason: '',
authAttemptsLeft: null, // from a mismatch
authRetryAt: 0,         // epoch ms; > now means a cooldown or lockout is counting down
authTick: 0,            // bumped once a second only while authRetryAt is in the future
authConfig: null,       // GET /config, or null → defaults
password: '', fullName: '', phone: '', code: '', newPassword: '',   // gate fields (email already exists)
pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: ''  // change-password modal
```

`signedIn` remains the one boolean the shell keys off (`isHome`, `shellPad`, the frame timer …). Only a successful login, verify-email or refresh — or the optimistic restore of a stored session — sets it true. `role` remains the **view mode** (`user | admin`); `canAdmin = account.role ∈ {admin, superadmin}` decides whether it may ever be `admin`.

### Token plumbing (`api/client.js`)

```
configureAuth({ getToken, refresh, onTerminal })   // called once by authBoot()
```

In `apiFetch`:
1. If `opts.auth !== false` and `getToken()` returns a string, add `Authorization: Bearer <token>`. Every base gets it — harmless for the still-open services, correct the day one of them enforces.
2. On a non-2xx build `ApiError` with `status`, `body`, `reason = body?.detail?.reason ?? ''` and `message`: `detail.error` when `detail` is an object; the `msg` fields joined with "; " when `detail` is FastAPI's 422 array; otherwise the existing fallbacks. (The gate does not show the 422 text — it substitutes its own one-line message — but other callers now get readable text instead of JSON.)
3. If `status === 401 && opts.auth !== false && !opts._retried`:
   - `reason === 'expired'` → `await refresh()` (the controller's single-flight); if it resolves, re-issue the same request with `_retried: true` and the new token. If it rejects, throw the original error.
   - `reason ∈ TERMINAL = {invalid, wrong_type, session_revoked, password_changed, replayed, no_account, disabled}` → `onTerminal(reason, message)` then throw.
   - `missing_token` → throw (a client bug; nothing to recover).

### Controller flows (`logic/auth.js`)

- **`authBoot()`** — `configureAuth(...)`; if `refreshToken` → `authBooting:true` → `authRefresh()`; on settle `authBooting:false`. Registers the `storage` listener. If not signed in, fetches `/config` (non-blocking; defaults until it answers).
- **`authRefresh()`** — single-flight: returns `this._refreshing` if set. Re-reads the refresh token from `loadSession()` (another tab may have rotated it), posts it, and on success stores `account`, both new tokens, `signedIn:true`, clamps `role`. On a 401 → `authSignedOut(message)` and rethrow. On any other failure (network, 5xx, 429) → leave state alone and rethrow; only a 401 means the token is finished.
- **`authSignedOut(notice)`** — clears `account`, both tokens, `pwOpen`; `signedIn:false, role:'user', view:'home', navOpen:false, queueOpen:false, detail:null, acctOpen:false, authMode:'signin', authNotice: notice`. `saveSession` then removes the key.
- **`authSignIn()` / `authRegister()` / `authVerify()` / `authResend()` / `authForgot()` / `authReset()`** — one method per gate action; each sets `authBusy`, calls `authApi`, and maps the result per the table below. A `SessionResponse` is applied by one shared `authEnter(resp)` (account, tokens, `signedIn`, `view:'home'`, `navOpen:true`, role clamp, fields cleared, `flash(resp.message)` when present).
- **`authSSO()`** — `flash('Single sign-on is not available yet — sign in with your email and password.')`.
- **`authSetMode(mode)`** — switches mode, clears `authError`, `code`, `authAttemptsLeft`; keeps `email` and `authNotice`.
- **`authSignOut()`** — `authApi.logout({refresh_token})` fire-and-forget (errors swallowed), then `authSignedOut('')`.
- **`authSignOutEverywhere()`** — `await authApi.logoutEverywhere()`; `authSignedOut('')`; `flash('Signed out of N sessions.')`; on failure still signs out locally.
- **`pwOpenModal() / pwClose() / pwSubmit()`** — the modal; on 200 → `authSignedOut(resp.message)`.
- **`authStop()`** — clears the tick timer and the `storage` listener.
- **Countdown** — `authArm(seconds)` sets `authRetryAt = now + seconds*1000` and starts a 1 s interval bumping `authTick` until it passes; `authVals` derives `authCountdown` (`mm:ss`) and `authCoolingDown`.
- **`storage` listener** — key removed by another tab → `authSignedOut('')`; `refreshToken` changed → adopt it and the stored `account`.

### Gate panel — modes and server mapping

Only the sticky right panel changes. Every mode reuses the panel's input style, the dark primary button (`.hv3`) and the outlined secondary (`.hv4`), the existing "Member access"-style kicker/h2/blurb block, the 11px footnote and the "Read the customer journey →" link. Two lines are added to the chrome: a neutral **notice** and an **error** in `var(--st-risk)`. The primary button disables and reads "Signing in…"/"Creating…"/"Confirming…"/"Sending…"/"Setting…" while busy. Enter submits in every mode.

| Mode | Fields | Buttons / links | Server → UI |
|---|---|---|---|
| **signin** | email, password | **Continue** · **Single sign-on** (inert toast) · "Forgot password?" → forgot · "Create an account" → register (hidden when `authConfig.self_registration === false`) | 200 → `authEnter` · 401 `invalid_credentials` → verbatim · 429 `locked` → verbatim + countdown from `retry_after_seconds` + "Reset your password" link (→ forgot, email kept) · 403 `email_not_verified` → mode **verify**, notice = message, **no** resend call, cooldown armed from otp defaults · 403 `disabled` → verbatim · 422 → "Enter a valid email address and password." · network → "Couldn't reach the sign-in service. Check the backend is running." |
| **register** | full name, email, password (hint "At least *N* characters — a memorable phrase beats a short one with symbols." N from config, default 12), phone (optional) | **Create account** · "Back to sign in" | 202 → **verify**, notice = message, `authArm(otp.resend_cooldown_seconds)`, password cleared · 400 `password` / `email` / `full_name` / `organization_id_required` → verbatim · 403 `registration_closed` → verbatim, link hidden thereafter · 409 `no_organization` → verbatim |
| **verify** | code (`inputMode="numeric"`, spaces and dashes stripped) — shows the email; "Use a different email" → signin | **Confirm** · **Resend code** (disabled with `mm:ss` while cooling down) | 200 → `authEnter` · 400 `mismatch` → verbatim, `authAttemptsLeft`, field kept · 400 `expired` / `too_many_attempts` / `no_code` → verbatim, code cleared, Resend rendered as the primary · resend 202 → notice, typed code cleared, cooldown re-armed · resend 429 `cooldown` / `hourly_cap` → verbatim + countdown · 404 `no_account` → verbatim, mode signin |
| **forgot** | email (prefilled) | **Send reset code** · "Back to sign in" | 202 → **reset**, notice = message, cooldown armed · 429 → verbatim + countdown |
| **reset** | code, new password (min-length hint) — shows the email | **Set password** · **Resend code** (posts `/password/forgot` again, same cooldown) · "Back to sign in" | 200 → **signin**, notice = message, code and password cleared, email kept · 400 `password` / `password_unchanged` → verbatim, **code kept**, password cleared · 400 `mismatch` → attempts left, code kept · dead-code reasons → code cleared, Resend emphasised · 404 → verbatim, mode signin |

`authVals` exposes for the panel: `authMode`, `authBusy`, `authError`, `authNotice`, `authAttemptsLeft`, `authCountdown`, `authCoolingDown`, `authCanRegister`, `authMinLength`, `authCodeLength`, the field values and their `set*` handlers, `authKey` (Enter → the mode's primary action), and one action per button (`authSignIn, authSSO, authRegister, authVerify, authResend, authForgot, authReset, authGoSignin, authGoRegister, authGoForgot`).

### Signed-in shell

- **TopBar**: `acctInitial` (first letter of `full_name`, else of email, else "?"), `acctName`, `acctEmail`. Header gains the email line; everything else identical.
- **Account menu rows** (`acctItems`, still data-driven): Pricing · Support · **Admin view / User view** *(only when `canAdmin`)* · **Change password** (`pwOpenModal`) · **Sign out everywhere** (`authSignOutEverywhere`). The separated **Sign out** row calls `authSignOut`.
- **Role gating**: `canAdmin` derived from `account.role`; `authEnter` and the constructor clamp `role` to `'user'` when it is false. The navigator already filters admin items on `s.role === 'admin'`, so nothing else changes. Superadmin sees the same Admin view as admin.
- **PasswordModal**: title "Change password"; fields current password, new password (min-length hint); Cancel / **Change password** (busy: "Changing…"). 200 → close, `authSignedOut(resp.message)` — the API ends this session too. 401 `invalid_credentials`, 400 `password` / `password_unchanged` → verbatim inside the modal. Escape and the scrim close it.

### Persistence (`session.js`)

- `saveSession` additionally writes `refreshToken` and `account: {id, email, full_name, role, organization_id, status, email_verified}`. **Never** `accessToken`.
- `loadSession` restores `refreshToken` when it is a non-empty string and `account` when it is an object whose `id/email/full_name/role/status` are strings and `role` is one of the three; `signedIn` is restored **only if** `signedIn === true && refreshToken` — otherwise it returns `{}` as today. A slice from the prototype gate therefore lands on the sign-in panel.
- The key stays `hoistra.session.v1`.

### Edge cases

- **Two tabs** — refresh re-reads the token from storage; the `storage` listener adopts a rotation or a sign-out from the other tab. This is what keeps an ordinary two-tab wake-up from tripping `replayed`.
- **Boot refresh: network failure** → stay signed in on the stored identity; `authBooting:false`; the next `expired` 401 refreshes again. **Boot refresh: any 401** → gate with the message.
- **`/config` unreachable** → defaults (12 chars; self-registration on; 6 digits · 10 min · 5 tries · 60 s · 5/hour).
- **422** → generic message, `reason` empty.
- **Concurrent `expired` 401s** → one refresh, every caller retried once with the new token.
- `X-Auth-Config-Warning` is ignored (operator concern, surfaced in backend logs).
- The frame animation (`core.js` `_frameTimer`, runs while `!signedIn`) and every other timer are unchanged.

### Out of scope

`GET /roles`, `GET /users`, `POST /users/{id}/role` (an admin users screen); SSO; protecting non-auth endpoints; the tenant name in the menu header (still the literal "Planum Technologies" — `organization_id` is a UUID, no name endpoint exists).

## Testing

`test/auth.test.mjs` — `node:test`, the real `HoistraLogic` in Node, `globalThis.fetch` mocked per `METHOD /path` exactly as `test/buildingsCrud.test.mjs` does. Nothing reaches a host.

1. Login 200 → `signedIn`, `account`, both tokens in state; `refreshToken` in `localStorage`, `accessToken` **absent** from it; the next `apiFetch` carries `Authorization: Bearer`; a `user`-role account has `role:'user'` and no Admin-view row.
2. Login 401 → `authError` verbatim, still gated · 429 `locked` → `authRetryAt` set, countdown string present · 403 `email_not_verified` → `authMode:'verify'`, no `POST /resend-code` in the call log.
3. Register 202 → verify, cooldown armed, password cleared · verify `mismatch` → attempts left, code kept · verify 200 → signed in · resend 429 → countdown · resend 202 → typed code cleared.
4. Forgot 202 → reset · reset `password_unchanged` → code kept, password cleared · reset 200 → signin with the notice, email kept.
5. Boot with a stored refresh token → exactly one `POST /refresh`, both tokens rotated, `account` replaced · boot `replayed` → gate, notice set, storage key gone · boot network failure → still signed in, stored account intact.
6. Interceptor: a bearer call answering 401 `expired` → one `POST /refresh`, the call re-issued once and its result returned · two such calls at once → **one** refresh · 401 `session_revoked` → signed out, storage cleared · a failing `/login` never calls `/refresh`.
7. Sign out → `POST /logout` with the refresh token; storage cleared even when logout rejects · sign out everywhere → `everywhere:true` with the bearer, `flash` mentions `sessions_ended`.
8. Change password 200 → modal closed, tokens cleared, gate in signin with the message · 401 → `pwError` verbatim, still signed in.
9. Role gating: an `admin` account gets the toggle; restoring `role:'admin'` with a `user` account resets to `user`.
10. `session.js`: a legacy slice `{signedIn:true, email}` restores `{}`; a slice with `refreshToken` + `account` restores both.
11. `client.js`: `ApiError.message` is `detail.error` for an object detail and the joined FastAPI text for an array; `reason` is read from `detail.reason`.

Existing suites stay green (`npm test`); `npm run build` succeeds.

## Verification in a browser

Headless Chrome (per the existing verification approach) against `npm run dev` with `VITE_DEV_PROXY_TARGET` pointed at a throwaway Node mock that serves `/backend/ops-intelligence/api/auth/*` with the captured response shapes, on a spare port. Every gate mode, the countdowns, the modal and the role-gated menu are exercised there. **No request reaches `localhost:3000`, the production-pointed backend, or its database; no migrations are run.** A real-backend pass happens only if Hussain later authorises the migrations and the account writes.

## Documentation

- `docs/shell-and-shared-components.md` §4.1 — the five gate modes, what each server reason does, the inert SSO button. §4.2 — the account menu rows, role gating, the change-password modal's sign-out-on-success.
- Header comments in `session.js` (what is now persisted and why the access token is not) and `api/client.js` (bearer, interceptor, `auth:false`).
