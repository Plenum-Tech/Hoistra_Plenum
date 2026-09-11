"""The check that runs before a document is bound to a building.

The risk these pin is ordinary: a contract filed under the wrong property, a vendor
certificate for a firm that has never worked there, a Bishopsgate document uploaded under
Riverside. Each is one click to make and months to notice.

Three rules carry most of the weight here, and each has a test that would fail if it were
relaxed:

  * a document is *matched* only when something IDENTIFIES the building — its name, its
    reference, its postcode. A country that agrees is not identity: half the portfolio is in
    the same country.
  * "the vendor is not one of this building's" and "this building has no vendors on record"
    are different answers, and only the first is a warning.
  * a new building confirms nothing, so its first document is uncertain, never matched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.engines.ingestion import claims as C
from src.engines.ingestion import validation as V
from src.engines.ingestion.ontology import BuildingOntology

RIVERSIDE = "f12e9629-95ed-5722-80cc-a1397f627850"
BISHOPSGATE = "4791b70b-862a-5a75-8d24-5fe13d039131"


def riverside(**over) -> BuildingOntology:
    o = BuildingOntology(
        building_id=RIVERSIDE, name="Riverside Court",
        names=["Riverside Court", "Riverside Court, Manchester"], codes=["B-006"],
        country_code="UK", country="United Kingdom", region="Greater Manchester",
        postcode="M3 4LZ", vendors=["Aqua Services Ltd", "Northern Fire Safety Ltd"],
        assets=["B-006-AHU-01", "B-006-CHILLER-01", "Air Handling Unit 1"],
        certificate_types=["EICR"], certificate_numbers=["EICR-2024-11"],
        contract_refs=["CN-2024-0042"], document_names=["EICR 2024.pdf"],
        counts={"assets": 6, "documents": 5, "certificates": 1, "vendors": 2,
                "work_orders": 12, "meters": 2, "floors": 3, "contracts": 1},
    )
    for k, v in over.items():
        setattr(o, k, v)
    return o


def new_building() -> BuildingOntology:
    return BuildingOntology(building_id=str(uuid4()), name="Harbour View",
                            names=["Harbour View"], codes=[], country_code="UK", counts={})


def find(findings, check):
    return next((f for f in findings if f.check == check), None)


# ── reading the document ────────────────────────────────────────────────────────────

class TestClaims:
    def test_a_labelled_property_is_a_claim(self):
        c = C.from_document(text="Site: Bishopsgate Tower\nWorks carried out in June.")
        assert c.buildings == ["Bishopsgate Tower"]

    def test_an_unlabelled_name_is_not(self):
        # A bare name in a contract is as often the counterparty's address as the site's.
        c = C.from_document(text="This agreement is made at Bishopsgate Tower between …")
        assert c.buildings == []

    def test_the_vendor_is_read_from_its_label_and_from_a_company_suffix(self):
        c = C.from_document(text="Contractor: Aqua Services Ltd\nIssued by Northern Fire Safety Ltd")
        assert any("Aqua Services" in v for v in c.vendors)

    def test_country_names_become_codes(self):
        c = C.from_document(text="Registered in Singapore for works in Singapore.")
        assert c.countries == ["SG"]

    def test_a_uk_postcode_and_asset_codes_are_picked_up(self):
        c = C.from_document(text="Site address: 4 Quay St, Manchester M3 4LZ. Plant: B-006-AHU-01.")
        assert "M3 4LZ" in c.postcodes
        assert "B-006-AHU-01" in c.assets

    def test_extractor_fields_win_and_are_attributed(self):
        c = C.from_document(extracted={"building_name": "Riverside Court", "country_code": "UK"},
                            text="")
        assert c.buildings == ["Riverside Court"] and c.countries == ["UK"]
        assert c.sources["Riverside Court"].startswith("field:")

    def test_the_file_name_is_the_last_resort(self):
        c = C.from_document(file_name="Bishopsgate_Tower_EICR_2026.pdf")
        assert c.buildings and "Bishopsgate" in c.buildings[0]
        assert c.sources[c.buildings[0]] == "filename"

    def test_a_document_that_says_nothing_claims_nothing(self):
        assert C.from_document(text="Invoice for services rendered. Total £1,200.").is_empty()


class TestNameOverlap:
    def test_exact_and_contained_names_match(self):
        assert C.name_overlap("Riverside Court", ["Riverside Court, Manchester"])[0] == 1.0

    def test_different_buildings_do_not(self):
        assert C.name_overlap("Bishopsgate Tower", ["Riverside Court"])[0] == 0.0

    def test_generic_words_alone_are_not_a_match(self):
        # "Tower" and "House" are shared by half a portfolio; matching on them is matching
        # on nothing.
        assert C.name_overlap("The Tower House", ["Bishopsgate Tower"])[0] == 0.0


# ── the checks ──────────────────────────────────────────────────────────────────────

class TestCompare:
    def test_the_building_it_names_is_this_one(self):
        c = C.from_document(extracted={"building_name": "Riverside Court"})
        f = find(V.compare(c, riverside()), "building_name")
        assert f.direction == V.SUPPORTS

    def test_the_building_it_names_is_another_one(self):
        c = C.from_document(extracted={"building_name": "Bishopsgate Tower"})
        f = find(V.compare(c, riverside()), "building_name")
        assert f.direction == V.CONFLICTS and "Bishopsgate" in f.message

    def test_a_country_that_disagrees_is_a_conflict_and_says_why(self):
        c = C.from_document(extracted={"country": "Singapore"})
        f = find(V.compare(c, riverside()), "country")
        assert f.direction == V.CONFLICTS
        assert "Regulations" in f.message

    def test_a_vendor_the_building_does_not_know_is_a_conflict(self):
        c = C.from_document(extracted={"vendor_name": "Gulf Cooling LLC"})
        f = find(V.compare(c, riverside()), "vendor")
        assert f.direction == V.CONFLICTS and "Aqua Services Ltd" in f.message

    def test_a_vendor_the_building_does_know_supports_it(self):
        c = C.from_document(extracted={"vendor_name": "Aqua Services Ltd"})
        assert find(V.compare(c, riverside()), "vendor").direction == V.SUPPORTS

    def test_a_vendor_is_unknown_not_wrong_when_the_building_knows_no_vendors(self):
        c = C.from_document(extracted={"vendor_name": "Gulf Cooling LLC"})
        f = find(V.compare(c, riverside(vendors=[])), "vendor")
        assert f.direction == V.UNKNOWN

    def test_asset_references_are_checked_both_ways(self):
        assert find(V.compare(C.from_document(text="Plant: B-006-AHU-01"), riverside()),
                    "assets").direction == V.SUPPORTS
        assert find(V.compare(C.from_document(text="Plant: B-007-AHU-09"), riverside()),
                    "assets").direction == V.CONFLICTS

    def test_a_postcode_is_identity(self):
        assert find(V.compare(C.from_document(text="Site address: M3 4LZ"), riverside()),
                    "postcode").direction == V.SUPPORTS

    def test_a_new_building_says_it_has_nothing_to_check_against(self):
        f = find(V.compare(C.from_document(text=""), new_building()), "building_evidence")
        assert f is not None and f.direction == V.UNKNOWN


class TestVerdict:
    def _v(self, c, o=None):
        o = o or riverside()
        return V.verdict_for(V.compare(c, o), o)

    def test_naming_the_building_is_a_match(self):
        verdict, conf = self._v(C.from_document(extracted={"building_name": "Riverside Court"}))
        assert verdict == V.MATCHED and conf > 0.5

    def test_the_country_agreeing_is_not_a_match(self):
        # Half the portfolio is in the UK. Agreement here is not identification.
        verdict, _ = self._v(C.from_document(extracted={"country": "United Kingdom"}))
        assert verdict == V.UNCERTAIN

    def test_a_known_vendor_alone_is_not_a_match_either(self):
        verdict, _ = self._v(C.from_document(extracted={"vendor_name": "Aqua Services Ltd"}))
        assert verdict == V.UNCERTAIN

    def test_a_document_that_names_nothing_is_uncertain(self):
        verdict, _ = self._v(C.from_document(text="Invoice for services rendered."))
        assert verdict == V.UNCERTAIN

    def test_one_conflict_outweighs_any_amount_of_agreement(self):
        c = C.from_document(extracted={"building_name": "Riverside Court",
                                       "country": "Singapore",
                                       "vendor_name": "Aqua Services Ltd"})
        verdict, _ = self._v(c)
        assert verdict == V.MISMATCH

    def test_the_first_document_on_a_new_building_is_never_matched_on_thin_air(self):
        verdict, _ = self._v(C.from_document(text="Annual service report."), new_building())
        assert verdict == V.UNCERTAIN

    def test_but_a_new_building_can_still_be_identified_by_name(self):
        o = new_building()
        verdict, _ = self._v(C.from_document(extracted={"building_name": o.name}), o)
        assert verdict == V.MATCHED


# ── suggesting the right building ───────────────────────────────────────────────────

PORTFOLIO = [
    {"building_id": RIVERSIDE, "name": "Riverside Court", "building_code": "B-006",
     "country_code": "UK", "region": "Greater Manchester", "postcode": "M3 4LZ"},
    {"building_id": BISHOPSGATE, "name": "Bishopsgate Tower", "building_code": "B-002",
     "country_code": "UK", "region": "London", "postcode": "EC2M 4QS"},
]


class TestSuggestion:
    def _cands(self, c, selected=RIVERSIDE):
        scored = [{"building_id": r["building_id"], "name": r["name"],
                   "score": V.score_candidate(c, r), "selected": r["building_id"] == selected}
                  for r in PORTFOLIO]
        scored = [s for s in scored if s["score"] > 0]
        scored.sort(key=lambda x: -x["score"])
        return scored

    def test_the_building_the_document_names_is_offered(self):
        c = C.from_document(extracted={"building_name": "Bishopsgate Tower"})
        s = V.suggestion_from(self._cands(c), RIVERSIDE)
        assert s and s["building_id"] == BISHOPSGATE

    def test_nothing_is_offered_when_the_chosen_building_fits_best(self):
        c = C.from_document(extracted={"building_name": "Riverside Court"})
        assert V.suggestion_from(self._cands(c), RIVERSIDE) is None

    def test_nothing_is_offered_on_a_country_both_share(self):
        # Two UK buildings score alike; "did you mean the other one?" would be noise.
        c = C.from_document(extracted={"country": "United Kingdom"})
        assert V.suggestion_from(self._cands(c), RIVERSIDE) is None

    def test_a_postcode_alone_identifies_the_other_building(self):
        c = C.from_document(text="Site address: 1 Bishopsgate, London EC2M 4QS")
        s = V.suggestion_from(self._cands(c), RIVERSIDE)
        assert s and s["building_id"] == BISHOPSGATE


class TestQuestion:
    def test_a_mismatch_with_a_suggestion_offers_the_swap(self):
        c = C.from_document(extracted={"building_name": "Bishopsgate Tower"})
        findings = V.compare(c, riverside())
        q = V.question_for(V.MISMATCH, findings, {"name": "Bishopsgate Tower"}, "Riverside Court")
        assert "Bishopsgate Tower" in q and "Riverside Court" in q and q.endswith("?")

    def test_a_mismatch_without_one_asks_for_a_reason(self):
        c = C.from_document(extracted={"vendor_name": "Gulf Cooling LLC"})
        q = V.question_for(V.MISMATCH, V.compare(c, riverside()), None, "Riverside Court")
        assert "why" in q.lower() and q.endswith("?")

    def test_uncertainty_says_it_cannot_confirm_rather_than_that_it_is_wrong(self):
        q = V.question_for(V.UNCERTAIN, [], None, "Riverside Court")
        assert "cannot confirm" in q and "Riverside Court" in q


# ── the explanation ─────────────────────────────────────────────────────────────────

class TestExplanationAssessment:
    def _assess(self, text_in, findings=None):
        import asyncio
        from src.engines.ingestion import explanation as E
        return asyncio.run(E.assess(explanation=text_in, findings=findings or [],
                                    building_name="Riverside Court",
                                    document_name="contract.pdf", verdict=V.MISMATCH))

    @pytest.mark.parametrize("answer", ["yes", "ok", "just do it", "it's fine", "proceed", ""])
    def test_an_answer_that_is_not_a_reason_never_resolves_anything(self, answer):
        out = self._assess(answer)
        assert out["resolves"] is False

    def test_without_a_model_an_explanation_is_recorded_and_the_warning_stands(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
        out = self._assess("They took over the contract from the previous managing agent in May.")
        assert out["resolves"] is False and out["method"] == "no_model"
        assert "override" in out["reason"]


# ── the decision ────────────────────────────────────────────────────────────────────

class FakeResult:
    def __init__(self, row): self._row = row
    def mappings(self): return self
    def first(self): return self._row
    def scalar(self): return (self._row or {}).get("id")


class FakeSession:
    """Answers the case SELECT with a canned row and remembers the UPDATE's parameters."""

    def __init__(self, case):
        self.case, self.updates, self.audits = case, [], []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.strip().upper().startswith("SELECT"):
            return FakeResult(self.case)
        if "ingestion_audit_events" in sql:
            self.audits.append(params or {})
            return FakeResult({"id": str(uuid4())})
        self.updates.append(params or {})
        return FakeResult(None)

    async def commit(self):
        pass


