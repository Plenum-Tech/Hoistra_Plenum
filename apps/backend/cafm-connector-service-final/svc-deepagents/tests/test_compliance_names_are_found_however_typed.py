"""A building, vendor, inspector or document named in a compliance question is found however it is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compares a typed name
with a stored one did it its own way. The compliance tools had four of them:

* list_building_certificates / list_vendor_accreditations kept every row a name touched by
  substring or one-letter closeness, so "JASMEET" also returned "Jasmeet Heights", "Acme" also
  returned "Pacme Systems Ltd", and "the building" returned "The Building Centre".
* count_compliance_certificates handed the typed text to the service's substring test, so
  "ACME Facilities Limited" counted 0 certificates of "Acme Facilities Ltd", "Okafor James" 0 of
  "James Okafor", and "Acme" counted Pacme's certificates too.
* manage_vector_membership dropped every word of one or two letters, so "Building 7" matched every
  "Building ..." document (Building 70 too), and "B090765" never met "B-090765_FRA.pdf".
* the deterministic answer re-filtered vendor rows by substring (so "Acme Facilities Limited"
  lost "Acme Facilities Ltd") and took any shared word, "building" included, for a named vendor.

Every one now ranks by the shared ladder (agents/name_match.py): exact, same letters and digits,
phrase, every word in any order, close spelling — exact beats partial, several equal matches are
all returned, and a phrase of kind words alone ("the building") names nothing.
No service and no database is touched: the HTTP client is replaced.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import compliance_engine_agent as cea
from src.agents.compliance_answer import (
    _query_mentions_vendor,
    build_deterministic_compliance_answer,
)


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _run(coro):
    return asyncio.run(coro)


# ── list_building_certificates(building_name=...) ─────────────────────────────────────────────

BUILDING_ROWS = [
    {"id": "c1", "building_name": "JASMEET", "certificate_number": "EICR-0907"},
    {"id": "c2", "building_name": "Jasmeet Heights", "certificate_number": "FRA-1100"},
    {"id": "c3", "building_name": "hussain tower", "certificate_number": "GAS-3001"},
    {"id": "c4", "building_name": "RT5, Crossrail", "certificate_number": "LOLER-55"},
    {"id": "c5", "building_name": "B-090765", "certificate_number": "EPC-0765"},
    {"id": "c6", "building_name": "Building 7", "certificate_number": "EICR-0007"},
    {"id": "c7", "building_name": "Building 70", "certificate_number": "EICR-0070"},
    {"id": "c8", "building_name": "The Building Centre", "certificate_number": "FRA-0008"},
    {"id": "c9", "building_name": "Bishopsgate Tower", "certificate_number": "FRA-0009"},
    {"id": "c10", "building_name": "JASMEET", "certificate_number": "GAS-0907"},
]


def _buildings(typed):
    out = cea._filter_by_field({"ok": True, "count": len(BUILDING_ROWS), "certificates": list(BUILDING_ROWS)},
                               "building_name", typed)
    return out, sorted(r["id"] for r in out["certificates"])


@pytest.mark.parametrize("typed,ids", [
    ("JASMEET BUILDING", ["c1", "c10"]),      # the kind word beside the name; not Jasmeet Heights
    ("jasmeet", ["c1", "c10"]),               # an exact name beats the name it starts
    ("Jasmet", ["c1", "c10"]),                # one letter missing: the clearly closest only
    ("Jasmeet Heights", ["c2"]),
    ("tower hussain", ["c3"]),                # another word order
    ("RT5 Crossrail", ["c4"]),                # without its comma
    ("B090765", ["c5"]),                      # a code without its hyphen
    ("Building 7", ["c6"]),                   # never Building 70
    ("tower", ["c3", "c9"]),                  # two equal matches: both, never one picked
])
def test_a_building_is_found_however_it_is_typed(typed, ids):
    out, got = _buildings(typed)
    assert got == ids
    assert out["building_name_match"].get("no_match") is not True


def test_the_building_alone_names_no_building():
    out, got = _buildings("the building")
    match = out["building_name_match"]
    assert match["no_match"] is True and match["matched_values"] == []
    assert len(got) == len(BUILDING_ROWS)          # every row back, for the model to decide


def test_a_close_spelling_and_a_shared_name_are_said_so():
    out, _ = _buildings("Jasmet")
    assert out["building_name_match"]["matched_by"] == "close spelling"
    assert "JASMEET" in out["building_name_match"]["note"]
    out, _ = _buildings("tower")
    assert sorted(out["building_name_match"]["matched_values"]) == ["Bishopsgate Tower", "hussain tower"]
    assert "never pick one" in out["building_name_match"]["note"]


def test_building_7_is_never_building_70():
    rows = [r for r in BUILDING_ROWS if r["id"] != "c6"]
    out = cea._filter_by_field({"certificates": rows}, "building_name", "Building 7")
    assert out["building_name_match"]["no_match"] is True


# ── vendor_name on list_vendor_accreditations / list_building_certificates ────────────────────

VENDOR_ROWS = [
    {"id": "v1", "vendor_name": "Acme Facilities Ltd", "certificate_number": "NICEIC-1"},
    {"id": "v2", "vendor_name": "Acme Facilities Ltd North", "certificate_number": "NICEIC-2"},
    {"id": "v3", "vendor_name": "Pacme Systems Ltd", "certificate_number": "GS-3"},
    {"id": "v4", "vendor_name": "RDM Electrical Services Ltd", "certificate_number": "NIC-4"},
    {"id": "v5", "vendor_name": "Kurt J. Lesker Company", "certificate_number": "ISO-5"},
    {"id": "v6", "vendor_name": "Supplier Direct Ltd", "certificate_number": "CHAS-6"},
    {"id": "v7", "vendor_name": "C&H Electrical Ltd", "certificate_number": "NIC-7"},
]


def _vendors(typed, rows=VENDOR_ROWS):
    out = cea._filter_by_field({"certificates": list(rows)}, "vendor_name", typed)
    return out, sorted(r["id"] for r in out["certificates"])


@pytest.mark.parametrize("typed,ids", [
    ("Acme Facilities Limited", ["v1"]),      # Limited is Ltd; the exact name beats "... North"
    ("acme facilities ltd", ["v1"]),
    ("R.D.M. Electrcal", ["v4"]),             # initials with dots, a letter missing
    ("Kurt Lesker", ["v5"]),
    ("C & H Electrical", ["v7"]),
    ("Pacme", ["v3"]),
])
def test_a_vendor_is_found_however_it_is_typed(typed, ids):
    assert _vendors(typed)[1] == ids


def test_acme_finds_acme_facilities_and_never_pacme():
    rows = [r for r in VENDOR_ROWS if r["id"] != "v2"]
    out, got = _vendors("Acme", rows)
    assert got == ["v1"]
    assert out["vendor_name_match"]["matched_values"] == ["Acme Facilities Ltd"]


def test_a_vendor_kind_word_alone_names_no_vendor():
    out, _ = _vendors("the supplier")
    assert out["vendor_name_match"]["no_match"] is True


def test_the_tools_filter_what_the_service_returned(monkeypatch):
    calls = []

    async def fake(method, base, path, **kw):
        calls.append(dict(kw.get("params") or {}))
        rows = VENDOR_ROWS if kw["params"]["cert_scope"] == "Vendor" else BUILDING_ROWS
        return _Resp({"ok": True, "count": len(rows), "certificates": list(rows)})

    monkeypatch.setattr(cea, "_request", fake)
    out = _run(cea.list_vendor_accreditations.ainvoke({"vendor_name": "ACME FACILITIES LIMITED"}))
    assert [r["id"] for r in out["certificates"]] == ["v1"]
    out = _run(cea.list_building_certificates.ainvoke({"building_name": "JASMEET BUILDING"}))
    assert sorted(r["id"] for r in out["certificates"]) == ["c1", "c10"]
    assert "vendor_name" not in calls[0] and "building_name" not in calls[1]   # matched here, not by substring


# ── count_compliance_certificates(vendor_name_contains / inspector_name / issuer_contains) ──────

def _register(*rows):
    out = []
    for i, (vendor, inspector, issuer) in enumerate(rows):
        out.append({"id": "r" + str(i), "cert_scope": "Vendor", "vendor_name": vendor,
                    "inspector_name": inspector, "issuer": issuer, "issuing_body": None,
                    "inspector_accreditation_number": None, "certificate_number": "N-" + str(i)})
    return out


def _service(monkeypatch, register):
    """operations-intelligence: the list, and the count's own substring filters."""
    calls = []

    async def fake(method, base, path, **kw):
        params = dict(kw.get("params") or {})
        calls.append((path, params))
        if path == "/api/compliance/certificates":
            return _Resp({"ok": True, "count": len(register), "certificates": list(register)})
        assert path == "/api/compliance/certificates/count"

        def hit(r):
            for p, col in (("vendor_name_contains", "vendor_name"), ("inspector_name", "inspector_name")):
                if params.get(p) and params[p].strip().lower() not in (r.get(col) or "").lower():
                    return False
            if params.get("issuer_contains"):
                blob = " ".join(str(r.get(c) or "") for c in ("issuer", "issuing_body",
                                                              "inspector_accreditation_number")).lower()
                if params["issuer_contains"].strip().lower() not in blob:
                    return False
            return True

        rows = [r for r in register if hit(r)]
        return _Resp({"ok": True, "count": len(rows), "matches": [
            {"id": r["id"], "vendor_name": r["vendor_name"], "inspector_name": r["inspector_name"],
             "issuer": r["issuer"], "certificate_number": r["certificate_number"]} for r in rows[:50]]})

    monkeypatch.setattr(cea, "_request", fake)
    return calls


