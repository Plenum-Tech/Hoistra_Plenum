# SVC-WORK-ORDER-MANAGEMENT — Full Test Report

| | |
|---|---|
| **Run at** | 07:06:36 UTC |
| **Base URL** | `http://localhost:8007` |
| **Total** | 61 |
| **Passed** | 55 ✅ |
| **Failed** | 6 ❌ |

---

## Summary of Failures
| Test | Result | Root Cause |
|---|---|---|
| **List assets** | HTTP 307 (expected 200) | httpx does not follow redirects by default; FastAPI redirects `/api/assets/` → `/api/assets` (no trailing slash). Endpoint works correctly when accessed without the trailing slash. |
| **List assets search=AHU** | HTTP 307 (expected 200) | httpx does not follow redirects by default; FastAPI redirects `/api/assets/` → `/api/assets` (no trailing slash). Endpoint works correctly when accessed without the trailing slash. |
| **List assets paginated (limit=5)** | HTTP 307 (expected 200) | httpx does not follow redirects by default; FastAPI redirects `/api/assets/` → `/api/assets` (no trailing slash). Endpoint works correctly when accessed without the trailing slash. |
| **List locations** | HTTP 307 (expected 200) | httpx does not follow redirects by default; FastAPI redirects `/api/assets/` → `/api/assets` (no trailing slash). Endpoint works correctly when accessed without the trailing slash. |
| **List locations search=Tower** | HTTP 307 (expected 200) | httpx does not follow redirects by default; FastAPI redirects `/api/assets/` → `/api/assets` (no trailing slash). Endpoint works correctly when accessed without the trailing slash. |
| **PPM list schedules** | HTTP 404 (expected 200) | Endpoint `/api/ppm/schedules` is not implemented in the PPM router; only `/api/ppm/due` exists. |

---

## Detailed Test Results

## Section 1. Health & Outlook

### ✅ PASS — Health check

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/health` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "status": "ok",
  "service": "svc-work-order-management"
}
```

### ✅ PASS — Outlook connection status

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/email/status` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "connected": true,
  "display_name": "Shashank Kanangi",
  "email": "shashank@plenum-tech.com"
}
```

## Section 2. Work Order Create

### ✅ PASS — Create WO (manual, high)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `201` |
| Expected Status | `201` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Create WO (urgent, email source)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `201` |
| Expected Status | `201` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "email",
  "asset": "LIFT-002",
  "location": "Podium Level 1",
  "issue_description": "Lift stuck between floors, occupant inside",
  "priority": "urgent",
  "request_type": "repair",
  "requester_name": "Security Desk",
  "requester_email": "security@building.com",
  "requester_phone": null
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705339033",
  "source": "email",
  "status": "pending_approval",
  "priority": "urgent",
  "asset": "LIFT-002",
  "location": "Podium Level 1",
  "issue_description": "Lift stuck between floors, occupant inside",
  "request_type": "repair",
  "requester_name": "Security Desk",
  "requester_email": "security@building.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705339275",
  "created_at": "2026-04-30T07:05:33.985759",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Create WO (critical, ppm)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `201` |
| Expected Status | `201` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "ppm",
  "asset": "GEN-001",
  "location": "Basement Plant Room",
  "issue_description": "Emergency generator quarterly maintenance overdue",
  "priority": "critical",
  "request_type": "maintenance",
  "requester_name": "PPM System",
  "requester_email": "ppm@plenum-tech.com",
  "requester_phone": null
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705342373",
  "source": "ppm",
  "status": "pending_approval",
  "priority": "critical",
  "asset": "GEN-001",
  "location": "Basement Plant Room",
  "issue_description": "Emergency generator quarterly maintenance overdue",
  "request_type": "maintenance",
  "requester_name": "PPM System",
  "requester_email": "ppm@plenum-tech.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705342713",
  "created_at": "2026-04-30T07:05:34.320216",
  "approved_at": null,
  "prepared_at": null
}
```

## Section 3. Validation Errors

### ✅ PASS — Missing asset -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Field required",
      "field": "asset"
    }
  ]
}
```

### ✅ PASS — Blank asset -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "   ",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Value error, field must not be blank",
      "field": "asset"
    }
  ]
}
```

### ✅ PASS — Invalid priority -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "extreme",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Input should be 'low', 'medium', 'high', 'urgent' or 'critical'",
      "field": "priority"
    }
  ]
}
```

### ✅ PASS — Invalid source -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "fax",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Input should be 'email', 'ppm', 'manual', 'tenant', 'internal' or 'remediation'",
      "field": "source"
    }
  ]
}
```

### ✅ PASS — Bad email -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "not-an-email",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "value is not a valid email address: An email address must have an @-sign.",
      "field": "requester_email"
    }
  ]
}
```

### ✅ PASS — Invalid request_type -> 422

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "priority": "high",
  "request_type": "demolition",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Input should be 'repair', 'maintenance', 'inspection' or 'installation'",
      "field": "request_type"
    }
  ]
}
```

## Section 4. Get & List

### ✅ PASS — List all WOs

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705342373",
    "source": "ppm",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705342713",
    "created_at": "2026-04-30T07:05:34.320216",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705339033",
    "source": "email",
    "status": "pending_approval",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "request_type": "repair",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705339275",
    "created_at": "2026-04-30T07:05:33.985759",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300700378053",
    "source": "email",
    "status": "closed",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": "HVAC Pro LLC",
    "scheduled_date": "2026-05-02",
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300700378524",
    "created_at": "2026-04-30T07:00:37.894128",
    "approved_at": "2026-04-30T07:00:48.453885Z",
    "prepared_at": "2026-04-30T07:00:48.687708Z"
  },
  {
    "work_order_id": "WO-202604300645229918",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300645230389",
    "created_at": "2026-04-30T06:45:23.080514",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841241006",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC Unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841241321",
    "created_at": "2026-04-29T18:41:24.475468",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841000082",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841000420",
    "created_at": "2026-04-29T18:41:00.383737",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840380366",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840380657",
    "created_at": "2026-04-29T18:40:38.409829",
    "approved_at": "2026-04-29T18:40:38.288350Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840379111",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840379400",
    "created_at": "2026-04-29T18:40:38.285136",
    "approved_at": "2026-04-29T18:40:38.191873Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840361565",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840362060",
    "created_at": "2026-04-29T18:40:36.532091",
    "approved_at": "2026-04-29T18:40:36.814819Z",
    "prepared_at": "2026-04-29T18:40:37.032021Z"
  },
  {
    "work_order_id": "WO-202604291816380093",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "AC unit",
    "location": "Tower A, Floor 3, Server Room",
    "issue_description": "AC unit has stopped cooling, making a loud noise and temperature is rising",
    "request_type": "repair",
    "requester_name": "Bala",
    "requester_email": "shashank@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291816380686",
    "created_at": "2026-04-29T18:16:38.382351",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291752122814",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291752123078",
    "created_at": "2026-04-29T17:52:12.620958",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751516221",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751516593",
    "created_at": "2026-04-29T17:51:51.963607",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751347532",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751347867",
    "created_at": "2026-04-29T17:51:35.091117",
    "approved_at": "2026-04-29T17:51:35.015203Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751346219",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751346490",
    "created_at": "2026-04-29T17:51:34.958342",
    "approved_at": "2026-04-29T17:51:34.935341Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751328181",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751328630",
    "created_at": "2026-04-29T17:51:33.157080",
    "approved_at": "2026-04-29T17:51:33.493871Z",
    "prepared_at": "2026-04-29T17:51:33.708025Z"
  },
  {
    "work_order_id": "WO-202604291749465476",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working since this morning. It makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749465758",
    "created_at": "2026-04-29T17:49:46.883314",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749261869",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749262126",
    "created_at": "2026-04-29T17:49:26.522532",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749033237",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749033489",
    "created_at": "2026-04-29T17:49:03.659837",
    "approved_at": "2026-04-29T17:49:03.541149Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749032102",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749032370",
    "created_at": "2026-04-29T17:49:03.545678",
    "approved_at": "2026-04-29T17:49:03.467068Z",
    "prepared_at": null
  }
]
```

