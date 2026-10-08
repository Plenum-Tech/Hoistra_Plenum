"""The compliance analyst reads every certificate, sized by need.

Measured 6 Oct 2026: the analyst, reviewer and revision calls were 42% of model spend, and
Plenum's 70-certificate register (94,869 characters) was already over the 90,000-character cap,
so certificates were dropped from the end before the analyst saw them. The digest keeps the 22
that need attention in full and puts the 48 current ones on one line each: 60% smaller, nothing
dropped.
"""
from __future__ import annotations

import json

from src.agents import register_digest as rd
from src.agents.context_budget import MODE


def cert(i, **kw):
    row = {"id": f"c{i}", "cert_scope": "Building", "certificate_type_code": "EICR", "certificate_number": f"N-{i}",
           "building_name": "Bishopsgate Tower", "expiry_date": "2027-06-30", "status": "Current", "days_to_expiry": 267,
           "issuer": "Acme Testing Ltd", "notes": "annual inspection, no observations " * 5}
    row.update(kw)
    return row


REGISTER = ([cert(i) for i in range(20)]
            + [cert(100, status="Lapsed", days_to_expiry=-12, expiry_date="2026-09-24"),
               cert(101, status="Expiring soon", days_to_expiry=20),
               cert(102, cert_scope="Vendor", vendor_name="Pennard Fire", vendor_block_state="Blocked"),
               cert(103, forensics_verdict="FAIL", forensics_risk_score=80),
               cert(104, expiry_date=None, days_to_expiry=None)])


def test_every_certificate_is_in_the_digest_and_none_is_dropped():
    d = rd.digest(REGISTER)
    assert d["total"] == len(REGISTER) == d["attention_count"] + len(d["current"])
    ids = {r["id"] for r in d["attention"]} | {line.split(" | ")[0] for line in d["current"]}
    assert ids == {r["id"] for r in REGISTER}


def test_what_needs_attention_stays_whole_with_the_reason():
    d = rd.digest(REGISTER)
    why = {r["id"]: r["needs_attention_because"] for r in d["attention"]}
    assert why == {"c100": ["lapsed"], "c101": ["expiring"], "c102": ["blocked vendor"],
                   "c103": ["authenticity"], "c104": ["no expiry on record"]}
    full = next(r for r in d["attention"] if r["id"] == "c100")
    assert full["issuer"] == "Acme Testing Ltd" and "notes" in full          # every field kept
    assert d["blocked_vendors"] == ["Pennard Fire"]


def test_a_current_certificate_is_one_quotable_line():
    line = next(line for line in rd.digest(REGISTER)["current"] if line.startswith("c3 |"))
    assert line == "c3 | Building | EICR | N-3 | Bishopsgate Tower | Bishopsgate Tower | 2027-06-30 | Current | 267"


def test_the_digest_is_much_smaller_than_the_rows():
    full = len(json.dumps(REGISTER))
    small = len(json.dumps(rd.digest(REGISTER)))
    assert small < full * 0.6


def test_a_small_list_or_rows_that_are_not_certificates_pass_through_unchanged():
    few = [cert(i) for i in range(5)]
    assert rd.maybe_digest(few) is few
    others = [{"name": f"type {i}"} for i in range(30)]
    assert rd.maybe_digest(others) is others


def _orch():
    import src.agents.orchestrator as O
    cls = next(v for v in vars(O).values() if isinstance(v, type) and hasattr(v, "_compliance_data_json"))
    return cls.__new__(cls)


def test_the_analyst_data_carries_the_digest():
    data = json.loads(_orch()._compliance_data_json(
        [{"tool": "list_building_certificates", "output": {"certificates": REGISTER}}]))
    block = data[0]["data"]["certificates"]
    assert block["register_digest"] is True and block["total"] == len(REGISTER)


def test_a_skill_lab_reference_replay_gets_the_whole_register_as_before():
    token = MODE.set("trim")
    try:
        data = json.loads(_orch()._compliance_data_json(
            [{"tool": "list_building_certificates", "output": {"certificates": REGISTER}}]))
    finally:
        MODE.reset(token)
    assert isinstance(data[0]["data"]["certificates"], list) and len(data[0]["data"]["certificates"]) == len(REGISTER)