def _count(**args):
    return _run(cea.count_compliance_certificates.ainvoke(args))


def test_a_vendor_typed_with_limited_is_counted(monkeypatch):
    _service(monkeypatch, _register(("Acme Facilities Ltd", None, None), ("Acme Facilities Ltd", None, None),
                                    ("Pacme Systems Ltd", None, None)))
    out = _count(vendor_name_contains="ACME Facilities Limited", cert_scope="Vendor")
    assert out["count"] == 2
    assert out["vendor_name_match"]["matched_values"] == ["Acme Facilities Ltd"]


def test_acme_counts_acme_facilities_and_never_pacme(monkeypatch):
    calls = _service(monkeypatch, _register(("Acme Facilities Ltd", None, None), ("Pacme Systems Ltd", None, None)))
    out = _count(vendor_name_contains="Acme", cert_scope="Vendor")
    assert out["count"] == 1
    listing = next(p for path, p in calls if path == "/api/compliance/certificates")
    assert listing.get("cert_scope") == "Vendor" and "vendor_name_contains" not in listing


def test_an_exact_vendor_is_not_counted_with_a_longer_namesake(monkeypatch):
    _service(monkeypatch, _register(("Acme Facilities Ltd", None, None), ("Acme Facilities Ltd", None, None),
                                    ("Acme Facilities Ltd North", None, None)))
    out = _count(vendor_name_contains="Acme Facilities Ltd")
    assert out["count"] == 2