### ✅ PASS — Get WO by ID

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/WO-202604300705336202` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Get unknown WO -> 404

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/WO-DOES-NOT-EXIST` |
| HTTP Status | `404` |
| Expected Status | `404` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "work_order_not_found",
      "message": "Work order 'WO-DOES-NOT-EXIST' not found",
      "field": null
    }
  ]
}
```

### ✅ PASS — Filter by status=pending_approval

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/?status=pending_approval` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705342373",
    "source": "ppm",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705342713",
    "created_at": "2026-04-30T07:05:34.320216",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705339033",
    "source": "email",
    "status": "pending_approval",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "request_type": "repair",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705339275",
    "created_at": "2026-04-30T07:05:33.985759",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300645229918",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300645230389",
    "created_at": "2026-04-30T06:45:23.080514",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841241006",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC Unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841241321",
    "created_at": "2026-04-29T18:41:24.475468",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841000082",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841000420",
    "created_at": "2026-04-29T18:41:00.383737",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291816380093",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "AC unit",
    "location": "Tower A, Floor 3, Server Room",
    "issue_description": "AC unit has stopped cooling, making a loud noise and temperature is rising",
    "request_type": "repair",
    "requester_name": "Bala",
    "requester_email": "shashank@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291816380686",
    "created_at": "2026-04-29T18:16:38.382351",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291752122814",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291752123078",
    "created_at": "2026-04-29T17:52:12.620958",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751516221",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751516593",
    "created_at": "2026-04-29T17:51:51.963607",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749465476",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working since this morning. It makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749465758",
    "created_at": "2026-04-29T17:49:46.883314",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749261869",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749262126",
    "created_at": "2026-04-29T17:49:26.522532",
    "approved_at": null,
    "prepared_at": null
  }
]
```

### ✅ PASS — Filter by priority=high

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/?priority=high` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840380366",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840380657",
    "created_at": "2026-04-29T18:40:38.409829",
    "approved_at": "2026-04-29T18:40:38.288350Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840379111",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840379400",
    "created_at": "2026-04-29T18:40:38.285136",
    "approved_at": "2026-04-29T18:40:38.191873Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840361565",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840362060",
    "created_at": "2026-04-29T18:40:36.532091",
    "approved_at": "2026-04-29T18:40:36.814819Z",
    "prepared_at": "2026-04-29T18:40:37.032021Z"
  },
  {
    "work_order_id": "WO-202604291751347532",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751347867",
    "created_at": "2026-04-29T17:51:35.091117",
    "approved_at": "2026-04-29T17:51:35.015203Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751346219",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751346490",
    "created_at": "2026-04-29T17:51:34.958342",
    "approved_at": "2026-04-29T17:51:34.935341Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751328181",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751328630",
    "created_at": "2026-04-29T17:51:33.157080",
    "approved_at": "2026-04-29T17:51:33.493871Z",
    "prepared_at": "2026-04-29T17:51:33.708025Z"
  },
  {
    "work_order_id": "WO-202604291749033237",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749033489",
    "created_at": "2026-04-29T17:49:03.659837",
    "approved_at": "2026-04-29T17:49:03.541149Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749032102",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749032370",
    "created_at": "2026-04-29T17:49:03.545678",
    "approved_at": "2026-04-29T17:49:03.467068Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749015763",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749016170",
    "created_at": "2026-04-29T17:49:01.912792",
    "approved_at": "2026-04-29T17:49:02.187575Z",
    "prepared_at": "2026-04-29T17:49:02.369352Z"
  },
  {
    "work_order_id": "WO-202604291008061716",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291008061983",
    "created_at": "2026-04-29T10:08:06.352775",
    "approved_at": "2026-04-29T10:08:06.394215Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291008060563",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291008060839",
    "created_at": "2026-04-29T10:08:06.237051",
    "approved_at": "2026-04-29T10:08:06.314081Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291008044339",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291008044865",
    "created_at": "2026-04-29T10:08:04.617768",
    "approved_at": "2026-04-29T10:08:05.038920Z",
    "prepared_at": "2026-04-29T10:08:05.245182Z"
  },
  {
    "work_order_id": "WO-202604291007124532",
    "source": "manual",
    "status": "prepared",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Carrier UAE",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291007124974",
    "created_at": "2026-04-29T10:07:12.634866",
    "approved_at": "2026-04-29T10:07:13.069591Z",
    "prepared_at": "2026-04-29T10:07:13.286583Z"
  }
]
```

### ✅ PASS — Filter by source=email

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/?source=email` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705342373",
    "source": "ppm",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705342713",
    "created_at": "2026-04-30T07:05:34.320216",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705339033",
    "source": "email",
    "status": "pending_approval",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "request_type": "repair",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705339275",
    "created_at": "2026-04-30T07:05:33.985759",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300700378053",
    "source": "email",
    "status": "closed",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": "HVAC Pro LLC",
    "scheduled_date": "2026-05-02",
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300700378524",
    "created_at": "2026-04-30T07:00:37.894128",
    "approved_at": "2026-04-30T07:00:48.453885Z",
    "prepared_at": "2026-04-30T07:00:48.687708Z"
  },
  {
    "work_order_id": "WO-202604300645229918",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300645230389",
    "created_at": "2026-04-30T06:45:23.080514",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841241006",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC Unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841241321",
    "created_at": "2026-04-29T18:41:24.475468",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841000082",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841000420",
    "created_at": "2026-04-29T18:41:00.383737",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840380366",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840380657",
    "created_at": "2026-04-29T18:40:38.409829",
    "approved_at": "2026-04-29T18:40:38.288350Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840379111",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840379400",
    "created_at": "2026-04-29T18:40:38.285136",
    "approved_at": "2026-04-29T18:40:38.191873Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291840361565",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291840362060",
    "created_at": "2026-04-29T18:40:36.532091",
    "approved_at": "2026-04-29T18:40:36.814819Z",
    "prepared_at": "2026-04-29T18:40:37.032021Z"
  },
  {
    "work_order_id": "WO-202604291816380093",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "AC unit",
    "location": "Tower A, Floor 3, Server Room",
    "issue_description": "AC unit has stopped cooling, making a loud noise and temperature is rising",
    "request_type": "repair",
    "requester_name": "Bala",
    "requester_email": "shashank@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291816380686",
    "created_at": "2026-04-29T18:16:38.382351",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291752122814",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291752123078",
    "created_at": "2026-04-29T17:52:12.620958",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751516221",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751516593",
    "created_at": "2026-04-29T17:51:51.963607",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751347532",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751347867",
    "created_at": "2026-04-29T17:51:35.091117",
    "approved_at": "2026-04-29T17:51:35.015203Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751346219",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751346490",
    "created_at": "2026-04-29T17:51:34.958342",
    "approved_at": "2026-04-29T17:51:34.935341Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751328181",
    "source": "manual",
    "status": "closed",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection \u2014 compressor noise",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": "Updated Vendor",
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751328630",
    "created_at": "2026-04-29T17:51:33.157080",
    "approved_at": "2026-04-29T17:51:33.493871Z",
    "prepared_at": "2026-04-29T17:51:33.708025Z"
  },
  {
    "work_order_id": "WO-202604291749465476",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working since this morning. It makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749465758",
    "created_at": "2026-04-29T17:49:46.883314",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749261869",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749262126",
    "created_at": "2026-04-29T17:49:26.522532",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749033237",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 2",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749033489",
    "created_at": "2026-04-29T17:49:03.659837",
    "approved_at": "2026-04-29T17:49:03.541149Z",
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749032102",
    "source": "manual",
    "status": "preparing",
    "priority": "high",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Bulk test WO 1",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749032370",
    "created_at": "2026-04-29T17:49:03.545678",
    "approved_at": "2026-04-29T17:49:03.467068Z",
    "prepared_at": null
  }
]
```

### ✅ PASS — Filter active WOs endpoint

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/filter/active` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[]
```

### ✅ PASS — Filter pending-approval endpoint

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/filter/pending-approval` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705342373",
    "source": "ppm",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705342713",
    "created_at": "2026-04-30T07:05:34.320216",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705339033",
    "source": "email",
    "status": "pending_approval",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "request_type": "repair",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705339275",
    "created_at": "2026-04-30T07:05:33.985759",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300645229918",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300645230389",
    "created_at": "2026-04-30T06:45:23.080514",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841241006",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC Unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841241321",
    "created_at": "2026-04-29T18:41:24.475468",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291841000082",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291841000420",
    "created_at": "2026-04-29T18:41:00.383737",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291816380093",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "AC unit",
    "location": "Tower A, Floor 3, Server Room",
    "issue_description": "AC unit has stopped cooling, making a loud noise and temperature is rising",
    "request_type": "repair",
    "requester_name": "Bala",
    "requester_email": "shashank@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291816380686",
    "created_at": "2026-04-29T18:16:38.382351",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291752122814",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291752123078",
    "created_at": "2026-04-29T17:52:12.620958",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291751516221",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291751516593",
    "created_at": "2026-04-29T17:51:51.963607",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749465476",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has stopped working since this morning. It makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749465758",
    "created_at": "2026-04-29T17:49:46.883314",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604291749261869",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604291749262126",
    "created_at": "2026-04-29T17:49:26.522532",
    "approved_at": null,
    "prepared_at": null
  }
]
```

### ✅ PASS — List paginated (page=1, limit=5)

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/?page=1&limit=5` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "work_order_id": "WO-202604300705342373",
    "source": "ppm",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705342713",
    "created_at": "2026-04-30T07:05:34.320216",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705339033",
    "source": "email",
    "status": "pending_approval",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "request_type": "repair",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705339275",
    "created_at": "2026-04-30T07:05:33.985759",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300705336202",
    "source": "manual",
    "status": "pending_approval",
    "priority": "high",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300705336473",
    "created_at": "2026-04-30T07:05:33.703569",
    "approved_at": null,
    "prepared_at": null
  },
  {
    "work_order_id": "WO-202604300700378053",
    "source": "email",
    "status": "closed",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": "HVAC Pro LLC",
    "scheduled_date": "2026-05-02",
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300700378524",
    "created_at": "2026-04-30T07:00:37.894128",
    "approved_at": "2026-04-30T07:00:48.453885Z",
    "prepared_at": "2026-04-30T07:00:48.687708Z"
  },
  {
    "work_order_id": "WO-202604300645229918",
    "source": "email",
    "status": "pending_approval",
    "priority": "critical",
    "asset": "HVAC unit",
    "location": "Tower B, Floor 2, Room 4B",
    "issue_description": "The HVAC unit has completely stopped working and makes a loud rattling sound before shutting off.",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "james.carter@tenantcorp.com",
    "vendor": null,
    "scheduled_date": null,
    "scheduled_time": null,
    "cmms_work_order_id": null,
    "journey_log_id": "JL-202604300645230389",
    "created_at": "2026-04-30T06:45:23.080514",
    "approved_at": null,
    "prepared_at": null
  }
]
```

## Section 5. Work Order Update

### ✅ PASS — Patch vendor + scheduled_date

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705336202` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "vendor": "Acme HVAC Services",
  "scheduled_date": "2026-05-10"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "Acme HVAC Services",
  "scheduled_date": "2026-05-10",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Patch estimated_duration

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705336202` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "estimated_duration": 3.5
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "Acme HVAC Services",
  "scheduled_date": "2026-05-10",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Negative duration -> 422

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705336202` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "estimated_duration": -5.0
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Value error, estimated_duration must be positive",
      "field": "estimated_duration"
    }
  ]
}
```

## Section 6. Approve

### ✅ PASS — Approve WO1 -> preparing

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705336202/approve` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "preparing",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "Acme HVAC Services",
  "scheduled_date": "2026-05-10",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": "2026-04-30T07:05:38.630614Z",
  "prepared_at": null
}
```

### ✅ PASS — Double approve -> 409

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705336202/approve` |
| HTTP Status | `409` |
| Expected Status | `409` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "approval_not_pending",
      "message": "Work order 'WO-202604300705336202' cannot be approved \u2014 current status is 'preparing'",
      "field": null
    }
  ]
}
```

### ✅ PASS — Approve unknown ID -> 404

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-GHOST/approve` |
| HTTP Status | `404` |
| Expected Status | `404` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "work_order_not_found",
      "message": "Work order 'WO-GHOST' not found",
      "field": null
    }
  ]
}
```

## Section 7. Prepare

### ✅ PASS — Prepare WO1 (vendor + date)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705336202/prepare` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "vendor": "HVAC Pro LLC",
  "scheduled_date": "2026-05-02",
  "estimated_duration": 2.0
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "prepared",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "HVAC Pro LLC",
  "scheduled_date": "2026-05-02",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": "2026-04-30T07:05:38.630614Z",
  "prepared_at": "2026-04-30T07:05:39.389606Z"
}
```

## Section 8. Status Transitions

### ✅ PASS — WO1: prepared -> active

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705336202/status` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "new_status": "active",
  "notes": "Technician dispatched"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "active",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "HVAC Pro LLC",
  "scheduled_date": "2026-05-02",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": "2026-04-30T07:05:38.630614Z",
  "prepared_at": "2026-04-30T07:05:39.389606Z"
}
```

### ✅ PASS — WO1: active -> completed

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705336202/status` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "new_status": "completed",
  "notes": "Compressor bearing replaced"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "completed",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "HVAC Pro LLC",
  "scheduled_date": "2026-05-02",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": "2026-04-30T07:05:38.630614Z",
  "prepared_at": "2026-04-30T07:05:39.389606Z"
}
```

### ✅ PASS — WO1: close endpoint

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705336202/close` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "work_order_id": "WO-202604300705336202",
  "source": "manual",
  "status": "closed",
  "priority": "high",
  "asset": "AHU-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Compressor making unusual noise and vibrating heavily",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": "HVAC Pro LLC",
  "scheduled_date": "2026-05-02",
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705336473",
  "created_at": "2026-04-30T07:05:33.703569",
  "approved_at": "2026-04-30T07:05:38.630614Z",
  "prepared_at": "2026-04-30T07:05:39.389606Z"
}
```

### ✅ PASS — Close already closed -> 409

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705336202/close` |
| HTTP Status | `409` |
| Expected Status | `409` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "work_order_closed",
      "message": "Work order 'WO-202604300705336202' is already closed",
      "field": null
    }
  ]
}
```

### ✅ PASS — Skip pending->active (invalid) -> 422

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705342373/status` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "new_status": "active"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "invalid_status_transition",
      "message": "Cannot transition 'pending_approval' \u2192 'active'. Allowed: ['preparing', 'closed']",
      "field": "new_status"
    }
  ]
}
```

### ✅ PASS — Invalid status value -> 422

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/WO-202604300705342373/status` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "new_status": "flying"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "validation_error",
      "message": "Input should be 'pending_approval', 'preparing', 'prepared', 'active', 'completed' or 'closed'",
      "field": "new_status"
    }
  ]
}
```

## Section 9. Close from Pending

### ✅ PASS — Close WO3 from pending_approval

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/WO-202604300705342373/close` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "work_order_id": "WO-202604300705342373",
  "source": "ppm",
  "status": "closed",
  "priority": "critical",
  "asset": "GEN-001",
  "location": "Basement Plant Room",
  "issue_description": "Emergency generator quarterly maintenance overdue",
  "request_type": "maintenance",
  "requester_name": "PPM System",
  "requester_email": "ppm@plenum-tech.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705342713",
  "created_at": "2026-04-30T07:05:34.320216",
  "approved_at": null,
  "prepared_at": null
}
```

## Section 10. Status History

### ✅ PASS — WO1 status history

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/WO-202604300705336202/history` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "history_id": "SH-202604300705336475",
    "work_order_id": "WO-202604300705336202",
    "from_status": null,
    "to_status": "pending_approval",
    "changed_by": "system",
    "notes": null,
    "changed_at": "2026-04-30T07:05:33.647595Z"
  },
  {
    "history_id": "SH-202604300705386306",
    "work_order_id": "WO-202604300705336202",
    "from_status": "pending_approval",
    "to_status": "preparing",
    "changed_by": "system",
    "notes": null,
    "changed_at": "2026-04-30T07:05:38.630635Z"
  },
  {
    "history_id": "SH-202604300705393896",
    "work_order_id": "WO-202604300705336202",
    "from_status": "preparing",
    "to_status": "prepared",
    "changed_by": "system",
    "notes": null,
    "changed_at": "2026-04-30T07:05:39.389626Z"
  },
  {
    "history_id": "SH-202604300705397067",
    "work_order_id": "WO-202604300705336202",
    "from_status": "prepared",
    "to_status": "active",
    "changed_by": "system",
    "notes": "Technician dispatched",
    "changed_at": "2026-04-30T07:05:39.706765Z"
  },
  {
    "history_id": "SH-202604300705400264",
    "work_order_id": "WO-202604300705336202",
    "from_status": "active",
    "to_status": "completed",
    "changed_by": "system",
    "notes": "Compressor bearing replaced",
    "changed_at": "2026-04-30T07:05:40.026424Z"
  },
  {
    "history_id": "SH-202604300705403954",
    "work_order_id": "WO-202604300705336202",
    "from_status": "completed",
    "to_status": "closed",
    "changed_by": "system",
    "notes": null,
    "changed_at": "2026-04-30T07:05:40.395469Z"
  }
]
```

### ✅ PASS — Unknown WO history -> 404

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/work-orders/WO-GHOST/history` |
| HTTP Status | `404` |
| Expected Status | `404` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "work_order_not_found",
      "message": "Work order 'WO-GHOST' not found",
      "field": null
    }
  ]
}
```

## Section 11. Bulk Status Update

### ✅ PASS — Create WO bulk-A

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `201` |
| Expected Status | `201` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "PUMP-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Pump vibrating",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705421616",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "PUMP-001",
  "location": "Level 3 Plant Room",
  "issue_description": "Pump vibrating",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705421868",
  "created_at": "2026-04-30T07:05:42.245207",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Create WO bulk-B

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/work-orders/` |
| HTTP Status | `201` |
| Expected Status | `201` |
| Result | PASS |

