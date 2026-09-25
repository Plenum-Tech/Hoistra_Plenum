# Sessions, Spaces and Custom Reports — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the seeded Sessions, Spaces and custom report in the Hoistra frontend with working state: sessions keyed by the orchestrator thread, spaces read from and written to svc-udr with live badges, and reports that run their prompt on a cadence.

**Architecture:** Three new store mix-ins on the existing controller (`sessions.js`, `spacesLive.js`, `reports.js`), each with a pure shaping function under `node --test`, a `localStorage` slice for what Plenum also keeps in the browser, and the svc-udr Saved Spaces API for custom spaces. Two new screens (Sessions, Space) and a rewritten CustomReport body; the Navigator reads the new view-model keys.

**Tech Stack:** React 18 + Vite 5, plain JS store (`Controller` / `HoistraLogic`), `node --test` on Node 24, fetch wrapper in `src/api/client.js`.

**Spec:** `docs/superpowers/specs/2026-09-08-sessions-spaces-reports-design.md`

## Global Constraints

- Node 24 (`nvm use 24.18.1`); `npm test` = `node --test "test/**/*.test.mjs"`; `npx vite build` must pass.
- Reads through `GET` only during verification. The spaces write routes are wired for the user's clicks and are **not** exercised by tests or by the implementer against the running stack.
- No seed numbers anywhere in the three groups: an unsourced figure renders as `—`.
- Components stay pure functions of `vals`; behaviour lives on the controller; inline styles.
- Do not commit (the user asked not to commit without asking).

---

### Task 1: `reports.js` pure core — cadences, `nextRunAt`, markdown export, storage

**Files:**
- Create: `apps/frontend/src/logic/reports.js`
- Test: `apps/frontend/test/reports.test.mjs`

**Interfaces (produces):**
```js
export const REPORTS_KEY = 'hoistra.reports.v1';
export const CADENCES;          // [{ label, badge, everyMs? , daily?: 'HH:MM', pickDays?: true }]
export const DAYS;              // ['Sun', … 'Sat']
export function cadenceLabel(cad)          // cad = { i, days:[int], time:'HH:MM' }
export function cadenceBadge(cad)
export function nextRunAt(cad, fromMs)     // ms of the next run strictly after fromMs
export function reportMarkdown(report, run)
export function loadReports(storage) / saveReports(list, storage)   // storage: { getItem, setItem, removeItem }
export function makeReport({ key, name, prompt, sessionId, page, cad, now })
```

- [ ] Write `test/reports.test.mjs` covering: interval cadence adds `everyMs`; daily 02:00 from 14:00 → tomorrow 02:00, from 01:00 → today 02:00; chosen days Mon+Thu at 14:00 from Tue → Thu 14:00, from Thu 15:00 → next Mon; empty days → null; `cadenceLabel` for days reads "Refresh Mon, Thu at 14:00"; `reportMarkdown` contains the title, prompt, and answer; round-trip load/save with an in-memory storage, and a corrupt entry is dropped.
- [ ] Run `node --test test/reports.test.mjs` → fails (module missing).
- [ ] Implement `reports.js` (pure part only) and re-run → pass.

### Task 2: `sessions.js` pure core — records, sync, shaping, storage

**Files:**
- Create: `apps/frontend/src/logic/sessions.js`
- Test: `apps/frontend/test/sessions.test.mjs`

**Interfaces (produces):**
```js
export const SESSIONS_KEY = 'hoistra.sessions.v1';
export function ago(atMs, nowMs)               // 'just now' | 'N mins ago' | 'N hours ago' | 'N days ago'
export function newSessionId()
export function makeSession({ id, title, page, kind, at })   // kind 'chat' | 'task'
export function syncTurns(rec, turns, nowMs)   // trims to 40 turns, recomputes calls/domain, bumps at
export function sessionDomain(rec)             // 'Compliance' | 'Energy' | 'Vendors' | 'Work orders' | 'Documents' | 'Migration' | 'Orchestrator'
export function spaceKeyOf(domain)             // 'compliance' | 'energy' | 'vendors' | 'ops' | null
export function sessionIcon(rec)
export function trimSessions(list)             // cap 60, newest first
export function loadSessions(storage) / saveSessions(list, storage)
export function shapeSessionList(sessions, { query, space, nowMs })  // → [{ day, rows:[{ id, title, when, page, domain, icon, turns, kind }] }]
```

- [ ] Tests: `ago` bands; `syncTurns` keeps the last 40 and computes `calls` from bot turns and `domain` (rich ⇒ Compliance); `spaceKeyOf`; `shapeSessionList` groups Today/Yesterday, filters by query (case-insensitive on title) and by space (`compliance` matches domain, a uuid matches `spaceId`); `saveSessions` shrinks on a storage that throws once (drops `trace` first, then oldest); `loadSessions` drops records without `id`/`title`.
- [ ] Run → fails; implement; run → pass.

### Task 3: `spacesLive.js` pure core — built-in spaces and badges

**Files:**
- Create: `apps/frontend/src/logic/spacesLive.js`, `apps/frontend/src/api/spaces.js`
- Test: `apps/frontend/test/spacesLive.test.mjs`

**Interfaces (produces):**
```js
export const BUILTIN_SPACES = [{ key, name, icon, domain, view?, module?, page }]
export function shapeSpaces({ home, vendors, saved, sessions, savedError, savedLoading })
// → { builtin:[{ key, name, icon, badge, count, tone, kpis:[{label,value}], page, sessions }],
//     custom:[{ id, name, createdAt, sessions }], byKey:{ [key|id]: entry }, savedLive, savedError, savedLoading }
export const spacesApi = { list(), create(name, createdBy), rename(id, name), remove(id) }
```