def test_a_count_the_service_could_not_list_whole_says_it_is_approximate(monkeypatch):
    register = _register(*([("Acme Facilities Ltd", None, None)] * 60 + [("Acme Facilities Ltd North", None, None)] * 3))
    _service(monkeypatch, register)
    out = _count(vendor_name_contains="acme facilities ltd")
    assert out["count"] == 63 and out["approximate"] is True
    assert "Acme Facilities Ltd North" in out["note"]


def test_an_inspector_typed_surname_first_is_counted(monkeypatch):
    _service(monkeypatch, _register(("Acme Facilities Ltd", "James Okafor", None), ("Acme Facilities Ltd", "Ann Lee", None)))
    out = _count(inspector_name="Okafor James")
    assert out["count"] == 1
    assert out["inspector_name_match"]["matched_values"] == ["James Okafor"]


def test_two_vendors_sharing_a_name_are_both_counted_and_named(monkeypatch):
    _service(monkeypatch, _register(("Acme Facilities Ltd", None, None), ("Acme Lifts Ltd", None, None),
                                    ("Acme Lifts Ltd", None, None)))
    out = _count(vendor_name_contains="Acme")
    assert out["count"] == 3
    assert sorted(out["vendor_name_match"]["matched_values"]) == ["Acme Facilities Ltd", "Acme Lifts Ltd"]


