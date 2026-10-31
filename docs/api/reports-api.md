# Custom reports — pin a session's question, remove cards, refreshed by the server

Backend only; the shell's `logic/reports.js` still keeps reports in `localStorage` and re-runs
them while the tab is open. This API replaces that: reports and their cards are stored per
user, and **the server refreshes each card on its cadence whether or not a browser is open**,
asking the orchestrator as the card's owner (so the answer is drawn from exactly the buildings
they may see). All routes need `Authorization: Bearer <token>`; every row is the caller's own.

Base: `/backend/ops-intelligence`.

## The model

- **Report** — a person's named collection of cards. Created explicitly, or automatically
  ("My report") the first time they pin a card without naming one.
- **Card** — one pinned question: `prompt` (the session's question), `name`, where it came
  from (`source.session_id`, `source.page`, `source.message_id`), a `refresh` cadence, a
  `timezone`, a `position`, `enabled`, and its `status` (`pending | running | ready | error | paused`).
- **Run** — one refresh: `ran_at`, `duration_ms`, `ok`, `trigger` (`schedule | manual | seed`),
  `answer` (markdown), `tool_calls` (which tools the orchestrator read), `rich`, `error`.
  The newest 10 are kept; lists return the newest 3 by default (`?runs=`).

## Cadence (`refresh`)

Send a preset key or one object:

| Send | Meaning |
|---|---|
| `"30m"`, `"1h"`, `"6h"`, `"12h"`, `"24h"` | `{"every_minutes": 30 / 60 / 360 / 720 / 1440}` |
| `"daily"` | `{"daily_at": "02:00"}` |
| `{"every_minutes": n}` | every *n* minutes, 5 ≤ n ≤ 10080 |
| `{"daily_at": "HH:MM"}` | every day at that time **in the card's `timezone`** |
| `{"days": [0-6…], "time": "HH:MM"}` | chosen weekdays at that time; `0 = Sunday … 6 = Saturday` (same as the shell) |

`timezone` is an IANA name (`Europe/London`, `Asia/Dubai`); default `UTC`. Clock cadences
follow the owner's clock across DST. `GET /api/reports/refresh-options` returns the presets
(`options[]` with `key`, `label`, `badge`, `refresh`, `pick_days`) and `days[]` so the menu
matches the server.

Bad cadences are refused with `422 {reason: "bad_refresh" | "bad_time" | "no_days" | "bad_timezone"}`.

## Endpoints

### `GET /api/reports?runs=3`
All of the caller's reports, each with its `cards[]`; each card carries `runs[]` (newest
first) and `latest_run`. `runs=0` skips run bodies for a light list.

```json
{ "ok": true, "count": 1, "reports": [{
  "id": "…", "name": "My report", "card_count": 2,
  "cards": [{
    "id": "…", "report_id": "…", "name": "Lapsing certificates", "prompt": "Which certificates lapse this month?",
    "source": {"session_id": "s_…", "page": "Compliance", "message_id": null},
    "refresh": {"every_minutes": 60}, "refresh_label": "every 1 hour", "timezone": "UTC",
    "position": 0, "enabled": true, "status": "ready",
    "last_run_at": "2026-09-14T10:02:11+00:00", "next_run_at": "2026-09-14T11:02:11+00:00", "last_error": null,
    "latest_run": {"id": "…", "ran_at": "…", "duration_ms": 41230, "ok": true, "trigger": "schedule",
                   "answer": "Two certificates lapse this month …", "tool_calls": [{"tool": "list_certificates", "input": {…}}],
                   "rich": null, "error": null},
    "runs": [ … ]
  }]
}]}
```

### `POST /api/reports` `{name}` → 201 `{report}` · `PATCH /api/reports/{id}` `{name}` · `DELETE /api/reports/{id}`
Delete removes the report and every card on it (`cards_removed`).

### `POST /api/reports/cards` → 201 — **pin from the session chat**

```json
{
  "prompt": "Which certificates lapse this month?",      // the session's question (required)
  "name": "Lapsing certificates",                         // optional; defaults to the prompt
  "report_id": null,                                      // omit → your first report (created if needed)
  "refresh": "1h",                                        // or an object, see above
  "timezone": "Europe/London",
  "source_session_id": "s_…", "source_page": "Compliance", "source_message_id": "m_…",
  "seed": { "answer": "<the answer already on screen>", "tool_calls": [ … ], "rich": null },
  "run_now": true
}
```
- `seed` is the answer the user is looking at when they pin: it is stored as a `trigger: "seed"`
  run so the card is never empty. Send `answer` and `tool_calls` straight from the workflow
  response.
- `run_now: true` (default) queues the first **live** refresh immediately — the scheduler picks
  it up within one tick (30 s) and the card goes `pending → running → ready`. Poll
  `GET /api/reports/cards/{id}` or re-list.

Response: `{ok, report, card}` (card includes `runs`).

### `GET /api/reports/cards/{card_id}?runs=3` → `{card}` with `runs[]`

### `PATCH /api/reports/cards/{card_id}`
Any of `name`, `prompt`, `refresh`, `timezone`, `enabled` (false pauses, true resumes),
`position`, `report_id` (move to another of your reports). Changing the cadence reschedules
from now.

### `DELETE /api/reports/cards/{card_id}` — **remove one card**
Removes only that card; the report and its other cards are untouched.
→ `{ok, card_id, report_id, removed: true}`.

### `POST /api/reports/cards/{card_id}/run`
Refresh now and wait for the answer (up to the run timeout, 180 s). Returns `{ok, card, run}`;
`ok` is the run's success. `409 {reason: "already_running"}` if a refresh is in flight.

### `GET /api/reports/cards/{card_id}/runs?limit=3` → `{runs[]}`

## Status and errors

| Card `status` | Meaning |
|---|---|
| `pending` | queued; the next tick will run it |
| `running` | a refresh is in flight |
| `ready` | last refresh succeeded (`last_run_at`) |
| `error` | last refresh failed (`last_error`); it retries on its cadence |
| `paused` | `enabled: false`, or the owner was deactivated/deleted — it will not run until resumed |

| Status | `reason` | Meaning |
|---|---|---|
| 401 | `missing_token`, `invalid_token` | not signed in |
| 404 | `report_not_found`, `card_not_found` | not one of yours (never 403 — existence is not confirmed) |
| 409 | `already_running` | Run now while a refresh is in flight |
| 422 | `bad_refresh`, `bad_time`, `no_days`, `bad_timezone`, `no_prompt` | body refused |

## What the scheduler does

Every 30 s (`REPORT_SCHEDULER_TICK_SECONDS`) the service claims the most overdue enabled
card (`FOR UPDATE SKIP LOCKED`, so replicas never double-run), mints a short-lived token for
its owner, POSTs the prompt to deep-agents `/api/workflow/run-stateful` with the report brief
as `context`, and stores the answer as a run. A run left "running" for 20 minutes (a restart
mid-refresh) is put back to pending. A failed refresh is recorded as a run with `error` — the
card never silently looks current. Off switch: `REPORT_SCHEDULER_ENABLED=false`.
