"""Rules S-3, S-4 and S-5 of the approved SPEC.md (Trace Lab, 4 Nov 2026).

R33-12 "Which vendors are not eligible to work here?" (Compliance Engine, Plenum Technologies) answered "five vendors are
explicitly blocked ... the dashboard reports nine blocked vendors ... the other four need checking directly in the vendor
list", as one paragraph. The records: 5 vendors carry the block flag; 4 more hold a lapsed accreditation but are not
blocked (5 + 4 = 9). S-5: blocked is the vendor's block flag; at-risk vendors are a separate list. S-4: a which/list
question is answered as a short list. The compliance engine had no tool that reads the block flag.

R36-1 counted WO-B-102-4602, a landing-door repair, as the LOLER examination already raised. S-3: only a work order for the
inspection itself covers it.
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib

from src.agents import compliance_engine_agent as ce
from src.agents import contract_performance_agent as cp

SKILLS = pathlib.Path(__file__).resolve().parents[1] / "skills" / "compliance"


def _session(monkeypatch, answers):
    from src import database

    seen = []

    class _R:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            s = str(sql)
            seen.append(s)
            for marker, rows in answers:
                if marker in s:
                    return _R(rows)
            return _R([])

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _S())
    return seen


def test_blocked_and_at_risk_vendors_are_two_lists(monkeypatch):
    from src.http_client import caller_organization_id

    seen = _session(monkeypatch, [
        ("compliance_certificates", [{"vendor_name": "Apex Mechanical", "lapsed": "CHAS_SSIP", "since": "2026-10-25"}]),
        ("FROM plenum_cafm.vendors v", [{"vendor_name": "Pennard Fire Services",
                                         "blocked_accreditation_type": "BAFE SP203-1 Registration Certificate"}]),
    ])
    tok = caller_organization_id.set("ad88d1ed-4376-447c-8196-e4e5b81d5af6")
    try:
        out = asyncio.run(getattr(cp.list_blocked_vendors, "coroutine", cp.list_blocked_vendors)())
    finally:
        caller_organization_id.reset(tok)
    assert [b["vendor"] for b in out["blocked"]] == ["Pennard Fire Services"]
    assert out["at_risk"] == [{"vendor": "Apex Mechanical", "lapsed": "CHAS_SSIP", "since": "2026-10-25"}]
    rules = out["answer_rules"].lower()
    assert "two lists" in rules and "never" in rules and "short list" in rules
    cert_sql = next(s for s in seen if "compliance_certificates" in s)
    assert "expiry_date" in cert_sql and "block_state" in cert_sql and "organization_id" in cert_sql


def test_the_compliance_engine_can_read_the_block_flag():
    assert cp.list_blocked_vendors in ce.COMPLIANCE_ENGINE_TOOLS


def test_only_a_work_order_for_the_inspection_covers_it(monkeypatch):
    from src.agents import wo_sla

    async def rows(sql, params):
        return [{"asset_id": "lift-01", "wo_code": "WO-B-102-4602", "status": "Open",
                 "title": "Landing door lock fault - lift 2"},
                {"asset_id": "lift-01", "wo_code": "WO-B-102-4700", "status": "Draft",
                 "title": "LOLER thorough examination for lift 1"}]

    monkeypatch.setattr(wo_sla, "_rows", rows)
    monkeypatch.setattr(wo_sla, "_scope", lambda: "org-1")
    data = {"certificates": [{"certificate_number": "LOLER-B-102-LIFT-01", "certificate_type_code": "LOLER",
                              "asset_id": "lift-01", "days_to_expiry": -8, "expiry_date": "2026-10-27"}]}
    out = asyncio.run(ce._with_due_and_work_orders(data))
    wos = {w["wo_code"]: w["covers_inspection"] for w in out["certificates"][0]["open_work_orders"]}
    assert wos == {"WO-B-102-4602": False, "WO-B-102-4700": True}
    assert "covers_inspection" in out["status_rule"]


def test_the_skills_carry_the_list_and_for_each_rules():
    text = (SKILLS / "answering.md").read_text(encoding="utf-8").lower()
    assert "short list" in text and "for each" in text and "missing" in text


def test_the_skills_define_blocked_as_the_flag_not_the_badge():
    """The compliance skill called a lapsed accreditation 'blocked' (the risk badge), so the rerun of R33-12 still merged
    the two lists. S-5 is the definition now, in every skill file the engine reads."""
    for name in ("core.md", "tool-routing.md", "tool-selection.md"):
        text = (SKILLS / name).read_text(encoding="utf-8")
        assert "a vendor holding a lapsed or blocked accreditation" not in text, name
        assert "list_blocked_vendors" in text, name
    assert "risk badge" not in (SKILLS / "tool-routing.md").read_text(encoding="utf-8").lower()