**Request Payload**

```json
{
  "source": "manual",
  "asset": "PUMP-002",
  "location": "Level 3 Plant Room",
  "issue_description": "Pump leaking",
  "priority": "high",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "requester_phone": "+971501234567"
}
```

**Response Body**

```json
{
  "work_order_id": "WO-202604300705424661",
  "source": "manual",
  "status": "pending_approval",
  "priority": "high",
  "asset": "PUMP-002",
  "location": "Level 3 Plant Room",
  "issue_description": "Pump leaking",
  "request_type": "repair",
  "requester_name": "James Carter",
  "requester_email": "james.carter@tenantcorp.com",
  "vendor": null,
  "scheduled_date": null,
  "scheduled_time": null,
  "cmms_work_order_id": null,
  "journey_log_id": "JL-202604300705424913",
  "created_at": "2026-04-30T07:05:42.549708",
  "approved_at": null,
  "prepared_at": null
}
```

### ✅ PASS — Bulk close 2 WOs

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/work-orders/bulk/status` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "work_order_ids": [
    "WO-202604300705421616",
    "WO-202604300705424661"
  ],
  "new_status": "closed",
  "notes": "Bulk closed in test"
}
```

**Response Body**

```json
{
  "updated": 2,
  "failed": 0,
  "succeeded_ids": [
    "WO-202604300705421616",
    "WO-202604300705424661"
  ],
  "failed_details": []
}
```

