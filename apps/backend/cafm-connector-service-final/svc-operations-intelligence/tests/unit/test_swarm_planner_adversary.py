"""Planner packet + Adversary Approve/Flag/Escalate + confidence gate."""
from datetime import date, timedelta

from src.swarm.adversary import (
    CONFIDENCE_GATE,
    apply_confidence_gate,
    finalize_adversary,
    AdversaryResult,
    validate_block_state_change,
    validate_vendor_email_draft,
)
from src.swarm.planner import plan_compliance_scan, packet_to_dict
from src.swarm.worker import WorkerResult, merge_worker_results


def test_plan_compliance_scan_parallel_streams():
    packet = plan_compliance_scan(scope="all")
    assert packet.parallel is True
    assert len(packet.tasks) == 2
    streams = {t.stream for t in packet.tasks}
    assert streams == {"building", "vendor"}
    d = packet_to_dict(packet)
    assert d["intent"] == "compliance_scan"
    assert len(d["tasks"]) == 2


def test_plan_building_only():
    packet = plan_compliance_scan(scope="building")
    assert len(packet.tasks) == 1
    assert packet.tasks[0].stream == "building"


def test_confidence_gate_escalates_below_85():
    result = AdversaryResult(approved=True)
    gated = apply_confidence_gate(result, confidence=0.84)
    assert gated.approved is False
    assert gated.outcome == "escalated"
    assert any("confidence_below" in r for r in gated.reasons)
    assert CONFIDENCE_GATE == 0.85


def test_confidence_gate_passes_at_85():
    result = AdversaryResult(approved=True)
    gated = apply_confidence_gate(result, confidence=0.85)
    assert gated.approved is True
    assert gated.outcome == "approved"


def test_block_flagged_when_expiry_future():
    result = validate_block_state_change(
        accreditation_type="Gas Safe",
        expiry_date=date.today() + timedelta(days=10),
        proposed_block_state="Blocked",
    )
    assert result.approved is False
    assert result.outcome == "flagged"


def test_finalize_escalates_missing():
    result = finalize_adversary(
        AdversaryResult(approved=False, reasons=["missing_expiry_date"])
    )
    assert result.outcome == "escalated"


def test_vendor_email_flagged_on_mismatch():
    draft = {
        "to": "wrong@example.com",
        "subject": "[URGENT] Accreditation renewal required — Gas Safe — Acme",
        "body": "Gas Safe\nhttps://example.com",
    }
    result = validate_vendor_email_draft(
        vendor_contact_email="right@example.com",
        accreditation_type="Gas Safe",
        renewal_url="https://example.com",
        draft=draft,
    )
    assert result.approved is False
    assert result.outcome in {"flagged", "escalated"}


def test_merge_worker_results():
    a = WorkerResult(
        worker="building_cert_worker",
        stream="building",
        ok=True,
        scanned=3,
        alerts_created=1,
        adversary_passed=1,
        adversary_flagged=1,
    )
    b = WorkerResult(
        worker="vendor_cert_worker",
        stream="vendor",
        ok=True,
        scanned=2,
        blocks_set=1,
        adversary_escalated=1,
        adversary_failed=1,
    )
    m = merge_worker_results([a, b])
    assert m["building_scanned"] == 3
    assert m["vendor_scanned"] == 2
    assert m["alerts_created"] == 1
    assert m["blocks_set"] == 1
    assert m["adversary_flagged"] == 1
    assert m["adversary_escalated"] == 1
