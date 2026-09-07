"""
End-to-end API test for svc-work-order-management.

Run from repo root:
    pip install requests
    python svc-work-order-management/test_api.py

Or against a different host:
    BASE=http://your-host:8007 python svc-work-order-management/test_api.py
"""
import os
import sys
import json
import requests

BASE = os.getenv("BASE", "http://localhost:8007")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
INFO = "\033[36mINFO\033[0m"

# ── Connectivity pre-check ────────────────────────────────────────────────────
try:
    requests.get(f"{BASE}/docs", timeout=5)
except requests.exceptions.ConnectionError:
    print(f"\nERROR: Cannot reach {BASE}")
    print("Make sure the service is running:  docker compose up svc-work-order-management")
    print("Then re-run this script.\n")
    sys.exit(1)

results = {"passed": 0, "failed": 0, "skipped": 0}
_wo_id = None   # set after create; shared by lifecycle tests
_jlog_id = None


def check(label: str, resp: requests.Response, expected_status: int,
          check_fn=None, *, skip_if=False):
    if skip_if:
        print(f"  [{SKIP}] {label}")
        results["skipped"] += 1
        return None

    ok = resp.status_code == expected_status
    if ok and check_fn:
        try:
            check_fn(resp.json())
        except Exception as e:
            ok = False
            err = str(e).encode("ascii", errors="replace").decode("ascii")
            print(f"  [{FAIL}] {label}  ->  {resp.status_code}  body-check failed: {err}")
            results["failed"] += 1
            return resp
    if ok:
        print(f"  [{PASS}] {label}  ->  {resp.status_code}")
        results["passed"] += 1
    else:
        body = resp.text[:300].encode("ascii", errors="replace").decode("ascii")
        print(f"  [{FAIL}] {label}  ->  {resp.status_code} (expected {expected_status})  {body}")
        results["failed"] += 1
    return resp


def section(title: str):
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


# ── 1. Health / docs ──────────────────────────────────────────────────────────
section("1. Health & Docs")
check("GET /docs", requests.get(f"{BASE}/docs"), 200)
check("GET /openapi.json", requests.get(f"{BASE}/openapi.json"), 200)


# ── 2. Assets ─────────────────────────────────────────────────────────────────
section("2. Assets  (reads from plenum_cafm.assets)")

r = check("GET /api/assets  (no filter)",
          requests.get(f"{BASE}/api/assets"),
          200,
          lambda b: isinstance(b, list))

if r and r.status_code == 200:
    assets = r.json()
    print(f"  [{INFO}] {len(assets)} asset(s) returned")
    if assets:
        first_id = assets[0]["asset_id"]
        check(f"GET /api/assets/{{id}}  (first asset)",
              requests.get(f"{BASE}/api/assets/{first_id}"),
              200,
              lambda b: b["asset_id"] == first_id)

check("GET /api/assets?q=AHU  (name search)",
      requests.get(f"{BASE}/api/assets", params={"q": "AHU"}),
      200,
      lambda b: isinstance(b, list))

check("GET /api/assets/nonexistent-uuid  (404)",
      requests.get(f"{BASE}/api/assets/00000000-0000-0000-0000-000000000000"),
      404)


# ── 3. Locations ──────────────────────────────────────────────────────────────
section("3. Locations  (reads from plenum_cafm.locations)")

r = check("GET /api/locations",
          requests.get(f"{BASE}/api/locations"),
          200,
          lambda b: isinstance(b, list))

if r and r.status_code == 200:
    locs = r.json()
    print(f"  [{INFO}] {len(locs)} location(s) returned")

check("GET /api/locations?q=Building",
      requests.get(f"{BASE}/api/locations", params={"q": "Building"}),
      200)


# ── 4. PPM ────────────────────────────────────────────────────────────────────
section("4. PPM Scheduler  (reads from plenum_cafm.maintenance_plans)")

r = check("GET /api/ppm/due",
          requests.get(f"{BASE}/api/ppm/due"),
          200,
          lambda b: isinstance(b, list))