### ✅ PASS — Process hardcoded sample email (AI pipeline)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/email/process/sample` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "status": "created",
  "work_order_id": "WO-202604300706100756",
  "journey_log_id": "JL-202604300706101033",
  "priority": "critical",
  "assessment_summary": {
    "criticality_level": "critical",
    "response_time_hours": 4,
    "safety_score": 0,
    "critical_safety": false,
    "ppe_required": [],
    "compliance_required": false,
    "suggested_timeframe": "same_day",
    "estimated_duration_hrs": 2,
    "required_skills": [
      "HVAC repair",
      "electrical systems"
    ],
    "sla_deadline_hours": 4,
    "parts_needed": 2,
    "vendor_type": "specialist"
  },
  "full_assessment": {
    "criticality": {
      "level": "critical",
      "safety_score": 0,
      "operational_score": 5,
      "financial_score": 3,
      "compliance_score": 0,
      "overall_score": 8,
      "response_time_hours": 4,
      "reasoning": "The HVAC unit's failure affects temperature control and could lead to operational disruptions."
    },
    "safety": {
      "critical_safety_detected": false,
      "safety_types": [],
      "ppe_required": [],
      "confined_space": false,
      "hazmat": false,
      "fall_risk": false,
      "electrical_risk": true
    },
    "compliance": {
      "compliance_required": false,
      "types": [],
      "regulatory_body": null,
      "documentation_needed": []
    },
    "location": {
      "validated": true,
      "building": "Tower B",
      "floor": 2,
      "room": "4B",
      "zone": null,
      "access_restrictions": []
    },
    "asset_intelligence": {
      "asset_type": "HVAC",
      "estimated_age_years": null,
      "warranty_status": "unknown",
      "last_maintenance_date": null,
      "known_issues": [],
      "recommendation": "repair",
      "estimated_cost_range": "500-2000 AED"
    },
    "site_clearance": {
      "required": false,
      "hot_work_permit": false,
      "confined_space_permit": false,
      "electrical_isolation": true,
      "notes": "Ensure electrical isolation before maintenance."
    },
    "parts_list": [
      {
        "part_name": "fan motor",
        "quantity": 1,
        "urgency": "high",
        "part_number": null
      },
      {
        "part_name": "capacitor",
        "quantity": 1,
        "urgency": "medium",
        "part_number": null
      }
    ],
    "inventory": {
      "check_required": true,
      "notes": "inventory check pending for necessary parts."
    },
    "vendors": [
      {
        "rank": 1,
        "vendor_type": "specialist",
        "required_certifications": [],
        "notes": "preferred HVAC repair vendor"
      }
    ],
    "technician": {
      "required_skills": [
        "HVAC repair",
        "electrical systems"
      ],
      "certifications_needed": [
        "HVAC certification"
      ],
      "team_size": 1,
      "seniority_level": "senior",
      "estimated_duration_hours": 2
    },
    "schedule": {
      "suggested_timeframe": "same_day",
      "estimated_duration_hours": 2,
      "time_constraints": [
        "need to avoid peak hours"
      ],
      "preferred_time_window": null,
      "constraints": []
    },
    "workspace_pin": {
      "pin_priority": "high",
      "quick_actions": [
        {
          "label": "Assign Technician",
          "action": "assign_technician"
        },
        {
          "label": "Contact Vendor",
          "action": "contact_vendor"
        },
        {
          "label": "Order Parts",
          "action": "order_parts"
        },
        {
          "label": "Notify Requester",
          "action": "notify_requester"
        }
      ],
      "dashboard_flags": []
    },
    "journey": {
      "initial_step": "pending_approval",
      "next_steps": [
        "assign_technician",
        "schedule_visit"
      ],
      "sla_deadline_hours": 4,
      "tracking_tags": []
    }
  }
}
```

## Section 12. Email Intake (AI Pipeline)

### ✅ PASS — Process custom email dict (AI pipeline)

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/email/process` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "id": "TEST-EMAIL-001",
  "from": "tenant.manager@example.com",
  "from_name": "Sarah Johnson",
  "subject": "Water leak in bathroom Level 5",
  "body": "Hello, there is a water leak under the sink in the male bathroom on Level 5 of Tower A. Water is pooling on the floor and causing a slip hazard. Please send a plumber urgently. Sarah Johnson, Floor Manager, +971-55-111-2222",
  "received_at": "2026-04-30T08:00:00Z",
  "attachments": []
}
```

**Response Body**

```json
{
  "status": "created",
  "work_order_id": "WO-202604300706318503",
  "journey_log_id": "JL-202604300706318768",
  "priority": "critical",
  "assessment_summary": {
    "criticality_level": "critical",
    "response_time_hours": 4,
    "safety_score": 5,
    "critical_safety": true,
    "ppe_required": [
      "non-slip shoes",
      "gloves"
    ],
    "compliance_required": false,
    "suggested_timeframe": "same_day",
    "estimated_duration_hrs": 2,
    "required_skills": [
      "plumbing"
    ],
    "sla_deadline_hours": 4,
    "parts_needed": 1,
    "vendor_type": "plumbing specialist"
  },
  "full_assessment": {
    "criticality": {
      "level": "critical",
      "safety_score": 5,
      "operational_score": 4,
      "financial_score": 3,
      "compliance_score": 0,
      "overall_score": 4,
      "response_time_hours": 4,
      "reasoning": "Water leak under sink poses significant safety hazards due to pooling water."
    },
    "safety": {
      "critical_safety_detected": true,
      "safety_types": [
        "slip hazard"
      ],
      "ppe_required": [
        "non-slip shoes",
        "gloves"
      ],
      "confined_space": false,
      "hazmat": false,
      "fall_risk": true,
      "electrical_risk": false
    },
    "compliance": {
      "compliance_required": false,
      "types": [],
      "regulatory_body": null,
      "documentation_needed": []
    },
    "location": {
      "validated": true,
      "building": "Tower A",
      "floor": "5",
      "room": "male bathroom",
      "zone": null,
      "access_restrictions": []
    },
    "asset_intelligence": {
      "asset_type": "sink",
      "estimated_age_years": 5,
      "warranty_status": "expired",
      "last_maintenance_date": "2022-05-01",
      "known_issues": [],
      "recommendation": "repair",
      "estimated_cost_range": "200-800 AED"
    },
    "site_clearance": {
      "required": false,
      "hot_work_permit": false,
      "confined_space_permit": false,
      "electrical_isolation": false,
      "notes": null
    },
    "parts_list": [
      {
        "part_name": "faucet repair kit",
        "quantity": 1,
        "urgency": "high",
        "part_number": "FRK-123"
      }
    ],
    "inventory": {
      "check_required": true,
      "notes": "inventory check pending"
    },
    "vendors": [
      {
        "rank": 1,
        "vendor_type": "plumbing specialist",
        "required_certifications": [
          "plumbing certification"
        ],
        "notes": "preferred for urgent repairs"
      }
    ],
    "technician": {
      "required_skills": [
        "plumbing"
      ],
      "certifications_needed": [
        "plumbing certification"
      ],
      "team_size": 1,
      "seniority_level": "senior",
      "estimated_duration_hours": 2
    },
    "schedule": {
      "suggested_timeframe": "same_day",
      "estimated_duration_hours": 2,
      "time_constraints": [],
      "preferred_time_window": null,
      "constraints": []
    },
    "workspace_pin": {
      "pin_priority": "high",
      "quick_actions": [
        {
          "label": "Assign Technician",
          "action": "assign_technician"
        },
        {
          "label": "Contact Vendor",
          "action": "contact_vendor"
        },
        {
          "label": "Order Parts",
          "action": "order_parts"
        },
        {
          "label": "Notify Requester",
          "action": "notify_requester"
        }
      ],
      "dashboard_flags": []
    },
    "journey": {
      "initial_step": "pending_approval",
      "next_steps": [
        "assign_technician",
        "schedule_visit"
      ],
      "sla_deadline_hours": 4,
      "tracking_tags": []
    }
  }
}
```

### ✅ PASS — List all journeys

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "jlog_id": "JL-202604300706318768",
    "work_order_id": "WO-202604300706318503",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:31.876865+00:00",
      "expected_end": "2026-04-30T11:06:31.876865+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:31.876887+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:31.933389Z",
    "updated_at": "2026-04-30T07:06:31.933389Z"
  },
  {
    "jlog_id": "JL-202604300706101033",
    "work_order_id": "WO-202604300706100756",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:10.103356+00:00",
      "expected_end": "2026-04-30T11:06:10.103356+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:10.103372+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:10.160243Z",
    "updated_at": "2026-04-30T07:06:10.160243Z"
  },
  {
    "jlog_id": "JL-202604300705424913",
    "work_order_id": "WO-202604300705424661",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:42.917152+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:42.917152+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:42.491404+00:00",
      "expected_end": "2026-05-02T07:05:42.491404+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:42.917152Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:42.491413+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:42.549708Z",
    "updated_at": "2026-04-30T07:05:42.917152Z"
  },
  {
    "jlog_id": "JL-202604300705421868",
    "work_order_id": "WO-202604300705421616",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:42.847441+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:42.847441+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:42.186822+00:00",
      "expected_end": "2026-05-02T07:05:42.186822+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:42.847441Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:42.186831+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:42.245207Z",
    "updated_at": "2026-04-30T07:05:42.847441Z"
  },
  {
    "jlog_id": "JL-202604300705342713",
    "work_order_id": "WO-202604300705342373",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:41.425198+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:41.425198+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:34.271385+00:00",
      "expected_end": "2026-04-30T11:05:34.271385+00:00",
      "duration_hours": 4
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:41.425198Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:34.271403+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:34.320216Z",
    "updated_at": "2026-04-30T07:05:41.425198Z"
  },
  {
    "jlog_id": "JL-202604300705339275",
    "work_order_id": "WO-202604300705339033",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:33.927625+00:00",
      "expected_end": "2026-05-01T07:05:33.927625+00:00",
      "duration_hours": 24
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 24,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:33.927639+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:33.985759Z",
    "updated_at": "2026-04-30T07:05:33.985759Z"
  },
  {
    "jlog_id": "JL-202604300705336473",
    "work_order_id": "WO-202604300705336202",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:38.666474+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:39.443927+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:39.747048+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:40.107337+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:40.434560+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:40.434560+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:33.647398+00:00",
      "expected_end": "2026-05-02T07:05:33.647398+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": 0,
    "actual_start": "2026-04-30T07:05:39.747048Z",
    "actual_end": "2026-04-30T07:05:40.107337Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:33.647408+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:33.703569Z",
    "updated_at": "2026-04-30T07:05:40.434560Z"
  },
  {
    "jlog_id": "JL-202604300700378524",
    "work_order_id": "WO-202604300700378053",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.523248+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.743601+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.959457+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:49.180066+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:49.366105+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:00:49.366105+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:00:37.852516+00:00",
      "expected_end": "2026-04-30T11:00:37.852516+00:00",
      "duration_hours": 4
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": 0,
    "actual_start": "2026-04-30T07:00:48.959457Z",
    "actual_end": "2026-04-30T07:00:49.180066Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:00:37.852537+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:00:37.894128Z",
    "updated_at": "2026-04-30T07:00:49.366105Z"
  },
  {
    "jlog_id": "JL-202604300645230389",
    "work_order_id": "WO-202604300645229918",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T06:45:23.038910+00:00",
      "expected_end": "2026-04-30T10:45:23.038910+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T06:45:23.038910+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T06:45:23.080514Z",
    "updated_at": "2026-04-30T06:45:23.080514Z"
  },
  {
    "jlog_id": "JL-202604291841241321",
    "work_order_id": "WO-202604291841241006",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:41:24.132147+00:00",
      "expected_end": "2026-04-29T22:41:24.132147+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:41:24.475468Z",
    "updated_at": "2026-04-29T18:41:24.475468Z"
  },
  {
    "jlog_id": "JL-202604291841000420",
    "work_order_id": "WO-202604291841000082",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:41:00.042035+00:00",
      "expected_end": "2026-04-29T22:41:00.042035+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:41:00.383737Z",
    "updated_at": "2026-04-29T18:41:00.383737Z"
  },
  {
    "jlog_id": "JL-202604291840380657",
    "work_order_id": "WO-202604291840380366",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:38.336834+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T18:40:38.336834+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:40:38.065755+00:00",
      "expected_end": "2026-05-01T18:40:38.065755+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:40:38.409829Z",
    "updated_at": "2026-04-29T18:40:38.336870Z"
  },
  {
    "jlog_id": "JL-202604291840379400",
    "work_order_id": "WO-202604291840379111",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:38.256244+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T18:40:38.256244+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:40:37.940115+00:00",
      "expected_end": "2026-05-01T18:40:37.940115+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:40:38.285136Z",
    "updated_at": "2026-04-29T18:40:38.256275Z"
  },
  {
    "jlog_id": "JL-202604291840362060",
    "work_order_id": "WO-202604291840361565",
    "status": "completed",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": "Approved via test",
        "status": "completed",
        "timestamp": "2026-04-29T18:40:38.622292+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:37.089325+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:37.313254+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:37.483332+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:37.695235+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T18:40:37.695235+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:40:36.206085+00:00",
      "expected_end": "2026-05-01T18:40:36.206085+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:40:36.532091Z",
    "updated_at": "2026-04-29T18:40:38.622322Z"
  },
  {
    "jlog_id": "JL-202604291816380686",
    "work_order_id": "WO-202604291816380093",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:16:38.068713+00:00",
      "expected_end": "2026-04-29T22:16:38.068713+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:16:38.382351Z",
    "updated_at": "2026-04-29T18:16:38.382351Z"
  },
  {
    "jlog_id": "JL-202604291752123078",
    "work_order_id": "WO-202604291752122814",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:52:12.307841+00:00",
      "expected_end": "2026-04-29T21:52:12.307841+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:52:12.620958Z",
    "updated_at": "2026-04-29T17:52:12.620958Z"
  },
  {
    "jlog_id": "JL-202604291751516593",
    "work_order_id": "WO-202604291751516221",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:51.659350+00:00",
      "expected_end": "2026-04-29T21:51:51.659350+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:51.963607Z",
    "updated_at": "2026-04-29T17:51:51.963607Z"
  },
  {
    "jlog_id": "JL-202604291751347867",
    "work_order_id": "WO-202604291751347532",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:35.055471+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:51:35.055471+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:34.786761+00:00",
      "expected_end": "2026-05-01T17:51:34.786761+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:35.091117Z",
    "updated_at": "2026-04-29T17:51:35.055504Z"
  },
  {
    "jlog_id": "JL-202604291751346490",
    "work_order_id": "WO-202604291751346219",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:34.989604+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:51:34.989604+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:34.649092+00:00",
      "expected_end": "2026-05-01T17:51:34.649092+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:34.958342Z",
    "updated_at": "2026-04-29T17:51:34.989645Z"
  },
  {
    "jlog_id": "JL-202604291751328630",
    "work_order_id": "WO-202604291751328181",
    "status": "completed",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": "Approved via test",
        "status": "completed",
        "timestamp": "2026-04-29T17:51:35.376796+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:33.775367+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:34.009929+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:34.167356+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:34.394137+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:51:34.394137+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:32.863074+00:00",
      "expected_end": "2026-05-01T17:51:32.863074+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:33.157080Z",
    "updated_at": "2026-04-29T17:51:35.376832Z"
  }
]
```

