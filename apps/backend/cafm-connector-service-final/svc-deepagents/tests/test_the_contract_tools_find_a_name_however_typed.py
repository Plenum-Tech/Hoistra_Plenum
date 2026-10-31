"""The contract tools find a vendor or a building however its name is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compares a typed name
with a stored one did it its own way. The contract tools had two of their own:

* vendor_name (scorecards, contracts, invoices) — every typed word had to be a whole word of the
  company name, so "the contractor Acme" and "Acme Fac" lost "Acme Facilities Ltd", while a code a
  digit away ("SL100234" for "SL100235") was taken as one wrong letter and handed another company.
* building_name (invoices) — passed to the building resolver once, as typed: "Kew building" missed
  "Kew" (too short for its containment rule), "B090765" missed B-090765, "tower hussain" missed
  "Hussain Tower", "Jasmet" missed JASMEET, "Building 7" was resolved to "Building 70", and "the
  building" resolved to whichever building has "building" in its name.
* list_contract_parameters had no building filter at all: the model compared building names itself.

They now rank by the shared ladder (agents/name_match.py), the one ontology_qa runs in SQL: exact,
compact, phrase at a word start, every word in any order, close spelling only when clearly closest.
A name shared by several records returns all of them; a phrase of only kind words names nothing.
"""
from __future__ import annotations

import re

import pytest

from src.agents import contract_performance_agent as cp


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


# ── vendors ──────────────────────────────────────────────────────────────────────────

ACME = "Acme Facilities Ltd"
GK = "Gough and Kelly Ltd"


def _scorecards(*names):
    return {"ok": True, "scorecards": [
        {"vendor_id": f"v{i}", "vendor_name": n, "overall_score": 70, "score_month": "2026-09-01"}
        for i, n in enumerate(names)
    ]}


def _fake(payload, calls: list | None = None):
    async def _req(method, base, path, **kwargs):
        if calls is not None:
            calls.append((path, dict(kwargs.get("params") or {})))
        return _Resp(payload)
    return _req


@pytest.mark.parametrize("typed", [
    "Acme", "acme facilities", "ACME FACILITIES LTD", "Acme Facilities Ltd.", "Acme Facilites",
    "the contractor Acme", "Acme Fac", "vendor Acme Facilities", "Acme Facilities Limited",
], ids=["first word", "lowercase", "shouting", "trailing dot", "typo", "kind word", "prefix",
        "kind word first", "limited for ltd"])
async def test_a_vendor_is_found_however_its_name_is_typed(monkeypatch, typed):
    monkeypatch.setattr(cp, "_request", _fake(_scorecards(ACME, GK)))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": typed})
    assert out["vendor_match"] == "one", out.get("answer_hint")
    assert [r["vendor_name"] for r in out["scorecards"]] == [ACME]


async def test_two_vendors_sharing_the_name_both_come_back(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(_scorecards(ACME, "Acme Lifts Ltd", GK)))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Acme"})
    assert out["vendor_match"] == "ambiguous"
    assert {r["vendor_name"] for r in out["scorecards"]} == {ACME, "Acme Lifts Ltd"}
    assert ACME in out["answer_hint"] and "Acme Lifts Ltd" in out["answer_hint"]


async def test_two_vendors_with_one_name_are_not_merged_into_one(monkeypatch):
    """Two companies registered as "Acme Facilities Ltd": the ids tell them apart."""
    monkeypatch.setattr(cp, "_request", _fake(_scorecards(ACME, ACME, GK)))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Acme"})
    assert out["vendor_match"] == "ambiguous"
    assert len(out["scorecards"]) == 2
    assert "v0" in out["answer_hint"] and "v1" in out["answer_hint"]


async def test_one_vendors_monthly_rows_are_one_vendor(monkeypatch):
    rows = {"ok": True, "scorecards": [
        {"vendor_id": "v0", "vendor_name": ACME, "score_month": m}
        for m in ("2026-08-01", "2026-09-01")]}
    monkeypatch.setattr(cp, "_request", _fake(rows))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Acme"})
    assert out["vendor_match"] == "one"
    assert len(out["scorecards"]) == 2


