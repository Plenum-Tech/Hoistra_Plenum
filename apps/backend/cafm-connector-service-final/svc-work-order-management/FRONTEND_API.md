# Work Order Module — Frontend API Reference

**Base URL (production):** `https://<your-azure-container>/work-order`  
**Base URL (local dev):** `http://localhost:8007`  
**Interactive docs:** `<base>/docs`

All responses are JSON. All error responses follow the same envelope (see [Error Format](#error-format)).

---

## Table of Contents

1. [Data Types & Enums](#1-data-types--enums)
2. [Work Orders](#2-work-orders)
3. [Approvals](#3-approvals)
4. [Email Intake](#4-email-intake)
5. [PPM Scheduler](#5-ppm-scheduler)
6. [Journey Logs](#6-journey-logs)
7. [Assets & Locations](#7-assets--locations)
8. [Dashboard](#8-dashboard)
9. [Error Format](#9-error-format)
10. [UI Flow Recipes](#10-ui-flow-recipes)
11. [Chat Interface (GPT Agent)](#11-chat-interface-gpt-agent)

---

## 1. Data Types & Enums

### Work Order Status (state machine)

```
pending_approval → preparing → prepared → active → completed → closed
                ↘                       ↗
                 → closed (from any state)
```

| Status | Meaning | UI label suggestion |
|---|---|---|
| `pending_approval` | Submitted, waiting for manager approval | Pending Approval |
| `preparing` | Approved — team is preparing | Preparing |
| `prepared` | Ready to dispatch to CMMS | Ready |
| `active` | Sent to CMMS, work in progress | Active |
| `completed` | Work done | Completed |
| `closed` | Fully closed | Closed |

### Priority

`low` · `medium` · `high` · `urgent` · `critical`

### Source (how the WO was created)

`email` · `ppm` · `manual` · `tenant` · `internal` · `remediation`

### Request Type

`repair` · `maintenance` · `inspection` · `installation`

### WorkOrderResponse fields

```typescript
interface WorkOrderResponse {
  work_order_id: string;          // e.g. "WO-20260504094532123456"
  source: string | null;
  status: string | null;
  priority: string | null;
  asset: string | null;           // asset name / code string
  location: string | null;
  issue_description: string | null;
  request_type: string | null;
  requester_name: string | null;
  requester_email: string | null;
  vendor: string | null;
  scheduled_date: string | null;  // "YYYY-MM-DD"
  scheduled_time: string | null;  // "HH:MM"
  cmms_work_order_id: string | null;
  journey_log_id: string | null;
  created_at: string | null;      // ISO 8601
  approved_at: string | null;
  prepared_at: string | null;
}
```

---

## 2. Work Orders

### 2.1 Create a Work Order

```
POST /api/work-orders/
```

**Request body:**

```json
{
  "source": "manual",
  "asset": "HVAC-301",
  "location": "Building A - Roof Level",
  "issue_description": "Grinding noise and reduced cooling capacity",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "John Smith",
  "requester_email": "john@example.com",
  "requester_phone": "+971-50-123-4567"
}
```

| Field | Required | Notes |
|---|---|---|
| `source` | Yes | One of the Source enum values |
| `asset` | Yes | Free text — asset name or code |
| `location` | Yes | Free text |
| `issue_description` | Yes | Becomes the WO title |
| `priority` | No | Default: `medium` |
| `request_type` | No | Default: `repair` |
| `requester_name` | Yes | |
| `requester_email` | Yes | Must be valid email format |
| `requester_phone` | No | |

**Response: 201 Created** — returns `WorkOrderResponse`

```json
{
  "work_order_id": "WO-20260504094532123456",
  "status": "pending_approval",
  "priority": "high",
  "asset": "HVAC-301",
  "location": "Building A - Roof Level",
  "journey_log_id": "jlog-abc123",
  "created_at": "2026-05-04T09:45:32Z",
  ...
}
```

**UI notes:**
- After creation, the WO is always in `pending_approval`.
- Save `work_order_id` and `journey_log_id` — you'll need both for polling.
- To watch for approval in real time, open the SSE stream at `/api/email/watch/{work_order_id}`.

---

### 2.2 List Work Orders

```
GET /api/work-orders/?status=active&priority=high&page=1&limit=20
```

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by status value |
| `priority` | string | Filter by priority value |
| `asset` | string | Substring search on asset name |
| `from_date` | ISO datetime | Created at or after |
| `to_date` | ISO datetime | Created at or before |
| `page` | int | Default 1 |
| `limit` | int | Default 20, max 200 |

**Response: 200 OK** — array of `WorkOrderResponse`

```json
[
  { "work_order_id": "WO-...", "status": "active", ... },
  { "work_order_id": "WO-...", "status": "pending_approval", ... }
]
```

---

### 2.3 Get a Single Work Order

```
GET /api/work-orders/{work_order_id}
```

**Response: 200 OK** — `WorkOrderResponse`  
**Response: 404** — work order not found

---

### 2.4 Quick Filter Shortcuts

```
GET /api/work-orders/filter/active
GET /api/work-orders/filter/pending-approval
```

Both return an array of `WorkOrderResponse`, sorted newest first. Useful for dashboard panels and notification badges.

---

### 2.5 Update a Work Order

```
PATCH /api/work-orders/{work_order_id}
```

Use this to fill in preparation details (vendor, schedule, etc.) without changing status.

**Request body** (all fields optional — only send what you're changing):

```json
{
  "vendor": "CoolTech HVAC Services",
  "scheduled_date": "2026-05-10",
  "scheduled_time": "09:00",
  "estimated_duration": 3.5,
  "inspection_required": true,
  "special_requirements": "Requires roof access permit",
  "cmms_work_order_id": "CMMS-88421"
}
```

**Response: 200 OK** — updated `WorkOrderResponse`

---

### 2.6 Transition Status

```
PATCH /api/work-orders/{work_order_id}/status
```

Moves a WO through the state machine. Will reject invalid transitions.

**Request body:**

```json
{
  "new_status": "active",
  "notes": "Dispatched to technician after CMMS sync"
}
```

**Valid transitions:**

| From | Can go to |
|---|---|
| `pending_approval` | `preparing`, `closed` |
| `preparing` | `prepared`, `closed` |
| `prepared` | `active`, `preparing`, `closed` |
| `active` | `completed`, `closed` |
| `completed` | `closed` |
| `closed` | — (terminal) |

**Response: 200 OK** — updated `WorkOrderResponse`  
**Response: 422** — invalid transition (includes `allowed` list in error detail)

---

### 2.7 Approve a Work Order

```
POST /api/work-orders/{work_order_id}/approve
```

Shortcut for transitioning `pending_approval → preparing`. Sets `approved_at` timestamp.

No request body needed.

**Response: 200 OK** — updated `WorkOrderResponse`  
**Response: 409** — WO is not in `pending_approval` state

---

### 2.8 Prepare a Work Order

```
POST /api/work-orders/{work_order_id}/prepare
```

Transitions to `prepared` and accepts preparation data in the same call.

**Request body** (same fields as PATCH update — all optional):

```json
{
  "vendor": "CoolTech HVAC Services",
  "scheduled_date": "2026-05-10",
  "scheduled_time": "09:00",
  "estimated_duration": 3.5
}
```

**Response: 200 OK** — updated `WorkOrderResponse`

---

### 2.9 Close a Work Order

```
POST /api/work-orders/{work_order_id}/close
```

Closes from any non-closed status. No request body.

**Response: 200 OK** — updated `WorkOrderResponse`  
**Response: 409** — WO is already closed

---

### 2.10 Bulk Status Update

```
PATCH /api/work-orders/bulk/status
```

Apply the same status transition to multiple WOs at once.

**Request body:**

```json
{
  "work_order_ids": ["WO-001", "WO-002", "WO-003"],
  "new_status": "closed",
  "notes": "Batch close after audit"
}
```

**Response: 200 OK:**

```json
{
  "updated": 2,
  "failed": 1,
  "succeeded_ids": ["WO-001", "WO-003"],
  "failed_details": [
    { "work_order_id": "WO-002", "reason": "invalid_transition:active->closed" }
  ]
}
```

**UI notes:** Always show the `failed_details` so the user knows which ones didn't apply.

---

### 2.11 Status History

```
GET /api/work-orders/{work_order_id}/history
```

Returns all status transitions for a WO, oldest first.

**Response: 200 OK:**

```json
[
  {
    "work_order_id": "WO-...",
    "from_status": null,
    "to_status": "pending_approval",
    "changed_at": "2026-05-04T09:45:32Z",
    "notes": null
  },
  {
    "work_order_id": "WO-...",
    "from_status": "pending_approval",
    "to_status": "preparing",
    "changed_at": "2026-05-04T10:12:00Z",
    "notes": "Approved via email"
  }
]
```

Use this to build a timeline/audit trail view.

---

## 3. Approvals

### 3.1 Respond to an Approval Request

```
POST /api/work-orders/approvals/{approval_request_id}/respond?approved=true&notes=Looks+good
```

Both params are query params (not body).

| Param | Type | Required | Notes |
|---|---|---|---|
| `approved` | bool | Yes | `true` or `false` |
| `notes` | string | No | Optional manager notes |

**Response: 200 OK** — result object from the approval workflow

**Response: 404** — approval request ID not found  
**Response: 409** — already processed

**UI notes:** This endpoint calls out to the CMMS via `AIMMS_API_URL`. If that URL isn't configured, it will return 503. This endpoint is typically called by a backend webhook handler when a manager clicks "Approve / Reject" in an email link — not directly from the main UI. The main UI should instead poll the WO status or use the SSE watch stream.

---

## 4. Email Intake

### 4.1 Check Outlook Connection

```
GET /api/email/status
```

Use this on the settings / admin page to verify Outlook is connected.

**Response: 200 OK:**

```json
{
  "connected": true,
  "display_name": "Shashank Kanangi",
  "email": "shashank@plenum-tech.com"
}
```

or on failure:

```json
{
  "connected": false,
  "error": "Token expired or invalid"
}
```

---

### 4.2 Process a Single Email (manual / test)

```
POST /api/email/process
```

Pass any raw email dict. The service runs the full AI pipeline and creates a WO.

**Request body:**

```json
{
  "subject": "Urgent - HVAC-301 making grinding noise",
  "body": "Hi Team,\n\nHVAC-301 on Building A - Roof Level...\n\nRegards,\nJohn Smith\nPhone: +971-50-123-4567",
  "from": "john@example.com",
  "from_name": "John Smith",
  "id": "AAMkADFj..."
}
```

**Response: 200 OK:**

```json
{
  "status": "created",
  "work_order_id": "WO-20260504094532",
  "priority": "high",
  "asset": "HVAC-301",
  "requester_name": "John Smith",
  "assessment_summary": "Critical HVAC fault — grinding noise indicates bearing failure. High priority.",
  "full_assessment": { ... }
}
```

**Possible `status` values:**

| Status | Meaning | UI action |
|---|---|---|
| `created` | WO created successfully | Show WO ID, offer to watch for approval |
| `missing_info` | Not enough info to create WO | Show `missing_fields` list |
| `not_maintenance` | Email is not a maintenance request | Inform user, discard |

---

### 4.3 Poll Outlook Inbox

```
POST /api/email/poll?max_emails=20
```

Manually trigger an inbox poll. Each unread email is processed:
- If it looks like an approval reply → runs approval pipeline
- Otherwise → creates a new WO

**Response: 200 OK:**

```json
{
  "fetched": 5,
  "created": 3,
  "approved": 1,
  "rejected": 0,
  "missing_info": 1,
  "skipped": 0,
  "errors": 0,
  "work_orders": ["WO-001", "WO-002", "WO-003"]
}
```

**UI notes:** The background poller runs automatically every 60 seconds. This endpoint is for "poll now" buttons or admin testing.

---

### 4.4 Watch a Work Order for Approval (SSE Stream)

```
GET /api/email/watch/{work_order_id}
```

Opens a Server-Sent Events stream. Connect immediately after WO creation. The stream yields events until the manager approves/rejects (or after 10 minutes).

**How to connect in JavaScript:**

```javascript
const es = new EventSource(`${BASE_URL}/api/email/watch/${workOrderId}`);

es.onmessage = (event) => {
  if (event.data === '[DONE]') {
    es.close();
    return;
  }
  const payload = JSON.parse(event.data);
  handleEvent(payload);
};

es.onerror = () => es.close();
```

**Event payload shape:**

```typescript
interface SSEEvent {
  step: string;
  status: 'running' | 'complete' | 'error' | 'warning';
  message: string;
  data?: Record<string, any>;
}
```

**Event sequence (happy path):**

| `step` | `status` | When |
|---|---|---|
| `waiting_approval` | `running` | Initial — waiting for manager |
| `waiting_approval` | `running` | Heartbeat every 10s while pending |
| `waiting_approval` | `complete` | Manager approved |
| `technician_assigned` | `complete` | Technician name resolved |
| `notifications_sent` | `complete` | Emails sent |
| — | — | Stream sends `[DONE]` and closes |

**Rejection path:**

| `step` | `status` | When |
|---|---|---|
| `waiting_approval` | `error` | Manager rejected |

**Timeout path (10 minutes no response):**

| `step` | `status` | When |
|---|---|---|
| `waiting_approval` | `warning` | 10 min elapsed, background poller will handle it |

**UI recipe — progress stepper:**

```javascript
function handleEvent({ step, status, message, data }) {
  switch (step) {
    case 'waiting_approval':
      if (status === 'complete') markStep('approval', 'done');
      if (status === 'error')    markStep('approval', 'rejected');
      if (status === 'running')  updateHeartbeat(message);
      break;
    case 'technician_assigned':
      markStep('assignment', 'done');
      showTechnicianName(data?.technician_name);
      break;
    case 'notifications_sent':
      markStep('notifications', 'done');
      break;
  }
}
```

---

### 4.5 Stream Sample Email Pipeline (test / demo)

```
POST /api/email/process/sample/stream
POST /api/email/process/sample/missing-info/stream
```

These send a hardcoded email to your own Outlook inbox, pick it up, run the full pipeline, and stream each step as SSE events. Same event format as the watch stream above. Use for demo or smoke-testing the full flow without a real inbound email.

**Full event sequence:**

| `step` | Description |
|---|---|
| AI pipeline steps (15 steps) | `asset_identification`, `criticality_assessment`, `compliance_check`, etc. |
| `notification` | Confirmation email sent to requester |
| `approval_request` | Approval email sent to facility manager |
| `waiting_approval` | Waiting for manager reply (same as §4.4) |
| `technician_assigned` | After approval |
| `notifications_sent` | Final notifications sent |

---

## 5. PPM Scheduler

### 5.1 Get Due Schedules

```
GET /api/ppm/due
```

Returns all PPM schedules that are currently due.

**Response: 200 OK** — array of schedule objects from the CMMS.

**Requires:** `AIMMS_API_URL` env var to be set. Returns 500 if CMMS is unreachable.

---

### 5.2 Trigger Scheduler Run

```
POST /api/ppm/run
```

Checks for due schedules and auto-creates a work order for each one.

**Response: 200 OK:**

```json
{
  "created": ["WO-20260504091200", "WO-20260504091201"]
}
```

**UI notes:** This is typically triggered by a cron or a "Run PPM Check" button in admin. The returned WO IDs can be linked to their detail pages.

---

## 6. Journey Logs

Every work order gets a journey log created automatically at WO creation. The journey tracks milestones (stages the WO passes through) and calculates health metrics.

### 6.1 List Journeys

```
GET /api/journeys/?status=active&page=1&limit=20
```

| Param | Type | Description |
|---|---|---|
| `work_order_id` | string | Filter to a specific WO |
| `status` | string | Journey status filter |
| `page` | int | Default 1 |
| `limit` | int | Default 20, max 200 |

**Response: 200 OK** — array of `JourneyResponse`

---

### 6.2 Get Journey by Work Order

```
GET /api/journeys/by-work-order/{work_order_id}
```

The most useful journey endpoint — given a WO ID, get its full journey.

**Response: 200 OK:**

```json
{
  "jlog_id": "jlog-abc123",
  "work_order_id": "WO-20260504094532",
  "status": "active",
  "journey_status": "in_progress",
  "current_step": "preparing",
  "milestones": [
    { "name": "created",          "status": "completed", "timestamp": "2026-05-04T09:45:32Z" },
    { "name": "pending_approval", "status": "completed", "timestamp": "2026-05-04T09:45:32Z" },
    { "name": "preparing",        "status": "current",   "timestamp": "2026-05-04T10:12:00Z" },
    { "name": "prepared",         "status": "pending",   "timestamp": null },
    { "name": "active",           "status": "pending",   "timestamp": null },
    { "name": "completed",        "status": "pending",   "timestamp": null },
    { "name": "closed",           "status": "pending",   "timestamp": null }
  ],
  "assigned_technician_name": "Ahmed Al-Rashidi",
  "expected_timeline": { "duration_hours": 4 },
  "created_at": "2026-05-04T09:45:32Z"
}
```

**Milestone status values:** `pending` · `current` · `completed` · `skipped`

**UI notes:** Use `milestones` to render a visual stepper/timeline component. `current_step` tells you which step is active.

---

### 6.3 Get Journey by ID

```
GET /api/journeys/{jlog_id}
```

---

### 6.4 Journey Health

```
GET /api/journeys/{jlog_id}/health
```

Returns computed health metrics for the journey.

**Response: 200 OK:**

```json
{
  "health_status": "on_track",
  "completion_percentage": 42.8,
  "cost_overrun": false,
  "time_overrun": false
}
```

**`health_status` values:** `on_track` · `in_progress` · `at_risk` · `completed`

Use this to show a coloured health badge next to each WO in the list.

---

### 6.5 Update a Milestone

```
PATCH /api/journeys/{jlog_id}/milestone
```

Manually mark a milestone as complete, current, or skipped.

**Request body:**

```json
{
  "milestone_name": "prepared",
  "status": "completed",
  "notes": "All parts sourced and team briefed"
}
```

**Valid `status` values:** `pending` · `current` · `completed` · `skipped`

**Response: 200 OK** — updated journey log

---

### 6.6 Journey Analytics

```
GET /api/journeys/analytics/summary
```

Aggregate stats across all journeys. Use for a management overview / KPI panel.

**Response: 200 OK:**

```json
{
  "total_journeys": 74,
  "completed": 57,
  "active": 12,
  "in_progress_journeys": 12,
  "failed_journeys": 0,
  "completion_rate": 0.770,
  "avg_completion_hours": 6.4,
  "milestone_completion_rates": {
    "created": 1.0,
    "pending_approval": 0.95,
    "preparing": 0.85,
    "prepared": 0.80,
    "active": 0.78,
    "completed": 0.77,
    "closed": 0.77
  }
}
```

---

## 7. Assets & Locations

Used to populate dropdowns when creating or filtering work orders.

### 7.1 List Assets

```
GET /api/assets?q=HVAC&page=1&limit=50
```

| Param | Type | Description |
|---|---|---|
| `q` | string | Case-insensitive name substring search |
| `page` | int | Default 1 |
| `limit` | int | Default 50, max 200 |

**Response: 200 OK:**

```json
[
  { "asset_id": "uuid", "asset_name": "HVAC-301", "asset_code": "MOB-AHU-301", "category": "..." },
  { "asset_id": "uuid", "asset_name": "HVAC-302", "asset_code": "MOB-AHU-302", "category": "..." }
]
```

---

### 7.2 Get Asset by ID

```
GET /api/assets/{asset_id}
```

`asset_id` must be a valid UUID. Returns 404 if not found or if the string is not a UUID.

---

### 7.3 List Locations

```
GET /api/locations?q=Building+A&limit=100
```

| Param | Type | Description |
|---|---|---|
| `q` | string | Case-insensitive name substring search |
| `page` | int | Default 1 |
| `limit` | int | Default 100, max 500 |

**Response: 200 OK:**

```json
[
  { "location_id": "uuid", "name": "Building A - Roof Level" },
  { "location_id": "uuid", "name": "Building A - Ground Floor" }
]
```

**UI notes:** Load these on page mount and cache them for dropdown use. The list is small (< 500 records) and rarely changes.

---

## 8. Dashboard

### 8.1 Dashboard Statistics

```
GET /api/dashboard/stats
```

Returns aggregate counts for a KPI / overview panel.

**Response: 200 OK:**

```json
{
  "total": 74,
  "by_status": {
    "pending_approval": 5,
    "preparing": 3,
    "prepared": 2,
    "active": 7,
    "completed": 12,
    "closed": 45
  },
  "by_priority": {
    "low": 10,
    "medium": 30,
    "high": 20,
    "urgent": 8,
    "critical": 6
  },
  "by_source": {
    "email": 40,
    "manual": 20,
    "ppm": 10,
    "tenant": 4
  },
  "created_today": 3,
  "assets_by_category": {
    "Air Handler": 12,
    "Chiller": 6,
    "Generator": 3
  }
}
```

**UI recipe:** Use `by_status` for a status donut chart, `by_priority` for a priority bar chart, `created_today` for the top badge.

---

## 9. Error Format

All 4xx and 5xx responses return this envelope:

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "field must not be blank",
      "field": "asset"
    }
  ]
}
```

| `code` | HTTP | When |
|---|---|---|
| `validation_error` | 422 | Request body field failed validation |
| `work_order_not_found` | 404 | WO ID doesn't exist |
| `journey_not_found` | 404 | Journey ID doesn't exist |
| `asset_not_found` | 404 | Asset UUID doesn't exist |
| `invalid_status_transition` | 422 | Status transition not allowed by state machine |
| `work_order_already_closed` | 409 | Trying to close an already-closed WO |
| `approval_not_pending` | 409 | Trying to approve a WO that isn't pending |
| `approval_request_not_found` | 404 | Approval request ID invalid |
| `approval_already_processed` | 409 | Approval already actioned |
| `outlook_not_configured` | 503 | `OUTLOOK_ACCESS_TOKEN` env var not set |
| `outlook_not_connected` | 503 | Outlook token invalid or expired |
| `database_error` | 500 | DB query failed |
| `internal_error` | 500 | Unexpected server error |

**Handling in frontend:**

```javascript
async function callApi(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const err = await res.json();
    // err.errors[0].code — machine-readable
    // err.errors[0].message — human-readable
    // err.errors[0].field — which input field caused it (for 422s)
    throw err;
  }
  return res.json();
}
```

---

## 10. UI Flow Recipes

### Recipe A — Work Order List Page

```
1. GET /api/dashboard/stats          → populate KPI cards
2. GET /api/work-orders/?page=1      → populate table
3. GET /api/work-orders/filter/pending-approval → badge count for notifications icon
```

Polling: refresh the list every 30s or on user action.

---

### Recipe B — Create Work Order Form

```
1. GET /api/locations               → populate location dropdown
2. GET /api/assets?q={search}       → populate asset autocomplete (debounce 300ms)
3. POST /api/work-orders/           → submit form
4. On success: open SSE stream → GET /api/email/watch/{work_order_id}
5. Show a live stepper while waiting for approval
```

---

### Recipe C — Work Order Detail Page

```
1. GET /api/work-orders/{id}                  → WO data
2. GET /api/journeys/by-work-order/{id}       → journey + milestones for stepper
3. GET /api/journeys/{jlog_id}/health         → health badge
4. GET /api/work-orders/{id}/history          → audit trail timeline
```

---

### Recipe D — Approve / Reject from UI (admin panel)

```
1. GET /api/work-orders/filter/pending-approval   → list of WOs needing approval
2. POST /api/work-orders/{id}/approve             → approve
   or
   POST /api/work-orders/{id}/status  body: { "new_status": "closed" }  → reject/close
```

---

### Recipe E — Journey Timeline Component

Use data from `GET /api/journeys/by-work-order/{id}`:

```javascript
// milestones array drives a horizontal stepper
milestones.map(m => ({
  label: m.name.replace('_', ' '),
  state: m.status,        // 'completed' | 'current' | 'pending' | 'skipped'
  timestamp: m.timestamp, // show on hover
}))
```

---

### Recipe F — Dashboard KPI Panel

```
GET /api/dashboard/stats

→ Total WOs card: stats.total
→ Open WOs card: stats.by_status.pending_approval + preparing + prepared + active
→ Created today card: stats.created_today
→ Status donut chart: stats.by_status
→ Priority bar chart: stats.by_priority
→ Source breakdown: stats.by_source
```

---

### Recipe G — Real-time Email Demo Flow

```javascript
// 1. POST /api/email/process/sample/stream
const es = new EventSource(`${BASE_URL}/api/email/process/sample/stream`);

es.onmessage = ({ data }) => {
  if (data === '[DONE]') { es.close(); return; }
  const { step, status, message } = JSON.parse(data);
  appendLogLine(`[${step}] ${status}: ${message}`);
};
```

Each event maps to a pipeline step you can display as a progress list.

---

## Health Check

```
GET /health
```

---

## 11. Chat Interface (GPT Agent)

The chat interface replaces the rigid 15-step pipeline with a GPT-powered conversational agent.
The same orchestrator handles all three entry points — the LLM decides which intelligence tools
to call (criticality, safety, scheduling, vendors, etc.) based on context.

---

### POST /api/chat/

Start or continue a conversation. Omit `session_id` to start a new session.

**Request**
```typescript
interface ChatRequest {
  message: string;
  session_id?: string | null;   // omit on first message
}
```

**Response**
```typescript
interface ChatResponse {
  session_id: string;           // persist this and send on subsequent turns
  reply: string;                // agent's text reply
  work_order: WorkOrderCreated | null;  // populated when a WO is created
}

interface WorkOrderCreated {
  success: boolean;
  work_order_id: string;
  status: string;
  priority: string;
  source: string;
  asset: string | null;
  location: string | null;
  issue_description: string;
  vendor: string | null;
  scheduled_date: string | null;
  scheduled_time: string | null;
  message: string;
}
```

**Example — first turn**
```javascript
const res = await fetch(`${BASE_URL}/api/chat/`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: 'The AC in Meeting Room 3 is making a loud noise.' })
})
const { session_id, reply, work_order } = await res.json()
// session_id → store locally, send on next turn
// work_order → non-null when agent has confirmed and created the WO
```

**Example — subsequent turn**
```javascript
const res = await fetch(`${BASE_URL}/api/chat/`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: 'Yes, go ahead and create it.', session_id })
})
```

---

### POST /api/chat/email

Ingest a parsed email payload directly into the agent. The agent extracts the issue, looks up
the asset, and either creates a WO automatically or asks clarifying questions.

**Request**
```typescript
interface EmailIngestRequest {
  subject?: string;
  body?: string;
  sender_name?: string;
  sender_email?: string;
  asset?: string;
  location?: string;
}
```

**Response** — same `ChatResponse` shape as above.

---

### POST /api/chat/ppm

Trigger a PPM work order via the agent. Called by the scheduler when a maintenance schedule
is due. The agent runs all intelligence tools and creates the WO with minimal conversation.

**Request**
```typescript
interface PPMTriggerRequest {
  schedule_id: string;
  asset_id: string;
  asset_name: string;
  description: string;
  maintenance_type?: string;
  next_due_date?: string;
  frequency?: string;
}
```

**Response** — same `ChatResponse` shape as above.

---

### GET /api/chat/{session_id}/history

Returns the full conversation history for a session (system prompt excluded).

**Response**
```typescript
interface HistoryResponse {
  session_id: string;
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  count: number;
}
```

---

### Recipe H — Chat UI integration

```typescript
// State
let sessionId: string | null = null;
const messages: Array<{ role: string; content: string; work_order?: object }> = [];

async function sendMessage(text: string) {
  const res = await fetch(`${BASE_URL}/api/chat/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, session_id: sessionId }),
  });
  const data = await res.json();

  sessionId = data.session_id;                // persist across turns
  messages.push({ role: 'user', content: text });
  messages.push({ role: 'assistant', content: data.reply, work_order: data.work_order });

  if (data.work_order) {
    // Show WO summary card — work_order.work_order_id, .priority, .status, etc.
    renderWorkOrderCard(data.work_order);
  }
  renderMessages(messages);
}
```

**Session lifecycle:**
- `session_id` is a UUID stored in `plenum_cafm.wo_chat_sessions`
- Sessions survive server restarts (DB-backed JSONB message history)
- Omit `session_id` (or send a non-existent one) to start a fresh conversation
- Call `GET /api/chat/{session_id}/history` to restore history on page reload

Returns `{ "status": "ok", "service": "svc-work-order-management" }` with HTTP 200 when the service is up. Use this for Azure health probe configuration.
