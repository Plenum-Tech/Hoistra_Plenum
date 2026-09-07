import json, httpx, datetime, sys

BASE = "http://localhost:8007"
results = []

def ts():
    return datetime.datetime.utcnow().strftime("%H:%M:%S UTC")

def run(label, method, path, payload=None, expected_status=None):
    url = BASE + path
    try:
        if method == "GET":
            r = httpx.get(url, timeout=30)
        elif method == "POST":
            r = httpx.post(url, json=payload, timeout=30)
        elif method == "PATCH":
            r = httpx.patch(url, json=payload, timeout=30)
        try:
            body = r.json()
        except Exception:
            body = r.text
        passed = (r.status_code == expected_status) if expected_status else (r.status_code < 400)
        results.append({
            "label": label,
            "method": method,
            "path": path,
            "payload": payload,
            "status_code": r.status_code,
            "expected": expected_status,
            "passed": passed,
            "response": body,
        })
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {label} -> HTTP {r.status_code}")
        return body, r.status_code
    except Exception as e:
        results.append({
            "label": label, "method": method, "path": path,
            "payload": payload, "status_code": "ERROR",
            "expected": expected_status, "passed": False,
            "response": str(e),
        })
        print(f"[ERROR] {label} -> {e}")
        return None, 0

print("=" * 60)
print("SVC-WORK-ORDER-MANAGEMENT  Full Test Run")
print(f"Started: {ts()}")
print("=" * 60)

# ── 1. HEALTH ──────────────────────────────────────────────────────────
print("\n-- 1. Health & Outlook --")
run("Health check", "GET", "/health", expected_status=200)
run("Outlook connection status", "GET", "/api/email/status", expected_status=200)

# ── 2. WORK ORDER CREATE ───────────────────────────────────────────────
print("\n-- 2. Work Order Create --")
WO_BASE = {
    "source": "manual",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise and vibrating heavily",
    "priority": "high",
    "request_type": "repair",
    "requester_name": "James Carter",
    "requester_email": "shashank@plenum-tech.com",
    "requester_phone": "+971501234567",
}
body, _ = run("Create WO (manual, high)", "POST", "/api/work-orders/", WO_BASE, 201)
WO1 = body["work_order_id"] if body and "work_order_id" in body else "WO-MISSING"
JL1 = body.get("journey_log_id", "") if body else ""
print(f"  WO1={WO1}  JL1={JL1}")

body2, _ = run("Create WO (urgent, email source)", "POST", "/api/work-orders/", {
    **WO_BASE,
    "source": "email",
    "priority": "urgent",
    "asset": "LIFT-002",
    "location": "Podium Level 1",
    "issue_description": "Lift stuck between floors, occupant inside",
    "requester_name": "Security Desk",
    "requester_email": "security@building.com",
    "requester_phone": None,
}, 201)
WO2 = body2["work_order_id"] if body2 and "work_order_id" in body2 else "WO-MISSING2"

body3, _ = run("Create WO (critical, ppm)", "POST", "/api/work-orders/", {
    **WO_BASE,
    "source": "ppm",
    "priority": "critical",
    "asset": "GEN-001",
    "location": "Basement Plant Room",
    "issue_description": "Emergency generator quarterly maintenance overdue",
    "request_type": "maintenance",
    "requester_name": "PPM System",
    "requester_email": "ppm@plenum-tech.com",
    "requester_phone": None,
}, 201)
WO3 = body3["work_order_id"] if body3 and "work_order_id" in body3 else "WO-MISSING3"

# ── 3. VALIDATION ERRORS ───────────────────────────────────────────────
print("\n-- 3. Validation Errors --")
run("Missing asset -> 422", "POST", "/api/work-orders/",
    {k: v for k, v in WO_BASE.items() if k != "asset"}, 422)
run("Blank asset -> 422", "POST", "/api/work-orders/",
    {**WO_BASE, "asset": "   "}, 422)
run("Invalid priority -> 422", "POST", "/api/work-orders/",
    {**WO_BASE, "priority": "extreme"}, 422)
run("Invalid source -> 422", "POST", "/api/work-orders/",
    {**WO_BASE, "source": "fax"}, 422)
run("Bad email -> 422", "POST", "/api/work-orders/",
    {**WO_BASE, "requester_email": "not-an-email"}, 422)
run("Invalid request_type -> 422", "POST", "/api/work-orders/",
    {**WO_BASE, "request_type": "demolition"}, 422)