## Section 13. Journey Logs

### ✅ PASS — Get journey by ID

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/JL-202604300705336473` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "jlog_id": "JL-202604300705336473",
  "work_order_id": "WO-202604300705336202",
  "status": "completed",
  "journey_status": "completed",
  "milestones": [
    {
      "name": "pending_approval",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:38.666474+00:00"
    },
    {
      "name": "preparing",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.443927+00:00"
    },
    {
      "name": "prepared",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.747048+00:00"
    },
    {
      "name": "active",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.107337+00:00"
    },
    {
      "name": "completed",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    },
    {
      "name": "closed",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    }
  ],
  "expected_timeline": {
    "start": "2026-04-30T07:05:33.647398+00:00",
    "expected_end": "2026-05-02T07:05:33.647398+00:00",
    "duration_hours": 48
  },
  "current_step": "closed",
  "completed": "true",
  "asset_id": null,
  "source_system": "api",
  "assigned_technician_id": null,
  "assigned_technician_name": null,
  "team_members": null,
  "estimated_cost": null,
  "actual_cost": null,
  "estimated_duration_hours": 48,
  "actual_duration_hours": 0,
  "actual_start": "2026-04-30T07:05:39.747048Z",
  "actual_end": "2026-04-30T07:05:40.107337Z",
  "resources_used": null,
  "completion_quality_score": null,
  "customer_satisfaction_score": null,
  "notes": null,
  "status_change_history": {
    "2026-04-30T07:05:33.647408+00:00": {
      "new_status": "in_progress",
      "old_status": null
    }
  },
  "milestone_history": null,
  "created_by": null,
  "updated_by": null,
  "created_at": "2026-04-30T07:05:33.703569Z",
  "updated_at": "2026-04-30T07:05:40.434560Z"
}
```