- [ ] Tests with trimmed real fixtures: compliance badge "15 lapsed" from building 6 + vendor 9; energy "3 anomalies" and cost KPI "£19,252"; vendors "2 below 80" from scores [46.58, 20.38, 85.61]; ops "500+ to approve" when count ≥ 500 and "25 to approve" otherwise; `—` and `count:null` when a source is null; custom spaces map with session counts; `savedError` passes through.
- [ ] Run → fails; implement; run → pass.

### Task 4: Controller wiring — state, mix-ins, `ccAsk` hooks, reload slice

**Files:**
- Modify: `src/logic/HoistraLogic.js` (state, constructor restore, `setState` sync hook, mix-ins)
- Modify: `src/logic/complianceLive.js` (`_ccSession` → `state.sessionId`, `sessionEnsure`, `ccChatReset`)
- Modify: `src/logic/core.js` (`orch(task, ctx, chain, opts)` records via `recordSession` unless `opts.record === false`; `componentDidMount` calls `spLoad()`, `rpStart()`; `componentWillUnmount` clears timers; `ctxLabel` for `sessions` / `space`)
- Modify: `src/logic/session.js` (VIEWS + `sessions`, `space`; persist `sessionId`, `spaceKey`)
- Modify: `src/logic/constants.js` (remove `SESSIONS`, `ASSET_RISK`, `CADENCES`, `DAYS`, `CADENCE_LABEL`, `CADENCE_BADGE`)
- Add controller methods to `sessions.js` (`sessionsMethods`), `spacesLive.js` (`spacesMethods`), `reports.js` (`reportsMethods`)
- Test: extend `test/chat.test.mjs`; new `test/store.test.mjs`

**Controller methods:**
```js
// sessions
sessionEnsure(q, page)  → id   // creates the record on the first question; returns state.sessionId
sessionSync()                  // mirrors state.ccChat into the active record (called from setState)
openSession(id); deleteSession(id); fileSession(id, spaceId); openSessions(); newQuery()
// spaces
spModel(); spLoad(); spCreate(name); spRename(id, name); spDelete(id); openSpace(key); spaceEntry(key)
// reports
rpCreate(); rpRun(key); rpStart(); rpStop(); rpTick(); rpDelete(key); rpExport(key); rpOpen(key)
```

- [ ] Tests: asking from home creates `sessions[0]` with `id === state.sessionId`, `turns.length === 2` after the dead-backend reply, `page === 'Home'`; `ccChatReset` drops `sessionId`; `openSession(id)` restores `ccChat` and sets `view: 'chat'`; a `task` from `orch()` is recorded with `kind: 'task'`; `rpCreate` with a chat session makes a report whose first run fails against the dead backend → `status: 'error'`, `runs[0].error` set, `nextRunAt` still scheduled; `spLoad` against the dead backend sets `spError` and `spaces: null`, and `spModel().builtin.length === 4`.
- [ ] Run → fails; implement; `npm test` → pass.

### Task 5: View model — `renderVals.js`

**Files:**
- Modify: `src/logic/renderVals.js`

Keys (replace the seeded ones; add the new pages):
```
navSpaces: [{ name, n, icon, active, click }]            // built-in then custom
navSpaceNew, navSpaceName, setNavSpaceName, navSpaceKey (Enter creates / Escape cancels), navSpaceCreate, navSpaceCancel, navSpaceNote
navSessions: [{ label, when, icon, active, click }]      // newest 8
allSessions: () => openSessions()
navSections[].count                                      // live figures from spModel().byKey
navReports[].badge                                       // cadence badge | Pending | Running | Failed
report: { title, kicker, lastRun, meta, next, tools, answer, rich, status, error, runs:[{ label, active, pick }] }
reportPending, reportRunning, reportReady, reportError, runReport, exportReport, deleteReport
reportSources: [{ label, tick, color, chip, pick }]      // chat sessions, newest 5
isSessions, sessionsPage: { count, query, setQuery, chips:[{ label, on, pick }], groups:[{ day, rows:[…+open, remove, fileOptions, fileTo] }] }
isSpace, spacePage: { kicker, name, badge, kpis, isCustom, openPage, openLabel, rename…, remove, ask…, groups }
```

- [ ] Rewrite the affected blocks; keep every other key untouched. `npm test` and `npx vite build` pass.

### Task 6: Screens — Navigator, Sessions, Space, CustomReport, Chat header, App routing

**Files:**
- Modify: `src/components/shell/Navigator.jsx` (spaces with icon + badge, inline new-space row, sessions with `active`, "All sessions")
- Create: `src/components/shell/SessionList.jsx` (shared by Sessions and Space)
- Create: `src/screens/Sessions.jsx`, `src/screens/Space.jsx`
- Modify: `src/screens/CustomReport.jsx` (body = latest run; states; Run now / Export / Delete; previous refreshes)
- Modify: `src/screens/Chat.jsx` (header shows the session title and a "File in space" select)
- Modify: `src/App.jsx` (`isSessions`, `isSpace`)

- [ ] Build passes; manual run through `npm run dev` proxied to the gateway.

### Task 7: Docs

- Modify: `apps/frontend/README.md` (organisation list + a "Sessions, spaces and reports" section), root `README.md` (Notes), `apps/frontend/docs/shell-and-shared-components.md` §4.3 paragraph on Spaces / Sessions / New report.

### Task 8: Verify

- `npm test`, `npx vite build`; rebuild only the frontend container:
  `docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --build --no-deps frontend-app`;
  `curl -s localhost:3000/ | head` and the `GET /backend/udr/api/spaces` read. No write route is called.
