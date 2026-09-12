# Sessions, Spaces and Custom Reports — design (2026-09-08)

## Problem

The navigator's Sessions, Spaces and Reports groups are seed data. Five sessions are a constant,
the four spaces carry fixed badges, and the one custom report renders a hardcoded asset table.
Nothing a user does survives a reload. The Plenum AI shell has the same three concepts working
against real state, and this port should too — reading what Plenum already exposes and holding
in the browser only what Plenum itself holds in the browser.

## What the Plenum backend provides (verified against the running stack)

| Concept | Plenum source | Route | Persisted where |
| --- | --- | --- | --- |
| Saved spaces (customer-named LHS buckets) | svc-udr `spaces.py` | `GET/POST /api/spaces`, `PATCH/DELETE /api/spaces/{id}` | `plenum_cafm.saved_spaces` |
| Built-in space figures | svc-operations-intelligence | `GET /api/compliance/saved-space/summary`, `/api/energy/anomalies`, `/api/contract-performance/saved-space/summary`, `/api/approvals` | engine tables (read-only) |
| Session = orchestrator thread | svc-deepagents | `POST /api/workflow/run-stateful` with `session_id`; `GET /api/workflow/status/{id}` | LangGraph Postgres checkpointer; **no list route** |
| Pinned runs | `plenum_cafm.pinned_run` | none — table only | — |

Plenum's own shell keeps the chat turns in `localStorage` (`plenum_deep_agent_turns_v1`) and
classifies a session into a built-in space by the engine that answered (`infer_saved_space`).
This design does the same.

## Sessions

**Record** (`src/logic/sessions.js`, stored under `hoistra.sessions.v1`):

```
{ id, kind: 'chat'|'task', title, label, page, at, createdAt, turns: [ccChat messages],
  calls: [tool names seen], spaceId: null|uuid, task?, ctx?, steps? }
```

- `id` is the orchestrator `session_id`; the first question makes the record, follow-ups from
  any page (chat page or a side dock) append to it. `state.sessionId` replaces the private
  `_ccSession`.
- The transcript is mirrored into the record whenever `ccChat` changes (one hook in
  `HoistraLogic.setState`, next to `saveSession`). Messages are already serialisable — only file
  names ride on them.
- Orchestrator tasks (`orch()`) stay sessions of kind `task` so the dock can reopen them.
- Storage is capped: 60 sessions, 40 messages each; on a quota error traces are dropped from
  the oldest sessions, then the oldest sessions.
- Reload: the active `sessionId` is part of the reload slice (`session.js`); the transcript is
  restored from its record, so the conversation page comes back as it was and follow-ups keep
  the server thread.
- Opening a chat session restores its transcript and makes it active; a task session reopens
  the dock on that task. "New query" / "New thread" clear the transcript and drop the id.
- A session's engine domain is `domainOf` over every tool the thread used; it picks the
  navigator icon and files the session under a built-in space.
- **All sessions** is a view (`sessions`): search, grouped by day, page and domain per row,
  delete, and "file in" a custom space. Space chips filter the list.

## Spaces

`src/logic/spacesLive.js` — `shapeSpaces({ home, vendors, saved, sessions })`, pure.

Built-in (fixed keys, live figures, never seed numbers — "—" until the source answers):

| Key | Badge | Source already loaded |
| --- | --- | --- |
| compliance | `N lapsed` (building + vendor) | `homeRaw.compliance` (saved-space summary) |
| energy | `N anomalies` | `homeRaw.anomalies` |
| vendors | `N below 80` | `vpModel().vendors` (newest scorecard per vendor) |
| ops | `N to approve` (`500+` when the page cap is hit) | `homeRaw.approvals` |

Each also carries a KPI row for its page (certificates on record / lapsed / drafts / blocked
vendors; open anomalies / annualised cost; vendors scored / average / blocked; approvals by
severity) and the sessions whose domain maps to it.

Custom: `GET /backend/udr/api/spaces` on mount (org-scoped when `VITE_ORGANIZATION_ID` is set).
The navigator "+" opens an inline name field; Enter → `POST /api/spaces`. Rename and delete go
through `PATCH` / `DELETE`. Membership of sessions in a custom space is client-side (`spaceId`
on the record) because `saved_space_item` has no route. When svc-udr does not answer the
built-ins still render and the custom section says so.

**Space view** (`space`, `spaceKey`): kicker (built-in / saved), name (rename inline for saved),
badge, KPI row and "Open <page>" for built-ins, delete for saved, an ask bar whose question
starts a new session filed in the space, then the session list.

The navigator section counts (Compliance 3, Vendors 3, Energy 6, Work orders 14, Buildings 24)
are the same figures and read the same model; Buildings reads the live site count.

## Custom reports (pinned runs)

`src/logic/reports.js`, stored under `hoistra.reports.v1`:

```
{ key, name, prompt, sessionId, page, cad: { i, days, time }, createdAt, lastRunAt,
  nextRunAt, status: 'pending'|'running'|'ready'|'error', error, runs: [{ at, ms, answer,
  calls, rich, error }] }   // runs newest first, last 3 kept
```

- Cadences: 30 min · 1 hr · 6 hr · 12 hr · 24 hr · daily 02:00 · chosen days at a time.
  `nextRunAt(cad, from)` is pure and tested.
- Create (navigator menu): source = one of the recent chat sessions (its first question is the
  prompt), cadence, name. Creating runs it at once.
- Run: `POST /api/workflow/run-stateful` with a fresh `report-<key>-<ts>` session id (no history
  bleed, and the compliance preflight's structured answer is available) and a context line that
  says this is a scheduled report. Success stores the answer, tool names and structured payload;
  failure stores the error and keeps the schedule.
- Scheduler: a 30-second tick runs due reports one at a time while the app is open and the user
  is signed in. The page says so ("re-run while Hoistra is open").
- Report page: title, "Built from the session …", last refresh and next run, cadence, Run now,
  Export (downloads `<name>.md` built from the run), Delete; body is the latest run rendered as
  the chat renders it (structured compliance answer or markdown), with the previous refreshes
  selectable. Pending / running / error states are explicit.
- Navigator badge: cadence badge, or Pending / Running / Failed.
- Home "Pinned runs" chips keep listing the reports by name.

## Removed

`SESSIONS`, `ASSET_RISK`, the seeded report and the hardcoded space badges leave `constants.js`
/ the initial state. `newSpace` and `exportReport` stop being scripted orchestrator flows.
The "Assets (Pending)" section opens its module instead of the deleted seed report.

## Not done, and disclosed in the UI

- No server list of sessions: the list is this browser's. Another device sees the thread on the
  server only if it knows the id.
- Session ↔ space filing is client-side (no `saved_space_item` route).
- Reports re-run only while the app is open (no pinned-run route or scheduler on the server).
- Creating / renaming / deleting a space writes `plenum_cafm.saved_spaces` on the user's click.

## Testing

`node --test` (Node 24): pure shaping in `sessions.js`, `spacesLive.js`, `reports.js`
(fixtures trimmed from real responses); controller tests in the style of `test/chat.test.mjs`
for session creation, restore, new thread, report create → run → error path with a dead
backend, and space list failure. `vite build` must pass.