# ── 4. GET / LIST ──────────────────────────────────────────────────────
print("\n-- 4. Get & List --")
run("List all WOs", "GET", "/api/work-orders/", expected_status=200)
run("Get WO by ID", "GET", f"/api/work-orders/{WO1}", expected_status=200)
run("Get unknown WO -> 404", "GET", "/api/work-orders/WO-DOES-NOT-EXIST", expected_status=404)
run("Filter by status=pending_approval", "GET", "/api/work-orders/?status=pending_approval", expected_status=200)
run("Filter by priority=high", "GET", "/api/work-orders/?priority=high", expected_status=200)
run("Filter by source=email", "GET", "/api/work-orders/?source=email", expected_status=200)
run("Filter active WOs endpoint", "GET", "/api/work-orders/filter/active", expected_status=200)
run("Filter pending-approval endpoint", "GET", "/api/work-orders/filter/pending-approval", expected_status=200)
run("List paginated (page=1, limit=5)", "GET", "/api/work-orders/?page=1&limit=5", expected_status=200)

# ── 5. PATCH (mutable fields) ──────────────────────────────────────────
print("\n-- 5. Work Order Update --")
run("Patch vendor + scheduled_date", "PATCH", f"/api/work-orders/{WO1}",
    {"vendor": "Acme HVAC Services", "scheduled_date": "2026-05-10"}, 200)
run("Patch estimated_duration", "PATCH", f"/api/work-orders/{WO1}",
    {"estimated_duration": 3.5}, 200)
run("Negative duration -> 422", "PATCH", f"/api/work-orders/{WO1}",
    {"estimated_duration": -5.0}, 422)

# ── 6. APPROVE ─────────────────────────────────────────────────────────
print("\n-- 6. Approve --")
run("Approve WO1 -> preparing", "POST", f"/api/work-orders/{WO1}/approve", expected_status=200)
run("Double approve -> 409", "POST", f"/api/work-orders/{WO1}/approve", expected_status=409)
run("Approve unknown ID -> 404", "POST", "/api/work-orders/WO-GHOST/approve", expected_status=404)

# ── 7. PREPARE ─────────────────────────────────────────────────────────
print("\n-- 7. Prepare --")
run("Prepare WO1 (vendor + date)", "POST", f"/api/work-orders/{WO1}/prepare",
    {"vendor": "HVAC Pro LLC", "scheduled_date": "2026-05-02", "estimated_duration": 2.0}, 200)

# ── 8. STATUS TRANSITIONS ──────────────────────────────────────────────
print("\n-- 8. Status Transitions --")
run("WO1: prepared -> active", "PATCH", f"/api/work-orders/{WO1}/status",
    {"new_status": "active", "notes": "Technician dispatched"}, 200)
run("WO1: active -> completed", "PATCH", f"/api/work-orders/{WO1}/status",
    {"new_status": "completed", "notes": "Compressor bearing replaced"}, 200)
run("WO1: close endpoint", "POST", f"/api/work-orders/{WO1}/close", expected_status=200)
run("Close already closed -> 409", "POST", f"/api/work-orders/{WO1}/close", expected_status=409)
run("Skip pending->active (invalid) -> 422", "PATCH", f"/api/work-orders/{WO3}/status",
    {"new_status": "active"}, 422)
run("Invalid status value -> 422", "PATCH", f"/api/work-orders/{WO3}/status",
    {"new_status": "flying"}, 422)

# ── 9. CLOSE FROM PENDING ──────────────────────────────────────────────
print("\n-- 9. Close from pending --")
run("Close WO3 from pending_approval", "POST", f"/api/work-orders/{WO3}/close", expected_status=200)

# ── 10. STATUS HISTORY ─────────────────────────────────────────────────
print("\n-- 10. Status History --")
run("WO1 status history", "GET", f"/api/work-orders/{WO1}/history", expected_status=200)
run("Unknown WO history -> 404", "GET", "/api/work-orders/WO-GHOST/history", expected_status=404)

# ── 11. BULK STATUS UPDATE ─────────────────────────────────────────────
print("\n-- 11. Bulk Status Update --")
bA, _ = run("Create WO bulk-A", "POST", "/api/work-orders/",
    {**WO_BASE, "asset": "PUMP-001", "issue_description": "Pump vibrating"}, 201)
bB, _ = run("Create WO bulk-B", "POST", "/api/work-orders/",
    {**WO_BASE, "asset": "PUMP-002", "issue_description": "Pump leaking"}, 201)
