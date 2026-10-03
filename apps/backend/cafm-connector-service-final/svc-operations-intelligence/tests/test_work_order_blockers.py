"""Work orders blocked by their vendor: the classification is pure and the route is signed-in only."""
from __future__ import annotations

from datetime import date

from src.engines import work_order_blockers as wb

TODAY = date(2026, 10, 2)


def test_a_blocked_vendor_and_a_lapsed_certificate_are_hard_blockers_once_each():
    row = {"block_state": "Blocked", "block_reason": "Accreditation lapsed: BAFE SP203-1 Registration Certificate",
           "certs": [{"type": "BAFE SP203-1 Registration Certificate", "status": "Lapsed", "expiry": "2026-09-14"},
                     {"type": "BAFE SP203-1 Registration Certificate", "status": "Lapsed", "expiry": "2025-09-14"}],
           "contracts": [{"name": "Fire alarm maintenance", "end": "2027-08-09", "status": "active"}],
           "unconfirmed_sla": [{"ref": "PN-2024-FIRE-02", "status": "draft"}]}
    out = wb.classify(row, TODAY)
    assert [b["kind"] for b in out] == ["accreditation_lapsed", "vendor_blocked", "contract_terms_unconfirmed"]
    assert out[0]["hard"] and out[1]["hard"] and not out[2]["hard"]
    assert out[0]["detail"] == "BAFE SP203-1 Registration Certificate lapsed on 2026-09-14"


def test_contracts_ending_soon_are_a_risk_and_expired_ones_a_blocker():
    soon = wb.classify({"contracts": [{"name": "Generator PPM", "end": "2026-11-16", "status": "active"}]}, TODAY)
    assert soon == [{"kind": "contract_renewal_due", "hard": False, "date": "2026-11-16",
                     "detail": "Generator PPM ends 2026-11-16 (45 days); renewal not recorded"}]
    gone = wb.classify({"contracts": [{"name": "Generator PPM", "end": "2026-09-30", "status": "active"}]}, TODAY)
    assert gone[0]["kind"] == "contract_expired" and gone[0]["hard"]
    assert wb.classify({"contracts": [{"name": "X", "end": "2027-01-01", "status": "draft"}]}, TODAY)[0]["kind"] == "contract_inactive"
    assert wb.classify({"contracts": [{"name": "X", "end": "2027-06-01", "status": "active"}], "certs": []}, TODAY) == []


def test_an_expiring_certificate_is_soft_and_so_is_overdue_ahead_of_expiry():
    out = wb.classify({"certs": [{"type": "NICEIC", "status": "Current", "expiry": "2026-10-20"}]}, TODAY)
    assert out == [{"kind": "accreditation_expiring", "hard": False, "date": "2026-10-20", "detail": "NICEIC expires on 2026-10-20"}]
    # The register marks a renewal "Overdue" ahead of expiry (Apex Mechanical CHAS_SSIP, 2 Oct 2026):
    # the cover still stands, so the job is not stopped.
    out = wb.classify({"certs": [{"type": "CHAS_SSIP", "status": "Overdue", "expiry": "2026-10-25"}]}, TODAY)
    assert out[0]["kind"] == "accreditation_expiring" and not out[0]["hard"]
    assert wb.classify({"certs": [{"type": "NICEIC", "status": "Overdue", "expiry": "2025-09-06"}]}, TODAY)[0]["kind"] == "accreditation_lapsed"


def test_the_route_needs_a_signed_in_caller():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.work_order_blockers import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/work-orders/blockers").status_code in (401, 403)