def test_a_vendor_kind_word_alone_counts_nothing(monkeypatch):
    _service(monkeypatch, _register(("Supplier Direct Ltd", None, None)))
    out = _count(vendor_name_contains="the supplier")
    assert out["count"] == 0 and out["vendor_name_match"]["no_match"] is True


def test_an_issuer_is_counted_by_its_register_name(monkeypatch):
    _service(monkeypatch, _register(("Acme Facilities Ltd", None, "NICEIC"), ("Acme Facilities Ltd", None, "Gas Safe")))
    out = _count(issuer_contains="niceic")
    assert out["count"] == 1


def test_a_count_without_a_name_is_one_call_as_before(monkeypatch):
    calls = _service(monkeypatch, _register(("Acme Facilities Ltd", None, None)))
    out = _count(status="Lapsed")
    assert out["count"] == 1 and [path for path, _ in calls] == ["/api/compliance/certificates/count"]


# ── manage_vector_membership(query=...) ─────────────────────────────────────────────────────────

DOCS = [
    {"document_id": "d1", "certificate_type_name": "Electrical Installation Condition Report",
     "certificate_type_code": "EICR", "vendor_name": "Acme Facilities Ltd",
     "raw_metadata": {"source_filename": "Building 7 EICR.pdf"}},
    {"document_id": "d2", "certificate_type_name": "Electrical Installation Condition Report",
     "certificate_type_code": "EICR", "vendor_name": "Pacme Systems Ltd",
     "raw_metadata": {"source_filename": "Building 70 EICR.pdf"}},
    {"document_id": "d3", "certificate_type_name": "Fire Risk Assessment", "certificate_type_code": "FRA",
     "vendor_name": "Northgate Fire Ltd", "raw_metadata": {"source_filename": "B-090765_FRA.pdf"}},
    {"document_id": "d4", "certificate_type_name": "Air Conditioning Inspection Report (TM44)",
     "certificate_type_code": "TM44", "vendor_name": "Coolair Ltd",
     "raw_metadata": {"source_filename": "JASMEET_TM44.pdf"}},
]


def _membership(monkeypatch, query):
    async def fake(method, base, path, **kw):
        docs = DOCS if kw["params"]["cert_scope"] == "Building" else []
        return _Resp({"ok": True, "certificates": list(docs)})

    monkeypatch.setattr(cea, "_request", fake)
    out = _run(cea.manage_vector_membership.ainvoke({"query": query}))
    return sorted(i["document_id"] for i in out["items"])


@pytest.mark.parametrize("query,ids", [
    ("Building 7", ["d1"]),                   # never Building 70
    ("B090765", ["d3"]),                      # a code typed without its hyphen, inside a filename
    ("Jasmet", ["d4"]),                       # one letter missing
    ("Acme", ["d1"]),                         # never Pacme
    ("air conditioning", ["d4"]),
    ("TM44", ["d4"]),
    ("report", []),                           # a kind word alone names no document
    ("", ["d1", "d2", "d3", "d4"]),           # empty: manage them all, as before
])
def test_a_document_is_found_however_it_is_named(monkeypatch, query, ids):
    assert _membership(monkeypatch, query) == ids