### ✅ PASS — Get journey health

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/JL-202604300705336473/health` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "health_status": "on_track",
  "completion_percentage": 83.3,
  "time_overrun_hours": 0,
  "cost_overrun": 0.0,
  "on_track": true,
  "requires_attention": false
}
```

### ✅ PASS — Milestone: pending_approval -> completed

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/journeys/JL-202604300705336473/milestone` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "milestone_name": "pending_approval",
  "status": "completed",
  "notes": "Auto-approved"
}
```

**Response Body**

```json
{
  "jlog_id": "JL-202604300705336473",
  "work_order_id": "WO-202604300705336202",
  "status": "completed",
  "journey_status": "completed",
  "milestones": [
    {
      "name": "pending_approval",
      "notes": "Auto-approved",
      "status": "completed",
      "timestamp": "2026-04-30T07:06:33.173103+00:00"
    },
    {
      "name": "preparing",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.443927+00:00"
    },
    {
      "name": "prepared",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.747048+00:00"
    },
    {
      "name": "active",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.107337+00:00"
    },
    {
      "name": "completed",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    },
    {
      "name": "closed",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    }
  ],
  "expected_timeline": {
    "start": "2026-04-30T07:05:33.647398+00:00",
    "expected_end": "2026-05-02T07:05:33.647398+00:00",
    "duration_hours": 48
  },
  "current_step": "closed",
  "completed": "true",
  "asset_id": null,
  "source_system": "api",
  "assigned_technician_id": null,
  "assigned_technician_name": null,
  "team_members": null,
  "estimated_cost": null,
  "actual_cost": null,
  "estimated_duration_hours": 48,
  "actual_duration_hours": 0,
  "actual_start": "2026-04-30T07:05:39.747048Z",
  "actual_end": "2026-04-30T07:05:40.107337Z",
  "resources_used": null,
  "completion_quality_score": null,
  "customer_satisfaction_score": null,
  "notes": null,
  "status_change_history": {
    "2026-04-30T07:05:33.647408+00:00": {
      "new_status": "in_progress",
      "old_status": null
    }
  },
  "milestone_history": null,
  "created_by": null,
  "updated_by": null,
  "created_at": "2026-04-30T07:05:33.703569Z",
  "updated_at": "2026-04-30T07:06:33.173129Z"
}
```

### ✅ PASS — Milestone: preparing -> current

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/journeys/JL-202604300705336473/milestone` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Request Payload**

```json
{
  "milestone_name": "preparing",
  "status": "current"
}
```

**Response Body**

```json
{
  "jlog_id": "JL-202604300705336473",
  "work_order_id": "WO-202604300705336202",
  "status": "completed",
  "journey_status": "completed",
  "milestones": [
    {
      "name": "pending_approval",
      "notes": "Auto-approved",
      "status": "completed",
      "timestamp": "2026-04-30T07:06:33.173103+00:00"
    },
    {
      "name": "preparing",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:06:33.441578+00:00"
    },
    {
      "name": "prepared",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.747048+00:00"
    },
    {
      "name": "active",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.107337+00:00"
    },
    {
      "name": "completed",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    },
    {
      "name": "closed",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    }
  ],
  "expected_timeline": {
    "start": "2026-04-30T07:05:33.647398+00:00",
    "expected_end": "2026-05-02T07:05:33.647398+00:00",
    "duration_hours": 48
  },
  "current_step": "preparing",
  "completed": "true",
  "asset_id": null,
  "source_system": "api",
  "assigned_technician_id": null,
  "assigned_technician_name": null,
  "team_members": null,
  "estimated_cost": null,
  "actual_cost": null,
  "estimated_duration_hours": 48,
  "actual_duration_hours": 0,
  "actual_start": "2026-04-30T07:05:39.747048Z",
  "actual_end": "2026-04-30T07:05:40.107337Z",
  "resources_used": null,
  "completion_quality_score": null,
  "customer_satisfaction_score": null,
  "notes": null,
  "status_change_history": {
    "2026-04-30T07:05:33.647408+00:00": {
      "new_status": "in_progress",
      "old_status": null
    }
  },
  "milestone_history": null,
  "created_by": null,
  "updated_by": null,
  "created_at": "2026-04-30T07:05:33.703569Z",
  "updated_at": "2026-04-30T07:06:33.441605Z"
}
```

### ✅ PASS — Milestone: nonexistent -> 422

| Field | Value |
|---|---|
| Method | `PATCH` |
| Path | `/api/journeys/JL-202604300705336473/milestone` |
| HTTP Status | `422` |
| Expected Status | `422` |
| Result | PASS |

**Request Payload**

```json
{
  "milestone_name": "nonexistent_step",
  "status": "completed"
}
```

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "milestone_not_found",
      "message": "Milestone 'nonexistent_step' not found in journey 'JL-202604300705336473'",
      "field": null
    }
  ]
}
```

### ✅ PASS — Get journey by WO ID

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/by-work-order/WO-202604300705336202` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "jlog_id": "JL-202604300705336473",
  "work_order_id": "WO-202604300705336202",
  "status": "completed",
  "journey_status": "completed",
  "milestones": [
    {
      "name": "pending_approval",
      "notes": "Auto-approved",
      "status": "completed",
      "timestamp": "2026-04-30T07:06:33.173103+00:00"
    },
    {
      "name": "preparing",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:06:33.441578+00:00"
    },
    {
      "name": "prepared",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:39.747048+00:00"
    },
    {
      "name": "active",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.107337+00:00"
    },
    {
      "name": "completed",
      "notes": null,
      "status": "completed",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    },
    {
      "name": "closed",
      "notes": null,
      "status": "current",
      "timestamp": "2026-04-30T07:05:40.434560+00:00"
    }
  ],
  "expected_timeline": {
    "start": "2026-04-30T07:05:33.647398+00:00",
    "expected_end": "2026-05-02T07:05:33.647398+00:00",
    "duration_hours": 48
  },
  "current_step": "preparing",
  "completed": "true",
  "asset_id": null,
  "source_system": "api",
  "assigned_technician_id": null,
  "assigned_technician_name": null,
  "team_members": null,
  "estimated_cost": null,
  "actual_cost": null,
  "estimated_duration_hours": 48,
  "actual_duration_hours": 0,
  "actual_start": "2026-04-30T07:05:39.747048Z",
  "actual_end": "2026-04-30T07:05:40.107337Z",
  "resources_used": null,
  "completion_quality_score": null,
  "customer_satisfaction_score": null,
  "notes": null,
  "status_change_history": {
    "2026-04-30T07:05:33.647408+00:00": {
      "new_status": "in_progress",
      "old_status": null
    }
  },
  "milestone_history": null,
  "created_by": null,
  "updated_by": null,
  "created_at": "2026-04-30T07:05:33.703569Z",
  "updated_at": "2026-04-30T07:06:33.441605Z"
}
```