async def test_a_code_a_digit_away_is_another_company(monkeypatch):
    """"SL100234" is not "SL100235" with one wrong letter: a code is compared whole."""
    rows = _scorecards("SafeLift Engineering Ltd (SL100235)", GK)
    monkeypatch.setattr(cp, "_request", _fake(rows))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "SL100234"})
    assert out["vendor_match"] == "none"
    assert len(out["scorecards"]) == 2, "an unmatched name still returns the set"


async def test_the_full_name_beats_a_longer_one_that_contains_it(monkeypatch):
    rows = _scorecards(ACME, "Acme Facilities Ltd (Scotland)")
    monkeypatch.setattr(cp, "_request", _fake(rows))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "Acme Facilities Ltd"})
    assert [r["vendor_name"] for r in out["scorecards"]] == [ACME]


async def test_a_code_typed_with_a_stray_comma_still_finds_the_vendor(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(_scorecards("RT5, Crossrail", GK)))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": "RT5 Crossrail"})
    assert [r["vendor_name"] for r in out["scorecards"]] == ["RT5, Crossrail"]


@pytest.mark.parametrize("typed", ["details for the contractor", "the suppliers", "our vendor"])
async def test_a_phrase_of_only_fillers_and_kind_words_names_no_vendor(monkeypatch, typed):
    monkeypatch.setattr(cp, "_request", _fake(_scorecards(ACME, GK)))
    out = await cp.list_vendor_scorecards.ainvoke({"vendor_name": typed})
    assert len(out["scorecards"]) == 2
    assert "vendor_match" not in out, f"{typed!r} was used as a company name"


async def test_the_contract_and_invoice_tools_match_vendors_the_same_way(monkeypatch):
    contracts = {"ok": True, "parameters": [
        {"vendor_name": ACME, "contract_ref": "C-1"}, {"vendor_name": GK, "contract_ref": "C-2"}]}
    monkeypatch.setattr(cp, "_request", _fake(contracts))
    out = await cp.list_contract_parameters.ainvoke({"vendor_name": "the contractor Acme"})
    assert [p["vendor_name"] for p in out["parameters"]] == [ACME]

    invoices = {"ok": True, "invoices": [
        {"invoice_ref": "INV-1", "vendor_name": ACME, "flagged_count": 0},
        {"invoice_ref": "INV-2", "vendor_name": "Acme Lifts Ltd", "flagged_count": 0},
        {"invoice_ref": "INV-3", "vendor_name": GK, "flagged_count": 0}]}
    monkeypatch.setattr(cp, "_request", _fake(invoices))
    out = await cp.list_invoices.ainvoke({"vendor_name": "Acme Fac"})
    assert [r["invoice_ref"] for r in out["invoices"]] == ["INV-1"]
    out = await cp.list_invoices.ainvoke({"vendor_name": "acme"})
    assert out["vendor_match"] == "ambiguous"
    assert [r["invoice_ref"] for r in out["invoices"]] == ["INV-1", "INV-2"]


# ── buildings: list_invoices ─────────────────────────────────────────────────────────

JASMEET = {"building_id": "b-jas", "name": "JASMEET", "code": "B-090765"}
KEW = {"building_id": "b-kew", "name": "Kew", "code": "B-000012"}
HUSSAIN = {"building_id": "b-hus", "name": "Hussain Tower", "code": "B-000044"}
B70 = {"building_id": "b-70", "name": "Building 70", "code": "B-000070"}
B7E = {"building_id": "b-7e", "name": "Building 7 East", "code": "B-000071"}
HTN = {"building_id": "b-htn", "name": "Hussain Tower North", "code": "B-000045"}
HTS = {"building_id": "b-hts", "name": "Hussain Tower South", "code": "B-000046"}