def case_row(**over):
    row = {"id": uuid4(), "organization_id": uuid4(), "actor_user_id": uuid4(),
           "actor_role": "admin", "document_name": "contract.pdf", "document_id": None,
           "selected_building_id": UUID(RIVERSIDE), "final_building_id": None,
           "suggested_building_id": None, "verdict": V.MISMATCH, "confidence": 0.6,
           "status": V.NEEDS_CONFIRMATION, "outcome": None, "findings": [], "claims": {},
           "ontology": {"name": "Riverside Court"}, "candidates": [], "question": "why?",
           "explanation": "they took over in May", "assessment": {"resolves": False},
           "events": [], "created_at": datetime.now(timezone.utc),
           "updated_at": datetime.now(timezone.utc), "decided_at": None, "decided_by": None}
    row.update(over)
    return row


def decide(case, approve):
    import asyncio
    s = FakeSession(case)
    out = asyncio.run(V.decide(s, case_id=case["id"], approve=approve,
                               actor_user_id=uuid4(), actor_role="admin", note=None))
    return out, s


class TestDecision:
    def test_a_yes_over_a_standing_warning_is_an_override(self):
        _, s = decide(case_row(), True)
        assert s.updates[0]["oc"] == "overridden"
        assert s.audits[0]["oc"] == "overridden" and s.audits[0]["ap"] is not None

    def test_a_yes_after_the_agent_was_satisfied_is_approved_on_confirmation(self):
        _, s = decide(case_row(assessment={"resolves": True}), True)
        assert s.updates[0]["oc"] == "approved_on_confirmation"

    def test_a_yes_on_a_clean_case_is_an_acceptance(self):
        _, s = decide(case_row(verdict=V.MATCHED, status=V.VALIDATED, assessment={}), True)
        assert s.updates[0]["oc"] == "accepted"

    def test_a_yes_after_moving_it_is_recorded_as_a_reassignment(self):
        row = case_row(verdict=V.MATCHED, status=V.VALIDATED,
                       events=[{"event": "reassigned", "to": "Bishopsgate Tower"}])
        _, s = decide(row, True)
        assert s.updates[0]["oc"] == "reassigned"

    def test_a_no_rejects_it_and_leaves_no_final_building(self):
        out, s = decide(case_row(), False)
        assert s.updates[0]["oc"] == "rejected" and s.updates[0]["ok"] is False
        assert s.audits[0]["ap"] is None
        assert "not ingested" in out["message"].lower()

    def test_a_decided_case_cannot_be_decided_again(self):
        out, s = decide(case_row(status=V.ACCEPTED, outcome="accepted"), True)
        assert out["ok"] is False and out["error"] == "case_closed"
        assert not s.updates

    @pytest.mark.parametrize("outcome", ["accepted", "overridden", "reassigned",
                                         "approved_on_confirmation", "rejected"])
    def test_every_outcome_the_protocol_writes_is_one_the_audit_accepts(self, outcome):
        from src.engines.auth.ingestion_audit import OUTCOMES
        assert outcome in OUTCOMES

    def test_the_audit_carries_the_warning_the_explanation_and_the_case(self):
        _, s = decide(case_row(), True)
        a = s.audits[0]
        assert a["w"] == "why?" and a["x"] == "they took over in May"
        assert a["case"] is not None
        assert a["d"] and "case_id" in a["d"]