### ✅ PASS — Journey for unknown WO -> 404

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/by-work-order/WO-GHOST` |
| HTTP Status | `404` |
| Expected Status | `404` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "journey_not_found",
      "message": "No journey for work order 'WO-GHOST'",
      "field": null
    }
  ]
}
```

### ✅ PASS — Journey analytics summary

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/analytics/summary` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "total_journeys": 29,
  "completed": 9,
  "active": 20,
  "in_progress_journeys": 4,
  "failed_journeys": 0,
  "completion_rate": 0.31,
  "avg_completion_hours": 29.0,
  "milestone_completion_rates": {
    "pending_approval": 0.621,
    "preparing": 0.207,
    "prepared": 0.207,
    "active": 0.207,
    "completed": 0.207,
    "closed": 0.0
  }
}
```

### ✅ PASS — Journey list with status filter

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/?status=active` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "jlog_id": "JL-202604300706318768",
    "work_order_id": "WO-202604300706318503",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:31.876865+00:00",
      "expected_end": "2026-04-30T11:06:31.876865+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:31.876887+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:31.933389Z",
    "updated_at": "2026-04-30T07:06:31.933389Z"
  },
  {
    "jlog_id": "JL-202604300706101033",
    "work_order_id": "WO-202604300706100756",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:10.103356+00:00",
      "expected_end": "2026-04-30T11:06:10.103356+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:10.103372+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:10.160243Z",
    "updated_at": "2026-04-30T07:06:10.160243Z"
  },
  {
    "jlog_id": "JL-202604300705339275",
    "work_order_id": "WO-202604300705339033",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:33.927625+00:00",
      "expected_end": "2026-05-01T07:05:33.927625+00:00",
      "duration_hours": 24
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 24,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:33.927639+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:33.985759Z",
    "updated_at": "2026-04-30T07:05:33.985759Z"
  },
  {
    "jlog_id": "JL-202604300645230389",
    "work_order_id": "WO-202604300645229918",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T06:45:23.038910+00:00",
      "expected_end": "2026-04-30T10:45:23.038910+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T06:45:23.038910+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T06:45:23.080514Z",
    "updated_at": "2026-04-30T06:45:23.080514Z"
  },
  {
    "jlog_id": "JL-202604291841241321",
    "work_order_id": "WO-202604291841241006",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:41:24.132147+00:00",
      "expected_end": "2026-04-29T22:41:24.132147+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:41:24.475468Z",
    "updated_at": "2026-04-29T18:41:24.475468Z"
  },
  {
    "jlog_id": "JL-202604291841000420",
    "work_order_id": "WO-202604291841000082",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:41:00.042035+00:00",
      "expected_end": "2026-04-29T22:41:00.042035+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:41:00.383737Z",
    "updated_at": "2026-04-29T18:41:00.383737Z"
  },
  {
    "jlog_id": "JL-202604291840380657",
    "work_order_id": "WO-202604291840380366",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:38.336834+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T18:40:38.336834+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:40:38.065755+00:00",
      "expected_end": "2026-05-01T18:40:38.065755+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:40:38.409829Z",
    "updated_at": "2026-04-29T18:40:38.336870Z"
  },
  {
    "jlog_id": "JL-202604291840379400",
    "work_order_id": "WO-202604291840379111",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T18:40:38.256244+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T18:40:38.256244+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:40:37.940115+00:00",
      "expected_end": "2026-05-01T18:40:37.940115+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:40:38.285136Z",
    "updated_at": "2026-04-29T18:40:38.256275Z"
  },
  {
    "jlog_id": "JL-202604291816380686",
    "work_order_id": "WO-202604291816380093",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:16:38.068713+00:00",
      "expected_end": "2026-04-29T22:16:38.068713+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:16:38.382351Z",
    "updated_at": "2026-04-29T18:16:38.382351Z"
  },
  {
    "jlog_id": "JL-202604291752123078",
    "work_order_id": "WO-202604291752122814",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:52:12.307841+00:00",
      "expected_end": "2026-04-29T21:52:12.307841+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:52:12.620958Z",
    "updated_at": "2026-04-29T17:52:12.620958Z"
  },
  {
    "jlog_id": "JL-202604291751516593",
    "work_order_id": "WO-202604291751516221",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:51.659350+00:00",
      "expected_end": "2026-04-29T21:51:51.659350+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:51.963607Z",
    "updated_at": "2026-04-29T17:51:51.963607Z"
  },
  {
    "jlog_id": "JL-202604291751347867",
    "work_order_id": "WO-202604291751347532",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:35.055471+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:51:35.055471+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:34.786761+00:00",
      "expected_end": "2026-05-01T17:51:34.786761+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:35.091117Z",
    "updated_at": "2026-04-29T17:51:35.055504Z"
  },
  {
    "jlog_id": "JL-202604291751346490",
    "work_order_id": "WO-202604291751346219",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:51:34.989604+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:51:34.989604+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:51:34.649092+00:00",
      "expected_end": "2026-05-01T17:51:34.649092+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:51:34.958342Z",
    "updated_at": "2026-04-29T17:51:34.989645Z"
  },
  {
    "jlog_id": "JL-202604291749465758",
    "work_order_id": "WO-202604291749465476",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:49:46.575954+00:00",
      "expected_end": "2026-04-29T21:49:46.575954+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:49:46.883314Z",
    "updated_at": "2026-04-29T17:49:46.883314Z"
  },
  {
    "jlog_id": "JL-202604291749262126",
    "work_order_id": "WO-202604291749261869",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:49:26.212725+00:00",
      "expected_end": "2026-04-29T21:49:26.212725+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:49:26.522532Z",
    "updated_at": "2026-04-29T17:49:26.522532Z"
  },
  {
    "jlog_id": "JL-202604291749033489",
    "work_order_id": "WO-202604291749033237",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:49:03.583175+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:49:03.583175+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:49:03.348990+00:00",
      "expected_end": "2026-05-01T17:49:03.348990+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:49:03.659837Z",
    "updated_at": "2026-04-29T17:49:03.583213Z"
  },
  {
    "jlog_id": "JL-202604291749032370",
    "work_order_id": "WO-202604291749032102",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T17:49:03.515693+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T17:49:03.515693+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T17:49:03.237132+00:00",
      "expected_end": "2026-05-01T17:49:03.237132+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T17:49:03.545678Z",
    "updated_at": "2026-04-29T17:49:03.515722Z"
  },
  {
    "jlog_id": "JL-202604291008061983",
    "work_order_id": "WO-202604291008061716",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T10:08:06.440016+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T10:08:06.440016+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T10:08:06.198388+00:00",
      "expected_end": "2026-05-01T10:08:06.198388+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T10:08:06.352775Z",
    "updated_at": "2026-04-29T10:08:06.440041Z"
  },
  {
    "jlog_id": "JL-202604291008060839",
    "work_order_id": "WO-202604291008060563",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T10:08:06.367998+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T10:08:06.367998+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T10:08:06.083991+00:00",
      "expected_end": "2026-05-01T10:08:06.083991+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T10:08:06.237051Z",
    "updated_at": "2026-04-29T10:08:06.368034Z"
  },
  {
    "jlog_id": "JL-202604291007124974",
    "work_order_id": "WO-202604291007124532",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T10:07:13.169081+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-29T10:07:13.349644+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-29T10:07:13.349644+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T10:07:12.497570+00:00",
      "expected_end": "2026-05-01T10:07:12.497570+00:00",
      "duration_hours": 48
    },
    "current_step": "prepared",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T10:07:12.634866Z",
    "updated_at": "2026-04-29T10:07:13.349703Z"
  }
]
```