def _norm(v):
    """svc-operations-intelligence compliance.site_links.normalize_name."""
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip())
    return s[4:] if s.startswith("the ") else s


def _ref(v):
    return re.sub(r"[^A-Z0-9]+", "", str(v or "").upper())


def _resolver(register):
    """GET /api/energy/buildings/resolve as building_resolver.choose_building answers it: an exact
    code, an exact normalised name, then containment for names of five letters or more."""
    def answer(params):
        def hit(b):
            return {"building_id": b["building_id"], "name": b["name"], "site_id": None}
        ref = _ref(params.get("code"))
        if len(ref) >= 2:
            hits = [b for b in register if _ref(b["code"]) == ref]
            if len(hits) == 1:
                return {"ok": True, "outcome": "resolved", "reason": "building_code",
                        "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
        n = _norm(params.get("name"))
        if n:
            hits = [b for b in register if _norm(b["name"]) == n]
            if len(hits) == 1:
                return {"ok": True, "outcome": "resolved", "reason": "name",
                        "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
            if len(hits) > 1:
                return {"ok": True, "outcome": "review", "reason": "ambiguous_name",
                        "building_id": None, "candidates": [hit(b) for b in hits]}
            if len(n) >= 5:
                hits = [b for b in register if len(_norm(b["name"])) >= 5
                        and abs(len(_norm(b["name"])) - len(n)) <= 25
                        and (n in _norm(b["name"]) or _norm(b["name"]) in n)]
                if len(hits) == 1:
                    return {"ok": True, "outcome": "resolved", "reason": "name_contains",
                            "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
                if len(hits) > 1:
                    return {"ok": True, "outcome": "review", "reason": "ambiguous_name_contains",
                            "building_id": None, "candidates": [hit(b) for b in hits]}
        return {"ok": True, "outcome": "unmatched", "reason": "no_match", "building_id": None,
                "candidates": []}
    return answer


def _invoice(ref, b):
    return {"invoice_ref": ref, "building_id": b["building_id"], "building_name": b["name"],
            "building_reference": b["code"], "vendor_name": ACME, "flagged_count": 0}


class _Service:
    """The resolver over ``register``; the invoice listing over ``invoices``, narrowed by
    building_id the way the service narrows it."""

    def __init__(self, register, invoices):
        self.resolve = _resolver(register)
        self.invoices = invoices
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, base, path, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append((path, params))
        if path.endswith("/buildings/resolve"):
            return _Resp(self.resolve(params))
        if path.endswith("/contract-performance/invoices"):
            rows = [r for r in self.invoices
                    if not params.get("building_id") or r["building_id"] == params["building_id"]]
            return _Resp({"ok": True, "count": len(rows), "invoices": rows})
        raise AssertionError(f"unexpected path {path}")

    def resolver_calls(self):
        return [p for path, p in self.calls if path.endswith("/buildings/resolve")]


REGISTER = [JASMEET, KEW, HUSSAIN, B70]
INVOICES = [_invoice("INV-J", JASMEET), _invoice("INV-K", KEW), _invoice("INV-H", HUSSAIN),
            _invoice("INV-70", B70)]


@pytest.mark.parametrize("typed,ref", [
    ("JASMEET BUILDING", "INV-J"),
    ("Kew building", "INV-K"),
    ("B090765", "INV-J"),
    ("tower hussain", "INV-H"),
    ("Jasmet", "INV-J"),
], ids=["kind word", "short name with kind word", "code without hyphen", "word order", "typo"])
async def test_a_buildings_invoices_are_found_however_it_is_typed(monkeypatch, typed, ref):
    svc = _Service(REGISTER, INVOICES)
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": typed})
    assert "error" not in out, out
    assert [r["invoice_ref"] for r in out["invoices"]] == [ref]


async def test_building_7_is_not_building_70(monkeypatch):
    svc = _Service(REGISTER, INVOICES)
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "Building 7"})
    assert out.get("error") == "building_not_resolved", out
    assert "INV-70" not in str(out)


async def test_a_false_containment_hit_does_not_make_a_real_match_ambiguous(monkeypatch):
    svc = _Service([B70, B7E], [_invoice("INV-70", B70), _invoice("INV-7E", B7E)])
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "Building 7"})
    assert [r["invoice_ref"] for r in out["invoices"]] == ["INV-7E"]