def test_only_a_decided_case_may_be_ingested():
    from src.engines.ingestion.validation import _row_to_case
    for status, expected in ((V.VALIDATED, False), (V.NEEDS_CLARIFICATION, False),
                             (V.NEEDS_CONFIRMATION, False), (V.ACCEPTED, True),
                             (V.OVERRIDDEN, True), (V.REASSIGNED, True), (V.REJECTED, False)):
        assert _row_to_case({"status": status})["may_ingest"] is expected


# ── the routes hold the same boundary as everything else ────────────────────────────

from datetime import datetime as _dt, timezone as _tz  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import auth as auth_routes  # noqa: E402
from src.app import app  # noqa: E402
from src.db import get_session  # noqa: E402
from src.engines.auth.tokens import Principal  # noqa: E402

ORG = UUID("11111111-1111-5111-8111-111111111111")
MINE, THEIRS = uuid4(), uuid4()


def principal(role="user", buildings=None, can_ingest=True):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=_dt.now(_tz.utc), password_changed_at=0,
                     role=role, can_ingest=can_ingest, building_ids=buildings)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    def add(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    async def commit(self): pass


async def _exploding():
    yield Exploding()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


class TestRoutes:
    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/ingestion/validate"), ("GET", "/api/ingestion/cases"),
        ("GET", "/api/ingestion/audit"),
    ])
    def test_every_route_needs_a_token(self, client, method, path):
        r = client.request(method, path, json={"building_id": str(MINE)})
        assert r.status_code == 401 and r.json()["detail"]["reason"] == "missing_token"

    def test_validating_against_another_building_is_refused(self, client):
        as_(principal(buildings=(MINE,)))
        r = client.post("/api/ingestion/validate", json={"building_id": str(THEIRS),
                                                         "document_name": "x.pdf"})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "building_not_allocated"

    def test_a_read_only_user_cannot_start_a_validation(self, client):
        as_(principal(buildings=(MINE,), can_ingest=False))
        r = client.post("/api/ingestion/validate", json={"building_id": str(MINE),
                                                         "document_name": "x.pdf"})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "cannot_ingest"

    def test_a_read_only_user_cannot_decide_one_either(self, client):
        as_(principal(buildings=(MINE,), can_ingest=False))
        r = client.post(f"/api/ingestion/cases/{uuid4()}/decide", json={"approve": True})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "cannot_ingest"

    def test_the_decision_has_no_default(self, client):
        # `approve` is required: a client that forgets it must not be read as a yes.
        as_(principal(role="admin"))
        r = client.post(f"/api/ingestion/cases/{uuid4()}/decide", json={})
        assert r.status_code == 422

    def test_reading_another_buildings_ontology_is_refused(self, client):
        as_(principal(buildings=(MINE,)))
        r = client.get(f"/api/ingestion/buildings/{THEIRS}/ontology")
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "building_not_allocated"

    def test_the_protocol_does_not_branch_on_role(self):
        """An admin gets the same checks as a user — the spec's point, and the code's.

        Read from the source: no branch in the engine or the router asks what role the
        caller has, because selecting the wrong building is a mistake seniority does not
        prevent.
        """
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "src"
        for rel in ("engines/ingestion/validation.py", "api/routes/ingestion.py"):
            src = (root / rel).read_text(encoding="utf-8")
            for marker in ("is_admin", "role ==", 'role in (', "at_least("):
                assert marker not in src, (rel, marker)