def test_the_single_document_test_agrees():
    assert cea._match_membership_query("Building 7", DOCS[0]) is True
    assert cea._match_membership_query("Building 7", DOCS[1]) is False


# ── the deterministic answer ────────────────────────────────────────────────────────────────────

def _vendor_tool(rows, **tool_input):
    return {"tool": "list_vendor_accreditations", "input": tool_input, "output": {"certificates": rows}}


def _accreditation(vendor, number, vendor_id=None):
    return {"vendor_name": vendor, "vendor_id": vendor_id, "certificate_type_name": "NICEIC Approved Contractor",
            "certificate_number": number, "status": "Current", "expiry_date": "2027-01-01", "days_to_expiry": 64}


def test_the_answer_keeps_a_vendor_typed_with_limited():
    tools = [_vendor_tool([_accreditation("Acme Facilities Ltd", "NICEIC-1")], vendor_name="Acme Facilities Limited")]
    answer = build_deterministic_compliance_answer("compliance status of Acme Facilities Limited", tools)
    assert "Compliance status — Acme Facilities Ltd" in answer
    assert "NICEIC-1" in answer


def test_the_answer_keeps_a_vendor_asked_for_by_its_id():
    vid = "6f1c2b9e-1d1a-4c55-9a51-0e6c3c1f7a10"
    tools = [_vendor_tool([_accreditation("Acme Facilities Ltd", "NICEIC-1", vendor_id=vid)], vendor_id=vid)]
    answer = build_deterministic_compliance_answer("compliance status of this vendor", tools)
    assert "NICEIC-1" in answer


def test_the_answer_names_every_vendor_a_shared_name_matched():
    tools = [_vendor_tool([_accreditation("Acme Facilities Ltd", "NICEIC-1"), _accreditation("Acme Lifts Ltd", "LOLER-2")],
                          vendor_name="Acme")]
    answer = build_deterministic_compliance_answer("compliance status of Acme", tools)
    assert "Acme Facilities Ltd" in answer.split("\n")[0] and "Acme Lifts Ltd" in answer.split("\n")[0]
    assert "NICEIC-1" in answer and "LOLER-2" in answer


def test_a_vendor_kind_word_alone_is_not_a_named_vendor():
    tools = [_vendor_tool([_accreditation("Supplier Direct Ltd", "CHAS-6")], vendor_name="the supplier")]
    answer = build_deterministic_compliance_answer("which vendor accreditations are current", tools) or ""
    assert "No accreditation certificates were found for **the supplier**" not in answer


@pytest.mark.parametrize("question,vendor,named", [
    ("Which certificates does JASMEET BUILDING hold?", "Building Compliance Services Ltd", False),
    ("fire certificates for hussain tower", "Fire Safety Ltd", False),
    ("what does Fire Safety Ltd hold?", "Fire Safety Ltd", True),
    ("AIB Solutions Limited - what is the compliance status", "AIB Solutions Limited", True),
    ("certs for Lesker", "Kurt J. Lesker Company", True),
    ("show Acme's accreditations", "Acme Facilities Ltd", True),
])
def test_a_question_names_a_vendor_by_its_own_name_not_by_a_word_any_name_has(question, vendor, named):
    assert _query_mentions_vendor(question, {"vendor_name": vendor}) is named


def test_a_building_question_keeps_the_building_rows_when_a_vendor_shares_a_word():
    tools = [
        {"tool": "list_building_certificates", "input": {"building_name": "JASMEET BUILDING"}, "output": {
            "certificates": [{"building_name": "JASMEET", "certificate_type_name": "EICR", "certificate_number": "EICR-0907",
                              "status": "Current", "expiry_date": "2027-02-01", "days_to_expiry": 95}]}},
        _vendor_tool([_accreditation("Building Compliance Services Ltd", "NICEIC-9")]),
    ]
    answer = build_deterministic_compliance_answer("Which building certificates does JASMEET BUILDING hold?", tools) or ""
    assert "Compliance status — Building Compliance Services Ltd" not in answer
    assert "EICR-0907" in answer