### ✅ PASS — Journey list paginated (page=1, limit=10)

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/journeys/?page=1&limit=10` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[
  {
    "jlog_id": "JL-202604300706318768",
    "work_order_id": "WO-202604300706318503",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:31.876865+00:00",
      "expected_end": "2026-04-30T11:06:31.876865+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:31.876887+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:31.933389Z",
    "updated_at": "2026-04-30T07:06:31.933389Z"
  },
  {
    "jlog_id": "JL-202604300706101033",
    "work_order_id": "WO-202604300706100756",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:06:10.103356+00:00",
      "expected_end": "2026-04-30T11:06:10.103356+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:06:10.103372+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:06:10.160243Z",
    "updated_at": "2026-04-30T07:06:10.160243Z"
  },
  {
    "jlog_id": "JL-202604300705424913",
    "work_order_id": "WO-202604300705424661",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:42.917152+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:42.917152+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:42.491404+00:00",
      "expected_end": "2026-05-02T07:05:42.491404+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:42.917152Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:42.491413+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:42.549708Z",
    "updated_at": "2026-04-30T07:05:42.917152Z"
  },
  {
    "jlog_id": "JL-202604300705421868",
    "work_order_id": "WO-202604300705421616",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:42.847441+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:42.847441+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:42.186822+00:00",
      "expected_end": "2026-05-02T07:05:42.186822+00:00",
      "duration_hours": 48
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:42.847441Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:42.186831+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:42.245207Z",
    "updated_at": "2026-04-30T07:05:42.847441Z"
  },
  {
    "jlog_id": "JL-202604300705342713",
    "work_order_id": "WO-202604300705342373",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:41.425198+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:41.425198+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:34.271385+00:00",
      "expected_end": "2026-04-30T11:05:34.271385+00:00",
      "duration_hours": 4
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": "2026-04-30T07:05:41.425198Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:34.271403+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:34.320216Z",
    "updated_at": "2026-04-30T07:05:41.425198Z"
  },
  {
    "jlog_id": "JL-202604300705339275",
    "work_order_id": "WO-202604300705339033",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:33.927625+00:00",
      "expected_end": "2026-05-01T07:05:33.927625+00:00",
      "duration_hours": 24
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 24,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:33.927639+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:33.985759Z",
    "updated_at": "2026-04-30T07:05:33.985759Z"
  },
  {
    "jlog_id": "JL-202604300705336473",
    "work_order_id": "WO-202604300705336202",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": "Auto-approved",
        "status": "completed",
        "timestamp": "2026-04-30T07:06:33.173103+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:06:33.441578+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:39.747048+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:40.107337+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:05:40.434560+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:05:40.434560+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:05:33.647398+00:00",
      "expected_end": "2026-05-02T07:05:33.647398+00:00",
      "duration_hours": 48
    },
    "current_step": "preparing",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 48,
    "actual_duration_hours": 0,
    "actual_start": "2026-04-30T07:05:39.747048Z",
    "actual_end": "2026-04-30T07:05:40.107337Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:05:33.647408+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:05:33.703569Z",
    "updated_at": "2026-04-30T07:06:33.441605Z"
  },
  {
    "jlog_id": "JL-202604300700378524",
    "work_order_id": "WO-202604300700378053",
    "status": "completed",
    "journey_status": "completed",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.523248+00:00"
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.743601+00:00"
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:48.959457+00:00"
      },
      {
        "name": "active",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:49.180066+00:00"
      },
      {
        "name": "completed",
        "notes": null,
        "status": "completed",
        "timestamp": "2026-04-30T07:00:49.366105+00:00"
      },
      {
        "name": "closed",
        "notes": null,
        "status": "current",
        "timestamp": "2026-04-30T07:00:49.366105+00:00"
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T07:00:37.852516+00:00",
      "expected_end": "2026-04-30T11:00:37.852516+00:00",
      "duration_hours": 4
    },
    "current_step": "closed",
    "completed": "true",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": 0,
    "actual_start": "2026-04-30T07:00:48.959457Z",
    "actual_end": "2026-04-30T07:00:49.180066Z",
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T07:00:37.852537+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T07:00:37.894128Z",
    "updated_at": "2026-04-30T07:00:49.366105Z"
  },
  {
    "jlog_id": "JL-202604300645230389",
    "work_order_id": "WO-202604300645229918",
    "status": "active",
    "journey_status": "in_progress",
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-30T06:45:23.038910+00:00",
      "expected_end": "2026-04-30T10:45:23.038910+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": "api",
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": 4,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": {
      "2026-04-30T06:45:23.038910+00:00": {
        "new_status": "in_progress",
        "old_status": null
      }
    },
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-30T06:45:23.080514Z",
    "updated_at": "2026-04-30T06:45:23.080514Z"
  },
  {
    "jlog_id": "JL-202604291841241321",
    "work_order_id": "WO-202604291841241006",
    "status": "active",
    "journey_status": null,
    "milestones": [
      {
        "name": "pending_approval",
        "notes": null,
        "status": "current",
        "timestamp": null
      },
      {
        "name": "preparing",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "prepared",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "active",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "completed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      },
      {
        "name": "closed",
        "notes": null,
        "status": "pending",
        "timestamp": null
      }
    ],
    "expected_timeline": {
      "start": "2026-04-29T18:41:24.132147+00:00",
      "expected_end": "2026-04-29T22:41:24.132147+00:00",
      "duration_hours": 4
    },
    "current_step": "pending_approval",
    "completed": "false",
    "asset_id": null,
    "source_system": null,
    "assigned_technician_id": null,
    "assigned_technician_name": null,
    "team_members": null,
    "estimated_cost": null,
    "actual_cost": null,
    "estimated_duration_hours": null,
    "actual_duration_hours": null,
    "actual_start": null,
    "actual_end": null,
    "resources_used": null,
    "completion_quality_score": null,
    "customer_satisfaction_score": null,
    "notes": null,
    "status_change_history": null,
    "milestone_history": null,
    "created_by": null,
    "updated_by": null,
    "created_at": "2026-04-29T18:41:24.475468Z",
    "updated_at": "2026-04-29T18:41:24.475468Z"
  }
]
```

### ✅ PASS — Dashboard stats

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/dashboard/stats` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
{
  "total": 29,
  "by_status": {
    "prepared": 1,
    "closed": 9,
    "preparing": 8,
    "pending_approval": 11
  },
  "by_priority": {
    "high": 16,
    "critical": 12,
    "urgent": 1
  },
  "by_source": {
    "manual": 16,
    "email": 12,
    "ppm": 1
  },
  "created_today": 9,
  "assets_by_category": {
    "Unknown": 100
  }
}
```

## Section 14. Dashboard

### ❌ FAIL — List assets

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/assets/` |
| HTTP Status | `307` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
""
```

> **Note:** httpx does not follow HTTP redirects by default. FastAPI redirects trailing-slash URLs to non-trailing-slash URLs (307 Temporary Redirect). The endpoint functions correctly when called without the trailing slash.

## Section 15. Assets & Locations

### ❌ FAIL — List assets search=AHU

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/assets/?search=AHU` |
| HTTP Status | `307` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
""
```

> **Note:** httpx does not follow HTTP redirects by default. FastAPI redirects trailing-slash URLs to non-trailing-slash URLs (307 Temporary Redirect). The endpoint functions correctly when called without the trailing slash.

### ❌ FAIL — List assets paginated (limit=5)

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/assets/?page=1&limit=5` |
| HTTP Status | `307` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
""
```

> **Note:** httpx does not follow HTTP redirects by default. FastAPI redirects trailing-slash URLs to non-trailing-slash URLs (307 Temporary Redirect). The endpoint functions correctly when called without the trailing slash.

### ✅ PASS — Get asset by unknown ID -> 404

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/assets/ASSET-DOES-NOT-EXIST` |
| HTTP Status | `404` |
| Expected Status | `404` |
| Result | PASS |

**Response Body**

```json
{
  "success": false,
  "errors": [
    {
      "code": "asset_not_found",
      "message": "Asset 'ASSET-DOES-NOT-EXIST' not found",
      "field": null
    }
  ]
}
```

### ❌ FAIL — List locations

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/locations/` |
| HTTP Status | `307` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
""
```

> **Note:** httpx does not follow HTTP redirects by default. FastAPI redirects trailing-slash URLs to non-trailing-slash URLs (307 Temporary Redirect). The endpoint functions correctly when called without the trailing slash.

### ❌ FAIL — List locations search=Tower

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/locations/?search=Tower` |
| HTTP Status | `307` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
""
```

> **Note:** httpx does not follow HTTP redirects by default. FastAPI redirects trailing-slash URLs to non-trailing-slash URLs (307 Temporary Redirect). The endpoint functions correctly when called without the trailing slash.

### ✅ PASS — PPM due schedules

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/ppm/due` |
| HTTP Status | `200` |
| Expected Status | `200` |
| Result | PASS |

**Response Body**

```json
[]
```

## Section 16. PPM Scheduler

### ❌ FAIL — PPM list schedules

| Field | Value |
|---|---|
| Method | `GET` |
| Path | `/api/ppm/schedules` |
| HTTP Status | `404` |
| Expected Status | `200` |
| Result | FAIL |

**Response Body**

```json
{
  "detail": "Not Found"
}
```

> **Note:** The `/api/ppm/schedules` endpoint is not yet implemented in the PPM router. Only `/api/ppm/due` is available.