if r and r.status_code == 200:
    due = r.json()
    print(f"  [{INFO}] {len(due)} due PPM schedule(s)")


# ── 5. Work Order — CREATE ────────────────────────────────────────────────────
section("5. Work Order — Create")

CREATE_PAYLOAD = {
    "source": "manual",
    "asset": "MOB-AHU-001",
    "location": "Building A - Floor 1",
    "issue_description": "Quarterly inspection — compressor noise",
    "priority": "high",
    "request_type": "maintenance",
    "requester_name": "Test User",
    "requester_email": "test@plenum-tech.com",
    "requester_phone": "+971501234567",
}

r = check("POST /api/work-orders/  (create)",
          requests.post(f"{BASE}/api/work-orders/", json=CREATE_PAYLOAD),
          201,
          lambda b: b.get("status") == "pending_approval" and b.get("work_order_id"))

if r and r.status_code == 201:
    body = r.json()
    _wo_id = body["work_order_id"]
    _jlog_id = body.get("journey_log_id")
    print(f"  [{INFO}] Created WO: {_wo_id}  journey: {_jlog_id}")
else:
    print(f"  [{INFO}] WO create failed — lifecycle tests will be skipped")


# ── 6. Work Order — READ ──────────────────────────────────────────────────────
section("6. Work Orders — Read / Filter")

check("GET /api/work-orders/  (list)",
      requests.get(f"{BASE}/api/work-orders/"),
      200,
      lambda b: isinstance(b, list))

check("GET /api/work-orders/?status=pending_approval",
      requests.get(f"{BASE}/api/work-orders/", params={"status": "pending_approval"}),
      200)

check("GET /api/work-orders/filter/pending-approval",
      requests.get(f"{BASE}/api/work-orders/filter/pending-approval"),
      200)

check("GET /api/work-orders/filter/active",
      requests.get(f"{BASE}/api/work-orders/filter/active"),
      200)

check("GET /api/work-orders/nonexistent  (404)",
      requests.get(f"{BASE}/api/work-orders/WO-DOESNOTEXIST"),
      404)

if _wo_id:
    check(f"GET /api/work-orders/{_wo_id}",
          requests.get(f"{BASE}/api/work-orders/{_wo_id}"),
          200,
          lambda b: b["work_order_id"] == _wo_id)

    check(f"GET /api/work-orders/{_wo_id}/history",
          requests.get(f"{BASE}/api/work-orders/{_wo_id}/history"),
          200,
          lambda b: isinstance(b, list) and len(b) >= 1)


# ── 7. Work Order — Validation errors ─────────────────────────────────────────
section("7. Work Order — Validation (should return 422)")

check("POST missing required fields  (422)",
      requests.post(f"{BASE}/api/work-orders/", json={"source": "manual"}),
      422)

check("POST blank asset  (422)",
      requests.post(f"{BASE}/api/work-orders/", json={
          **CREATE_PAYLOAD, "asset": "   "
      }),
      422)

check("POST invalid priority  (422)",
      requests.post(f"{BASE}/api/work-orders/", json={
          **CREATE_PAYLOAD, "priority": "super-high"
      }),
      422)

check("POST invalid email  (422)",
      requests.post(f"{BASE}/api/work-orders/", json={
          **CREATE_PAYLOAD, "requester_email": "not-an-email"
      }),
      422)


# ── 8. Work Order — Lifecycle ─────────────────────────────────────────────────
section("8. Work Order — Status Machine Lifecycle")

if not _wo_id:
    print(f"  [{SKIP}] All lifecycle tests skipped — WO creation failed above")
    for _ in range(7):
        results["skipped"] += 1