bulk_ids = []
if bA and "work_order_id" in bA: bulk_ids.append(bA["work_order_id"])
if bB and "work_order_id" in bB: bulk_ids.append(bB["work_order_id"])
if bulk_ids:
    run("Bulk close 2 WOs", "PATCH", "/api/work-orders/bulk/status",
        {"work_order_ids": bulk_ids, "new_status": "closed", "notes": "Bulk closed in test"}, 200)

# ── 12. EMAIL INTAKE ───────────────────────────────────────────────────
print("\n-- 12. Email Intake --")
run("Process hardcoded sample email (AI pipeline)", "POST", "/api/email/process/sample", expected_status=200)
run("Process custom email dict (AI pipeline)", "POST", "/api/email/process", {
    "id": "TEST-EMAIL-001",
    "from": "tenant.manager@example.com",
    "from_name": "Sarah Johnson",
    "subject": "Water leak in bathroom Level 5",
    "body": (
        "Hello, there is a water leak under the sink in the male bathroom "
        "on Level 5 of Tower A. Water is pooling on the floor and causing a slip hazard. "
        "Please send a plumber urgently. "
        "Sarah Johnson, Floor Manager, +971-55-111-2222"
    ),
    "received_at": "2026-04-30T08:00:00Z",
    "attachments": [],
}, expected_status=200)

# ── 13. JOURNEYS ───────────────────────────────────────────────────────
print("\n-- 13. Journey Logs --")
run("List all journeys", "GET", "/api/journeys/", expected_status=200)
if JL1:
    run("Get journey by ID", "GET", f"/api/journeys/{JL1}", expected_status=200)
    run("Get journey health", "GET", f"/api/journeys/{JL1}/health", expected_status=200)
    run("Milestone: pending_approval -> completed", "PATCH", f"/api/journeys/{JL1}/milestone",
        {"milestone_name": "pending_approval", "status": "completed", "notes": "Auto-approved"}, 200)
    run("Milestone: preparing -> current", "PATCH", f"/api/journeys/{JL1}/milestone",
        {"milestone_name": "preparing", "status": "current"}, 200)
    run("Milestone: nonexistent -> 422", "PATCH", f"/api/journeys/{JL1}/milestone",
        {"milestone_name": "nonexistent_step", "status": "completed"}, 422)
run("Get journey by WO ID", "GET", f"/api/journeys/by-work-order/{WO1}", expected_status=200)
run("Journey for unknown WO -> 404", "GET", "/api/journeys/by-work-order/WO-GHOST", expected_status=404)
run("Journey analytics summary", "GET", "/api/journeys/analytics/summary", expected_status=200)
run("Journey list with status filter", "GET", "/api/journeys/?status=active", expected_status=200)
run("Journey list paginated (page=1, limit=10)", "GET", "/api/journeys/?page=1&limit=10", expected_status=200)

# ── 14. DASHBOARD ──────────────────────────────────────────────────────
print("\n-- 14. Dashboard --")
run("Dashboard stats", "GET", "/api/dashboard/stats", expected_status=200)

# ── 15. ASSETS & LOCATIONS ─────────────────────────────────────────────
print("\n-- 15. Assets & Locations --")
run("List assets", "GET", "/api/assets/", expected_status=200)
run("List assets search=AHU", "GET", "/api/assets/?search=AHU", expected_status=200)
run("List assets paginated (limit=5)", "GET", "/api/assets/?page=1&limit=5", expected_status=200)
run("Get asset by unknown ID -> 404", "GET", "/api/assets/ASSET-DOES-NOT-EXIST", expected_status=404)
run("List locations", "GET", "/api/locations/", expected_status=200)
run("List locations search=Tower", "GET", "/api/locations/?search=Tower", expected_status=200)

# ── 16. PPM SCHEDULER ─────────────────────────────────────────────────
print("\n-- 16. PPM Scheduler --")
run("PPM due schedules", "GET", "/api/ppm/due", expected_status=200)
run("PPM list schedules", "GET", "/api/ppm/schedules", expected_status=200)

# ── SUMMARY ────────────────────────────────────────────────────────────
passed = sum(1 for r in results if r["passed"])
failed = sum(1 for r in results if not r["passed"])
print("\n" + "=" * 60)
print(f"TOTAL: {len(results)}  PASSED: {passed}  FAILED: {failed}")
print(f"Completed: {ts()}")
print("=" * 60)

with open("/tmp/test_results.json", "w") as f:
    json.dump({
        "run_at": ts(),
        "base_url": BASE,
        "summary": {"total": len(results), "passed": passed, "failed": failed},
        "results": results,
    }, f, indent=2, default=str)
print("Saved -> /tmp/test_results.json")