async def test_the_building_names_no_building(monkeypatch):
    svc = _Service(REGISTER, INVOICES)
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "the building"})
    assert out.get("error") == "building_not_resolved"
    assert svc.resolver_calls() == [], "a phrase of only kind words must not be looked up"
    assert "invoices" not in out


async def test_two_buildings_matching_the_name_both_come_back_and_neither_is_picked(monkeypatch):
    svc = _Service([HTN, HTS], [_invoice("INV-N", HTN), _invoice("INV-S", HTS)])
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "tower hussain"})
    assert out.get("error") == "building_not_resolved"
    assert out["outcome"] == "review"
    assert {c["name"] for c in out["candidates"]} == {"Hussain Tower North", "Hussain Tower South"}
    assert "invoices" not in out


async def test_an_ambiguous_resolver_answer_is_still_passed_on(monkeypatch):
    svc = _Service([HTN, HTS], [])
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "Hussain Tower"})
    assert out["outcome"] == "review"
    assert {c["name"] for c in out["candidates"]} == {"Hussain Tower North", "Hussain Tower South"}


async def test_a_building_found_only_by_its_invoices_says_so(monkeypatch):
    svc = _Service(REGISTER, INVOICES)
    monkeypatch.setattr(cp, "_request", svc)
    out = await cp.list_invoices.ainvoke({"building_name": "tower hussain"})
    assert "Hussain Tower" in out["building_note"]


# ── buildings: list_contract_parameters ──────────────────────────────────────────────

def _contract(ref, b):
    return {"contract_ref": ref, "vendor_name": ACME, "building_id": b and b["building_id"],
            "building_name": b and b["name"], "building_reference": b and b["code"]}


CONTRACTS = {"ok": True, "parameters": [
    _contract("C-J", JASMEET), _contract("C-H", HUSSAIN), _contract("C-70", B70),
    _contract("C-U", None)]}


@pytest.mark.parametrize("typed,ref", [
    ("JASMEET BUILDING", "C-J"), ("B090765", "C-J"), ("tower hussain", "C-H"),
    ("Jasmet", "C-J"), ("Building 70", "C-70"),
])
async def test_a_buildings_contracts_are_found_however_it_is_typed(monkeypatch, typed, ref):
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS))
    out = await cp.list_contract_parameters.ainvoke({"building_name": typed})
    assert out["building_match"] == "one", out.get("building_hint")
    assert [p["contract_ref"] for p in out["parameters"]] == [ref]


async def test_a_building_with_no_contract_is_not_handed_another(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS))
    out = await cp.list_contract_parameters.ainvoke({"building_name": "Building 7"})
    assert out["building_match"] == "none"
    assert len(out["parameters"]) == 4, "the register must still come back"
    hint = out["building_hint"]
    assert "Building 7" in hint and "unplaced" in hint.lower()


async def test_two_buildings_sharing_the_name_are_both_returned(monkeypatch):
    two = {"ok": True, "parameters": [_contract("C-N", HTN), _contract("C-S", HTS)]}
    monkeypatch.setattr(cp, "_request", _fake(two))
    out = await cp.list_contract_parameters.ainvoke({"building_name": "Hussain Tower"})
    assert out["building_match"] == "ambiguous"
    assert [p["contract_ref"] for p in out["parameters"]] == ["C-N", "C-S"]


async def test_the_building_is_not_a_contract_filter(monkeypatch):
    monkeypatch.setattr(cp, "_request", _fake(CONTRACTS))
    out = await cp.list_contract_parameters.ainvoke({"building_name": "the building"})
    assert len(out["parameters"]) == 4
    assert "building_match" not in out