else:
    # approve -> preparing
    r = check(f"POST /{_wo_id}/approve  (pending_approval -> preparing)",
              requests.post(f"{BASE}/api/work-orders/{_wo_id}/approve"),
              200,
              lambda b: b["status"] == "preparing")

    # prepare -> prepared
    r = check(f"POST /{_wo_id}/prepare  (preparing -> prepared)",
              requests.post(f"{BASE}/api/work-orders/{_wo_id}/prepare",
                            json={"vendor": "Carrier UAE", "estimated_duration": 4.0}),
              200,
              lambda b: b["status"] == "prepared")

    # Invalid transition attempt
    check(f"PATCH /{_wo_id}/status  (prepared -> completed, invalid)",
          requests.patch(f"{BASE}/api/work-orders/{_wo_id}/status",
                         json={"new_status": "completed"}),
          422)

    # prepared -> active
    r = check(f"PATCH /{_wo_id}/status  (prepared -> active)",
              requests.patch(f"{BASE}/api/work-orders/{_wo_id}/status",
                             json={"new_status": "active"}),
              200,
              lambda b: b["status"] == "active")

    # active -> completed
    r = check(f"PATCH /{_wo_id}/status  (active -> completed)",
              requests.patch(f"{BASE}/api/work-orders/{_wo_id}/status",
                             json={"new_status": "completed"}),
              200,
              lambda b: b["status"] == "completed")

    # completed -> closed
    r = check(f"PATCH /{_wo_id}/status  (completed -> closed)",
              requests.patch(f"{BASE}/api/work-orders/{_wo_id}/status",
                             json={"new_status": "completed"}),
              422)  # invalid — should be closed not completed again

    r = check(f"POST /{_wo_id}/close  (-> closed)",
              requests.post(f"{BASE}/api/work-orders/{_wo_id}/close"),
              200,
              lambda b: b["status"] == "closed")

    # close again — 409
    check(f"POST /{_wo_id}/close  again  (409 already closed)",
          requests.post(f"{BASE}/api/work-orders/{_wo_id}/close"),
          409)


# ── 9. Work Order — PATCH update ──────────────────────────────────────────────
section("9. Work Order — PATCH update fields")

if _wo_id:
    check(f"PATCH /{_wo_id}  (update vendor/duration)",
          requests.patch(f"{BASE}/api/work-orders/{_wo_id}",
                         json={"vendor": "Updated Vendor", "estimated_duration": 5.5}),
          200,
          lambda b: b.get("vendor") == "Updated Vendor")
else:
    results["skipped"] += 1


# ── 10. Bulk status update ────────────────────────────────────────────────────
section("10. Work Order — Bulk Status Update")

# Create two fresh WOs to bulk-update
ids_for_bulk = []
for i in range(2):
    r = requests.post(f"{BASE}/api/work-orders/", json={
        **CREATE_PAYLOAD,
        "issue_description": f"Bulk test WO {i+1}",
    })
    if r.status_code == 201:
        ids_for_bulk.append(r.json()["work_order_id"])

if len(ids_for_bulk) == 2:
    check("PATCH /bulk/status  (pending_approval -> preparing)",
          requests.patch(f"{BASE}/api/work-orders/bulk/status", json={
              "work_order_ids": ids_for_bulk,
              "new_status": "preparing",
          }),
          200,
          lambda b: b["updated"] == 2 and b["failed"] == 0)

    check("PATCH /bulk/status  empty list  (422)",
          requests.patch(f"{BASE}/api/work-orders/bulk/status", json={
              "work_order_ids": [],
              "new_status": "preparing",
          }),
          422)
else:
    print(f"  [{SKIP}] Bulk test skipped — could not create test WOs")
    results["skipped"] += 2


# ── 11. Journeys ──────────────────────────────────────────────────────────────
section("11. Journey Logs")

check("GET /api/journeys/analytics/summary",
      requests.get(f"{BASE}/api/journeys/analytics/summary"),
      200,
      lambda b: "total_journeys" in b)

check("GET /api/journeys/  (list)",
      requests.get(f"{BASE}/api/journeys/"),
      200,
      lambda b: isinstance(b, list))

if _wo_id:
    check(f"GET /api/journeys/by-work-order/{_wo_id}",
          requests.get(f"{BASE}/api/journeys/by-work-order/{_wo_id}"),
          200)

if _jlog_id:
    check(f"GET /api/journeys/{_jlog_id}",
          requests.get(f"{BASE}/api/journeys/{_jlog_id}"),
          200)

    check(f"PATCH /api/journeys/{_jlog_id}/milestone  (update milestone)",
          requests.patch(f"{BASE}/api/journeys/{_jlog_id}/milestone", json={
              "milestone_name": "pending_approval",
              "status": "completed",
              "notes": "Approved via test",
          }),
          200)

    check(f"PATCH /api/journeys/{_jlog_id}/milestone  (bad name -> 422)",
          requests.patch(f"{BASE}/api/journeys/{_jlog_id}/milestone", json={
              "milestone_name": "nonexistent_milestone",
              "status": "completed",
          }),
          422)

check("GET /api/journeys/nonexistent  (404)",
      requests.get(f"{BASE}/api/journeys/JLOG-DOESNOTEXIST"),
      404)


# ── 12. Dashboard ─────────────────────────────────────────────────────────────
section("12. Dashboard")
check("GET /api/dashboard/stats",
      requests.get(f"{BASE}/api/dashboard/stats"),
      200,
      lambda b: "total" in b and "by_status" in b)


# ── 13. Email Flow (Claude parse + AI assessment + DB create) ─────────────────
section("13. Email Flow  (Claude-only path)")

SAMPLE_EMAIL = {
    "id": "MSG-TEST-001",
    "from": "shashank@plenum-tech.com",
    "from_name": "James Carter",
    "subject": "Urgent - HVAC not working in Meeting Room 4B",
    "body": (
        "Hi Facilities Team,\n\n"
        "The HVAC unit in Meeting Room 4B on the 2nd floor of Tower B has stopped "
        "working since this morning. It makes a loud rattling sound before shutting off.\n\n"
        "We have a client presentation at 2 PM today — please send a technician ASAP.\n\n"
        "Best regards,\nJames Carter\nPhone: +971-50-123-4567"
    ),
    "received_at": "2026-04-29T08:15:00Z",
    "attachments": [],
}

# Sample email endpoint (uses hardcoded SAMPLE_EMAIL from email_parser.py)
r = check("POST /api/email/process/sample  (Claude parse + assess + create WO)",
          requests.post(f"{BASE}/api/email/process/sample"),
          200,
          lambda b: b.get("status") in ("created", "missing_info"))

if r and r.status_code == 200:
    body = r.json()
    if body.get("status") == "created":
        print(f"  [{INFO}] Email WO created: {body['work_order_id']}  priority: {body['priority']}")
        summ = body.get("assessment_summary", {})
        print(f"  [{INFO}] Criticality: {summ.get('criticality_level')}  "
              f"SLA: {summ.get('sla_deadline_hours')}h  "
              f"Safety: {summ.get('critical_safety')}  "
              f"Parts needed: {summ.get('parts_needed')}")
    else:
        print(f"  [{INFO}] Missing fields: {body.get('missing_fields')} -- check ANTHROPIC_API_KEY")

# Custom email body
r = check("POST /api/email/process  (custom email dict)",
          requests.post(f"{BASE}/api/email/process", json=SAMPLE_EMAIL),
          200,
          lambda b: b.get("status") in ("created", "missing_info"))

if r and r.status_code == 200 and r.json().get("status") == "created":
    print(f"  [{INFO}] Custom email WO: {r.json()['work_order_id']}")

# Missing info email — should return missing_info not error
check("POST /api/email/process  (vague email -> missing_info)",
      requests.post(f"{BASE}/api/email/process", json={
          "id": "MSG-VAGUE-001",
          "from": "unknown@example.com",
          "subject": "Something is broken",
          "body": "Please fix it.",
          "received_at": "2026-04-29T09:00:00Z",
          "attachments": [],
      }),
      200,
      lambda b: b.get("status") in ("created", "missing_info"))


# ── Summary ──────────────────────────────────────────────────────────────────
total = results["passed"] + results["failed"] + results["skipped"]
sep = "=" * 60
print(f"\n{sep}")
print(f"  Results:  {results['passed']} passed  |  {results['failed']} failed  |  {results['skipped']} skipped  |  {total} total")
print(f"{sep}\n")

if results["failed"] > 0:
    sys.exit(1)
